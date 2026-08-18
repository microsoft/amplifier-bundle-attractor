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

``amplifier-foundation``: published float, resolver-local pin
--------------------------------------------------------------
The ``amplifier-foundation`` dep in ``pipeline-runner/pyproject.toml`` is
declared in two places on purpose, and the split is the whole design:

* ``[project].dependencies`` -- ``@main``.  This is what consumers resolve.
* ``[tool.uv] override-dependencies`` -- a SHA.  This is what *we* resolve.

**Why the published requirement must float.**  uv identifies a git dependency
by its *ref string*, not by the commit that ref resolves to.  Two requirements
for the same package whose ref strings differ are a hard resolution error --
``Requirements contain conflicting URLs for package `amplifier-foundation``` --
even when one ref is an ancestor of the other.  Every other
``amplifier-foundation`` declaration in the ecosystem (this repo's bundle
includes, ``amplifier-app-wiki-weaver``, and downstream of it ``repo-weaver``)
names ``@main``, so any non-``@main`` ref on the published line makes
pipeline-runner un-co-installable with all of them.  A SHA pin on that line did
exactly that and blocked every fresh wiki-weaver install by every install verb
(#213).  A release tag would fail identically -- ``@v2.1.2`` is just as
different from ``@main`` as a SHA is -- and a bare version range cannot resolve
at all, since foundation publishes no artifacts to PyPI.  So the published line
is not a preference; it is the only ref that co-resolves.

**Why the pin still exists.**  The original problem
(microsoft-amplifier/amplifier-support#391, PR #201) is real: this repo commits
no lockfile, so an unconstrained ``@main`` meant every CI run re-resolved
foundation's live tip and an upstream change could redden CI with no local
change.  ``[tool.uv] override-dependencies`` closes that window without
touching what anybody else resolves.  uv applies an override only to the
project it is resolving as *root* -- precisely how CI consumes this module
(``uv sync`` with ``working-directory: modules/pipeline-runner``) -- and does
not apply it when this package is somebody else's dependency.  CI gets a fixed
foundation commit; consumers get plain ``@main``.

**Two mechanisms that look right and are not** (both measured, so nobody has to
re-derive them):

* ``[tool.uv.sources]`` with a ``rev`` does *not* stay local.  uv reads a git
  dependency's sources straight out of its checked-out ``pyproject.toml``, so
  the rev reappears in the dependent's resolution and the conflict returns
  verbatim -- even though the *built wheel's* ``Requires-Dist`` says ``@main``.
* ``[tool.uv] constraint-dependencies`` cannot carry a git ref at all: a URL in
  a constraint is itself a second conflicting URL for the package, so the
  resolver rejects it before it can constrain anything.

**Bump procedure.**  Update the SHA in ``[tool.uv] override-dependencies`` of
``pipeline-runner/pyproject.toml`` -- *not* the ``[project].dependencies``
line, which must stay ``@main`` -- then verify against this module's test suite
(``uv run pytest -q`` in ``modules/pipeline-runner``).  Bumping is CI-hygiene
work only; it changes nothing for installed users, who track foundation
``main`` and are guarded by the startup assertion below.  Apply the
compat-assert pattern to a foundation symbol the moment the runner starts
depending on a recently-added one (today it uses only long-stable core API --
``Bundle``, ``load_bundle``, imported lazily inside functions, and this
module's own suite exercises them through a stand-in -- so there is no
discriminating symbol to probe).
"""

from __future__ import annotations

import importlib

# Minimum required engine features — each entry is a (module, symbol) pair.
# Add a new entry here when the runner imports a symbol that was absent in an
# older engine snapshot (the incident: remote_dot absent <= bc6cbec, #96).
_REQUIRED_ENGINE_SYMBOLS: list[tuple[str, str]] = [
    ("amplifier_module_loop_pipeline.remote_dot", "load_remote_or_local_graph"),
    # issue #283: drive_engine() imports the engine's shared spawn-resolver to
    # feed `resolvable_profiles` into the startup provider preflight.  This
    # entry is also the only gate we CAN have on that keyword argument itself:
    # `check_provider_preflight` existed before it gained `resolvable_profiles`,
    # so a symbol probe on the function would pass against a stale engine and
    # the call would then die on a bare `TypeError` mid-run (the skew this
    # module's `_load_graph` docstring warns about).  `_spawn_resolvable_agents`
    # and that keyword landed in the SAME engine commit (ccbd89f, PR #280), so
    # probing the symbol is a faithful proxy for the signature.
    ("amplifier_module_loop_pipeline", "_spawn_resolvable_agents"),
]

# Human-readable minimum description for the actionable error message.
_ENGINE_MIN_DESCRIPTION = (
    "engine with remote_dot support (commit bc6cbec or later, PR #96) and the "
    "shared spawn-resolver / resolvable_profiles preflight argument "
    "(commit ccbd89f or later, PR #280)"
)


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
