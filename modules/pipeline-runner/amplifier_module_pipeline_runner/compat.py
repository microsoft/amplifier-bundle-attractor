"""Runner-engine compatibility assertion.

Chosen shape: startup compatibility assertion (compat-assert).

Tradeoff rationale
------------------
Three shapes were available to close the version-skew window:

1. **Pin the dep to a commit/tag** — closes the window structurally but
   requires a manual bump every time the engine changes.  Release-flow cost:
   whoever merges an engine PR must also bump the runner's pinned commit in
   ``pipeline-runner/pyproject.toml`` and cut a new release.  In a fast-moving
   repo with a single maintainer this is a constant friction tax and a known
   source of forgotten bumps.

2. **Collapse to a single package** — eliminates the skew problem entirely but
   is a larger refactor that changes the module boundary and the install story
   (users who want only the engine can no longer install it without the runner).
   Deferred: the boundary is intentional (engine is usable standalone).

3. **Startup compatibility assertion (chosen)** — keeps the floating dep but
   adds a check at runner startup that probes for a known-new symbol and fails
   loudly with an actionable message before any node runs.  The skew window
   still exists in theory (a stale cache could still be resolved), but it is
   detected immediately rather than mid-run, and the error message names both
   the required version and the resolution command.  This is the lowest
   friction shape for a single-repo, actively-developed package.

``amplifier-foundation @main`` float
-------------------------------------
The ``amplifier-foundation`` dep in ``pipeline-runner/pyproject.toml`` also
floats on ``@main``.  It is a separate package from a different repository;
pinning it requires coordinating with that repo's release cadence.  Explicit
deferral: the foundation symbols the runner uses (``Bundle``, ``load_bundle``
-- imported lazily inside functions, never at module level) are long-stable
core API, unlike the recently-added engine module that caused the incident,
so there is currently no discriminating symbol to probe.  Apply the same
compat-assert pattern here the moment the runner starts depending on a
recently-added foundation symbol.
"""

from __future__ import annotations

import importlib

# Minimum required engine features — each entry is a (module, symbol) pair.
# Add a new entry here when the runner imports a symbol that was absent in an
# older engine snapshot (the incident: remote_dot absent <= bc6cbec, #96).
_REQUIRED_ENGINE_SYMBOLS: list[tuple[str, str]] = [
    ("amplifier_module_loop_pipeline.remote_dot", "load_remote_or_local_graph"),
]

# Human-readable minimum description for the actionable error message.
_ENGINE_MIN_DESCRIPTION = "engine with remote_dot support (commit bc6cbec or later, PR #96)"


class IncompatibleEngineError(RuntimeError):
    """Raised when the installed engine is missing symbols required by this runner.

    Carries the full actionable message so callers (CLI or API) can surface it
    appropriately: the CLI catches this and calls sys.exit(1); the API path
    lets it propagate as a RuntimeError so the caller can handle it.
    """


def check_engine_compatibility() -> None:
    """Assert that the installed engine is compatible with this runner.

    Called at CLI startup and at the top of ``drive_engine()`` (before any
    engine imports execute) so a version-skew crash surfaces immediately with
    an actionable message rather than mid-pipeline with an opaque ImportError.

    Raises ``IncompatibleEngineError`` with an actionable message if the engine
    is incompatible.  Does nothing if the engine is compatible.  Idempotent:
    cheap repeated calls are safe (symbol probe only, no I/O).

    The CLI entry point (``cli.main``) catches ``IncompatibleEngineError`` and
    converts it to ``sys.exit(1)`` so the shell sees a non-zero exit code.
    The API path (``drive_engine``) lets the exception propagate as a
    ``RuntimeError`` subclass.
    """
    missing: list[str] = []
    for module_name, symbol_name in _REQUIRED_ENGINE_SYMBOLS:
        try:
            mod = importlib.import_module(module_name)
            if not hasattr(mod, symbol_name):
                missing.append(f"{module_name}.{symbol_name}")
        except (ImportError, ModuleNotFoundError):
            missing.append(f"{module_name} (module not found)")

    if not missing:
        return

    # Build actionable message — names the missing symbols, the required
    # engine description, and the reinstall command.
    missing_str = "\n  ".join(missing)
    message = (
        f"attractor: INCOMPATIBLE ENGINE — runner requires {_ENGINE_MIN_DESCRIPTION}\n"
        f"  but the installed engine is missing:\n"
        f"  {missing_str}\n"
        f"\n"
        f"  This is a version-skew problem: the runner was installed with a newer\n"
        f"  engine dependency than uv resolved from its cache.\n"
        f"\n"
        f"  Fix: reinstall the runner, forcing a fresh engine resolution:\n"
        f"    uv tool install --reinstall \\\n"
        f"      'amplifier-module-pipeline-runner @ "
        f"git+https://github.com/microsoft/amplifier-bundle-attractor"
        f"@main#subdirectory=modules/pipeline-runner'\n"
        f"\n"
        f"  Or, if running from the repo tree:\n"
        f"    cd modules/pipeline-runner && uv sync --reinstall"
    )
    raise IncompatibleEngineError(message)
