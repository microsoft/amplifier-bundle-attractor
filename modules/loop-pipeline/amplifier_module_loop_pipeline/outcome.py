"""Outcome model for Attractor pipeline node execution.

Defines StageStatus enum and Outcome dataclass that drive routing
decisions and state updates after each node handler completes.

Spec coverage: OUT-001–007, Section 5.2 (Outcome)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class StageStatus(Enum):
    """Status values for node execution outcomes.

    Spec Section 5.2: StageStatus values table.
    """

    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    RETRY = "retry"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass
class Outcome:
    """Result of executing a node handler.

    Drives edge selection (via preferred_label, suggested_next_ids),
    context updates, and retry/failure routing.

    Spec Section 5.2: Outcome model.
    """

    status: StageStatus
    preferred_label: str | None = None
    suggested_next_ids: list[str] | None = None
    context_updates: dict[str, Any] | None = None
    notes: str | None = None
    failure_reason: str | None = None
    session_id: str | None = (
        None  # child Amplifier session ID (if executed via AmplifierBackend)
    )
    #: Issue 10 / analog of WS-4 Sub-fix C: structured tool-invocation payload
    #: populated by ToolHandler on failure so the dashboard can display the
    #: command and output instead of the "command lost on failure" placeholder.
    #:
    #: Shape:
    #:   command    — resolved shell command (capped at 500 chars)
    #:   exit_code  — subprocess return code
    #:   duration_s — wall-clock seconds from process start to communicate()
    #:   stdout_tail — last ≤2 KiB of stdout; empty string, NOT None
    #:   stderr_tail — last ≤2 KiB of stderr; empty string, NOT None
    #:
    #: Optional key added by the 8 KiB truncation pass:
    #:   verification_gap.log_filtered — True when payload was truncated
    failed_step: dict[str, Any] | None = None

    @property
    def is_success(self) -> bool:
        """True if status is SUCCESS or PARTIAL_SUCCESS."""
        return self.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS)
