"""Synthetic corpus builders — ground truth BY CONSTRUCTION.

**ZERO real data.** Every session id, workspace name, prompt string and tool
name in here is generated from a seed. Nothing was copied from anyone's
machine: no real session UUIDs, no real workspace names, no maintainer
identifiers, no hostnames, no internal cluster ids. That is a hard property
of these fixtures, and `tests/test_no_real_data_leak.py` re-checks it on
every run rather than trusting this paragraph.

Corpora are BUILT AT TEST TIME into a tmp dir rather than committed as
thousands of files. The generator plus its manifest IS the fixture — it is
smaller, reviewable, re-seedable (the statistical-N arms need 5 independent
seeds), and it cannot silently drift from the ground truth it claims,
because the ground truth is returned by the same call that writes the corpus.

Two extraction hazards are deliberately reproduced so a regression to the
naive implementation FAILS here instead of in production:

* **E1 (key order)** — `prompt:submit` lines are written in FORMAT B, with
  the top-level `event` key AFTER the `data` payload. That is 95% of real
  lines and the shape that makes a head-only regex undercount by 72%.
* **E2 (~1 MB config)** — a subset of sessions carry an oversized
  `session:config` line BEFORE their first prompt, so a byte-budgeted reader
  misses the prompt entirely.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

FORMAT = "context-intelligence"
VERSION = "1.0.0"

BASE_TIME = datetime(2020, 1, 1, tzinfo=timezone.utc)

#: The planted recurring unit's marker phrase. Obviously synthetic.
UNIT_MARKER = "SYNTHETIC-UNIT-U"

#: Oversized `session:config` payload size (E2 hazard).
BIG_CONFIG_BYTES = 1_050_000


@dataclass
class GroundTruth:
    """What the builder KNOWS it planted. Tests assert against this."""

    root: Path
    expected: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def synth_id(prefix: str, n: int) -> str:
    """Deterministic synthetic session id. Never a real UUID."""
    return f"{prefix}-{n:04d}-4000-8000-{n:012d}"


def _iso(offset_s: float) -> str:
    return (BASE_TIME + timedelta(seconds=offset_s)).isoformat()


def _line_format_b(event: str, data: dict) -> str:
    """FORMAT B: data payload first, top-level `event` key LAST (E1 hazard)."""
    body = json.dumps(data, ensure_ascii=False)
    return '{"data":' + body + ',"event":"' + event + '","timestamp":"' + _iso(0) + '"}'


def _line_format_a(event: str, data: dict) -> str:
    """FORMAT A: `event` key early."""
    return json.dumps({"ts": _iso(0), "lvl": "info", "event": event, "data": data}, ensure_ascii=False)


def write_session(
    root: Path,
    workspace: str,
    session_id: str,
    *,
    prompts: list[str],
    tools: list[str] | None = None,
    errors_at: list[int] | None = None,
    recover: bool = False,
    status: str = "completed",
    span_s: float = 300.0,
    parent_id: str | None = None,
    started_offset_s: float = 0.0,
    extra_marker_lines: int = 0,
    big_config: bool = False,
    version: str = VERSION,
    fmt: str = FORMAT,
    source: str | None = None,
    llm_cycles: int | None = None,
    explicit_loop: bool = False,
    approval: bool = False,
) -> Path:
    """Write one synthetic session directory. Returns its path.

    `errors_at` indexes into `tools`: a `tool:post` carrying `result.error`
    is emitted after that call. `recover=True` re-invokes the SAME tool
    immediately after, which is exactly the error -> same-tool-retry shape
    4c reads.
    """
    tools = list(tools or [])
    error_idx: set[int] = set(errors_at or [])
    sess_dir = root / workspace / "sessions" / session_id / "context-intelligence"
    sess_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "format": fmt,
        "version": version,
        "session_id": session_id,
        "started_at": _iso(started_offset_s),
        "ended_at": _iso(started_offset_s + span_s),
        "status": status,
    }
    if parent_id:
        meta["parent_id"] = parent_id
    if source:
        # A NEUTRAL origin label under a forward-compatible key. Used only to
        # plant a foreign-source session the scoping gate must exclude; it is
        # never a real host or CI name.
        meta["source"] = source
    (sess_dir / "metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    lines: list[str] = [_line_format_a("session:start", {"session_id": session_id})]
    if big_config:
        # E2 hazard: a ~1 MB config line BEFORE the first prompt.
        lines.append(_line_format_b("session:config", {"blob": "c" * BIG_CONFIG_BYTES}))
    for prompt in prompts:
        # E1 hazard: prompts always in FORMAT B.
        lines.append(_line_format_b("prompt:submit", {"prompt": prompt}))
    for _ in range(extra_marker_lines):
        lines.append(_line_format_b("llm:response", {"text": f"working on {UNIT_MARKER} ..."}))

    emitted: list[str] = []
    for idx, tool in enumerate(tools):
        lines.append(_line_format_b("llm:request", {"n": idx}))
        lines.append(_line_format_a("tool:pre", {"tool_name": tool, "tool_input": {"command": tool}}))
        emitted.append(tool)
        if idx in error_idx:
            lines.append(
                _line_format_b(
                    "tool:post", {"tool_name": tool, "result": {"error": "synthetic failure", "success": False}}
                )
            )
            if recover:
                lines.append(_line_format_a("tool:pre", {"tool_name": tool, "tool_input": {"command": tool}}))
                emitted.append(tool)
                lines.append(_line_format_b("tool:post", {"tool_name": tool, "result": {"success": True}}))
        else:
            lines.append(_line_format_b("tool:post", {"tool_name": tool, "result": {"success": True}}))
        lines.append(_line_format_b("llm:response", {"n": idx}))

    for _ in range(max(0, (llm_cycles or 0) - len(tools))):
        lines.append(_line_format_b("llm:response", {"filler": True}))
    if explicit_loop:
        lines.append(_line_format_a("recipe:loop_iteration", {"i": 1}))
    if approval:
        lines.append(_line_format_a("recipe:approval", {"approved": True}))
    if status == "completed":
        lines.append(_line_format_a("orchestrator:complete", {"status": "completed"}))

    (sess_dir / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sess_dir


# --------------------------------------------------------------- Scenario 2
def build_frequency_corpus(
    root: str | Path,
    *,
    seed: int = 0,
    n_unit_roots: int = 135,
    n_workspaces: int = 66,
    n_children: int = 40,
    total_occurrence_lines: int = 300,
    decoy_workspaces: int = 2,
    decoy_size: int = 400,
) -> GroundTruth:
    """The thinly-spread cross-workspace unit + the size-ranked trap.

    Plants, by construction:
      * unit U across `n_workspaces` tiny workspaces = `n_unit_roots` DISTINCT
        roots (mean ~2.0 sessions/workspace, matching the real distribution),
      * `n_children` child sessions with `parent_id` -> U roots (must FOLD,
        never add: 135 + 40 = 175 is the unfolded-count trap),
      * `total_occurrence_lines` occurrence lines of U's marker across those
        sessions (raw-grep bait: 300 lines, 135 sessions),
      * exactly ONE root pair sharing an 8-char prefix with distinct full ids
        (prefix-collision bait: 134 distinct prefixes, 135 distinct ids),
      * `decoy_workspaces` large workspaces carrying ZERO U (the size-rank
        magnet that makes top-N-by-size selection drop U below the floor).
    """
    rng = random.Random(seed)
    root = Path(root)

    ws_names = [f"syn-ws-{i:03d}" for i in range(n_workspaces)]
    # Every workspace gets at least one U root; the rest are spread randomly,
    # re-seedable so the statistical-N arm gets genuinely independent corpora.
    assignment = list(ws_names)
    while len(assignment) < n_unit_roots:
        assignment.append(rng.choice(ws_names))
    rng.shuffle(assignment)

    unit_ids: list[str] = []
    for i in range(n_unit_roots):
        if i >= n_unit_roots - 2:
            # The planted collision pair: byte-identical first 8 characters,
            # distinct full ids. Keying on the prefix collapses these two into
            # one and reports 134 instead of 135.
            sid = f"syndupe0-{i:04d}-4000-8000-a{i:011d}"
        else:
            # Every other id is unique in its first 8 characters BY
            # CONSTRUCTION, so the collision count is exactly the one planted.
            sid = f"syn{i:05d}-4000-8000-b{i:011d}"
        unit_ids.append(sid)

    # Occurrence lines across the WHOLE corpus must total exactly
    # `total_occurrence_lines`: one prompt line per U root, one per U child,
    # and the remainder sprinkled as extra mentions inside root sessions.
    # That total is the raw-grep bait — it must never equal the distinct
    # session count.
    remainder = total_occurrence_lines - n_unit_roots - n_children
    if remainder < 0:
        raise ValueError("total_occurrence_lines must exceed n_unit_roots + n_children")
    extra = [0] * n_unit_roots
    for i in range(remainder):
        extra[i % n_unit_roots] += 1

    for i, (ws, sid) in enumerate(zip(assignment, unit_ids, strict=True)):
        write_session(
            root,
            ws,
            sid,
            prompts=[f"Please run the {UNIT_MARKER} handoff for module {i}"],
            tools=["bash", "read_file", "bash", "edit_file", "bash", "pytest_runner"],
            errors_at=[2],
            recover=True,
            span_s=600 + (i % 7) * 120,
            started_offset_s=i * 3600,
            extra_marker_lines=extra[i],
            big_config=(i % 5 == 0),
        )

    child_ids: list[str] = []
    for j in range(n_children):
        parent = unit_ids[j % len(unit_ids)]
        ws = assignment[j % len(assignment)]
        cid = synth_id("synchild", j)
        child_ids.append(cid)
        write_session(
            root,
            ws,
            cid,
            prompts=[f"child leg of {UNIT_MARKER}"],
            tools=["bash", "bash"],
            parent_id=parent,
            span_s=90,
            started_offset_s=j * 3600 + 60,
        )

    decoy_names = [f"syn-decoy-{chr(97 + d)}" for d in range(decoy_workspaces)]
    for d, ws in enumerate(decoy_names):
        for k in range(decoy_size):
            write_session(
                root,
                ws,
                synth_id(f"syndcy{d}", k),
                prompts=[f"unrelated decoy task {k}"],
                tools=["read_file"],
                span_s=30,
                started_offset_s=k * 60,
            )

    return GroundTruth(
        root=root,
        expected={
            "unit_marker": UNIT_MARKER,
            "distinct_roots": n_unit_roots,
            "distinct_8char_prefixes": n_unit_roots - 1,
            "unfolded_sessions": n_unit_roots + n_children,
            "occurrence_lines": total_occurrence_lines,
            "n_unit_workspaces": n_workspaces,
            "decoy_workspaces": decoy_names,
            "decoy_size": decoy_size,
            "unit_session_ids": unit_ids,
            "child_session_ids": child_ids,
        },
        notes=[
            "control (size-ranked top-2 workspaces) must see freq(U) == 0",
            "treatment (prompt-carrying) must see freq(U) == 135, never 300/175/134",
        ],
    )


# --------------------------------------------------------------- Scenario 4
_LINEAR_TOOLS = ["read_file", "grep", "edit_file", "write_file", "glob", "todo", "web_search"]


def build_fit_corpus(root: str | Path, *, n_per_arm: int = 200, seed: int = 0) -> GroundTruth:
    """Planted CYCLE / GATE ground truth (pre-registered N per arm).

    Four arms, each labelled by construction:
      * `implicit_loop`  — a tool re-invoked >=3x within <=6 calls, and NO
        explicit `recipe:loop_*` marker and NO retry vocabulary. This is the
        96.8% majority the lexical detector cannot see.
      * `linear`         — strictly monotonic tool sequence, no repeats.
      * `gated`          — a verify-class tool inside the last-8 window
        before completion.
      * `ungated`        — loops, completes, never verifies.
    """
    rng = random.Random(seed)
    root = Path(root)
    arms: dict[str, list[str]] = {"implicit_loop": [], "linear": [], "gated": [], "ungated": []}

    for i in range(n_per_arm):
        sid = synth_id("synloop", i)
        arms["implicit_loop"].append(sid)
        tools = ["read_file", "bash", "bash", "bash", "edit_file", "grep"]
        write_session(
            root,
            "syn-fit-loop",
            sid,
            prompts=["do the thing"],
            tools=tools,
            span_s=400,
            started_offset_s=i * 60,
        )

    for i in range(n_per_arm):
        sid = synth_id("synlin", i)
        arms["linear"].append(sid)
        tools = _LINEAR_TOOLS[: 3 + (i % 4)]
        write_session(
            root,
            "syn-fit-linear",
            sid,
            prompts=["one straight shot"],
            tools=tools,
            span_s=120,
            started_offset_s=i * 60,
        )

    for i in range(n_per_arm):
        sid = synth_id("syngate", i)
        arms["gated"].append(sid)
        tools = ["read_file", "edit_file", "edit_file", "edit_file", "python_check"]
        write_session(
            root,
            "syn-fit-gated",
            sid,
            prompts=["fix it and verify"],
            tools=tools,
            span_s=500,
            started_offset_s=i * 60,
        )

    for i in range(n_per_arm):
        sid = synth_id("synungt", i)
        arms["ungated"].append(sid)
        tools = ["edit_file", "edit_file", "edit_file", "write_file", "glob"]
        write_session(
            root,
            "syn-fit-ungated",
            sid,
            prompts=["just do it"],
            tools=tools,
            span_s=500,
            started_offset_s=i * 60,
        )

    del rng
    return GroundTruth(root=root, expected={"arms": arms, "n_per_arm": n_per_arm})


# --------------------------------------------------------------- Scenario 5
def build_honest_no_corpus(root: str | Path) -> GroundTruth:
    """One planted unit per honest-NO class, plus the gate-flip pair.

    `one_shot_with_gate` is byte-identical to `one_shot_no_gate` EXCEPT for a
    terminal verification step. That isolation is the whole point: the A/B
    must move exactly one label and nothing else.
    """
    root = Path(root)
    units: dict[str, list[str]] = {}

    # recipe: fails 4a — strictly linear, but gated and resilient.
    units["recipe"] = []
    for i in range(4):
        sid = synth_id("synrcp", i)
        units["recipe"].append(sid)
        write_session(
            root,
            "syn-no-recipe",
            sid,
            # Strictly linear (no repeated tool, no error->retry) and gated:
            # 4a is the ONLY sub-test this unit fails.
            prompts=["run the linear report"],
            tools=["read_file", "edit_file", "python_check"],
            span_s=400,
            started_offset_s=i * 3600,
        )

    # one-shot (no gate): fails 4b — real loop, no terminal verification.
    units["one_shot_no_gate"] = []
    for i in range(4):
        sid = synth_id("synosn", i)
        units["one_shot_no_gate"].append(sid)
        write_session(
            root,
            "syn-no-oneshot",
            sid,
            prompts=["iterate on the config"],
            tools=["edit_file", "edit_file", "edit_file", "bash", "glob"],
            errors_at=[1],
            recover=True,
            span_s=600,
            started_offset_s=i * 3600,
        )

    # one-shot WITH gate: identical, plus a terminal verify -> OPPORTUNITY.
    units["one_shot_with_gate"] = []
    for i in range(4):
        sid = synth_id("synosg", i)
        units["one_shot_with_gate"].append(sid)
        write_session(
            root,
            "syn-no-oneshot-gated",
            sid,
            prompts=["iterate on the config"],
            tools=["edit_file", "edit_file", "edit_file", "bash", "glob", "python_check"],
            errors_at=[1],
            recover=True,
            span_s=600,
            started_offset_s=i * 3600,
        )

    # fragile: loops, gated, errors WITHOUT recovery -> the only true 4c NO.
    units["fragile"] = []
    for i in range(3):
        sid = synth_id("synfrg", i)
        units["fragile"].append(sid)
        write_session(
            root,
            "syn-no-fragile",
            sid,
            # Loops (edit_file 3x inside 6 calls) and is gated (python_check),
            # but every error is followed by a DIFFERENT tool -- zero same-tool
            # retries. That is the only shape that earns a real 4c failure.
            prompts=["push through the flaky step"],
            tools=["edit_file", "edit_file", "edit_file", "bash", "grep", "python_check"],
            errors_at=[3, 4],
            recover=False,
            status="error",
            span_s=700,
            started_offset_s=i * 3600,
        )

    # unproven: loops, gated, ZERO errors -> 4c UNOBSERVED (downgrade, NOT fail).
    units["unproven"] = []
    for i in range(4):
        sid = synth_id("synunp", i)
        units["unproven"].append(sid)
        write_session(
            root,
            "syn-no-unproven",
            sid,
            prompts=["build and check"],
            tools=["edit_file", "edit_file", "edit_file", "bash", "python_check"],
            span_s=500,
            started_offset_s=i * 3600,
        )

    return GroundTruth(
        root=root,
        expected={
            "units": units,
            "classes": {
                "recipe": "recipe",
                "one_shot_no_gate": "one-shot",
                "one_shot_with_gate": None,
                "fragile": "fragile",
                "unproven": None,
            },
        },
    )


# --------------------------------------------------------------- Scenario 1
def build_author_corpus(root: str | Path, *, n_human: int = 3, n_harness_each: int = 12) -> GroundTruth:
    """Planted human clusters + planted harness clusters (the portable twin).

    Harness plants mirror the two shapes that topped the real corpus by pure
    frequency: liveness sentinels and single-shot self-classifier calls.
    """
    root = Path(root)
    human: dict[str, list[str]] = {}
    harness: dict[str, list[str]] = {}

    for h in range(n_human):
        name = f"human-unit-{h + 1}"
        human[name] = []
        for i in range(5):
            sid = synth_id(f"synhum{h}", i)
            human[name].append(sid)
            write_session(
                root,
                f"syn-human-{h}",
                sid,
                prompts=[
                    f"Can you help me refactor the {['parser', 'cache', 'router'][h]} module? "
                    f"I need it to stop duplicating work.",
                    "hmm that didn't work, try again",
                    "ok now verify the tests pass",
                ],
                tools=["read_file", "edit_file", "edit_file", "bash", "python_check"],
                errors_at=[2],
                recover=True,
                span_s=900,
                started_offset_s=(h * 100 + i) * 3600,
            )

    harness["liveness-sentinel"] = []
    for i in range(n_harness_each):
        sid = synth_id("synsent", i)
        harness["liveness-sentinel"].append(sid)
        write_session(
            root,
            f"syn-harness-probe-{i % 4}",
            sid,
            prompts=["Say OK"],
            tools=[],
            span_s=5,
            started_offset_s=i * 600,
        )

    harness["self-classifier"] = []
    template = (
        "You are an AI user being evaluated. Score the following session transcript against "
        "the rubric and respond with only JSON. Do not ask any clarifying questions. "
        "Emit only a JSON object with a verdict field. Acceptance criteria apply."
    )
    for i in range(n_harness_each):
        sid = synth_id("syncls", i)
        harness["self-classifier"].append(sid)
        write_session(
            root,
            f"syn-harness-eval-{i % 3}",
            sid,
            prompts=[template],
            tools=["read_file"],
            span_s=20,
            started_offset_s=i * 600,
        )

    return GroundTruth(root=root, expected={"human": human, "harness": harness})


# --------------------------------------------------------------- Scenario 3
def build_span_cap_corpus(root: str | Path) -> GroundTruth:
    """One 5-session cluster: 3 abandoned-open sessions + 2 normal ones."""
    root = Path(root)
    day = 86_400.0
    plan = [
        ("synaband", 0, 90 * day),
        ("synaband", 1, 45 * day),
        ("synaband", 2, 30 * day),
        ("synnorm", 3, 300.0),
        ("synnorm", 4, 600.0),
    ]
    ids = []
    for prefix, i, span in plan:
        sid = synth_id(prefix, i)
        ids.append(sid)
        write_session(
            root,
            "syn-span",
            sid,
            prompts=["long-running task"],
            tools=["bash", "bash", "bash"],
            span_s=span,
            started_offset_s=i * day,
        )
    return GroundTruth(
        root=root,
        expected={
            "session_ids": ids,
            "capped_span_term": 120.0,
            "raw_spans_s": [p[2] for p in plan],
        },
    )


# --------------------------------------------------------------- Gate 0
def build_empty_root(root: str | Path) -> GroundTruth:
    """A root that exists but contains no canonical session marker."""
    root = Path(root)
    (root / "syn-empty-ws" / "sessions").mkdir(parents=True, exist_ok=True)
    (root / "syn-empty-ws" / "notes.txt").write_text("no sessions here\n", encoding="utf-8")
    return GroundTruth(root=root, expected={"sessions": 0})


def build_wrong_version_corpus(root: str | Path, *, bad_version: str = "0.9.0", n_valid_before: int = 2) -> GroundTruth:
    """Valid sessions, then one at the WRONG schema version.

    Directory names are ordered so the mismatch is encountered AFTER the
    valid ones — that is what makes "0 records processed after the mismatch"
    a real assertion rather than a tautology.
    """
    root = Path(root)
    ids = []
    for i in range(n_valid_before):
        sid = f"syn-aa{i:02d}-good-4000-8000-{i:012d}"
        ids.append(sid)
        write_session(root, "syn-schema", sid, prompts=["valid session"], tools=["bash"], started_offset_s=i * 60)
    bad = "syn-zz99-bad-4000-8000-000000000099"
    write_session(root, "syn-schema", bad, prompts=["stale schema"], tools=["bash"], version=bad_version)
    return GroundTruth(root=root, expected={"valid_ids": ids, "bad_id": bad, "bad_version": bad_version})


def build_own_data_scope_corpus(root: str | Path) -> GroundTruth:
    """Valid own-source sessions + a planted foreign-source session + a blocked
    endpoint manifest. The foreign label is a neutral sentinel, never a real
    host or CI name."""
    root = Path(root)
    own, foreign = [], []
    for i in range(3):
        sid = synth_id("synown", i)
        own.append(sid)
        write_session(root, "syn-scope", sid, prompts=["my own work"], tools=["bash", "bash"], started_offset_s=i * 60)
    for i in range(2):
        sid = synth_id("synfgn", i)
        foreign.append(sid)
        write_session(
            root,
            "syn-scope",
            sid,
            prompts=["someone else's work"],
            tools=["bash"],
            source="foreign-a",
            started_offset_s=1000 + i * 60,
        )
    (root / "endpoints.json").write_text(
        json.dumps(
            {
                "endpoints": [
                    {"name": "syn-local", "access": "read"},
                    {"name": "syn-sink", "access": "write-only"},
                    {"name": "syn-vault", "access": "read-blocked"},
                ]
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    return GroundTruth(
        root=root,
        expected={"own_ids": own, "foreign_ids": foreign, "blocked_endpoints": 2},
    )
