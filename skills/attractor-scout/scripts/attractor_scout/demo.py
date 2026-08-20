"""The demonstration layer: brief assembly, gates, validation, publication.

The mining half answers *what recurs*. This half answers *what the pipeline
that would have converged it looks like* — for the user's own top-ranked
opportunity, with their own verified numbers.

Three properties are load-bearing, and each is machine-checked here rather
than promised:

**1. The LLM never states a number in the SIX TEACHING-PROSE SLOTS.**
`validate_narrative` scans every digit run in every prose slot against a
whitelist built from the re-verified stats this unit actually carries. An
invented count kills the assembly (exit 2, the same posture as `rank --strict`)
with the offending token named. Prose like "about a dozen" is always legal;
"12 sessions" is only legal if 12 is one of their verified numbers.

The scope of this guard is exactly those six slots, and the claim is scoped to
match. Numbers written INSIDE the generated `.dot` — budgets, `max_iterations`,
thresholds, a figure in a node's prompt or label — are NOT digit-whitelisted:
a `.dot` legitimately carries pipeline parameters, and a whitelist there would
false-positive on real ones. The `.dot` gets its own appropriate machine gate,
`attractor lint` + the authoring contract (see property 2); its numbers are
checked for structure, not cross-checked against the verified stats. The
self-certification panel's "what nothing checked" names that surface out loud
(`demo_templates.PANEL_PART2_BODY`), and see `validate_narrative` for the two
named limits of the whitelist itself.

**2. Nothing is published before the gates finish.** `run_ladder` walks the
degradation ladder — `attractor lint` when reachable, the vendored stdlib
doctrine checker always — and `assemble` copies the `.dot` and companion
beside the HTML only after a green verdict. A red verdict raises
`DemoGateRed`: the artifact never carries a broken demo. Unavailability of a
rung is a different fate from a red verdict — it is *labelled*, honestly, and
the demo still publishes at the level that actually ran.

**3. Execution order inside the ladder is not the rung order.** The doctrine
checker is the FLOOR, so it runs first in code: if it cannot execute at all,
nothing was verified and the honest label is `none` — asking the linter after
that could only produce a level label that lied about what ran. The rendered
panel still presents lint first, because that is the order of authority.

The one network-adjacent rung (`uvx`) is never reached from here on its own:
it arrives only as an explicit `--lint-cmd` override, which the skill supplies
only after the user says yes out loud.
"""

from __future__ import annotations

import json
import re
import shlex
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import demo_templates as T
from .errors import AttractorScoutError
from .naming import DEMO_DIR_STEM

# --------------------------------------------------------------------------
# Budgets and constants (D2 / D4).
# --------------------------------------------------------------------------

#: The convergence-factory shape in miniature: start, exit, 1-2 workers,
#: gate(s), budget wall, loud terminal.
DEMO_MAX_NODES = 9

#: One delegation, plus at most one gate-informed retry. Never more.
DEMO_MAX_ATTEMPTS = 2

#: Illustrative per-step reliability for the convergence arithmetic. This is
#: DELIBERATELY not derived from the user's data: nothing in the extract
#: measures per-step LLM success, and deriving one would be a fabricated
#: statistic wearing their data's clothes. Labeled as illustrative wherever
#: it is rendered.
P_STEP = 0.9

#: Attempt budget assumed for the gated-loop arithmetic when the generated
#: pipeline does not declare one of its own.
BUDGET_FALLBACK = 2
BUDGET_MAX = 12

#: The six named prose slots. All six required; no others honoured.
NARRATIVE_TEXT_SLOTS = (
    "scenario_gist",
    "q1_cycle_note",
    "q2_gate_note",
    "q3_recovery_note",
    "payoff_note",
)
NARRATIVE_WALK_SLOT = "pipeline_walk"
NARRATIVE_SLOTS = (*NARRATIVE_TEXT_SLOTS, NARRATIVE_WALK_SLOT)

MAX_SLOT_CHARS = 600

#: Small integers the narrative may always use: Q1/Q2/Q3 and the 4a/4b/4c
#: sub-test references. They name structure, not counts.
STRUCTURAL_DIGITS = frozenset({"0", "1", "2", "3", "4"})

