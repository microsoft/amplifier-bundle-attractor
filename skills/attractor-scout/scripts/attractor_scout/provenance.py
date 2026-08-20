"""Session PROVENANCE — deterministic human-vs-agent classification, at mining time.

The personalized artifact this skill produces is only worth reading if it
separates **what you did** from **what an agent did on your behalf**. Before
this module existed it could not: the qualifier admitted any session carrying a
`prompt:submit`, and a harness-fired root emits a byte-identical event. This
module is the discrimination that was missing, and it runs in the
DETERMINISTIC layer — before clustering, before ranking, before any model sees
anything.

WHAT THE DATA ACTUALLY SUPPORTS (measured; do not re-derive downstream)
----------------------------------------------------------------------
* **No positive human marker exists.** `session:start` carries only
  `parent_id` / `redaction` / `session_id` / `timestamp` — no argv, no mode,
  no `isatty`, no `launched_by`. "Human" can therefore only ever be
  PRESUMED from behavioural shape, never asserted from a recorded fact. The
  ladder below never defaults to human, and `UNKNOWN` is a real answer.
* **Definitive AGENT signals exist and are cheap**: a non-empty `parent_id`;
  a delegate/composite session-id shape; a first event of `session:fork`
  (measured 2000/2000 sub-sessions vs 0/9,149 roots — a perfect separator);
  and pipeline/recipe start events.
* **Strong structural heuristics**: an ephemeral or lane-shaped
  `working_dir` (tmp / scratch / worktree / lane / timestamped / mktemp)
  was agent-authored in 22/22 tmp cases and 39/40 across the wider pattern
  set. A single templated long brief, one-shot, leans agent. Two or more
  prompts with a real thinking gap between them was human in 3/3 and agent
  in 0/9.
* **False friends, deliberately NOT used**: a `deprecation:warning` first
  event (a bundle/time artifact), `application_host` (constant across the
  corpus), and UUID-shaped ids (thousands of UUID sessions carry parents).
  Each was measured and rejected; naming them here stops a future reader
  re-adopting one.
* **Honest residual**: the stable-workspace single-prompt band is
  contaminated with agent runs at a measured 21–29% and is genuinely
  undecidable from what is recorded. It resolves to `UNKNOWN` — never to
  human.

THE LADDER (first rung that fires wins; every verdict records WHICH and WHY)
---------------------------------------------------------------------------
    R0  AGENT            definitive lineage — parent_id / sid shape / session:fork
    R1  AGENT            definitive orchestration — pipeline/recipe start events
    R2  AGENT            structural path — ephemeral / lane-shaped working_dir
    R3  LIKELY-AGENT     one-shot templated long brief
    R4  HUMAN-PRESUMED   multi-prompt with a real inter-prompt gap
    R5  UNKNOWN          nothing decisive — carries its evidence, never guesses

POLICY (settled; see docs/designs/2026-08-20-scout-session-provenance.md)
------------------------------------------------------------------------
Opportunities are mined from **R4 only**. R5 UNKNOWN is counted and sampled in
the artifact's provenance panel but excluded from ranked opportunities — the
complaint being fixed IS contamination, and the measured band is too wide to
hand back as "your work". R0–R3 are excluded from opportunities, counted, and
surfaced as an already-automated footprint: context about what agents already
do for you, not an opportunity to act on.

Every threshold below is a named constant carrying its measured justification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from itertools import pairwise

# --------------------------------------------------------------- verdicts
AGENT = "agent"
LIKELY_AGENT = "likely-agent"
HUMAN_PRESUMED = "human-presumed"
UNKNOWN = "unknown"

#: Ordered rung ids. The ladder is evaluated in exactly this order and the
#: FIRST rung that fires wins, so a definitive signal can never be overridden
#: by a weaker one further down.
R0 = "R0"
R1 = "R1"
R2 = "R2"
R3 = "R3"
R4 = "R4"
R5 = "R5"
RUNGS = (R0, R1, R2, R3, R4, R5)

VERDICT_BY_RUNG = {
    R0: AGENT,
    R1: AGENT,
    R2: AGENT,
    R3: LIKELY_AGENT,
    R4: HUMAN_PRESUMED,
    R5: UNKNOWN,
}

RUNG_LABEL = {
    R0: "R0 agent — definitive lineage (parent / delegate id shape / fork)",
    R1: "R1 agent — definitive orchestration (pipeline or recipe start)",
    R2: "R2 agent — structural path (ephemeral or lane-shaped workspace)",
    R3: "R3 likely-agent — one-shot templated brief",
    R4: "R4 human-presumed — multi-prompt with a real thinking gap",
    R5: "R5 unknown — nothing decisive was recorded",
}

#: Verdicts that are EXCLUDED from ranked opportunities. Everything except R4.
NON_OPPORTUNITY_VERDICTS = frozenset({AGENT, LIKELY_AGENT, UNKNOWN})

#: The one verdict opportunities may be mined from.
OPPORTUNITY_VERDICT = HUMAN_PRESUMED

# ------------------------------------------------------------ R0 constants
#: Delegate sessions are written under a zero-filled id prefix. Measured: this
#: prefix never appears on a session that also behaves like a root.
DELEGATE_SID_PREFIX = "0000000000000000-"

#: Composite delegate ids carry an `_<agent-name>` suffix after the id body.
#: Anchored to the END of the id so an underscore inside a workspace name
#: cannot false-positive.
COMPOSITE_SID_SUFFIX_RE = re.compile(r"_[A-Za-z][\w.-]*$")

#: First-event fork marker. Measured 2000/2000 sub-sessions vs 0/9,149 roots —
#: the single cleanest separator in the corpus, and the reason the ladder
#: bothers to record which event came FIRST rather than merely which occurred.
FORK_FIRST_EVENT = "session:fork"

# ------------------------------------------------------------ R1 constants
#: Orchestration starts. A session carrying any of these was started BY a
#: pipeline or recipe, not by a person typing. (`extract.machine_launched`
#: previously keyed on a subset of these and was measured DEAD on the
#: production path — pipeline-start roots and prompt-carrying roots were
#: disjoint sets. The rung keeps the signal honestly instead of pretending
#: a dead field earned its place in the author prior.)
ORCHESTRATION_START_EVENTS = frozenset(
    {
        "pipeline:start",
        "pipeline:node_start",
        "recipe:start",
        "recipe:loop_start",
    }
)

# ------------------------------------------------------------ R2 constants
WS_EPHEMERAL = "ephemeral"
WS_STABLE = "stable"
WS_UNRECORDED = "unrecorded"

#: STRUCTURAL PATH PATTERNS — the R2 pattern set, deliberately conservative.
#: Each entry is (name, predicate-kind, pattern). Measured: tmp roots were
#: agent-authored in 22/22 cases, and the wider set held at 39/40. Anything
#: that did not clear that bar is NOT in this list; a workspace that merely
#: looks unusual is left to R5 rather than called agent on a hunch.
_TMP_PREFIXES = ("/tmp/", "/var/tmp/", "/private/tmp/", "/private/var/tmp/")
_EPHEMERAL_SEGMENTS = ("/.scratch/", "/worktrees/", "/lanes/", "/.worktrees/", "/.lanes/")
#: A goal-lane / batch directory stamped with a compact UTC timestamp.
_TIMESTAMPED_BASENAME_RE = re.compile(r".+-\d{8}T\d{6}Z$")
#: A `mktemp -d`-shaped basename: a trailing dot plus exactly six token chars.
_MKTEMP_BASENAME_RE = re.compile(r"\.\w{6}$")

# ------------------------------------------------------------ R3 constants
#: A templated brief is LONG. Below this length a "You are ..." opener is just
#: as likely to be a person setting context, so the rung declines to fire.
TEMPLATED_PROMPT_MIN_CHARS = 1_200

#: Heads that mark a machine-assembled brief: a markdown title, a role
#: preamble, or an injected context file. Anchored at the start of the prompt.
TEMPLATED_HEAD_RE = re.compile(r"^(?:#\s|You are |<context_file)")

#: R3 requires the session to be a true one-shot across the WHOLE file — a
#: second prompt means somebody came back, which is not one-shot behaviour.
ONE_SHOT_PROMPTS = 1

# ------------------------------------------------------------ R4 constants
#: A real human thinking gap between two prompts. Measured: >= 2 prompts with
#: at least one gap this long was human in 3/3 cases and agent in 0/9. Below
#: it, back-to-back prompts are indistinguishable from a scripted turn.
HUMAN_GAP_MIN_S = 45.0

#: Human presumption needs a genuine second turn — one prompt is never enough,
#: because the corpus records nothing that could make a single prompt human.
HUMAN_MIN_PROMPTS = 2

# ------------------------------------------------------------ prompt shapes
SHAPE_ABSENT = "absent"
SHAPE_TEMPLATED_LONG = "templated-head-long"
SHAPE_TEMPLATED_SHORT = "templated-head-short"
SHAPE_FREEFORM = "freeform"

#: Bound on per-rung samples surfaced in the artifact panel. Samples carry
#: EVIDENCE ONLY — never a session id, path, or prompt body.
MAX_SAMPLES_PER_RUNG = 3

# ------------------------------------------------------------- panel prose
#: Single home for the policy text, so SKILL.md, the renderer and the design
#: doc cannot drift from what the code actually does.
POLICY_NOTE = (
    "Opportunities are mined from R4 (human-presumed) sessions only. Everything else is "
    "counted here and kept out of the ranking."
)

UNKNOWN_NOTE = (
    "UNKNOWN is an honest answer, not a soft human. Nothing recorded at session start says who "
    "launched a run — no argv, no mode, no tty, no launcher — so a single-prompt session in a "
    "stable workspace cannot be told apart from an agent one-shot. Those sessions are counted "
    "here and excluded from the ranking rather than folded into your work."
)

ALREADY_AUTOMATED_NOTE = (
    "Sessions an agent ran on your behalf. This is context — what is already automated — not an opportunity to act on."
)

UPSTREAM_FIX_NOTE = (
    "Recording invocation provenance at session start (argv, mode, whether stdin was a tty, and "
    "the launching component) would collapse rungs R2-R5 into one recorded boolean. That is an "
    "upstream change to the event schema, not something this skill can infer."
)

#: The one bypass R4 cannot close from today's data. Named, not hidden.
R4_RESIDUAL_NOTE = (
    "A harness that paces its prompts like a person and runs in a stable workspace is "
    "indistinguishable from human work in today's data, so a few such runs may sit inside the "
    "human-presumed pool; the same upstream invocation-provenance marker is what closes this."
)


@dataclass(frozen=True)
class SessionSignals:
    """Everything the ladder is allowed to look at. Nothing else is consulted."""

    session_id: str = ""
    parent_id: str | None = None
    working_dir: str | None = None
    first_event: str | None = None
    orchestration_events: tuple[str, ...] = ()
    n_prompts: int = 0
    first_prompt: str = ""
    prompt_gaps_s: tuple[float, ...] = ()
    span_s: float | None = None


@dataclass(frozen=True)
class Provenance:
    """One auditable verdict: WHICH rung fired, WHY, and on what evidence."""

    verdict: str
    rung: str
    signal: str
    evidence: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "verdict": self.verdict,
            "rung": self.rung,
            "signal": self.signal,
            "evidence": dict(self.evidence),
        }


def classify_workspace(working_dir: str | None) -> tuple[str, str | None]:
    """Classify a `working_dir` into a workspace CLASS plus the pattern that fired.

    Returns `(WS_UNRECORDED, None)` when there is no working_dir at all — the
    absence of the field is never evidence of anything, and saying so
    explicitly is what keeps R2 conservative.
    """
    if not working_dir or not isinstance(working_dir, str):
        return WS_UNRECORDED, None
    path = working_dir.strip()
    if not path:
        return WS_UNRECORDED, None
    normalized = path.rstrip("/") or "/"
    with_slash = normalized + "/"
    for prefix in _TMP_PREFIXES:
        if with_slash.startswith(prefix):
            return WS_EPHEMERAL, "tmp-root"
    for segment in _EPHEMERAL_SEGMENTS:
        if segment in with_slash:
            return WS_EPHEMERAL, segment.strip("/")
    basename = normalized.rsplit("/", 1)[-1]
    if _TIMESTAMPED_BASENAME_RE.match(basename):
        return WS_EPHEMERAL, "timestamped-dir"
    if _MKTEMP_BASENAME_RE.search(basename):
        return WS_EPHEMERAL, "mktemp-dir"
    return WS_STABLE, None


def prompt_shape(text: str) -> str:
    """Shape of the first prompt — a CLASS, never the prompt body."""
    if not text or not isinstance(text, str) or not text.strip():
        return SHAPE_ABSENT
    stripped = text.lstrip()
    if TEMPLATED_HEAD_RE.match(stripped):
        return SHAPE_TEMPLATED_LONG if len(stripped) >= TEMPLATED_PROMPT_MIN_CHARS else SHAPE_TEMPLATED_SHORT
    return SHAPE_FREEFORM


def _evidence(sig: SessionSignals) -> dict:
    """The evidence dict every verdict carries. Identity-free by construction.

    `workspace_class` is a CLASS, not the path; `first_prompt_shape` is a
    shape, not the text. That is what makes a verdict safe to render in an
    artifact and safe to hand to the step-4 adjudication.
    """
    ws_class, ws_pattern = classify_workspace(sig.working_dir)
    gaps = tuple(sig.prompt_gaps_s or ())
    return {
        "prompt_count": int(sig.n_prompts or 0),
        "span_s": sig.span_s,
        "workspace_class": ws_class,
        "workspace_pattern": ws_pattern,
        "first_prompt_shape": prompt_shape(sig.first_prompt),
        "first_prompt_chars": len((sig.first_prompt or "").strip()),
        "max_prompt_gap_s": round(max(gaps), 1) if gaps else None,
        "first_event": sig.first_event,
    }


def _r0_signal(sig: SessionSignals) -> str | None:
    """Definitive lineage. Any one of these is conclusive on its own."""
    if sig.parent_id:
        return "parent_id"
    sid = str(sig.session_id or "")
    if sid.startswith(DELEGATE_SID_PREFIX):
        return "delegate-id-prefix"
    if COMPOSITE_SID_SUFFIX_RE.search(sid):
        return "composite-id-suffix"
    if sig.first_event == FORK_FIRST_EVENT:
        return "first-event-fork"
    return None


def _r1_signal(sig: SessionSignals) -> str | None:
    """Definitive orchestration: a pipeline or recipe started this session."""
    hits = sorted({str(e) for e in sig.orchestration_events if e in ORCHESTRATION_START_EVENTS})
    return f"orchestration:{hits[0]}" if hits else None


def _r2_signal(sig: SessionSignals) -> str | None:
    ws_class, ws_pattern = classify_workspace(sig.working_dir)
    return f"workspace:{ws_pattern}" if ws_class == WS_EPHEMERAL else None


def _r3_signal(sig: SessionSignals) -> str | None:
    if int(sig.n_prompts or 0) != ONE_SHOT_PROMPTS:
        return None
    return "one-shot-templated-brief" if prompt_shape(sig.first_prompt) == SHAPE_TEMPLATED_LONG else None


def _r4_signal(sig: SessionSignals) -> str | None:
    if int(sig.n_prompts or 0) < HUMAN_MIN_PROMPTS:
        return None
    gaps = tuple(sig.prompt_gaps_s or ())
    if gaps and max(gaps) >= HUMAN_GAP_MIN_S:
        return f"multi-prompt-gap>={HUMAN_GAP_MIN_S:g}s"
    return None


_LADDER = (
    (R0, _r0_signal),
    (R1, _r1_signal),
    (R2, _r2_signal),
    (R3, _r3_signal),
    (R4, _r4_signal),
)


def classify(signals: SessionSignals) -> Provenance:
    """Run the ladder. FIRST rung that fires wins; R5 UNKNOWN is the floor.

    There is no path through this function that returns human on an absence
    of evidence — that is the whole point of it.
    """
    evidence = _evidence(signals)
    for rung, probe in _LADDER:
        signal = probe(signals)
        if signal:
            return Provenance(verdict=VERDICT_BY_RUNG[rung], rung=rung, signal=signal, evidence=evidence)
    return Provenance(verdict=UNKNOWN, rung=R5, signal="no-decisive-signal", evidence=evidence)


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def gaps_from_timestamps(stamps: list) -> tuple[float, ...]:
    """Inter-prompt gaps, in seconds, from a list of prompt timestamps.

    Unparseable stamps are dropped rather than guessed at. Fewer than two
    parseable stamps means no gap can be claimed — which is a legitimate
    reason for R4 to decline, not a reason to invent one.

    ABS BY DESIGN — a named degradation, not a silent one. Each gap is the
    ABSOLUTE difference between consecutive stamps, so an out-of-order or
    clock-skewed pair still yields a positive magnitude rather than a negative
    number that would silently fail the R4 `>= HUMAN_GAP_MIN_S` test. The
    trade-off is deliberate and one-directional: it can only ever ADMIT a
    session to the human-presumed pool that a strictly-ordered reading would
    have declined, never exclude one it would have kept. Since R4 is the one
    pool the ranking trusts, erring toward "look again, a human may be here"
    on garbled timing is the safe direction; the paced-harness residual (see
    module docstring) is the same class of limit and closes the same way.
    """
    parsed = [t for t in (_parse_ts(s) for s in stamps) if t is not None]
    if len(parsed) < 2:
        return ()
    out: list[float] = []
    for earlier, later in pairwise(parsed):
        try:
            out.append(abs((later - earlier).total_seconds()))
        except (TypeError, ValueError):  # pragma: no cover - tz-mixed stamps
            continue
    return tuple(round(g, 1) for g in out)


def signals_from_record(rec: dict) -> SessionSignals:
    """Rebuild the ladder's inputs from an extract record.

    Fields this library does not write (an `extracts.jsonl` from an older
    miner) are simply absent, and their absence lands the record on R5 rather
    than on a fabricated verdict.
    """
    return SessionSignals(
        session_id=str(rec.get("session_id") or ""),
        parent_id=(str(rec["parent_id"]) if rec.get("parent_id") else None),
        working_dir=rec.get("working_dir"),
        first_event=rec.get("first_event"),
        orchestration_events=tuple(str(e) for e in (rec.get("orchestration_events") or ())),
        n_prompts=int(rec.get("n_prompts") or 0),
        first_prompt=str(rec.get("first_prompt") or ""),
        prompt_gaps_s=tuple(float(g) for g in (rec.get("prompt_gaps_s") or ())),
        span_s=rec.get("span_capped_s", rec.get("span_s")),
    )


def stamp_record(rec: dict) -> dict:
    """Classify one extract record IN PLACE, writing `rec['provenance']`."""
    rec["provenance"] = classify(signals_from_record(rec)).as_dict()
    return rec


def ensure_stamped(records: list[dict]) -> list[dict]:
    """Stamp any record that does not already carry a verdict.

    Idempotent, so re-reading an `extracts.jsonl` this library wrote does not
    reclassify it, while an extract from another miner still gets an honest
    (usually R5) verdict instead of silently bypassing the gate.
    """
    for rec in records:
        prov = rec.get("provenance")
        if not isinstance(prov, dict) or not prov.get("rung"):
            stamp_record(rec)
    return records


def verdict_of(rec: dict) -> str:
    """The record's verdict — FAIL-HONEST. An absent stamp reads UNKNOWN."""
    prov = rec.get("provenance")
    if isinstance(prov, dict):
        verdict = prov.get("verdict")
        if verdict in VERDICT_BY_RUNG.values():
            return str(verdict)
    return UNKNOWN


