"""Extraction spine — one compact record per session.

Crystallizes `ci_mine_v2.extract_session`. The emitted record schema is
FIELD-COMPATIBLE with the proven miner's `extracts.jsonl`, so the calibration
corpus is directly consumable by every detector in this package (that
compatibility is what lets the build-time real-corpus arms run at all).

Hard-won disciplines preserved verbatim:

* **E1 — parse the full line, never head-match.** ~95% of event lines put the
  `event` key AFTER a `data` payload; a key-order-dependent match undercounts
  `prompt:submit` by 72%.
* **E2 — line-budgeted, not byte-budgeted.** `session:config` lines are
  routinely ~1 MB; a byte window misses the prompts that follow them.
* **Errors come from `tool:post.result.error`** (schema truth — `tool:error`
  exists but is vanishingly rare: 10 events in 2,164 sessions).
* **Span capped at 7,200 s** — the raw max in the corpus is 90 days of an
  abandoned open session.
* **Dedup by FULL `session_id`**, never an 8-char prefix (measured 3.0%
  collision rate), and **fold children into their root via `parent_id`**.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

from .discover import Discovery, SessionRef, event_of, read_metadata

MAX_LINE_BYTES = 150_000
MAX_EVENTS_SCANNED = 6_000
FIRST_PROMPT_CHARS = 700
LATER_PROMPT_CHARS = 220
MAX_PROMPT_SAMPLES = 4
MAX_TOOL_SEQ = 15
MAX_TOOL_TAIL = 8
#: Full tool stream retained per record so the WINDOWED cycle detector
#: (S3: same tool >=3x within <=6 consecutive calls) can run without re-reading
#: the corpus. Capped so a runaway session cannot bloat the record.
MAX_TOOL_ALL = 400
SPAN_CAP_S = 7_200.0

_PROMPT_RE = re.compile(r'"prompt"\s*:\s*"((?:[^"\\]|\\.){0,4000})')
_TOOLNAME_RE = re.compile(r'"tool_name"\s*:\s*"([^"]{1,60})"')
_NOISE_RE = re.compile(
    r"<context_file\b.*?</context_file>|<system-reminder\b.*?</system-reminder>",
    re.DOTALL,
)

#: Human iteration vocabulary. A weak CORROBORATOR and author signal only —
#: never the cycle detector (lexical detection finds 3.2% of real loops).
LOOP_MARKER_RE = re.compile(
    r"\b(try again|again|retry|still (failing|broken|wrong|not)|that (didn'?t|doesn'?t) work|"
    r"nope|no,? |fix (it|that|this)|same (error|issue|problem)|revert|undo|"
    r"now it|but it|it'?s still|doesn'?t work|not working|broke|regressed|"
    r"one more|keep going|continue|next|iterate)\b",
    re.IGNORECASE,
)

#: Evidence-gate vocabulary. Bounded corroborator for S4, never the detector.
GATE_MARKER_RE = re.compile(
    r"\b(verify|verified|confirm|assert|check that|make sure|prove|"
    r"until (it|all|the) (passes|pass|green|works)|tests? pass|all green|"
    r"exit code|screenshot|read ?back|validate|acceptance|must (pass|match)|"
    r"succeed|success criteria|stop when|done when)\b",
    re.IGNORECASE,
)

#: Explicit loop markers in the EVENT stream (the 3.2% minority).
EXPLICIT_LOOP_EVENTS = ("recipe:loop_iteration", "recipe:loop_complete", "recipe:loop_start")


def clean_prompt(text: str, cap: int) -> str:
    text = _NOISE_RE.sub(" ", text)
    text = re.sub(r"</?[a-z_]+(?:\s[^>]*)?>", " ", text)
    return " ".join(text.split())[:cap]


def _span_seconds(meta: dict) -> float | None:
    started = meta.get("started_at")
    ended = meta.get("ended_at") or meta.get("last_event_at")
    if not started or not ended:
        return None
    try:
        delta = datetime.fromisoformat(str(ended)) - datetime.fromisoformat(str(started))
    except (TypeError, ValueError):
        return None
    return round(delta.total_seconds(), 1)


def _seq_signature(tool_all: list[str]) -> tuple[str | None, int]:
    """A-rung signature: SHA1 of the first 10 consecutive-deduplicated tools."""
    collapsed: list[str] = []
    for tool in tool_all:
        if not collapsed or collapsed[-1] != tool:
            collapsed.append(tool)
        if len(collapsed) >= 10:
            break
    if not collapsed:
        return None, 0
    return hashlib.sha1("|".join(collapsed).encode()).hexdigest()[:12], len(collapsed)


def _handle_big_line(line: str, state: dict) -> None:
    """Oversized line: read the event field only, never json-parse ~1 MB."""
    name = event_of(line.encode("utf-8", "replace"))
    if not name:
        return
    state["ev_names"][name] += 1
    if name == "llm:response":
        state["n_llm_resp"] += 1
    elif name == "llm:request":
        state["n_llm_req"] += 1
    elif name == "tool:pre":
        state["n_tools"] += 1
        m = _TOOLNAME_RE.search(line[:4000])
        if m:
            _record_tool(state, m.group(1)[:40])
    elif name == "prompt:submit":
        state["n_prompts"] += 1
        m = _PROMPT_RE.search(line[:400_000])
        if m:
            try:
                txt = json.loads('"' + m.group(1) + '"')
            except json.JSONDecodeError:
                txt = m.group(1)
            _record_prompt(state, txt)


def _record_tool(state: dict, name: str) -> None:
    if not name:
        return
    if len(state["tool_all"]) < MAX_TOOL_ALL:
        state["tool_all"].append(name)
    state["tool_tail_buf"].append(name)
    if len(state["tool_tail_buf"]) > MAX_TOOL_TAIL:
        state["tool_tail_buf"].pop(0)
    if len(state["tool_seq"]) < MAX_TOOL_SEQ:
        state["tool_seq"].append(name)
    if name == state["last_err_tool"]:
        state["n_err_recover"] += 1
        state["last_err_tool"] = None


def _record_prompt(state: dict, txt: str) -> None:
    if not isinstance(txt, str):
        return
    if not state["first_prompt_full"]:
        state["first_prompt_full"] = clean_prompt(txt, 2000)
    if len(state["prompts"]) >= MAX_PROMPT_SAMPLES:
        return
    cap = FIRST_PROMPT_CHARS if not state["prompts"] else LATER_PROMPT_CHARS
    cleaned = clean_prompt(txt, cap)
    if cleaned:
        state["prompts"].append(cleaned)


def extract_session(ref: SessionRef, *, strict_schema: bool = True) -> dict:
    """Build one compact record for a single session."""
    meta_path = ref.events_path.parent / "metadata.json"
    meta = read_metadata(meta_path, strict=strict_schema)

    state: dict = {
        "prompts": [],
        "first_prompt_full": "",
        "n_prompts": 0,
        "n_tools": 0,
        "n_llm_req": 0,
        "n_llm_resp": 0,
        "n_err": 0,
        "n_err_recover": 0,
        "n_delegates": 0,
        "n_artifacts": 0,
        "n_skills": 0,
        "n_compact": 0,
        "n_tool_error_events": 0,
        "n_explicit_loop": 0,
        "n_approvals": 0,
        "tool_all": [],
        "tool_seq": [],
        "tool_tail_buf": [],
        "bash_cmds": [],
        "last_err_tool": None,
        "ev_names": Counter(),
    }

    scanned = 0
    if ref.events_path.is_file():
        with ref.events_path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                scanned += 1
                if scanned > MAX_EVENTS_SCANNED:
                    break
                if len(line) > MAX_LINE_BYTES:
                    _handle_big_line(line, state)
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(ev, dict):
                    continue
                _consume_event(ev, state)

    span = _span_seconds(meta)
    span_capped = None if span is None else round(min(max(span, 0.0), SPAN_CAP_S), 1)
    seq_sig, seq_len = _seq_signature(state["tool_all"])
    later = " ".join(state["prompts"][1:])
    all_prompts = " ".join(state["prompts"])

    return {
        "session_id": ref.session_id,
        "parent_id": ref.parent_id,
        "workspace": ref.workspace,
        "status": meta.get("status"),
        "started_at": meta.get("started_at"),
        "span_s": span,
        "span_capped_s": span_capped,
        "n_prompts": state["n_prompts"],
        "prompts": state["prompts"],
        "first_prompt": state["first_prompt_full"],
        "tool_seq": state["tool_seq"],
        "tool_all": state["tool_all"],
        "tool_tail": list(state["tool_tail_buf"]),
        "n_tool_calls": state["n_tools"],
        "n_llm_requests": state["n_llm_req"],
        "n_llm_cycles": state["n_llm_resp"],
        "n_tool_errors": state["n_err"],
        "n_err_recover": state["n_err_recover"],
        "n_delegates": state["n_delegates"],
        "n_artifacts": state["n_artifacts"],
        "n_skill_events": state["n_skills"],
        "n_compactions": state["n_compact"],
        "n_tool_error_events": state["n_tool_error_events"],
        "n_explicit_loop_events": state["n_explicit_loop"],
        "n_approvals": state["n_approvals"],
        "bash_cmds": state["bash_cmds"],
        "loop_markers": len(LOOP_MARKER_RE.findall(later)),
        "gate_markers": len(GATE_MARKER_RE.findall(all_prompts)),
        "seq_sig": seq_sig,
        "seq_len": seq_len,
        "events_scanned": scanned,
        "scan_truncated": scanned > MAX_EVENTS_SCANNED,
        "machine_launched": bool(state["ev_names"].get("recipe:start") or state["ev_names"].get("pipeline:node_start")),
    }


def _consume_event(ev: dict, state: dict) -> None:
    name = ev.get("event")
    if name:
        state["ev_names"][name] += 1
    raw_data = ev.get("data")
    data: dict = raw_data if isinstance(raw_data, dict) else {}

    if name == "prompt:submit":
        state["n_prompts"] += 1
        _record_prompt(state, data.get("prompt") or "")
    elif name == "tool:pre":
        state["n_tools"] += 1
        tool = str(data.get("tool_name") or "")[:40]
        _record_tool(state, tool)
        if tool == "bash" and len(state["bash_cmds"]) < 25:
            args = data.get("tool_input") or data.get("tool_args") or data.get("args")
            if isinstance(args, dict) and isinstance(args.get("command"), str):
                state["bash_cmds"].append(args["command"][:200])
            elif isinstance(args, str):
                state["bash_cmds"].append(args[:220])
    elif name == "tool:post":
        # Schema truth: there is no usable `tool:error` event — errors live here.
        res = data.get("result")
        if isinstance(res, dict) and (res.get("error") or res.get("success") is False):
            state["n_err"] += 1
            state["last_err_tool"] = str(data.get("tool_name") or "")[:40] or None
    elif name == "llm:response":
        state["n_llm_resp"] += 1
    elif name == "llm:request":
        state["n_llm_req"] += 1
    elif name == "delegate:agent_spawned":
        state["n_delegates"] += 1
    elif name == "delegate:error":
        state["n_err"] += 1
    elif name == "artifact:write":
        state["n_artifacts"] += 1
    elif name in ("skill:loaded", "skills:discovered"):
        state["n_skills"] += 1
    elif name == "context:compaction":
        state["n_compact"] += 1
    elif name == "tool:error":
        state["n_err"] += 1
        state["n_tool_error_events"] += 1
    elif name in EXPLICIT_LOOP_EVENTS:
        state["n_explicit_loop"] += 1
    elif name == "recipe:approval":
        state["n_approvals"] += 1


def extract_all(
    refs: list[SessionRef],
    *,
    strict_schema: bool = True,
    fold_children: bool = True,
) -> list[dict]:
    """Extract many sessions, deduping by FULL session_id.

    `fold_children=True` folds a child session's toil into its `parent_id`
    root rather than counting it as an additional distinct session — the C1
    join discipline. A child whose parent is not in the selection is kept as
    its own record (dropping it would silently lose work).
    """
    records: list[dict] = []
    seen: set[str] = set()
    for ref in refs:
        if ref.session_id in seen:
            continue
        seen.add(ref.session_id)
        rec = extract_session(ref, strict_schema=strict_schema)
        if rec["n_prompts"] == 0 and rec["n_tool_calls"] == 0:
            continue
        records.append(rec)

    if not fold_children:
        return records

    by_id = {r["session_id"]: r for r in records}
    folded: list[dict] = []
    for rec in records:
        parent = rec.get("parent_id")
        if parent and parent in by_id and parent != rec["session_id"]:
            _fold_into(by_id[parent], rec)
            continue
        folded.append(rec)
    return folded


_FOLD_SUM_FIELDS = (
    "n_tool_calls",
    "n_llm_requests",
    "n_llm_cycles",
    "n_tool_errors",
    "n_err_recover",
    "n_delegates",
    "n_artifacts",
    "n_skill_events",
    "n_compactions",
    "n_explicit_loop_events",
    "n_approvals",
)


def _fold_into(root: dict, child: dict) -> None:
    """Fold a child session's observables into its root (C1 discipline)."""
    for field_name in _FOLD_SUM_FIELDS:
        root[field_name] = root.get(field_name, 0) + child.get(field_name, 0)
    root["tool_all"] = (root.get("tool_all") or []) + (child.get("tool_all") or [])
    root.setdefault("folded_children", []).append(child["session_id"])


def write_extracts(records: list[dict], out_path: str | Path) -> int:
    """Serialize records to a JSONL file. Returns the count written."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for rec in records:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return len(records)


def read_extracts(path: str | Path) -> list[dict]:
    """Read an `extracts.jsonl` produced by this library OR by `ci_mine_v2`."""
    out: list[dict] = []
    with Path(path).open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def extract_corpus(discovery: Discovery, refs: list[SessionRef], *, strict_schema: bool = True) -> list[dict]:
    """Convenience: extract + classify authors in one call."""
    from .author import classify_authors  # local import keeps module graph acyclic

    records = extract_all(refs, strict_schema=strict_schema)
    classify_authors(records)
    return records