#: Verdicts a demonstration may be generated for. An honest-NO is never
#: demonstrated: authoring a pipeline for work that failed the fit test would
#: demonstrate the anti-pattern.
DEMOABLE_VERDICTS = frozenset({"OPPORTUNITY", "OPPORTUNITY(unproven)"})

_DIGIT_RUN_RE = re.compile(r"\d+")
_SLUG_BAD_RE = re.compile(r"[^A-Za-z0-9._-]+")
_LINT_ERROR_RE = re.compile(r"\bERROR\b")

#: Verify-class tools, borrowed from the gate detector so the brief's evidence
#: and the fit verdict cannot disagree about what "a check" means.
_REQUIRED_FILES = ("pipeline.dot", "pipeline.md", "narrative.json")


class DemoGateRed(AttractorScoutError):
    """A machine gate returned a red verdict — the demo must not publish.

    Distinct from every other fail-loud condition: the inputs were fine and
    the tooling worked. The authored pipeline is what failed, and the honest
    response is to re-delegate once with the gate report attached, then stop.
    """


class DemoNarrativeInvalid(AttractorScoutError):
    """A narrative slot broke the count-integrity contract or its shape."""


# --------------------------------------------------------------------------
# Unit lookup and stat rendering.
# --------------------------------------------------------------------------


