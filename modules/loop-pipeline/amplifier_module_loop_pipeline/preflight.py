"""Startup provider preflight (issue #155, EXTENSIONS.md section 36).

Before the walk begins, cross-check every node's *declared* ``llm_provider``
against what the run can actually serve, and refuse to start -- naming each
failing node, its provider, and the missing credential -- instead of letting
the first unserviceable node crash every round and drain the entire iteration
budget (Mode A of issue #155), or letting the backend silently substitute a
different provider (Mode B; see the fail-loud guard in ``backend.py``).

"Unserviceable" is the maintainer ruling's static definition: no provider
adapter/profile is mounted for the declared provider.  The check is purely
static -- environment and config inspection only, never a live API call.

Issue #195 closed the residual hole in that definition.  A profile is a
*string* naming an agent; the original check only asked whether the string
was MAPPED, never whether the thing it names can be resolved.  A profile
naming an absent agent satisfies "mounted + credential present" and then
fails at EVERY spawn (``AmplifierBackend._run_with_spawn`` resolves
``coordinator.config["agents"][profile]`` and refuses an entry it cannot
find), so the run drains its budget in exactly the #155 crash loop instead
of refusing.  ``resolvable_profiles`` carries the set of profile names the
spawn backend can actually resolve, so that class refuses at startup too --
still statically, still with no live call and no spawn.

Scope decisions (deliberate, documented):

- **Declared providers only.**  A node with no ``llm_provider`` uses the
  engine default (``"anthropic"``, see ``backend.py``).  The implicit default
  is NOT policed here: policing it would make simulation mode (no providers
  mounted at all -- a documented degraded mode used heavily by tests) and
  mock-provider harnesses unreachable.  The CLI separately preflights the
  default provider's credential before any run (``pipeline-runner cli.py``).
- **Root graph only.**  Nested ``dot_file`` child pipelines are loaded
  mid-walk by the pipeline handler; their nodes are not visible at startup.
  A child graph's unserviceable declaration fails loud at its first
  execution via the backend's no-fallback guard rather than at startup.
- **LLM-consuming node types only.**  Only handler types that reach the LLM
  backend (``codergen``, ``stack.manager_loop``) are checked; an
  ``llm_provider`` attribute on e.g. a tool node is inert and ignored.
"""

from __future__ import annotations

import os
from collections.abc import Collection, Mapping

from .graph import Graph, Node

# Env var name per provider.  Mirrors (deliberately, with a cross-reference)
# ``amplifier_module_pipeline_runner.runner.PROVIDER_KEY_ENV`` -- the runner
# cannot import this module at its own module import time (its compat gate
# requires engine imports to stay deferred), so the two small maps are kept
# in sync by convention.  Used to NAME the likely-missing credential in
# refusal messages and to statically check profile serviceability.
PROVIDER_KEY_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
}

# Handler types that consume the LLM backend (and therefore a provider).
_LLM_HANDLER_TYPES = frozenset({"codergen", "stack.manager_loop"})


class ProviderPreflightError(Exception):
    """Raised before the walk begins when nodes declare unserviceable providers.

    One instance carries EVERY failing node (issue #155 R1: the refusal names
    each failing node, its provider, and the missing credential) so a
    misconfiguration costs one clear error, not a per-node discovery loop.
    """


def _effective_handler_type(node: Node) -> str:
    """Resolve the node's handler type the way the engine does (type > shape)."""
    from .validation import SHAPE_TO_HANDLER  # local: avoid import cycle

    if node.type:
        return node.type
    return SHAPE_TO_HANDLER.get(node.shape, "codergen")


def _declared_provider(node: Node) -> str | None:
    """The node's declared llm_provider (promoted field first, attrs fallback).

    Stylesheet-assigned providers are visible here too: ``apply_transforms``
    materializes stylesheet properties onto node attrs before validation, and
    the preflight runs after transforms.
    """
    if getattr(node, "llm_provider", None):
        return str(node.llm_provider)
    attr = node.attrs.get("llm_provider")
    return str(attr) if attr else None


def collect_declared_llm_providers(graph: Graph) -> dict[str, list[str]]:
    """Map declared provider -> sorted list of LLM-node ids declaring it.

    Tolerates graph stand-ins without a ``nodes`` mapping (some callers'
    tests drive the engine seams with bare stubs and ``validate=False``):
    no visible nodes means nothing to check -- same posture as
    ``validate_or_raise`` being opt-out on those paths.
    """
    declared: dict[str, list[str]] = {}
    nodes = getattr(graph, "nodes", None) or {}
    for node in nodes.values():
        if _effective_handler_type(node) not in _LLM_HANDLER_TYPES:
            continue
        provider = _declared_provider(node)
        if provider:
            declared.setdefault(provider, []).append(node.id)
    for node_ids in declared.values():
        node_ids.sort()
    return declared


