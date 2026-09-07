"""Guards for the standing model policy: gpt-5 is never a LIVE choice.

Owner policy (2026-09-07, standing): "I don't ever want to use gpt-5 again --
we should be using gpt-?.?-terra or gpt-?.?-luna only." A policy that lives
only in prose gets re-broken by the next author who copies an old example, so
this file makes the four mechanical halves of it decidable.

WHAT MAKES THIS HARD, AND WHY A `grep gpt-5` DOES NOT COVER IT. Three of the
four shapes below are invisible to a text search for the string:

  1. a GLOB pin (`llm_model="gpt-[5-9]*"`) contains no "gpt-5" and resolves,
     live, against the provider's catalog -- today, to gpt-5;
  2. a BARE PROVIDER pin (`llm_provider="openai"` with no `llm_model`) names
     no model at all, and the engine falls to its per-provider default
     pattern, which for openai is `gpt-5.*[0-9]`
     (loop-pipeline ``backend.py::_PROVIDER_DEFAULT_MODEL_PATTERN``). A live
     gpt-5 pin that no reader of the .dot can see;
  3. an INSTANCE pin whose runner cannot resolve it does not fall back to
     gpt-5 -- it refuses -- but a pin shipped without its runner-side
     definition is a pipeline that cannot start, so the fix for (1) and (2)
     has its own failure mode, and it gets a guard too.

So these guards read model SELECTION, not text: node attributes through the
repo's own stdlib-only DOT reader (``examples/authoring/check_authored_pipeline.py``,
already cross-checked against the engine parser by
``test_authoring_layer_gates.py``), plus the graph-level ``model_stylesheet``
rule bodies, which that reader does not surface because they are a graph
attribute rather than node attributes.

Comments are NOT scanned, deliberately. Dated records legitimately quote the
old pins -- ``task-runner.dot``'s issue-#155 timeline and
``feature-capsule.dot``'s fire-9 note both do -- and a guard that cannot tell
a record from a pin would force us to falsify history to stay green.

HONEST LIMITS, stated rather than discovered later:

  * Rule 2 forbids the ``openai`` MODULE outright rather than "openai without
    an llm_model". Per-node pairing IS available here, but the module's only
    non-gpt-5 use would be an explicit non-gpt-5 concrete id -- which the
    policy does not want either. Forbidding the address is the decidable
    statement of the policy; loosening it should be a deliberate edit to this
    guard, which is the point.
  * A pattern is judged by what it ADMITS, using fnmatch against
    representative bare gpt-5 ids, not by how it is spelled. ``gpt-[5-9]*``
    fails; ``gpt-[6-9]*`` passes; ``gpt-5.6-luna`` passes because it is a
    literal that admits only itself.
  * These guards see the repo's own tracked graphs. A graph fetched at run
    time is out of scope -- the engine's own startup preflight is the
    backstop there.

FAIL-CLOSED. An unreadable file, an absent DOT reader, or a scan that finds
zero graphs is a FAILURE, never a silent pass: a guard that green-lights on
"nothing to check" is the shape that lets a whole tree drift unwatched.
"""

from __future__ import annotations

import fnmatch
import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PIPELINE_DIR = _REPO_ROOT / ".github" / "capsule-pipeline"
_INSTANCE_TEMPLATE = _PIPELINE_DIR / "provider-instances.yaml"
_INSTALLER = _PIPELINE_DIR / "install_provider_instances.sh"
_WORKFLOW_DIR = _REPO_ROOT / ".github" / "workflows"

_SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", "site-packages"}

#: Provider MODULE names the engine resolves from its own closed table
#: (loop-pipeline ``provider_detection.PROVIDER_SPECS``). Anything else in an
#: ``llm_provider`` is a configured provider INSTANCE id.
_PROVIDER_MODULES = frozenset(
    {"anthropic", "openai", "gemini", "github-copilot", "openai-chatgpt"}
)

#: Provider modules whose engine default model pattern serves a bare gpt-5
#: generation when a node declares the provider and no model
#: (``backend.py::_PROVIDER_DEFAULT_MODEL_PATTERN["openai"] == "gpt-5.*[0-9]"``).
_MODULES_DEFAULTING_TO_GPT5 = frozenset({"openai"})