def rung_of(rec: dict) -> str:
    prov = rec.get("provenance")
    if isinstance(prov, dict) and prov.get("rung") in RUNGS:
        return str(prov["rung"])
    return R5


def is_opportunity_eligible(rec: dict) -> bool:
    """Only R4 human-presumed sessions may feed a ranked opportunity."""
    return verdict_of(rec) == OPPORTUNITY_VERDICT


def partition_records(records: list[dict]) -> dict[str, list[dict]]:
    """Split a corpus into the three policy pools. Nothing is discarded."""
    pools: dict[str, list[dict]] = {"human_presumed": [], "unknown": [], "agent": []}
    for rec in records:
        verdict = verdict_of(rec)
        if verdict == HUMAN_PRESUMED:
            pools["human_presumed"].append(rec)
        elif verdict == UNKNOWN:
            pools["unknown"].append(rec)
        else:
            pools["agent"].append(rec)
    return pools


@dataclass
class UnitGateResult:
    """What the mining boundary admitted, and what it kept out — with reasons."""

    admitted: list[dict] = field(default_factory=list)
    already_automated: list[dict] = field(default_factory=list)
    unattributed: list[dict] = field(default_factory=list)
    n_members_dropped: int = 0

    def as_dict(self) -> dict:
        return {
            "n_units_admitted": len(self.admitted),
            "n_units_already_automated": len(self.already_automated),
            "n_units_unattributed": len(self.unattributed),
            "n_members_dropped": self.n_members_dropped,
            "already_automated": list(self.already_automated),
            "unattributed": list(self.unattributed),
        }