def load_ranked(path: str | Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AttractorScoutError(f"could not read the ranking at {path}: {exc}") from exc


def find_unit(ranked: dict, unit_id: str | None) -> dict:
    """The unit to demonstrate. `None` means `opportunities[0]` (D1: K=1)."""
    opportunities = ranked.get("opportunities") or []
    if not opportunities:
        raise AttractorScoutError(
            "there are no opportunities in this ranking, so there is no subject to demonstrate. "
            "Render the primer-only artifact instead."
        )
    if unit_id is None:
        return opportunities[0]
    for unit in opportunities:
        if str(unit.get("unit_id")) == str(unit_id):
            return unit
    known = ", ".join(str(u.get("unit_id")) for u in opportunities[:10])
    raise AttractorScoutError(f"unit {unit_id!r} is not in this ranking's opportunities. Known unit ids: {known}")


def assert_demoable(unit: dict) -> None:
    verdict = str(unit.get("verdict", ""))
    if verdict not in DEMOABLE_VERDICTS:
        raise AttractorScoutError(
            f"unit {unit.get('unit_id')!r} carries verdict {verdict!r}. Demonstrations are only "
            f"generated for {sorted(DEMOABLE_VERDICTS)} — a pipeline authored for work that failed "
            f"the fit test would demonstrate the anti-pattern. Its remediation is its teaching."
        )


def slugify(name: str, unit_id: str) -> str:
    """`<sanitized name>-<unit_id>` in the `pipeline_name` charset."""
    base = _SLUG_BAD_RE.sub("-", str(name or "unit")).strip("-._") or "unit"
    tail = _SLUG_BAD_RE.sub("-", str(unit_id or "u")).strip("-._") or "u"
    return f"{base[:60]}-{tail}".strip("-._").lower()


def _num(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _render_number(value: float) -> str:
    """How a stat appears in the artifact — the exact string the whitelist uses."""
    rounded = round(value, 2)
    if abs(rounded - round(rounded)) < 1e-9:
        return str(round(rounded))
    return f"{rounded:g}"


def unit_stats(unit: dict) -> dict:
    """The verified stats block. Every number in a demo traces back to here."""
    lev = unit.get("leverage_detail") or {}
    return {
        "n_sessions": round(_num(unit.get("n_sessions"))),
        "med_tool_calls": round(_num(lev.get("med_tool_calls")), 2),
        "med_llm_cycles": round(_num(lev.get("med_llm_cycles")), 2),
        "med_span_s": round(_num(lev.get("med_span_capped_s")), 2),
        "err_rate": round(_num(lev.get("errors_per_session")), 2),
        "provisional": bool(unit.get("provisional")),
    }


def unit_fit(unit: dict) -> dict:
    fit_detail = unit.get("fit_detail") or {}
    return {
        "cycle": bool(fit_detail.get("cycle")),
        "gate": bool(fit_detail.get("gate")),
        "recovery": str(unit.get("recovery") or fit_detail.get("recovery") or "UNKNOWN"),
        "verdict": str(unit.get("verdict", "")),
    }


def rendered_stat_strings(stats: dict) -> list[str]:
    return [
        _render_number(stats["n_sessions"]),
        _render_number(stats["med_tool_calls"]),
        _render_number(stats["med_llm_cycles"]),
        _render_number(stats["med_span_s"]),
        _render_number(stats["err_rate"]),
    ]


def allowed_digit_runs(stats: dict) -> set[str]:
    """The digit whitelist: their verified numbers, plus the structural 0-4."""
    allowed = set(STRUCTURAL_DIGITS)
    for rendered in rendered_stat_strings(stats):
        allowed.add(rendered)
        allowed.update(_DIGIT_RUN_RE.findall(rendered))
    return allowed


# --------------------------------------------------------------------------
# Gate-tool evidence: what THEY already reach for to decide work is done.
# --------------------------------------------------------------------------


def gate_tool_census(unit: dict, extracts: list[dict] | None, *, top: int = 5) -> list[str]:
    """Verify-class tools seen in this unit's members' TERMINAL windows.

    This is the evidence the demo's gate command is derived from — the check
    the user already runs, not one invented for them. Returns [] honestly when
    no extract is available or nothing verify-shaped was observed; the brief
    says so in those words rather than fabricating a gate.
    """
    if not extracts:
        return []
    from .fit_gate import DEFAULT_GATE_CONFIG

    members = {str(m) for m in (unit.get("members") or [])}
    if not members:
        return []
    counts: Counter[str] = Counter()
    window = DEFAULT_GATE_CONFIG.tail_window
    for rec in extracts:
        if str(rec.get("session_id")) not in members:
            continue
        tail = rec.get("tool_tail") or (rec.get("tool_seq") or [])[-window:]
        for tool in tail:
            if str(tool) in DEFAULT_GATE_CONFIG.verify_tools:
                counts[str(tool)] += 1
    return [name for name, _ in counts.most_common(top)]


def read_extracts_if_present(path: str | Path | None) -> list[dict] | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_file():
        return None
    out: list[dict] = []
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


# --------------------------------------------------------------------------
# Brief assembly (D2).
# --------------------------------------------------------------------------


def _stats_lines(stats: dict) -> list[str]:
    return [
        f"distinct sessions:        {_render_number(stats['n_sessions'])}",
        f"median tool calls:        {_render_number(stats['med_tool_calls'])}",
        f"median LLM cycles:        {_render_number(stats['med_llm_cycles'])}",
        f"median wall span (s):     {_render_number(stats['med_span_s'])}",
        f"errors per session:       {_render_number(stats['err_rate'])}",
        f"frequency:                {'provisional (seen in only 2-3 sessions)' if stats['provisional'] else 'confirmed'}",
    ]


def _fit_lines(fit: dict) -> list[str]:
    return [
        f"4a cycle:     {'yes' if fit['cycle'] else 'no'}",
        f"4b gate:      {'yes' if fit['gate'] else 'no'}",
        f"4c recovery:  {T.RECOVERY_RENDER.get(fit['recovery'], fit['recovery'])}",
    ]


def build_brief(
    *,
    ranked_path: str | Path,
    unit_id: str | None,
    workdir: str | Path,
    extracts_path: str | Path | None = None,
) -> tuple[str, Path]:
    """Write `<workdir>/<slug>/brief.md`. Returns `(slug, brief_path)`.

    Deterministic: the same ranking and unit always produce the same brief.
    """
    ranked = load_ranked(ranked_path)
    unit = find_unit(ranked, unit_id)
    assert_demoable(unit)

    slug = slugify(str(unit.get("name")), str(unit.get("unit_id")))
    stats = unit_stats(unit)
    fit = unit_fit(unit)
    extracts = read_extracts_if_present(extracts_path)

    target = Path(workdir) / slug
    target.mkdir(parents=True, exist_ok=True)
    brief_path = target / "brief.md"
    brief_path.write_text(
        T.brief_markdown(
            unit_name=str(unit.get("name")),
            unit_id=str(unit.get("unit_id")),
            slug=slug,
            verdict=fit["verdict"],
            stats_lines=_stats_lines(stats),
            fit_lines=_fit_lines(fit),
            gate_evidence=gate_tool_census(unit, extracts),
            gist=unit.get("gist"),
            max_nodes=DEMO_MAX_NODES,
        ),
        encoding="utf-8",
    )
    return slug, brief_path


# --------------------------------------------------------------------------
# Narrative validation (D4) — fail loud, name the offending token.
# --------------------------------------------------------------------------


def validate_narrative(narrative, stats: dict, dot_text: str) -> dict:
    """Shape, digit-whitelist and node-name checks. Raises on any violation.

    Scope, stated precisely so the claim can't quietly widen: this guards the
    SIX teaching-prose slots. Numbers inside the generated ``.dot`` are not its
    business — that file gets ``attractor lint`` + the authoring contract, and
    a whitelist over pipeline parameters (``max_iterations``, thresholds) would
    false-positive on legitimate ones.

    Two NAMED LIMITS of the whitelist itself, documented rather than closed —
    both covered by expected-pass regression tests in
    ``tests/test_scenario6_demo_assembly.py`` so a future "I fixed it" is forced
    to update this docstring honestly:

    * **Decomposition.** The scan matches ``\\d+``, and a rendered stat like
      ``0.33`` whitelists the bare run ``33`` as a side effect, so "33 minutes"
      passes. Closing it would mean whitelisting only the exact rendered string
      and would ban the legitimate ``0.33`` -> ``33%`` kind of reference; the
      boundary is the tokenizer's, and the guard fails loud on the shape it can
      define cleanly (``93`` from ``930`` IS still rejected — decomposition only
      leaks the whole sub-runs a stat literally contains).
    * **Spelled-out numbers.** "forty-seven hours" is not a digit-run and
      passes. Detecting written numerals is NLP guesswork, and a fail-loud guard
      that guessed wrong would block honest prose — the worse failure for a
      trust surface whose whole value is not crying wolf.
    """
    if not isinstance(narrative, dict):
        raise DemoNarrativeInvalid(f"narrative.json must be a JSON object, got {type(narrative).__name__}")

    missing = [slot for slot in NARRATIVE_SLOTS if slot not in narrative]
    if missing:
        raise DemoNarrativeInvalid(
            f"narrative.json is missing required slot(s): {', '.join(missing)}. "
            f"All of {', '.join(NARRATIVE_SLOTS)} must be present."
        )

    allowed = allowed_digit_runs(stats)

    def _check_text(where: str, value) -> str:
        if not isinstance(value, str) or not value.strip():
            raise DemoNarrativeInvalid(f"narrative slot {where!r} must be a non-empty string")
        if len(value) > MAX_SLOT_CHARS:
            raise DemoNarrativeInvalid(
                f"narrative slot {where!r} is {len(value)} characters; the limit is {MAX_SLOT_CHARS}"
            )
        for token in _DIGIT_RUN_RE.findall(value):
            if token not in allowed:
                raise DemoNarrativeInvalid(
                    f"narrative slot {where!r} states the number {token!r}, which is not one of this "
                    f"unit's re-verified stats. Every number in the artifact comes from the "
                    f"deterministic layer; narrative may not invent one. "
                    f"(allowed: {', '.join(sorted(allowed))})"
                )
        return value

    cleaned: dict = {}
    for slot in NARRATIVE_TEXT_SLOTS:
        cleaned[slot] = _check_text(slot, narrative[slot])

    walk = narrative[NARRATIVE_WALK_SLOT]
    if not isinstance(walk, list) or not walk:
        raise DemoNarrativeInvalid("narrative slot 'pipeline_walk' must be a non-empty array")

    node_ids = dot_node_ids(dot_text)
    cleaned_walk: list[dict] = []
    for i, step in enumerate(walk):
        if not isinstance(step, dict) or "node" not in step or "note" not in step:
            raise DemoNarrativeInvalid(f"pipeline_walk[{i}] must be an object with 'node' and 'note'")
        node = str(step["node"])
        if node not in node_ids:
            raise DemoNarrativeInvalid(
                f"pipeline_walk[{i}] names node {node!r}, which does not exist in the pipeline. "
                f"Nodes actually in the graph: {', '.join(sorted(node_ids)) or '(none)'}"
            )
        cleaned_walk.append({"node": node, "note": _check_text(f"pipeline_walk[{i}].note", step["note"])})
    cleaned[NARRATIVE_WALK_SLOT] = cleaned_walk
    return cleaned


def dot_node_ids(dot_text: str) -> set[str]:
    """Node ids, parsed with the VENDORED checker's parser — one parser, one truth."""
    from .authoring_contract import DotParseError, parse_dot_min

    try:
        graph = parse_dot_min(dot_text)
    except DotParseError as exc:
        raise DemoNarrativeInvalid(f"the generated pipeline does not parse: {exc}") from exc
    return set(graph.nodes)


# --------------------------------------------------------------------------
# Convergence arithmetic (D4) — computed here, never by the LLM.
# --------------------------------------------------------------------------


def budget_from_dot(dot_text: str) -> int:
    """The demo pipeline's own attempt budget, read off the graph when stated."""
    from .authoring_contract import DotParseError, parse_dot_min

    try:
        graph = parse_dot_min(dot_text)
    except DotParseError:
        return BUDGET_FALLBACK
    found: list[int] = []
    for node in graph.nodes.values():
        raw = node.attrs.get("max_retries")
        if raw is None:
            continue
        try:
            val = int(str(raw).strip())
        except ValueError:
            continue
        if val > 0:
            found.append(val)
    if not found:
        return BUDGET_FALLBACK
    return max(1, min(BUDGET_MAX, max(found)))


def convergence_math(*, med_llm_cycles: float, budget: int) -> dict:
    """`p^n` once-through vs the gated loop's chance within its budget.

    Illustrative arithmetic with a fixed, labeled per-step reliability. Both
    numbers are computed in Python at assembly time; neither is ever asked of
    a language model.
    """
    chain_len = max(1, round(_num(med_llm_cycles, 1.0)))
    once_through = P_STEP**chain_len
    gated_loop = 1.0 - (1.0 - once_through) ** max(1, budget)
    return {
        "chain_len": chain_len,
        "p_step": P_STEP,
        "once_through": round(once_through, 4),
        "gated_loop": round(gated_loop, 4),
        "budget": max(1, budget),
        "label": T.MATH_LABEL,
    }


# --------------------------------------------------------------------------
# The verification ladder (D3).
# --------------------------------------------------------------------------


@dataclass
class LadderResult:
    level: str
    lint_verdict: str | None = None
    lint_not_run_reason: str | None = None
    doctrine_verdict: str | None = None
    doctrine_report: str | None = None
    red: bool = False
    red_reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "level": self.level,
            "lint_verdict": self.lint_verdict,
            "lint_not_run_reason": self.lint_not_run_reason,
            "doctrine_verdict": self.doctrine_verdict,
            "doctrine_report": self.doctrine_report,
        }


def run_doctrine_checker(dot_path: Path, companion_path: Path | None) -> tuple[str, str]:
    """The FLOOR (rung 3). Returns `(verdict, verbatim report)`.

    Runs in-process against the vendored, byte-pinned copy: no `python3` on
    PATH required, which is exactly the bundle-only case this rung exists for.
    Raises on an environmental crash so the caller can label `none`.
    """
    from .authoring_contract import CheckResult, DotParseError, parse_dot_min, render_report, run_checks

    pipeline_label = dot_path.name
    companion_label = companion_path.name if companion_path else "(none)"
    try:
        graph = parse_dot_min(dot_path.read_text(encoding="utf-8"))
    except (OSError, DotParseError) as exc:
        results = [CheckResult("A0", "the authored pipeline parses", False, f"{pipeline_label}: {exc}")]
        return "doctrine_bad", render_report(pipeline_label, companion_label, results, "doctrine_bad")

    results = run_checks(graph, companion_path)
    verdict = "doctrine_ok" if all(r.passed for r in results) else "doctrine_bad"
    return verdict, render_report(pipeline_label, companion_label, results, verdict)


def _run_lint(lint_argv: list[str], dot_path: Path) -> tuple[str, bool]:
    """Run a linter and capture its findings VERBATIM. Returns `(text, red)`."""
    try:
        # argv comes from a configured command, never from mined text.
        proc = subprocess.run(
            [*lint_argv, "lint", str(dot_path.name)],
            cwd=str(dot_path.parent),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"attractor lint: could not run ({exc})", False
    body = (proc.stdout or "") + (proc.stderr or "")
    body = body.strip() or f"(no output; exit {proc.returncode})"
    red = proc.returncode != 0 or bool(_LINT_ERROR_RE.search(body))
    return body, red


def run_ladder(
    *,
    dot_path: Path,
    companion_path: Path | None,
    relpath: str,
    lint_cmd: str | None = None,
) -> LadderResult:
    """Walk the degradation ladder and resolve the honest verification level.

    Execution order puts the doctrine checker first because it is the floor:
    if it cannot execute, nothing was verified and `none` is the only truthful
    label — running the linter afterwards could only produce a level string
    that overstated what happened. The rendered panel still shows lint first.
    """
    try:
        doctrine_verdict, doctrine_report = run_doctrine_checker(dot_path, companion_path)
    except Exception as exc:  # noqa: BLE001 - ANY environmental crash of the checker is rung 4, by design
        return LadderResult(
            level=T.LEVEL_NONE,
            lint_verdict=None,
            lint_not_run_reason=(
                "the bundled doctrine checker could not execute in this environment "
                f"({type(exc).__name__}: {exc}), so nothing machine-checked this pipeline"
            ),
            doctrine_verdict=None,
            doctrine_report=None,
        )

    doctrine_red = doctrine_verdict != "doctrine_ok"

    if lint_cmd:
        lint_argv = shlex.split(lint_cmd)
    elif shutil.which("attractor"):
        lint_argv = ["attractor"]
    else:
        lint_argv = []

    if not lint_argv:
        return LadderResult(
            level=T.LEVEL_DOCTRINE_ONLY,
            lint_verdict=None,
            lint_not_run_reason=T.lint_not_run_label(relpath),
            doctrine_verdict=doctrine_verdict,
            doctrine_report=doctrine_report,
            red=doctrine_red,
            red_reasons=([f"doctrine: {doctrine_verdict}"] if doctrine_red else []),
        )

    lint_text, lint_red = _run_lint(lint_argv, dot_path)
    reasons: list[str] = []
    if lint_red:
        reasons.append("attractor lint reported ERROR-level findings")
    if doctrine_red:
        reasons.append(f"doctrine: {doctrine_verdict}")
    return LadderResult(
        level=T.LEVEL_LINT_DOCTRINE,
        lint_verdict=lint_text,
        lint_not_run_reason=None,
        doctrine_verdict=doctrine_verdict,
        doctrine_report=doctrine_report,
        red=bool(reasons),
        red_reasons=reasons,
    )


# --------------------------------------------------------------------------
# Assembly + publication (D5).
# --------------------------------------------------------------------------


def _read_workdir(workdir: Path) -> tuple[str, Path, Path, dict]:
    dot_path = workdir / "pipeline.dot"
    companion_path = workdir / "pipeline.md"
    narrative_path = workdir / "narrative.json"
    missing = [name for name in _REQUIRED_FILES if not (workdir / name).is_file()]
    if missing:
        raise AttractorScoutError(
            f"the demo workdir {workdir} is missing {', '.join(missing)}. The authoring delegate "
            f"must write all three files: {', '.join(_REQUIRED_FILES)}."
        )
    try:
        narrative = json.loads(narrative_path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise DemoNarrativeInvalid(f"{narrative_path} is not valid JSON: {exc}") from exc
    return dot_path.read_text(encoding="utf-8"), dot_path, companion_path, narrative


def _publish(dot_path: Path, companion_path: Path, output_dir: Path, slug: str) -> tuple[str, str]:
    """Copy beside the HTML — AFTER the gates. Fails loud if the copy failed."""
    dest_dir = output_dir / DEMO_DIR_STEM
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_dot = dest_dir / f"{slug}.dot"
    dest_md = dest_dir / f"{slug}.md"
    try:
        shutil.copyfile(dot_path, dest_dot)
        shutil.copyfile(companion_path, dest_md)
    except OSError as exc:
        raise AttractorScoutError(
            f"could not publish the demo files into {dest_dir}: {exc}. The artifact must never "
            f"claim a file that does not exist."
        ) from exc
    if not dest_dot.is_file() or not dest_md.is_file():
        raise AttractorScoutError(f"publication into {dest_dir} did not produce both files")
    return f"{DEMO_DIR_STEM}/{slug}.dot", f"{DEMO_DIR_STEM}/{slug}.md"


def assemble_demo(
    *,
    ranked_path: str | Path,
    unit_id: str | None,
    workdir: str | Path,
    output_dir: str | Path,
    lint_cmd: str | None = None,
    generated_at: str,
) -> dict:
    """Gate, validate, publish, and return one `demos.json` demo entry."""
    ranked = load_ranked(ranked_path)
    unit = find_unit(ranked, unit_id)
    assert_demoable(unit)

    workdir = Path(workdir)
    slug = slugify(str(unit.get("name")), str(unit.get("unit_id")))
    dot_text, dot_path, companion_path, narrative = _read_workdir(workdir)

    stats = unit_stats(unit)
    fit = unit_fit(unit)
    cleaned_narrative = validate_narrative(narrative, stats, dot_text)

    dot_relpath = f"{DEMO_DIR_STEM}/{slug}.dot"
    ladder = run_ladder(
        dot_path=dot_path,
        companion_path=companion_path,
        relpath=dot_relpath,
        lint_cmd=lint_cmd,
    )
    if ladder.red:
        report_path = workdir / "gate-report.txt"
        report_path.write_text(
            "\n\n".join(
                part
                for part in (
                    "=== attractor lint ===\n" + (ladder.lint_verdict or "(not run)"),
                    "=== authoring contract ===\n" + (ladder.doctrine_report or "(not run)"),
                )
            ),
            encoding="utf-8",
        )
        raise DemoGateRed(
            f"the machine gates rejected this draft ({'; '.join(ladder.red_reasons)}). It was NOT "
            f"published — the artifact never carries a broken demo. The verbatim gate reports are "
            f"at {report_path}; re-delegate ONCE with them appended, and if it is still red, say so "
            f"and move on."
        )

    published_dot, published_md = _publish(dot_path, companion_path, Path(output_dir), slug)
    budget = budget_from_dot(dot_text)

    return {
        "unit_id": str(unit.get("unit_id")),
        "name": str(unit.get("name")),
        "slug": slug,
        "dot_relpath": published_dot,
        "companion_relpath": published_md,
        "dot_text": dot_text,
        "stats": stats,
        "fit": fit,
        "leverage_detail": {
            "leverage": round(_num(unit.get("leverage")), 3),
            "score": round(_num(unit.get("score")), 3),
        },
        "narrative": cleaned_narrative,
        "convergence_math": convergence_math(med_llm_cycles=stats["med_llm_cycles"], budget=budget),
        "verification": ladder.as_dict(),
        "invocation": {
            "run_cmd": T.run_cmd(published_dot),
            "author_cmd": T.author_cmd(unit_name=str(unit.get("name")), verdict=fit["verdict"], slug=slug),
            "install_cmd": T.CLI_INSTALL_CMD,
        },
        "generated_at": generated_at,
    }


def empty_demos_doc() -> dict:
    """The primer-only document: a demonstration needs a subject."""
    return {"primer": True, "explainer_url": T.EXPLAINER_URL, "demos": []}


def write_demos(demo: dict | None, out_path: str | Path, *, append: bool = False) -> dict:
    """Write (or append into) `demos.json`. Same unit twice replaces, never duplicates."""
    out = Path(out_path)
    doc = empty_demos_doc()
    if append and out.is_file():
        try:
            existing = json.loads(out.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise AttractorScoutError(f"{out} exists but is not valid JSON: {exc}") from exc
        if isinstance(existing, dict) and isinstance(existing.get("demos"), list):
            doc["demos"] = list(existing["demos"])
    if demo is not None:
        doc["demos"] = [d for d in doc["demos"] if d.get("unit_id") != demo["unit_id"]]
        doc["demos"].append(demo)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return doc


def not_yet_demonstrated(ranked: dict, demos_doc: dict, *, top: int = 5) -> list[dict]:
    """Step 9's menu: the next candidates, in rank order, already-done removed."""
    done = {str(d.get("unit_id")) for d in (demos_doc.get("demos") or [])}
    out = []
    for unit in ranked.get("opportunities") or []:
        if str(unit.get("unit_id")) in done:
            continue
        out.append({"unit_id": str(unit.get("unit_id")), "name": str(unit.get("name"))})
        if len(out) >= top:
            break
    return out