#: Representative concrete ids for "a bare gpt-5 generation". A pin is judged
#: by whether it ADMITS one of these, so the guard catches every spelling of
#: the range rather than one blessed string.
_BARE_GPT5_IDS = (
    "gpt-5",
    "gpt-5.2",
    "gpt-5.6",
    "gpt-5-mini",
    "gpt-5.2-codex",
    "gpt-5-2025-08-07",
)

_GLOB_CHARS = set("*?[")


# ---------------------------------------------------------------------------
# Reading model selection out of the repo's graphs
# ---------------------------------------------------------------------------


def _load_dot_reader() -> ModuleType:
    """The repo's own stdlib-only DOT reader, imported by path.

    Same loader shape as ``test_authoring_layer_gates.py`` (the module must be
    in ``sys.modules`` before ``exec_module`` for its dataclasses to resolve).
    """
    path = _REPO_ROOT / "examples" / "authoring" / "check_authored_pipeline.py"
    if not path.is_file():
        pytest.fail(
            f"fail-closed: the repo's stdlib DOT reader is missing at {path}. "
            "These guards read model selection through it; without it they "
            "would pass by finding nothing, which is the failure this note "
            "exists to prevent."
        )
    spec = importlib.util.spec_from_file_location("_gpt5guard_dotreader", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _tracked_dot_files() -> list[Path]:
    files = [
        p
        for p in _REPO_ROOT.rglob("*.dot")
        if not (_SKIP_DIRS & set(p.relative_to(_REPO_ROOT).parts))
    ]
    if not files:
        pytest.fail(
            "fail-closed: found zero .dot files under the repo root. Either "
            "the scan root is wrong or the graphs moved; either way this "
            "guard is not watching what it claims to watch."
        )
    return sorted(files)


_STYLESHEET_ATTR = re.compile(r'model_stylesheet\s*=\s*"(.*?)"', re.S)
_STYLESHEET_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)


def _stylesheet_pins(text: str, where: str) -> list[tuple[str, str | None, str | None]]:
    """``(where, provider, model)`` for each ``model_stylesheet`` rule body.

    The repo's DOT reader surfaces NODE attributes; ``model_stylesheet`` is a
    GRAPH attribute, so its rule bodies are parsed here. The grammar is the
    spec's Section 8.6 subset actually used in this repo:
    ``selector { prop: value; ... }``.
    """
    pins: list[tuple[str, str | None, str | None]] = []
    for sheet in _STYLESHEET_ATTR.findall(text):
        for selector, body in _STYLESHEET_RULE.findall(sheet):
            props: dict[str, str] = {}
            for decl in body.split(";"):
                key, sep, value = decl.partition(":")
                if sep:
                    props[key.strip()] = value.strip()
            if "llm_provider" in props or "llm_model" in props:
                pins.append(
                    (
                        f"{where} stylesheet rule '{selector.strip()}'",
                        props.get("llm_provider"),
                        props.get("llm_model"),
                    )
                )
    return pins


def _all_pins() -> list[tuple[str, str | None, str | None]]:
    """Every model selection in every tracked graph: nodes and stylesheets."""
    reader = _load_dot_reader()
    pins: list[tuple[str, str | None, str | None]] = []
    for path in _tracked_dot_files():
        rel = str(path.relative_to(_REPO_ROOT))
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:  # pragma: no cover - fail-closed branch
            pytest.fail(f"fail-closed: cannot read {rel}: {exc}")
        try:
            graph = reader.parse_dot_min(text)
        except Exception as exc:  # noqa: BLE001 - fail-closed branch
            pytest.fail(f"fail-closed: cannot parse {rel}: {exc!r}")
        for node_id, node in graph.nodes.items():
            provider = node.attrs.get("llm_provider")
            model = node.attrs.get("llm_model")
            if provider or model:
                pins.append((f"{rel} node '{node_id}'", provider, model))
        pins.extend(_stylesheet_pins(text, rel))
    return pins


def _admits_bare_gpt5(model: str) -> bool:
    """True when *model*, read as the engine reads it, can serve a bare gpt-5.

    A concrete id admits only itself; a glob admits everything it matches
    (``unified_llm.resolver.select_latest`` lowercases both sides).
    """
    lowered = model.lower()
    return any(fnmatch.fnmatch(candidate, lowered) for candidate in _BARE_GPT5_IDS)


def _is_glob(value: str) -> bool:
    return bool(set(value) & _GLOB_CHARS)