def check_provider_preflight(
    graph: Graph,
    *,
    mounted_providers: Collection[str] = (),
    profiles: Mapping[str, str] | None = None,
    resolvable_profiles: Collection[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    """Refuse to start when a declared ``llm_provider`` is unserviceable.

    Serviceability (static, no live API call):

    - a provider MODULE mounted under that name serves it
      (``mounted_providers`` -- the orchestrator's ``providers`` dict keys);
    - a PROFILE mapped for that name serves it *if* BOTH
      (a) the profile NAMES AN ADAPTER THIS RUN CAN RESOLVE -- when
          ``resolvable_profiles`` is supplied, the profile name must be in it
          (issue #195); and
      (b) its credential can be presumed present: for providers with a known
          credential env var (``PROVIDER_KEY_ENV``) the var must be set -- a
          profile whose agent can never construct its provider is exactly the
          issue-#155 crash loop; for unknown providers the credential gets the
          benefit of the doubt (nothing to check statically).

    ``resolvable_profiles`` is the set of profile names the backend that will
    actually consume them can resolve to a real adapter -- for the spawn
    backend, the keys of ``coordinator.config["agents"]``, which is precisely
    what ``AmplifierBackend._run_with_spawn`` looks the profile up in.  Pass
    ``None`` (the default) when that is not knowable on this path -- e.g. no
    coordinator, no ``session.spawn`` capability (profiles are then never
    consumed at all), or a coordinator whose config is not statically
    inspectable.  ``None`` means "do not police adapter resolution"; it never
    means "everything resolves".  Note (b) is NOT waived for unknown
    providers: adapter resolution is a config lookup that is equally knowable
    for every provider name, credentials are not.

    When NOTHING is mounted (no providers, no profiles) the check is skipped
    entirely: that is simulation mode, a documented degraded mode with its
    own loud warning, and refusing would make it unreachable.

    Raises:
        ProviderPreflightError: naming every failing node, its provider, and
        what is missing (the unresolvable profile and/or the credential).
        Zero nodes have executed; zero budget spent.
    """
    profiles = profiles or {}
    env = env if env is not None else os.environ
    if not mounted_providers and not profiles:
        return  # simulation mode -- nothing will consume a real provider

    declared = collect_declared_llm_providers(graph)
    if not declared:
        return

    mounted = set(mounted_providers)
    resolvable = None if resolvable_profiles is None else set(resolvable_profiles)
    failures: list[str] = []  # one line per failing node
    for provider, node_ids in sorted(declared.items()):
        if provider in mounted:
            continue
        key_env = PROVIDER_KEY_ENV.get(provider)
        if provider in profiles:
            profile_name = profiles[provider]
            problems: list[str] = []
            # (a) adapter resolution -- issue #195.  A profile is a STRING
            # naming an agent; a name nothing can resolve fails at EVERY
            # spawn, which is a drained budget, not a serviceable provider.
            if resolvable is not None and profile_name not in resolvable:
                known = ", ".join(sorted(resolvable)) or "none"
                problems.append(
                    f"profile '{profile_name}' is mapped for it, but no agent "
                    f"named '{profile_name}' can be resolved for the spawn "
                    f"backend (resolvable agents: {known}) -- every spawn for "
                    f"this node would fail"
                )
            # (b) credential presence -- issue #155.
            if key_env is not None and not env.get(key_env):
                problems.append(
                    f"profile '{profile_name}' is mounted for it, but its "
                    f"credential {key_env} is not set"
                )
            if not problems:
                continue  # profile resolves and credential present/unknowable
            reason = "; ".join(problems)
        else:
            cred = f" (credential: {key_env})" if key_env else ""
            reason = f"no provider module or profile is mounted for it{cred}"
        for node_id in node_ids:
            failures.append(
                f"  - node '{node_id}' declares llm_provider=\"{provider}\": {reason}"
            )

    if not failures:
        return

    raise ProviderPreflightError(
        "PROVIDER PREFLIGHT FAILED -- refusing to start the pipeline "
        "(issue #155, EXTENSIONS.md section 36). The following nodes declare "
        "an llm_provider this run cannot serve:\n"
        + "\n".join(failures)
        + "\nAn unserviceable provider would otherwise crash on every visit "
        "and can drain the entire iteration budget in a crash loop, or "
        "silently degrade to a different provider -- both are the failure "
        "class this preflight exists to prevent (e.g. dual-family critique "
        "silently collapsing to one model family). "
        "Fix: mount a provider/profile for each provider named above and set "
        "its credential env var, or change the node's llm_provider. "
        "Degrading to fewer providers must be an explicit graph/bundle "
        "change, never a silent fallback."
    )
