"""S4 / Fit-4b — GATE: does the exit stop on a CHECKED condition?

**Gate presence, not loop presence, is the scarce discriminator.** Measured:
a terminal verification precedes completion in 16.7% of all sessions and
30.2% of >=6-tool-call sessions. That scarcity is exactly why `one-shot`
(real loop, no gate) is the second-largest honest-NO class — and why a unit
that fails only 4b is one small intervention from converting.

Detector shape: a verify-class tool inside the LAST-8 tool window, OR a
`recipe:approval` event, OR a bounded verb regex over the stable `bash`
command field. `GATE_MARKER_RE` prompt vocabulary is a corroborator only.

`VERIFY_TOOLS` / `VERIFY_BASH_RE` are CONFIG, deliberately (Gap 1 / O4 is
open: the finalized allow-list is to be settled from a tool-name census over
the corpus, and finalization only ADDS recall). The provisional list below is
the one the 16.7% / 30.2% prevalences were measured with, so swapping it
without re-measuring would silently move a calibrated number.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: PROVISIONAL allow-list (Gap 1 open). Prevalences 16.7% / 30.2% were
#: measured with exactly this list — do not edit without re-running the
#: prevalence arm.
VERIFY_TOOLS: frozenset[str] = frozenset(
    {
        "python_check",
        "rust_check",
        "LSP",
        "dot_graph",
        "terminal_inspector",
        "android_inspector",
        "ios_inspector",
        "browser_screenshot",
        "browser_read",
        "browser_snapshot",
        "browser_wait_text",
        "browser_wait_for",
        "nano-banana",
    }
)

VERIFY_BASH_RE = re.compile(
    r"\b(pytest|make (test|check|lint)|npm (test|run test)|cargo (test|check|clippy)|"
    r"go test|ruff|pyright|mypy|curl -|exit code|\$\?)\b",
    re.IGNORECASE,
)

#: The terminal window: a check outside it is not a TERMINAL check.
TAIL_WINDOW = 8

#: Statuses that count as a successful completion for gate purposes.
SUCCESS_STATUSES = frozenset({"completed", "complete", "success", "succeeded"})


@dataclass
class GateConfig:
    """Swappable allow-list config (Gap 1 finalization lands here)."""

    verify_tools: frozenset[str] = VERIFY_TOOLS
    verify_bash_re: re.Pattern[str] = VERIFY_BASH_RE
    tail_window: int = TAIL_WINDOW
    provisional: bool = True


DEFAULT_GATE_CONFIG = GateConfig()


@dataclass
class GateVerdict:
    gate: bool
    terminal_check: bool
    approval: bool
    completed: bool
    evidence: str
    corroborators: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "gate": self.gate,
            "terminal_check": self.terminal_check,
            "approval": self.approval,
            "completed": self.completed,
            "evidence": self.evidence,
            "corroborators": list(self.corroborators),
        }


def _tail_tools(rec: dict, window: int) -> list[str]:
    """The last `window` tool calls, preferring the full stream when present."""
    tools = rec.get("tool_all")
    if tools:
        return [str(t) for t in tools[-window:]]
    tail = rec.get("tool_tail") or []
    return [str(t) for t in tail[-window:]]


def detect(rec: dict, *, config: GateConfig = DEFAULT_GATE_CONFIG) -> GateVerdict:
    """Terminal-verification detection for one session record."""
    tail = _tail_tools(rec, config.tail_window)
    tool_hit = next((t for t in tail if t in config.verify_tools), None)

    bash_hit = None
    bash_cmds = rec.get("bash_cmds") or []
    if bash_cmds:
        joined = " ".join(str(c) for c in bash_cmds[-config.tail_window :])
        m = config.verify_bash_re.search(joined)
        bash_hit = m.group(0) if m else None

    approval = int(rec.get("n_approvals", 0) or 0) > 0
    status = str(rec.get("status") or "").lower()
    completed = status in SUCCESS_STATUSES

    terminal_check = bool(tool_hit or bash_hit)
    # A record produced by an older miner may not carry every observable this
    # detector needs (`ci_mine_v2` records keep only the last-8 tool tail and
    # no `bash_cmds`, so the bounded bash-verb assist cannot be re-run). In
    # that case a locally-computed False is a PARTIAL answer, not a negative
    # result -- recomputing from half the inputs under-counts terminal checks
    # by roughly 13pp against the measured prevalence. So when the record
    # cannot support a full recomputation, its own precomputed observation is
    # honoured. Absence of an input is never treated as evidence of absence.
    inputs_complete = "tool_all" in rec and "bash_cmds" in rec
    if not terminal_check and not inputs_complete and "terminal_check" in rec:
        terminal_check = bool(rec["terminal_check"])
        if terminal_check:
            tool_hit = bash_hit = None

    corroborators: list[str] = []
    if int(rec.get("gate_markers", 0) or 0) > 0:
        corroborators.append("gate vocabulary in prompt text")

    if tool_hit:
        evidence = f"verify-class tool {tool_hit!r} within last {config.tail_window} calls"
    elif bash_hit:
        evidence = f"verify bash verb {bash_hit!r} in terminal commands"
    elif approval:
        evidence = "recipe:approval gate"
    elif terminal_check:
        evidence = "inherited terminal_check flag (no tool stream in record)"
    else:
        evidence = "no terminal verification before completion"

    return GateVerdict(
        gate=bool(terminal_check or approval),
        terminal_check=terminal_check,
        approval=approval,
        completed=completed,
        evidence=evidence,
        corroborators=corroborators,
    )


def cluster_gate(members: list[dict], *, config: GateConfig = DEFAULT_GATE_CONFIG) -> dict:
    """Cluster-level 4b.

    A cluster is gated if ANY member shows a real terminal verification.
    Rationale: a gate is a property of the WORK's exit condition, and one
    member demonstrating a machine-checkable exit proves the unit admits
    one — whereas requiring a majority would punish units whose tail window
    happened to miss the final check in most sessions. This is deliberately
    the more generous of the two readings and is stated so it can be
    argued with.
    """
    verdicts = [detect(m, config=config) for m in members]
    n = len(verdicts) or 1
    n_gate = sum(1 for v in verdicts if v.gate)
    return {
        "gate": n_gate > 0,
        "gate_share": n_gate / n,
        "n_members": len(verdicts),
        "n_gate": n_gate,
        "provisional_allowlist": config.provisional,
    }


def terminal_check_prevalence(records: list[dict], *, config: GateConfig = DEFAULT_GATE_CONFIG) -> dict:
    """Corpus-wide terminal-check prevalence (the Gap-1 measurement arm)."""
    total = len(records) or 1
    hits = 0
    big_total = 0
    big_hits = 0
    for rec in records:
        gate = detect(rec, config=config)
        if gate.terminal_check:
            hits += 1
        if int(rec.get("n_tool_calls", 0) or 0) >= 6:
            big_total += 1
            if gate.terminal_check:
                big_hits += 1
    return {
        "n_sessions": len(records),
        "terminal_check": hits,
        "prevalence_all": hits / total,
        "n_sessions_ge6_tools": big_total,
        "terminal_check_ge6": big_hits,
        "prevalence_ge6_tools": (big_hits / big_total) if big_total else 0.0,
    }