def _declared_ids(path: Path) -> set[str]:
    """``id:`` values in a settings-shaped YAML file, read without PyYAML.

    Same regex the installer uses, deliberately: the guard and the shell that
    enforces it at run time must agree on what "defines an instance" means.
    """
    return set(
        re.findall(r"^[ \t]*-?[ \t]*id:[ \t]*(\S+)[ \t]*$", path.read_text(), re.M)
    )


# ---------------------------------------------------------------------------
# 1. No pin may serve a bare gpt-5 generation
# ---------------------------------------------------------------------------


def test_no_pin_admits_a_bare_gpt5_generation() -> None:
    offenders = [
        (where, model)
        for where, _provider, model in _all_pins()
        if model and _admits_bare_gpt5(model)
    ]
    assert not offenders, (
        "Standing model policy: gpt-5 is not a live choice. These pins admit a "
        "bare gpt-5 generation (judged by fnmatch against "
        f"{list(_BARE_GPT5_IDS)}, the way the engine's resolver judges it):\n"
        + "\n".join(f"  - {where}: llm_model={model!r}" for where, model in offenders)
        + "\nAddress a `-terra` or `-luna` provider instance instead, with a "
        "CONCRETE llm_model (an instance id has no catalog adapter to resolve "
        "a glob against). See docs/DOT-AUTHORING-GUIDE.md, "
        "'Provider instances, and why they take a concrete id'."
    )


# ---------------------------------------------------------------------------
# 2. The invisible pin: a bare `openai` provider IS a gpt-5 choice
# ---------------------------------------------------------------------------


def test_no_graph_addresses_a_provider_module_that_defaults_to_gpt5() -> None:
    offenders = [
        (where, provider, model)
        for where, provider, model in _all_pins()
        if provider in _MODULES_DEFAULTING_TO_GPT5
    ]
    assert not offenders, (
        "A node declaring llm_provider=\"openai\" with no llm_model is a LIVE "
        "gpt-5 pin that names no model: the engine falls to "
        "_PROVIDER_DEFAULT_MODEL_PATTERN['openai'] == 'gpt-5.*[0-9]'. Under the "
        "standing policy the OpenAI family is addressed through a configured "
        "provider INSTANCE (`luna`, `terra`), never the bare module:\n"
        + "\n".join(
            f"  - {where}: llm_provider={p!r}, llm_model={m!r}"
            for where, p, m in offenders
        )
    )


# ---------------------------------------------------------------------------
# 3. An instance pin takes a concrete model -- a glob cannot resolve for one
# ---------------------------------------------------------------------------


def test_instance_pins_carry_a_concrete_model() -> None:
    problems: list[str] = []
    for where, provider, model in _all_pins():
        if not provider or provider in _PROVIDER_MODULES:
            continue
        if not model:
            problems.append(
                f"  - {where}: llm_provider={provider!r} with no llm_model -- the "
                "instance would run whatever default_model its settings happen "
                "to carry, which is a model choice nobody reading the graph can see"
            )
        elif _is_glob(model):
            problems.append(
                f"  - {where}: llm_provider={provider!r} with glob llm_model={model!r} "
                "-- a glob is resolved by unified_llm.resolve_latest_for, which has "
                "SDK adapters for the anthropic/openai/gemini triad only and fails "
                f"loud with \"no adapter found for provider '{provider}'\""
            )
    assert not problems, (
        "A provider INSTANCE id and a model glob are mutually exclusive:\n"
        + "\n".join(problems)
    )


# ---------------------------------------------------------------------------
# 4. A shipped pipeline that addresses an instance must ship its definition
# ---------------------------------------------------------------------------


