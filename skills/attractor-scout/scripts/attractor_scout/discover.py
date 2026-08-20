"""Discovery spine — enumerate + qualify (crystallized from `ci_mine_v2`).

Responsibilities (design.md §1a):

* Resolve the context-intelligence root
  (`AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH` -> `~/.amplifier/projects`).
* **Version-check every `metadata.json`** (`format=context-intelligence`,
  `version=1.0.0`) and FAIL LOUD on mismatch (`JsonlSchemaMismatch`).
* Root-session selection, then the **E3 prompt-carrying selector** — never
  top-N-by-workspace-size. The measured cost of getting this wrong is total:
  the #1 human opportunity lives across 66 workspaces of 1-5 sessions each
  and is invisible to any size-ranked selection.
* **Fail loud on empty**: `looked in <root>, found 0`.
* **Carry what decides PROVENANCE**: `working_dir` from the metadata, and the
  first event / orchestration markers from the one qualification read. This
  module never interprets them — `provenance.classify` is the only place that
  decides what they mean — but it must not drop them, because a
  `prompt:submit` alone says nothing about who submitted it.
* Own-data scoping: own-source sessions only; a session whose metadata
  explicitly declares a DIFFERENT origin is honoured and excluded, and
  write-only / read-blocked endpoints are never touched. Counters are exposed
  so the scenario can machine-check the exclusion.

Every read here is local filesystem I/O. Nothing in this module opens a
socket — that is the enforcement of "nothing leaves the machine", not a
promise about it.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import provenance
from .errors import EmptyCorpusError, JsonlSchemaMismatch

REQUIRED_FORMAT = "context-intelligence"
REQUIRED_VERSION = "1.0.0"

#: Canonical per-session marker. Discovery keys on THIS, never a shallower glob.
SESSION_MARKER = "context-intelligence/events.jsonl"

#: Own-data scope sentinel. Tier-C local JSONL is the caller's OWN data by
#: definition, so a session with no explicit origin label is own-source. Any
#: session whose metadata explicitly declares a different origin is out of
#: scope. This value is an internal sentinel, never a real host or CI name.
OWN_SOURCE = "own"

#: Endpoint access modes we refuse to read from.
UNREADABLE_ENDPOINT_MODES = frozenset({"write-only", "read-blocked"})

# E2: a `session:config` line is routinely ~1 MB. Qualification is
# LINE-budgeted, never byte-budgeted, or the prompt that follows the config
# blob is missed (a measured 28x undercount).
QUALIFY_MAX_LINES = 400
QUALIFY_MAX_BYTES = 12_000_000

_EVENT_RE_B = re.compile(rb'"event"\s*:\s*"([^"]+)"')


def ci_root(explicit: str | Path | None = None) -> Path:
    """Resolve the context-intelligence root."""
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("AMPLIFIER_CONTEXT_INTELLIGENCE_BASE_PATH")
    return Path(env) if env else Path.home() / ".amplifier" / "projects"


def event_of(line: bytes) -> str | None:
    """Return the TOP-LEVEL `event` name of a raw events.jsonl line.

    E1 — parse, don't head-match. This corpus carries two key orderings and
    the `event` key is NOT at a fixed offset:

        A  {"ts":..,"event":"X",..,"data":{..}}          event EARLY
        B  {"data":{..~1MB..},"event":"X","timestamp":..} event LAST

    Format B is ~95% of lines. A head-only regex therefore misses format-B
    `prompt:submit` entirely — a measured 72% undercount of prompt-carrying
    roots. Head-first for A (the top-level key precedes any nested "event"
    inside `data`), tail-LAST for B (the top-level key is the final one).
    """
    if line[:6] == b'{"ts":' or line[:7] == b'{"ts": ':
        m = _EVENT_RE_B.search(line[:1500])
        return m.group(1).decode("utf-8", "replace") if m else None
    tail = _EVENT_RE_B.findall(line[-800:])
    if tail:
        return tail[-1].decode("utf-8", "replace")
    m = _EVENT_RE_B.search(line[:1500])
    return m.group(1).decode("utf-8", "replace") if m else None


@dataclass
class ScopeCounters:
    """Machine-checkable own-data-scoping counters (own-scope exclusion gate)."""

    foreign_sessions_seen: int = 0
    foreign_sessions_mined: int = 0
    blocked_endpoints_declared: int = 0
    blocked_endpoints_touched: int = 0
    egress_bytes: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "foreign_sessions_seen": self.foreign_sessions_seen,
            "foreign_sessions_mined": self.foreign_sessions_mined,
            "blocked_endpoints_declared": self.blocked_endpoints_declared,
            "blocked_endpoints_touched": self.blocked_endpoints_touched,
            "egress_bytes": self.egress_bytes,
        }


@dataclass
class EventScan:
    """What ONE qualification read of an events file saw.

    Qualification used to answer a single question — "is there a
    `prompt:submit` in here?" — and that question has ZERO discriminating
    power: a harness-fired root emits a byte-identical event. The same read
    now also returns the provenance markers that DO discriminate (which event
    came first, and whether an orchestrator started this session), so the
    qualifier can hand downstream a stamped session instead of an anonymous
    one. Same file, same budget, one pass.
    """

    has_prompt: bool = False
    first_event: str | None = None
    orchestration_events: tuple[str, ...] = ()
    n_prompts_in_window: int = 0
    window_truncated: bool = False


@dataclass
class SessionRef:
    """One discovered session directory.

    `working_dir` is carried deliberately: it is the single most discriminating
    provenance field the corpus records, and dropping it here (as this class
    used to) made every downstream stage blind to the difference between a
    session you ran in your project and one a harness ran in a temp directory.
    It is plumbed, never interpreted, here — the ladder in `provenance.py` is
    the only place that decides what a path means.
    """

    workspace: str
    session_dir: str
    session_id: str
    parent_id: str | None
    started_at: str | None
    ended_at: str | None
    status: str | None
    events_path: Path
    is_root: bool
    working_dir: str | None = None
    #: Filled in by `qualify` from the single qualification read (see EventScan).
    prescan: EventScan | None = None


@dataclass
class Discovery:
    """Result of `enumerate_sessions`."""

    root: Path
    sessions: list[SessionRef] = field(default_factory=list)
    workspaces: dict[str, int] = field(default_factory=dict)
    scope: ScopeCounters = field(default_factory=ScopeCounters)
    metadata_files: int = 0

    @property
    def roots(self) -> list[SessionRef]:
        return [s for s in self.sessions if s.is_root]


def read_metadata(meta_path: Path, *, strict: bool = True) -> dict:
    """Read + version-check one `metadata.json`.

    FAIL LOUD (`JsonlSchemaMismatch`) on format/version mismatch when
    `strict`. A stale vendored copy of this library must never be able to
    return a wrong answer from a schema it does not recognize.
    """
    try:
        raw = meta_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise JsonlSchemaMismatch(
            str(meta_path), f"unreadable: {exc}", expected=f"{REQUIRED_FORMAT}/{REQUIRED_VERSION}"
        ) from exc
    if not raw.strip():
        raise JsonlSchemaMismatch(
            str(meta_path), "empty metadata.json", expected=f"{REQUIRED_FORMAT}/{REQUIRED_VERSION}"
        )
    try:
        meta = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise JsonlSchemaMismatch(
            str(meta_path), f"invalid json: {exc}", expected=f"{REQUIRED_FORMAT}/{REQUIRED_VERSION}"
        ) from exc
    if not isinstance(meta, dict):
        raise JsonlSchemaMismatch(
            str(meta_path), "metadata.json is not an object", expected=f"{REQUIRED_FORMAT}/{REQUIRED_VERSION}"
        )
    got_format, got_version = meta.get("format"), meta.get("version")
    if strict and got_format != REQUIRED_FORMAT:
        raise JsonlSchemaMismatch(
            str(meta_path), f"format mismatch: got {got_format!r}", expected=f"format={REQUIRED_FORMAT!r}"
        )
    if strict and got_version != REQUIRED_VERSION:
        raise JsonlSchemaMismatch(
            str(meta_path), f"version mismatch: got {got_version!r}", expected=f"version={REQUIRED_VERSION!r}"
        )
    return meta


def _is_root(meta: dict, dir_name: str) -> bool:
    """Root = no `parent_id`, and not a delegated `_self`/agent child."""
    if meta.get("parent_id"):
        return False
    sid = meta.get("session_id") or dir_name
    return not str(sid).startswith("0000000000000000-")


def _load_endpoint_scope(root: Path, counters: ScopeCounters) -> None:
    """Honour a declared endpoint manifest, if the corpus ships one.

    PROVISIONAL (design.md §6 names the constraint; the corpus schema for it
    is not measured). We read `<root>/endpoints.json` when present and record
    how many endpoints declare an unreadable access mode. The library never
    touches any of them, because the Tier-C path only ever reads local
    session files — so `blocked_endpoints_touched` is 0 by construction, not
    by good behaviour.
    """
    manifest = root / "endpoints.json"
    if not manifest.is_file():
        return
    try:
        data = json.loads(manifest.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return
    entries = data.get("endpoints", data) if isinstance(data, dict) else data
    if not isinstance(entries, list):
        return
    for ep in entries:
        if isinstance(ep, dict) and str(ep.get("access", "")).lower() in UNREADABLE_ENDPOINT_MODES:
            counters.blocked_endpoints_declared += 1


def working_dir_of(meta: dict) -> str | None:
    """The session's recorded working directory, if the corpus carries one.

    This is the single most discriminating provenance field available, and it
    was being dropped on the floor here. Read under a small set of neutral,
    forward-compatible keys for the same reason `_source_of` does: the schema
    may name it differently in a later version, and an absent value must read
    as ABSENT (never as a default path), because `provenance.classify_workspace`
    treats an unrecorded workspace as evidence of nothing.
    """
    for key in ("working_dir", "cwd", "workdir"):
        val = meta.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def _source_of(meta: dict) -> str:
    """Origin of a session's data — own vs a foreign source.

    NOTE ON SCHEMA: context-intelligence 1.0.0 declares NO standard origin
    field, so there is nothing to key on in the common case — and that is
    exactly right, because Tier-C local JSONL is the caller's OWN data by
    definition. An ABSENT origin label therefore means own-source; we never
    invent a second origin the schema does not declare.

    A corpus MAY carry an EXPLICIT origin under one of a small set of neutral,
    forward-compatible keys (`source`, `data_source`, `origin`). When present
    and different from `OWN_SOURCE`, that session is honoured and excluded from
    mining. The keys are chosen to be host- and vendor-neutral; no real machine
    or CI name appears here.
    """
    for key in ("source", "data_source", "origin"):
        val = meta.get(key)
        if isinstance(val, str) and val:
            return val
    return OWN_SOURCE


def enumerate_sessions(
    root: str | Path | None = None,
    *,
    strict_schema: bool = True,
) -> Discovery:
    """Walk the root, version-check every metadata, collect session refs.

    Raises `EmptyCorpusError` (message contains `looked in <root>, found 0`)
    when the canonical marker yields nothing, and `JsonlSchemaMismatch` on the
    first schema-mismatched `metadata.json` — the walk STOPS there, so no
    session past the mismatch is ever collected or extracted.
    """
    resolved = ci_root(root)
    disc = Discovery(root=resolved)
    if not resolved.is_dir():
        raise EmptyCorpusError(str(resolved), "sessions")

    _load_endpoint_scope(resolved, disc.scope)

    for proj in sorted(p for p in resolved.iterdir() if p.is_dir()):
        sessions_dir = proj / "sessions"
        if not sessions_dir.is_dir():
            continue
        for sess in sorted(s for s in sessions_dir.iterdir() if s.is_dir()):
            ci_dir = sess / "context-intelligence"
            meta_path = ci_dir / "metadata.json"
            if not meta_path.is_file():
                continue
            disc.metadata_files += 1
            meta = read_metadata(meta_path, strict=strict_schema)

            if _source_of(meta) != OWN_SOURCE:
                disc.scope.foreign_sessions_seen += 1
                continue

            disc.sessions.append(
                SessionRef(
                    workspace=proj.name,
                    session_dir=sess.name,
                    session_id=str(meta.get("session_id") or sess.name),
                    parent_id=(str(meta["parent_id"]) if meta.get("parent_id") else None),
                    started_at=meta.get("started_at"),
                    ended_at=meta.get("ended_at") or meta.get("last_event_at"),
                    status=meta.get("status"),
                    events_path=ci_dir / "events.jsonl",
                    is_root=_is_root(meta, sess.name),
                    working_dir=working_dir_of(meta),
                )
            )
            disc.workspaces[proj.name] = disc.workspaces.get(proj.name, 0) + 1

    if disc.metadata_files == 0 or not disc.sessions:
        raise EmptyCorpusError(str(resolved), "sessions")
    return disc


def scan_events(events_path: Path) -> EventScan:
    """ONE line-budgeted read that answers qualification AND provenance.

    LINE-budgeted (E2). Matches the top-level EVENT FIELD via `event_of`, not
    a raw substring, so a ~1 MB `session:config` payload that merely mentions
    the string cannot false-positive.

    Beyond "is there a prompt", this records the FIRST event name and any
    orchestration-start events inside the window. Those are what the
    provenance ladder needs and what a prompt-only qualifier could never
    supply: a `prompt:submit` proves a prompt was submitted, and nothing
    whatsoever about who submitted it.
    """
    scan = EventScan()
    read = 0
    orchestration: list[str] = []
    seen_events = 0
    try:
        with open(events_path, "rb") as fh:
            for _ in range(QUALIFY_MAX_LINES):
                line = fh.readline(2_000_000)
                if not line:
                    break
                read += len(line)
                if read > QUALIFY_MAX_BYTES:
                    scan.window_truncated = True
                    break
                name = event_of(line)
                if not name:
                    continue
                seen_events += 1
                if scan.first_event is None:
                    scan.first_event = name
                if name == "prompt:submit":
                    scan.has_prompt = True
                    scan.n_prompts_in_window += 1
                elif name in provenance.ORCHESTRATION_START_EVENTS and name not in orchestration:
                    orchestration.append(name)
            else:
                scan.window_truncated = True
    except OSError:
        return EventScan()
    scan.orchestration_events = tuple(orchestration)
    return scan


def carries_prompt(events_path: Path) -> bool:
    """Does this session carry a `prompt:submit`?

    NECESSARY, NEVER SUFFICIENT. This answers one narrow question and is kept
    as its own name so nobody mistakes it for an authorship test: a harness
    firing a single non-interactive run emits a byte-identical event to a
    person typing. Selection uses it to find sessions with work in them;
    `provenance.classify` decides whose work it was.
    """
    return scan_events(events_path).has_prompt


def qualify(
    discovery: Discovery,
    *,
    selector: str = "prompt-carrying",
    top_n_workspaces: int | None = None,
) -> list[SessionRef]:
    """Select the root sessions to mine.

    `selector="prompt-carrying"` is the E3 treatment and the only selector
    the product ships. `selector="size-ranked"` exists SOLELY as the
    Scenario-2 control arm — it is the documented trap (it drops the thinly-
    spread cross-workspace unit below the admission floor) and is never a
    production path.
    """
    roots = discovery.roots
    if selector == "size-ranked":
        if top_n_workspaces is None:
            raise ValueError("size-ranked selector requires top_n_workspaces")
        by_ws: dict[str, int] = {}
        for r in roots:
            by_ws[r.workspace] = by_ws.get(r.workspace, 0) + 1
        keep = {ws for ws, _ in sorted(by_ws.items(), key=lambda kv: (-kv[1], kv[0]))[:top_n_workspaces]}
        return [r for r in roots if r.workspace in keep]
    if selector != "prompt-carrying":
        raise ValueError(f"unknown selector: {selector!r}")

    qualified: list[SessionRef] = []
    for ref in roots:
        scan = scan_events(ref.events_path)
        if not scan.has_prompt:
            continue
        # Stamp the provenance markers from the SAME read. Selection stays
        # "prompt-carrying" (a session with no prompt has no work in it), but a
        # qualified session is no longer anonymous: whoever consumes it can see
        # what started it. Agent-authored roots are deliberately still
        # qualified here — they are COUNTED downstream as an already-automated
        # footprint, and dropping them at the door would make that count a lie.
        ref.prescan = scan
        qualified.append(ref)
    if not qualified:
        raise EmptyCorpusError(str(discovery.root), "prompt-carrying root sessions")
    return qualified
