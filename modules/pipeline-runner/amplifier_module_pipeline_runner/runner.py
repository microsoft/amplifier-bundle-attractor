"""Reusable engine harness: run an arbitrary attractor DOT pipeline.

Two-layer public API:

* ``drive_engine`` -- low-level. Caller supplies an already-built
  ``coordinator`` (with ``session.spawn`` already registered on it) and this
  function parses/transforms/validates the graph, seeds context, and drives
  ``PipelineEngine`` directly. This is the seam a consumer with its own
  session/bundle lifecycle (e.g. an existing resolver) plugs into.
* ``run_pipeline`` -- high-level convenience. Builds the prepared bundle,
  session, and spawn wiring itself, then calls ``drive_engine``. This is what
  the CLI uses.

Extracted from dot-graph-runner's ``dot_graph_runner/runner.py`` (~429 lines),
split into this two-function shape per the attractor-runner design (slice 0).
Uses ONLY the attractor engine's public modules
(``amplifier_module_loop_pipeline.{context,dot_parser,engine,handlers,backend,
validation,transforms}``).

Why the direct-engine path: the mounted loop-pipeline orchestrator (driven via
``session.execute()``) builds its own internal ``PipelineContext`` and exposes
``params`` only as a nested dict for LLM-prompt ``$key`` expansion -- there is
no seam to seed flat context keys that way, so a ``--param`` would never reach
``tool_command``/``tool_env``. Driving the engine directly is what lets a
``--param`` reach a tool node.

Every LLM (``box``) node spawns a full ``attractor-agent-*`` coding agent (its
own ``loop-agent`` orchestrator + filesystem/bash/search tools) via the
``session.spawn`` capability.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from amplifier_module_loop_pipeline.graph import Graph
    from amplifier_module_loop_pipeline.outcome import Outcome

# Maps llm_provider node values -> child agent name, cribbed from
# agents/pipeline-runner.yaml / bundles/attractor-pipeline.yaml. This is the
# default provider->agent map used when the caller doesn't supply its own
# ``profiles`` (discover_profiles-from-graph is deferred to a later slice).
DEFAULT_PROFILES: dict[str, str] = {
    "anthropic": "attractor-agent-anthropic",
    "openai": "attractor-agent-openai",
    "gemini": "attractor-agent-gemini",
}

# Env var name per provider -- used by the CLI's fail-loud preflight check
# (a provider's API key must be present BEFORE the engine starts running).
PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# Reserved context keys that seed_context sets itself -- a user --param may
# not collide with these (see seed_context's reserved-key guard).
_RESERVED_CONTEXT_KEYS: frozenset[str] = frozenset({"context.target_dir"})

# In-process cache: load the base bundle once; install deps once, then reuse
# the offline path for subsequent runs in the same process.
_BASE_BUNDLE: Any = None
_DEPS_INSTALLED = False


@dataclass
class PipelineResult:
    """Result of a ``run_pipeline`` invocation.

    Attributes:
        status: The pipeline outcome status string (e.g. "success", "fail").
        notes: Outcome notes, truncated to 4000 chars.
        failure_reason: The outcome's ``failure_reason`` when the engine
            terminated on a failure (e.g. no-matching-edge routing), else
            ``None``. Surfaced so a consumer can distinguish/why a run failed
            without re-parsing ``notes`` -- the direct-engine ``Outcome``
            carries this where the old mounted-orchestrator JSON did.
        logs_dir: Directory containing this run's logs (including the
            written ``pipeline.dot`` source).
        raw: JSON-serialized ``{"status": ..., "notes": ...}``, truncated to
            4000 chars.
    """

    status: str
    notes: str
    logs_dir: Path
    raw: str
    failure_reason: str | None = None


def seed_context(
    context: Any, params: Mapping[str, str] | None, cwd: Path | str
) -> None:
    """Seed a ``PipelineContext`` with flat ``--param`` keys plus reserved keys.

    Each ``params`` entry is set as a flat context key via ``context.set``
    (this is what lets a ``--param`` reach ``tool_command``/``tool_env`` --
    see the module docstring). After user params are seeded, the reserved key
    ``context.target_dir`` is set to ``str(cwd)`` -- tool nodes resolve
    relative paths against this (handlers/tool.py:
    ``context.get("context.target_dir") or graph.source_dir``).

    Reserved-key guard: if any user param key collides with a reserved
    context key, this raises ``ValueError`` BEFORE seeding anything --
    silently overwriting a reserved key (or being silently overwritten by
    the reserved-key seed below) would be confusing and non-obvious.

    There is intentionally only ONE reserved key -- ``context.target_dir``.
    ``context.work_dir`` is deliberately NOT set; the engine does not read it.

    Args:
        context: A ``PipelineContext``-like object exposing ``.set(key, value)``.
        params: Flat key->value params to seed (may be None/empty).
        cwd: The pipeline's working directory.

    Raises:
        ValueError: If a user param key collides with a reserved context key.
    """
    params = params or {}
    for key in params:
        if key in _RESERVED_CONTEXT_KEYS:
            raise ValueError(f"--param key {key!r} collides with reserved context key")

    for key, value in params.items():
        context.set(key, str(value))

    context.set("context.target_dir", str(cwd))


async def _load_graph(graph_or_dot: "Graph | str", *, source_dir: str | None = None):
    """Return (graph, cleanup). If graph_or_dot is a git+https:// URL, materialize
    the remote tree (async, before parse) and parse the local entry; otherwise
    behave exactly as before. cleanup() removes any per-run materialized view.

    Thin wrapper around ``amplifier_module_loop_pipeline.remote_dot.
    load_remote_or_local_graph`` -- the single materialize/parse/cleanup
    sequence shared with the mounted ``PipelineOrchestrator.execute()`` hook,
    so the two engine entry points can't diverge. Kept as a module-level
    function (rather than inlined into ``drive_engine``) so it stays
    independently monkeypatchable in tests.

    ``source_dir`` is the directory a file-backed ROOT graph was read from.
    A remote package derives its own from the materialized entry, and every
    *child* graph already gets one (``PipelineHandler.execute`` sets
    ``child_graph.source_dir``) -- only a local root arrived with it empty,
    which made ``resolve_dot_path`` fall through to ``context.target_dir``
    (the ``--cwd`` workspace) and look for sibling bricks there instead of
    beside the pipeline.

    It is applied HERE, in the runner, rather than by passing it into the
    engine helper: the runner and the engine are separate packages resolved
    independently (see ``compat.py`` -- a floating dependency plus a
    symbol-presence assertion, deliberately not a pin). Adding a parameter to
    the engine's signature would create a skew the compatibility check cannot
    see -- the symbol still exists, so the gate passes and the call then fails
    with a bare ``TypeError``. Setting the attribute on the returned graph
    needs nothing new from the engine.
    """
    from amplifier_module_loop_pipeline.remote_dot import load_remote_or_local_graph

    graph, cleanup = await load_remote_or_local_graph(graph_or_dot)
    # Never clobber: a remote package and an already-parsed Graph carry their
    # own, and theirs is the more specific answer.
    if source_dir and not getattr(graph, "source_dir", ""):
        graph.source_dir = source_dir
    return graph, cleanup


async def drive_engine(
    graph_or_dot: "Graph | str",
    coordinator: Any,
    *,
    params: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    logs_root: Path | str,
    hooks: Any = None,
    profiles: Mapping[str, str] | None = None,
    interviewer: Any = None,
    transform: bool,
    validate: bool = True,
    source_dir: str | None = None,
    resume_checkpoint: Any = None,
) -> "Outcome":
    """Drive the attractor engine directly against an already-built coordinator.

    Compatibility gate: ``check_engine_compatibility()`` is called first
    (before any engine imports execute) so a version-skew crash surfaces as
    an actionable ``IncompatibleEngineError`` rather than a bare
    ``ModuleNotFoundError`` mid-pipeline.  This covers both the CLI path and
    any consumer using this function as a library seam (incident 2026-07-28).

    Low-level API: the caller is responsible for building the session and
    registering ``session.spawn`` on ``coordinator`` before calling this
    (see ``run_pipeline`` for the high-level convenience that does this).

    Backend / session.spawn wiring (the part that had to be gotten right):
    ``AmplifierBackend._run_with_spawn`` (backend.py) obtains everything it
    needs from the ``coordinator`` object passed to ``AmplifierBackend()``:
      - ``coordinator.get_capability("session.spawn")`` -- the spawn fn.
      - ``getattr(coordinator, "session", None)`` -- the parent session for
        lineage tracking.
      - ``getattr(coordinator, "config", None).get("agents", {})`` -- the
        per-profile agent configs, used both to resolve which bundle to
        spawn and for the recursion guard (each entry must carry an inline
        non-pipeline ``session.orchestrator``).
    The caller's ``coordinator`` must already satisfy all three lookups
    (this is naturally true of a coordinator built via
    ``PreparedBundle.create_session()`` against the attractor-pipeline
    bundle -- see ``run_pipeline``).

    Args:
        graph_or_dot: A parsed ``Graph``, or raw DOT source text to parse.
        coordinator: An already-built coordinator with ``session.spawn``
            already registered on it.
        params: Flat key->value params seeded into context (see
            ``seed_context``). Also reaches LLM ``box`` prompts via
            ``graph.params_values``.
        cwd: Working directory for the pipeline (tool/box nodes write here).
            Defaults to ``Path.cwd()`` if not given.
        logs_root: Directory for this run's engine logs.
        hooks: Optional hooks object forwarded to the handler registry and engine.
        profiles: llm_provider -> agent-name routing map. Defaults to
            ``DEFAULT_PROFILES`` if not given.
        interviewer: Optional interviewer object forwarded to the handler
            registry (human-in-the-loop gate seam).
        transform: Required keyword. If True, run ``apply_transforms`` on the
            graph before validation/execution (stylesheet routing only fires
            if the graph sets ``model_stylesheet``).
        validate: If True (default), run ``validate_or_raise`` on the graph
            before execution -- fails loud on graph-shape problems before
            spending an LLM call.
        resume_checkpoint: A ``Checkpoint`` that has ALREADY passed the resume
            validation ladder's rungs 1-5
            (``checkpoint.load_checkpoint_for_resume``). When given, this
            drives ``engine.resume(...)`` instead of ``engine.run()``, after
            running the ladder's rung 6 (structural validity) against the
            parsed+transformed graph this call is about to execute -- that
            rung needs the real graph, which only exists at this point.
            ``None`` (the default) is the fresh path: nothing here reads a
            checkpoint, so a stale or foreign ``checkpoint.json`` in
            ``logs_root`` cannot affect the run.

    Returns:
        The engine's ``Outcome`` (``outcome.status.value``, ``outcome.notes``).
    """
    # Compat gate: probe required engine symbols before any engine import
    # executes.  Raises IncompatibleEngineError with an actionable message on
    # skew; idempotent (symbol probe only, no I/O).
    from .compat import check_engine_compatibility

    check_engine_compatibility()

    from amplifier_module_loop_pipeline.backend import AmplifierBackend
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.engine import PipelineEngine
    from amplifier_module_loop_pipeline.handlers import HandlerContext, HandlerRegistry
    from amplifier_module_loop_pipeline.transforms import apply_transforms
    from amplifier_module_loop_pipeline.validation import validate_or_raise

    graph, _source_cleanup = await _load_graph(graph_or_dot, source_dir=source_dir)

    try:
        resolved_cwd = Path(cwd) if cwd is not None else Path.cwd()

        context = PipelineContext()
        seed_context(context, params, resolved_cwd)

        if transform:
            graph = apply_transforms(graph, context)

        if validate:
            # Fail loud on graph-shape problems before spending an LLM call.
            validate_or_raise(graph)

        # Provider preflight (issue #155, EXTENSIONS.md section 36): before
        # the walk begins, cross-check every node's DECLARED llm_provider
        # against the profiles this run mounts (and each profile's statically
        # checkable credential env var) and refuse to start, naming each
        # failing node, its provider, and the missing credential.  On this
        # path the AmplifierBackend below spawns per-provider agents via the
        # `profiles` map -- a declared provider whose profile cannot construct
        # its provider (missing API key) used to crash on every visit and
        # drain the entire iteration budget in a crash loop
        # (`resolve_latest_for: no adapter found for provider 'openai'`).
        # Always on: the incident path was exactly this invoker.  A hermetic
        # harness that mocks spawn satisfies the static check by setting the
        # provider's env var (presence is checked, never validity).
        #
        # Issue #283 -- the residual of #195/#280 on THIS path.  A profile is
        # a STRING naming an agent; knowing the string is MAPPED is not
        # knowing the agent it names can be RESOLVED.  "profile mounted +
        # credential set" therefore used to accept a configuration whose
        # EVERY spawn was guaranteed to fail, and the run drained its whole
        # budget in exactly the #155 crash loop instead of refusing at
        # startup.  #280 closed that at `PipelineOrchestrator.execute()`;
        # drive_engine still passed no `resolvable_profiles` and so stayed
        # fail-open -- and drive_engine IS the original #155 incident invoker
        # (`attractor run` -> run_pipeline -> here).
        #
        # The set comes from the engine's OWN shared resolver,
        # `_spawn_resolvable_agents` -- the single home #280 established for
        # this rule, and exactly what `execute()` step 5b calls.  Re-deriving
        # `coordinator.config["agents"]` here would be a second copy of a rule
        # that MUST stay identical to what the spawn actually resolves
        # against; a preflight seeing a different set than the backend is
        # precisely the #196 false-refusal disease.
        #
        # Fail-closed when knowable, benefit of the doubt when not -- the same
        # posture as execute():
        #   * `None` (no `session.spawn` capability, or a coordinator whose
        #     config/agents is not a statically inspectable Mapping -- e.g. a
        #     bare stub coordinator) means "not knowable here, do not police
        #     it", never "everything resolves".  Those paths keep their
        #     current behavior exactly.
        #   * a discovery CRASH becomes the empty set (refuse), never None:
        #     unknowable-because-it-broke must not silently re-open the hole.
        from amplifier_module_loop_pipeline import _spawn_resolvable_agents
        from amplifier_module_loop_pipeline.preflight import check_provider_preflight

        # ONE profiles dict, shared with the AmplifierBackend constructed
        # below, so the map the preflight judges is literally the object the
        # backend routes with -- not an equal-looking rebuild of it.
        resolved_profiles = dict(profiles or DEFAULT_PROFILES)
        try:
            resolvable_profiles = _spawn_resolvable_agents(coordinator)
        except Exception:
            resolvable_profiles = frozenset()
        check_provider_preflight(
            graph,
            profiles=resolved_profiles,
            resolvable_profiles=resolvable_profiles,
        )

        # Default engine/handler observability to the coordinator's own hook stack
        # when the caller didn't supply hooks. A mounted observability hook (e.g.
        # a session-level logging/telemetry hook composed onto the bundle) lives on
        # ``coordinator.hooks``; the mounted-orchestrator path reaches it because
        # the session hands the orchestrator ``coordinator.hooks`` and it forwards
        # that same object into ``PipelineEngine(hooks=...)``. Driving the engine
        # directly, we must do the same, or the engine's ``pipeline:*`` events (and
        # handler-emitted ``provider:*``/``tool:*`` events) are emitted into nothing
        # and never reach the session's observers. ``getattr(..., None)`` keeps
        # bare test-stub coordinators (which may lack ``.hooks``) safe, and the
        # ``hooks is not None`` guard preserves an explicit caller override.
        effective_hooks = hooks if hooks is not None else getattr(coordinator, "hooks", None)

        backend = AmplifierBackend(
            # The SAME coordinator the preflight above read
            # `config["agents"]` from, and the SAME profiles dict it judged:
            # `AmplifierBackend._run_with_spawn` looks each profile up in
            # `coordinator.config["agents"]`, which is precisely the mapping
            # `_spawn_resolvable_agents(coordinator)` returned the keys of.
            # Nothing between that call and this one mutates either.
            coordinator=coordinator,
            profiles=resolved_profiles,
        )
        registry = HandlerRegistry(
            HandlerContext(
                backend=backend, hooks=effective_hooks, interviewer=interviewer
            )
        )
        engine = PipelineEngine(
            graph=graph,
            context=context,
            handler_registry=registry,
            logs_root=str(logs_root),
            hooks=effective_hooks,
        )

        if resume_checkpoint is not None:
            # Ladder rung 6, against the graph actually about to execute.
            from amplifier_module_loop_pipeline.checkpoint import (
                verify_checkpoint_structure,
            )

            verify_checkpoint_structure(resume_checkpoint, graph)
            return await engine.resume(
                resume_checkpoint,
                # --cwd is process-level wiring that cannot be serialized, so
                # the RESUMING invocation owns it (it behaves exactly as on
                # `run`). Everything else in the context is restored verbatim
                # from the checkpoint.
                context_overrides={"context.target_dir": str(resolved_cwd)},
            )

        return await engine.run()
    finally:
        _source_cleanup()


async def _resolve_agent_bundle(agent_name: str, config: dict[str, Any]) -> Any:
    """Resolve a per-node agent into a full, self-contained child Bundle.

    Adapted structurally from dot-graph-runner's ``_resolve_agent_bundle``.
    The recursion-avoidance mechanism: every child agent must carry an inline
    ``session.orchestrator`` set to a NON-pipeline orchestrator
    (``loop-agent``). Without it the spawned child inherits the parent's
    ``loop-pipeline`` orchestrator and re-runs the whole DOT (infinite
    recursion).

    Only the **inline** ``config`` shape is accepted -- a full agent dict with
    its own ``session`` (inline ``loop-agent`` orchestrator), ``providers``,
    ``tools``, ``hooks``, ``instruction``. This is what attractor-pipeline's
    ``agents:`` block declares.

    Layer-1 (the child's base/system prompt) is delivered by ``loop-agent``'s
    provider-default selection (``context/system-<provider>.md``, chosen from
    the child's ``providers``), or by an explicit ``system_prompt`` /
    ``system_prompt_file`` in the child's orchestrator config. ``loop-agent``
    is fail-loud on an empty Layer-1, so a successful spawn proves the real
    prompt was resolved. Agent ``context.include`` is deliberately NOT
    processed here -- ``loop-agent`` treats context includes as additive
    context, never as Layer-1, and every attractor agent leans on the
    provider default.

    The legacy ``{"bundle": "attractor:agents/<name>"}`` reference shape is no
    longer supported: attractor-pipeline's agents are all inline, so the
    indirection was dead. It now fails loud with an actionable message rather
    than silently carrying a resolution path no shipped config exercises.
    """
    if isinstance(config, dict) and config.get("bundle"):
        raise ValueError(
            f"Agent '{agent_name}' uses the removed "
            f'\'{{"bundle": "{config["bundle"]}"}}\' reference shape. '
            "Inline the agent definition (session/providers/tools/instruction) "
            "instead -- attractor agents are declared inline."
        )

    from amplifier_foundation import Bundle

    return Bundle(
        name=agent_name,
        version="1.0.0",
        session=config.get("session", {}),
        providers=config.get("providers", []),
        tools=config.get("tools", []),
        hooks=config.get("hooks", []),
        instruction=config.get("instruction")
        or config.get("system", {}).get("instruction"),
    )


def make_spawn_fn(
    prepared: Any,
    cwd: Path | None = None,
    *,
    child_constraint: Callable[[Any], Any] | None = None,
    spawn_timeout: float | None = None,
):
    """Build the ``session.spawn`` capability for a prepared bundle.

    Adapted from dot-graph-runner's ``make_spawn_fn``. Each pipeline node
    spawns a full child sub-session built from one of the bundle's
    per-provider agents (resolved to its own ``loop-agent`` orchestrator +
    tools).

    This signature matches what ``AmplifierBackend._run_with_spawn`` calls
    (amplifier_module_loop_pipeline/backend.py) regardless of whether the
    engine is driven via the mounted orchestrator or directly -- the spawn
    capability itself is unchanged by that switch.

    ``cwd`` is the pipeline working directory (``--cwd``). It is threaded
    explicitly into every box-node child session as ``session_cwd`` so the
    agent's filesystem/bash tools are rooted at ``--cwd`` -- mirroring how
    tool nodes get ``context.target_dir`` set explicitly. Without this,
    ``PreparedBundle.spawn`` falls back to inheriting the parent session's
    working_dir, which is fragile and leaves box nodes writing to the
    process cwd instead of ``--cwd``. This is the load-bearing host/DTU cwd
    fix -- preserve the ``session_cwd=cwd`` argument exactly.

    ``child_constraint`` (optional) is a caller-supplied hook that receives
    the resolved child ``Bundle`` and returns a (possibly modified) child
    ``Bundle`` -- the generic seam a consumer uses to constrain a spawned
    agent (e.g. a filesystem sandbox that denies writes to protected paths,
    or a read-only tool set for an ask-style pipeline). It is applied AFTER
    the per-agent resolve cache, so the constraint can depend on run-scoped
    state (the cache holds the unconstrained resolve; the constraint is
    re-applied cheaply per spawn). The runner itself stays domain-agnostic --
    it never inspects what the constraint does.

    ``spawn_timeout`` (optional) wraps each child spawn in
    ``asyncio.wait_for`` -- a long-running box node that hangs then fails
    loud rather than blocking the whole pipeline forever. ``None`` (default)
    means no timeout.
    """
    _agent_cache: dict[str, Any] = {}

    async def spawn_capability(
        agent_name: str,
        instruction: str,
        parent_session: Any,
        agent_configs: dict[str, dict[str, Any]],
        sub_session_id: str | None = None,
        orchestrator_config: dict[str, Any] | None = None,
        parent_messages: list[dict[str, Any]] | None = None,
        provider_preferences: list | None = None,
        self_delegation_depth: int = 0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        if agent_name in agent_configs:
            config = agent_configs[agent_name]
        elif agent_name in prepared.bundle.agents:
            config = prepared.bundle.agents[agent_name]
        else:
            available = list(agent_configs.keys()) + list(prepared.bundle.agents.keys())
            raise ValueError(f"Agent '{agent_name}' not found. Available: {available}")

        if agent_name not in _agent_cache:
            _agent_cache[agent_name] = await _resolve_agent_bundle(agent_name, config)
        child_bundle = _agent_cache[agent_name]

        if child_constraint is not None:
            child_bundle = child_constraint(child_bundle)

        spawn_coro = prepared.spawn(
            child_bundle=child_bundle,
            instruction=instruction,
            session_id=sub_session_id,
            parent_session=parent_session,
            orchestrator_config=orchestrator_config,
            parent_messages=parent_messages,
            provider_preferences=provider_preferences,
            self_delegation_depth=self_delegation_depth,
            session_cwd=cwd,
        )
        if spawn_timeout is not None:
            return await asyncio.wait_for(spawn_coro, timeout=spawn_timeout)
        return await spawn_coro

    return spawn_capability


def _local_bundle_path() -> Path:
    """Path to the local sibling attractor-pipeline bundle, if any.

    Computed relative to this file: this module lives at
    ``<repo>/modules/pipeline-runner/amplifier_module_pipeline_runner/runner.py``,
    so the repo root is ``parents[3]`` and the bundle is at
    ``<repo>/bundles/attractor-pipeline.yaml``. Overridable via the
    ``ATTRACTOR_PIPELINE_BUNDLE`` env var for out-of-tree development.
    """
    override = os.environ.get("ATTRACTOR_PIPELINE_BUNDLE")
    if override:
        return Path(override).expanduser()
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "bundles" / "attractor-pipeline.yaml"


_ATTRACTOR_PIPELINE_GIT = (
    "git+https://github.com/microsoft/amplifier-bundle-attractor@main"
    "#subdirectory=bundles/attractor-pipeline.yaml"
)


async def _load_base_bundle() -> Any:
    """Load the attractor-pipeline bundle (local sibling preferred, else git).

    Cached in a module global -- loaded once per process.
    """
    global _BASE_BUNDLE
    if _BASE_BUNDLE is not None:
        return _BASE_BUNDLE
    from amplifier_foundation import load_bundle

    local = _local_bundle_path()
    last_err: Exception | None = None
    if local.exists():
        try:
            _BASE_BUNDLE = await load_bundle(str(local))
            return _BASE_BUNDLE
        except Exception as e:  # noqa: BLE001
            last_err = e

    # TODO(slice-1/§8.6): verify local-sibling resolution for a built wheel /
    # DTU install (non-monorepo) -- this git fallback is the path that
    # exercises in that environment.
    try:
        _BASE_BUNDLE = await load_bundle(_ATTRACTOR_PIPELINE_GIT)
        return _BASE_BUNDLE
    except Exception as e:  # noqa: BLE001
        last_err = e

    raise RuntimeError(f"Could not load attractor-pipeline bundle: {last_err}")


async def _build_prepared(
    dot_source: str,
    logs_dir: Path,
    *,
    params: dict[str, str] | None,
    profiles: dict[str, str] | None,
    extra_overlays: Sequence[Any] | None = None,
) -> Any:
    """Compose base + a minimal orchestrator overlay, then prepare.

    We still mount the loop-pipeline module as ``session.orchestrator`` --
    ``AmplifierSession`` requires SOME orchestrator to be present at
    construction, and mounting the module is also what makes the
    attractor-pipeline bundle's static ``agents:`` block land in
    ``session.coordinator.config["agents"]`` (each entry already carrying
    its own inline ``loop-agent`` orchestrator -- see ``AmplifierBackend``'s
    recursion guard). ``drive_engine`` never calls this mounted
    orchestrator's ``execute()`` though -- it drives ``PipelineEngine``
    directly instead. ``dot_source``/``params``/``profiles`` are still
    forwarded into its config for parity/possible future use, but are
    otherwise inert for the direct-engine path (``drive_engine`` re-parses
    ``dot_source`` and re-seeds params itself).

    ``extra_overlays`` (optional) are additional ``Bundle`` overlays composed
    AFTER the runtime orchestrator overlay, in order. This is the generic
    seam a consumer uses to add cross-cutting configuration to every session
    and spawned child -- e.g. mounting an observability hook -- without the
    runner needing to know what the overlay contains.
    """
    global _DEPS_INSTALLED
    from amplifier_foundation import Bundle

    base = await _load_base_bundle()

    orchestrator_config: dict[str, Any] = {
        "dot_source": dot_source,
        "logs_root": str(logs_dir),
    }
    if params:
        orchestrator_config["params"] = params
    if profiles:
        orchestrator_config["profiles"] = profiles

    overlay = Bundle(
        name="pipeline-runner-runtime",
        version="1.0.0",
        session={
            "orchestrator": {
                "module": "loop-pipeline",
                "config": orchestrator_config,
            },
        },
    )

    composed = base.compose(overlay)
    for extra in extra_overlays or ():
        composed = composed.compose(extra)

    # First prepare in this process resolves/installs modules (slow, first
    # run only); subsequent ones take the offline path. Override with
    # ATTRACTOR_INSTALL_DEPS=0/1.
    env = os.environ.get("ATTRACTOR_INSTALL_DEPS")
    if env is not None:
        install_deps = env not in ("0", "false", "False", "")
    else:
        install_deps = not _DEPS_INSTALLED

    prepared = await composed.prepare(install_deps=install_deps)
    _DEPS_INSTALLED = True
    return prepared


def _get_runner_provenance() -> dict[str, str]:
    """Return runner package identity from install-time metadata.

    PEP 610 records a resolved VCS commit for git installs.  Editable installs
    may not have that identity, so provenance deliberately says ``"unknown"``
    rather than inferring a commit from a checkout that may not be the code
    being executed.
    """
    import contextlib
    from importlib import metadata

    runner_version = "unknown"
    runner_commit = "unknown"
    with contextlib.suppress(metadata.PackageNotFoundError):
        runner_version = metadata.version("amplifier-module-pipeline-runner")

    # Provenance must never break a completed run -- suppress broadly.
    with contextlib.suppress(Exception):
        direct_url_text = metadata.distribution(
            "amplifier-module-pipeline-runner"
        ).read_text("direct_url.json")
        if direct_url_text:
            commit_id = json.loads(direct_url_text).get("vcs_info", {}).get("commit_id")
            if commit_id:
                runner_commit = commit_id

    return {"runner_version": runner_version, "runner_commit": runner_commit}


def _augment_manifest_provenance(logs_dir: Path, provider: str) -> None:
    """Add runner-owned provenance fields after the engine writes its manifest.

    The engine owns ``engine_*`` fields.  This sequential post-run update owns
    only runner identity and the CLI/API provider selection, avoiding two
    writers for the same field while preserving all engine-owned legacy fields.
    """
    manifest_path = logs_dir / "manifest.json"
    if not manifest_path.exists():
        return

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(_get_runner_provenance())
    manifest["provider"] = provider
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


async def run_pipeline(
    dot_source: str,
    *,
    params: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    logs_root: Path | str | None = None,
    provider: str = "anthropic",
    profiles: Mapping[str, str] | None = None,
    hooks: Any = None,
    interviewer: Any = None,
    transform: bool = True,
    validate: bool = True,
    extra_overlays: Sequence[Any] | None = None,
    child_constraint: Callable[[Any], Any] | None = None,
    spawn_timeout: float | None = None,
    source_dir: Path | str | None = None,
) -> PipelineResult:
    """Run a DOT pipeline through the attractor engine, standalone.

    High-level convenience: builds the prepared bundle, session, and spawn
    wiring itself, then drives the engine via ``drive_engine``.

    Args:
        dot_source: The DOT digraph source text.
        params: Key-value map exposed to the pipeline as flat context keys
            (reaches ``$param`` expansion in LLM node prompts, tool_command
            substitution, AND tool_env -- see ``seed_context``).
        cwd: Working directory for the orchestrator session (created if
            absent). Defaults to ``Path.cwd()`` if not given.
        logs_root: Directory for this run's logs (created if absent).
            Defaults to a fresh tempdir if not given.
        provider: Stamped into ``manifest.json`` as runner-owned provenance
            and used for the CLI's own preflight checks. It does not alter
            engine routing -- the DOT's own ``llm_provider`` node attributes
            and the profiles map determine routing.
        profiles: llm_provider -> agent-name routing map. Defaults to
            ``DEFAULT_PROFILES`` if not given.
        hooks: Optional hooks object forwarded to the engine.
        interviewer: Optional interviewer object forwarded to the handler
            registry (human-in-the-loop gate seam).
        transform: If True (default), run ``apply_transforms`` before
            validation/execution.
        validate: If True (default), run ``validate_or_raise`` before execution.
        extra_overlays: Additional ``Bundle`` overlays composed AFTER the
            runtime orchestrator overlay, in order. The generic seam a
            consumer uses to add cross-cutting configuration to every
            session and spawned child -- e.g. mounting an observability
            hook -- without the runner needing to know what the overlay
            contains.
        child_constraint: Optional caller-supplied hook that receives the
            resolved child ``Bundle`` for each spawned agent and returns a
            (possibly modified) child ``Bundle`` -- the generic seam a
            consumer uses to constrain a spawned agent (e.g. a filesystem
            sandbox that denies writes to protected paths, or a read-only
            tool set for an ask-style pipeline).
        spawn_timeout: Optional timeout (seconds) wrapping each child spawn
            in ``asyncio.wait_for`` -- a long-running box node that hangs
            then fails loud rather than blocking the whole pipeline
            forever. ``None`` (default) means no timeout.

    Returns:
        A ``PipelineResult`` with status, notes, logs_dir, and raw JSON.
    """
    if logs_root is not None:
        logs_dir = Path(logs_root).expanduser().resolve()
    else:
        logs_dir = Path(tempfile.mkdtemp(prefix="attractor-run-"))
    logs_dir.mkdir(parents=True, exist_ok=True)

    cwd_path = Path(cwd).expanduser().resolve() if cwd is not None else Path.cwd()
    cwd_path.mkdir(parents=True, exist_ok=True)

    # A git+https:// entry is a URL, not DOT -- don't write it as pipeline.dot.
    # (drive_engine materializes it; the resolved graph is logged by the engine.)
    if not dot_source.startswith("git+https://"):
        (logs_dir / "pipeline.dot").write_text(dot_source, encoding="utf-8")

    resolved_profiles = dict(profiles) if profiles else dict(DEFAULT_PROFILES)

    prepared = await _build_prepared(
        dot_source,
        logs_dir,
        params=dict(params) if params else None,
        profiles=resolved_profiles,
        extra_overlays=extra_overlays,
    )
    session = await prepared.create_session(session_cwd=cwd_path)
    session.coordinator.register_capability(
        "session.spawn",
        make_spawn_fn(
            prepared,
            cwd=cwd_path,
            child_constraint=child_constraint,
            spawn_timeout=spawn_timeout,
        ),
    )

    try:
        async with session:
            outcome = await drive_engine(
                dot_source,
                session.coordinator,
                params=params,
                cwd=cwd_path,
                logs_root=logs_dir,
                hooks=hooks,
                profiles=resolved_profiles,
                interviewer=interviewer,
                transform=transform,
                validate=validate,
                source_dir=str(source_dir) if source_dir else None,
            )
    finally:
        # The engine creates its manifest at run start. Stamp runner-owned
        # fields even when later execution raises, so failed run directories
        # remain self-describing for incident analysis.
        _augment_manifest_provenance(logs_dir, provider)

    failure_reason = getattr(outcome, "failure_reason", None)
    data = {
        "status": outcome.status.value,
        "notes": outcome.notes or "",
    }
    text = json.dumps(data)

    return PipelineResult(
        status=data["status"],
        notes=str(data["notes"])[:4000],
        logs_dir=logs_dir,
        raw=text[:4000],
        failure_reason=str(failure_reason) if failure_reason else None,
    )


async def resume_pipeline(
    run_dir: Path | str,
    *,
    dot_source: str | None = None,
    params: Mapping[str, str] | None = None,
    cwd: Path | str | None = None,
    provider: str = "anthropic",
    profiles: Mapping[str, str] | None = None,
    hooks: Any = None,
    interviewer: Any = None,
    transform: bool = True,
    validate: bool = True,
    extra_overlays: Sequence[Any] | None = None,
    child_constraint: Callable[[Any], Any] | None = None,
    spawn_timeout: float | None = None,
) -> PipelineResult:
    """Resume an interrupted pipeline run from its checkpoint (spec §5.3).

    The explicit, opt-in counterpart to ``run_pipeline``.  ``run_pipeline``
    never reads a checkpoint back — resume happens here or not at all, which
    is what makes a stale or foreign ``checkpoint.json`` inert to a fresh run
    by construction rather than by a guard that can misfire.

    The run continues IN PLACE in ``run_dir`` (spec §5.6 / rule 1: the
    checkpoint IS ``{logs_root}/checkpoint.json``, and a resumed run is the
    same execution continued).  ``trace.jsonl`` appends after the interrupted
    records, ``manifest.json`` gains a ``resumes`` entry, and the completed
    nodes' directories are left untouched.

    IMPORTANT for callers: resume from the SAME working directory the
    interrupted run used.  File state produced by tool/agent nodes lives
    there, and the engine cannot verify it — this is the boundary where the
    graph-owned idempotency pattern (``examples/pipelines/12-graph-resume.dot``)
    is the right complementary tool.

    Args:
        run_dir: The interrupted run's ``logs_root`` — the directory holding
            ``checkpoint.json``.
        dot_source: Optional DOT source, for provenance/auditing.  It MUST
            fingerprint-match the checkpoint's embedded source or the resume
            is refused (a checkpoint binds to the run that wrote it; there is
            deliberately no override flag).  When omitted, the checkpoint's
            own embedded source is used — resume is self-contained.
        params: Additional flat context params.  A param may only ADD keys:
            colliding with a key the checkpoint restores is a loud error, not
            a silent shadow of restored state.
        cwd: Working directory for the resumed run.  Behaves exactly as on
            ``run_pipeline`` (process-level wiring cannot be serialized).
        provider, profiles, hooks, interviewer, transform, validate,
        extra_overlays, child_constraint, spawn_timeout: as ``run_pipeline``.

    Returns:
        A ``PipelineResult`` whose ``logs_dir`` is ``run_dir``.

    Raises:
        CheckpointResumeError: any rung of the validation ladder — missing,
            corrupted, wrong schema, already completed, graph mismatch, or
            structurally invalid.  Never a silent fresh start.
        ValueError: a ``--param`` collides with restored context state.
    """
    from amplifier_module_loop_pipeline.checkpoint import load_checkpoint_for_resume

    logs_dir = Path(run_dir).expanduser().resolve()

    # Ladder rungs 1-5.  Nothing below runs until these pass; rung 6 runs
    # inside drive_engine, against the parsed+transformed graph.
    checkpoint = load_checkpoint_for_resume(
        str(logs_dir / "checkpoint.json"),
        dot_source=dot_source,
    )
    resolved_dot_source = checkpoint.graph_dot_source

    # A resume-time param may only ADD keys.  Restored context wins by
    # construction (engine.resume applies the snapshot over the seeded
    # context), so a collision would silently discard what the caller asked
    # for — refuse instead.
    if params:
        collisions = sorted(set(params) & set(checkpoint.context_snapshot))
        if collisions:
            raise ValueError(
                f"--param key(s) {collisions!r} collide with context restored "
                "from the checkpoint. Restored state wins on resume, so the "
                "param would be silently discarded. Remove the param, or start "
                "a new run with 'attractor run' if you need a different value."
            )

    cwd_path = Path(cwd).expanduser().resolve() if cwd is not None else Path.cwd()
    cwd_path.mkdir(parents=True, exist_ok=True)

    resolved_profiles = dict(profiles) if profiles else dict(DEFAULT_PROFILES)

    prepared = await _build_prepared(
        resolved_dot_source,
        logs_dir,
        params=dict(params) if params else None,
        profiles=resolved_profiles,
        extra_overlays=extra_overlays,
    )
    session = await prepared.create_session(session_cwd=cwd_path)
    session.coordinator.register_capability(
        "session.spawn",
        make_spawn_fn(
            prepared,
            cwd=cwd_path,
            child_constraint=child_constraint,
            spawn_timeout=spawn_timeout,
        ),
    )

    try:
        async with session:
            outcome = await drive_engine(
                resolved_dot_source,
                session.coordinator,
                params=params,
                cwd=cwd_path,
                logs_root=logs_dir,
                hooks=hooks,
                profiles=resolved_profiles,
                interviewer=interviewer,
                transform=transform,
                validate=validate,
                resume_checkpoint=checkpoint,
            )
    finally:
        _augment_manifest_provenance(logs_dir, provider)

    failure_reason = getattr(outcome, "failure_reason", None)
    data = {
        "status": outcome.status.value,
        "notes": outcome.notes or "",
    }
    text = json.dumps(data)

    return PipelineResult(
        status=data["status"],
        notes=str(data["notes"])[:4000],
        logs_dir=logs_dir,
        raw=text[:4000],
        failure_reason=str(failure_reason) if failure_reason else None,
    )