def test_every_instance_the_ci_pipelines_address_is_provisioned() -> None:
    for required in (_INSTANCE_TEMPLATE, _INSTALLER):
        if not required.is_file():
            pytest.fail(
                f"fail-closed: {required.relative_to(_REPO_ROOT)} is missing. The "
                "shipped pipelines pin provider instances, and a GitHub-hosted "
                "runner has no Amplifier settings of its own -- without these the "
                "pipelines cannot start at all."
            )

    defined = _declared_ids(_INSTANCE_TEMPLATE)
    graphs = sorted(_PIPELINE_DIR.glob("*.dot"))
    assert graphs, "fail-closed: no pipelines found under .github/capsule-pipeline/"

    reader = _load_dot_reader()
    undefined: list[str] = []
    addressed_anywhere = False
    for path in graphs:
        graph = reader.parse_dot_min(path.read_text(encoding="utf-8"))
        for node_id, node in graph.nodes.items():
            provider = node.attrs.get("llm_provider")
            if not provider or provider in _PROVIDER_MODULES:
                continue
            addressed_anywhere = True
            if provider not in defined:
                undefined.append(
                    f"  - {path.name} node '{node_id}' addresses instance "
                    f"{provider!r}, which provider-instances.yaml does not define "
                    f"(it defines: {sorted(defined) or 'nothing'})"
                )
    assert not undefined, (
        "A pipeline may not address a provider instance this repo ships no "
        "runner-side definition for -- the run would be refused by the engine's "
        "startup preflight after the CI job had already been provisioned:\n"
        + "\n".join(undefined)
    )

    if addressed_anywhere:
        # The definition alone is inert: the workflows must install it, and the
        # credentials it references must reach the run.
        placeholders = set(
            re.findall(
                r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                re.sub(r"#.*", "", _INSTANCE_TEMPLATE.read_text()),
            )
        )
        assert placeholders, (
            "fail-closed: provider-instances.yaml references no ${VAR} "
            "placeholder. Either a literal credential was committed to it -- the "
            "thing it exists to prevent -- or the file is no longer a template."
        )
        installers = [
            wf
            for wf in sorted(_WORKFLOW_DIR.glob("*.yml"))
            if "install_provider_instances.sh" in wf.read_text()
        ]
        assert installers, (
            "The pipelines address provider instances but no workflow runs "
            ".github/capsule-pipeline/install_provider_instances.sh, so no "
            "GitHub-hosted run can serve them."
        )
        for wf in installers:
            text = wf.read_text()
            missing = sorted(
                name
                for name in placeholders
                if f"{name}:" not in text
                or (f"secrets.{name}" not in text and f"vars.{name}" not in text)
            )
            assert not missing, (
                f"{wf.name} installs the provider instances but never passes "
                f"{missing} into the steps that need them. The installed settings "
                "file holds ${VAR} placeholders verbatim; the engine expands them "
                "from the PROCESS environment at load time, so an absent var "
                "means an unresolved placeholder at run time. Either a repo "
                "secret or an Actions variable satisfies this -- OPENAI_BASE_URL "
                "is a VARIABLE (owner ruling 2026-09-07, it is an endpoint URL "
                "rather than a credential) and the workflows read "
                "`vars.X || secrets.X`; the exact expression is held by "
                "tests/test_provider_base_url_optional.py."
            )


# ---------------------------------------------------------------------------
# 5. The detector itself, seen RED -- a check never seen fail is unproven
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "pattern",
    ["gpt-5", "gpt-5.2", "gpt-[5-9]*", "gpt-5*", "gpt-5.*[0-9]", "GPT-5"],
)
def test_detector_flags_every_shape_that_serves_gpt5(pattern: str) -> None:
    assert _admits_bare_gpt5(pattern), (
        f"{pattern!r} can serve a bare gpt-5 generation but the detector did not "
        "flag it -- the guard above would pass over this pin."
    )


@pytest.mark.parametrize(
    "pattern",
    ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-[6-9]*", "claude-sonnet-*", "gemini-*-flash"],
)
def test_detector_admits_the_forms_the_policy_wants(pattern: str) -> None:
    assert not _admits_bare_gpt5(pattern), (
        f"{pattern!r} does not serve a bare gpt-5 generation, but the detector "
        "flagged it -- the guard above would reject a compliant pin."
    )


def test_stylesheet_pins_are_actually_read() -> None:
    """The stylesheet reader is the half the DOT reader cannot see.

    Without this, a stylesheet-only regression would pass every guard above by
    being invisible rather than by being compliant.
    """
    graph = 'digraph G { graph [model_stylesheet="\n .planning { llm_model: gpt-[5-9]*; llm_provider: openai; }\n"] }'
    pins = _stylesheet_pins(graph, "synthetic")
    assert pins == [("synthetic stylesheet rule '.planning'", "openai", "gpt-[5-9]*")], (
        f"stylesheet reader returned {pins!r} -- a stylesheet pin would be "
        "invisible to every guard in this file."
    )