def _unit_rung_mix(members: list[dict]) -> dict[str, int]:
    mix: dict[str, int] = {}
    for member in members:
        rung = rung_of(member)
        mix[rung] = mix.get(rung, 0) + 1
    return dict(sorted(mix.items()))


def gate_units(units: list[dict]) -> UnitGateResult:
    """THE MINING BOUNDARY: filter unit membership to R4 before ranking.

    Applied AFTER cluster membership has been re-verified against the extract
    (so a member id an LLM invented is still caught by the strict gate) and
    BEFORE ranking, so no non-R4 session can reach a score. Input units are
    never mutated — an admitted unit is a shallow copy carrying only its R4
    members.

    A unit that loses every member is not dropped: it is reported as either
    an already-automated footprint (agent rungs dominate) or an unattributed
    unit (UNKNOWN dominates), each with its rung mix.
    """
    result = UnitGateResult()
    for unit in units:
        members = list(unit.get("members") or [])
        eligible = [m for m in members if is_opportunity_eligible(m)]
        result.n_members_dropped += len(members) - len(eligible)
        if eligible:
            admitted = dict(unit)
            admitted["members"] = eligible
            admitted["provenance_mix"] = _unit_rung_mix(members)
            # The unit that reaches ranking has DIFFERENT membership, so its
            # deterministic author prior is recomputed over the members that
            # survived. Leaving the prior computed over excluded sessions
            # would let sessions that never reach the ranking decide how the
            # ones that do get labelled. The ADJUDICATED label is left alone:
            # it is a human-tier judgment, and this gate only ever removes
            # members, never promotes them.
            if "author_prior" in unit or "author_mix" in unit:
                from . import author as author_mod  # local import keeps the module graph acyclic

                prior = author_mod.cluster_author_prior(eligible)
                admitted["author_prior"] = prior["author_prior"]
                admitted["author_mix"] = prior["author_mix"]
            result.admitted.append(admitted)
            continue
        mix = _unit_rung_mix(members)
        summary = {
            "unit_id": unit.get("unit_id") or unit.get("id"),
            "name": unit.get("name"),
            "n_sessions": len({str(m.get("session_id")) for m in members if m.get("session_id")}),
            "provenance_mix": mix,
        }
        agent_n = sum(n for rung, n in mix.items() if rung in (R0, R1, R2, R3))
        unknown_n = mix.get(R5, 0)
        if agent_n >= unknown_n and agent_n > 0:
            summary["note"] = ALREADY_AUTOMATED_NOTE
            result.already_automated.append(summary)
        else:
            summary["note"] = UNKNOWN_NOTE
            result.unattributed.append(summary)
    return result


def summarize(records: list[dict], *, gate: UnitGateResult | None = None) -> dict:
    """The provenance panel payload: counts per rung, bounded evidence samples.

    Contains NO session ids, NO paths and NO prompt bodies — only counts,
    classes and shapes, so the panel is safe to render into an artifact that
    a person may share.
    """
    by_rung: dict[str, int] = {rung: 0 for rung in RUNGS}
    by_verdict: dict[str, int] = {AGENT: 0, LIKELY_AGENT: 0, HUMAN_PRESUMED: 0, UNKNOWN: 0}
    samples: list[dict] = []
    per_rung_sampled: dict[str, int] = {rung: 0 for rung in RUNGS}

    for rec in records:
        rung = rung_of(rec)
        verdict = verdict_of(rec)
        by_rung[rung] = by_rung.get(rung, 0) + 1
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
        raw_prov = rec.get("provenance")
        prov: dict = raw_prov if isinstance(raw_prov, dict) else {}
        if per_rung_sampled[rung] < MAX_SAMPLES_PER_RUNG:
            per_rung_sampled[rung] += 1
            samples.append(
                {
                    "rung": rung,
                    "verdict": verdict,
                    "signal": prov.get("signal", "no-decisive-signal"),
                    "evidence": dict(prov.get("evidence") or {}),
                }
            )

    payload = {
        "n_sessions": len(records),
        "by_rung": by_rung,
        "by_verdict": by_verdict,
        "rung_labels": dict(RUNG_LABEL),
        "opportunity_pool": by_verdict[HUMAN_PRESUMED],
        "already_automated": by_verdict[AGENT] + by_verdict[LIKELY_AGENT],
        "unknown_excluded": by_verdict[UNKNOWN],
        "samples": samples,
        "policy_note": POLICY_NOTE,
        "unknown_note": UNKNOWN_NOTE,
        "already_automated_note": ALREADY_AUTOMATED_NOTE,
        "upstream_fix_note": UPSTREAM_FIX_NOTE,
        "r4_residual_note": R4_RESIDUAL_NOTE,
    }
    if gate is not None:
        payload["units"] = gate.as_dict()
    return payload
