"""Retry logic with configurable policy and exponential backoff.

Node-level retry with configurable max_attempts, backoff strategy,
and allow_partial semantics. Used by the pipeline engine to wrap
handler execution.

Spec coverage: RETRY-001–011, FAIL-001, Section 3.5–3.6.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass, field
from typing import Any

from .context import PipelineContext
from .graph import Graph, Node
from .outcome import Outcome, StageStatus

logger = logging.getLogger(__name__)

# M-18: Exception types that are inherently retryable (transient failures)
_RETRYABLE_TYPES: tuple[type[BaseException], ...] = (
    TimeoutError,
    ConnectionError,
    OSError,
)

# M-18: HTTP status codes that are retryable
_RETRYABLE_HTTP_CODES = re.compile(r"\b(429|5\d{2})\b")

# M-18: HTTP status codes that are terminal
_TERMINAL_HTTP_CODES = re.compile(r"\b(400|401|403|404|405|422)\b")

# M-18: Keywords in exception messages that indicate retryable errors
_RETRYABLE_KEYWORDS = re.compile(
    r"rate.?limit|throttl|too many requests", re.IGNORECASE
)


def should_retry(exc: BaseException) -> bool:
    """Classify an exception as retryable or terminal (M-18).

    Retryable: TimeoutError, ConnectionError, OSError, rate-limit errors,
    HTTP 429/5xx errors.

    Terminal: ValueError, TypeError, KeyError, HTTP 400/401/403/404 errors,
    and anything else not classified as retryable.

    Spec Section 3.5: error classification.
    """
    # Check exception type first
    if isinstance(exc, _RETRYABLE_TYPES):
        return True

    # Check message for HTTP status codes and keywords
    msg = str(exc)

    # Terminal HTTP codes take precedence
    if _TERMINAL_HTTP_CODES.search(msg):
        return False

    # Retryable HTTP codes
    if _RETRYABLE_HTTP_CODES.search(msg):
        return True

    # Retryable keywords
    if _RETRYABLE_KEYWORDS.search(msg):
        return True

    # Default: terminal (don't retry unknown errors)
    return False


@dataclass
class BackoffConfig:
    """Configuration for retry delay calculation.

    Spec Section 3.6: BackoffConfig.
    """

    initial_delay_ms: float = 200.0
    backoff_factor: float = 2.0
    max_delay_ms: float = 60000.0
    jitter: bool = True

    def delay_for_attempt(self, attempt: int) -> float:
        """Calculate delay in milliseconds for a given attempt (1-indexed).

        Spec Section 3.6: delay_for_attempt algorithm.
        """
        delay = self.initial_delay_ms * (self.backoff_factor ** (attempt - 1))
        delay = min(delay, self.max_delay_ms)
        if self.jitter:
            delay = delay * random.uniform(0.5, 1.5)
        return delay


@dataclass
class RetryPolicy:
    """Retry policy for node execution.

    Spec Section 3.6: RetryPolicy.

    max_attempts is 1-indexed: 1 means no retries (just the initial try),
    3 means 1 initial + 2 retries.
    """

    max_attempts: int = 1
    backoff: BackoffConfig = field(default_factory=BackoffConfig)

    @classmethod
    def from_preset(cls, name: str) -> RetryPolicy:
        """Create a RetryPolicy from a named preset (L-15).

        Presets (spec §3.5 table, attractor-spec.md:554-560):
            none       — 1 attempt (no retries).
            standard   — 5 attempts, 200ms initial delay, factor 2.0.
            aggressive — 5 attempts, 500ms initial delay, factor 2.0.
            linear     — 3 attempts, 500ms initial delay, factor 1.0 (fixed).
            patient    — 3 attempts, 2000ms initial delay, factor 3.0.

        Raises ValueError for unknown preset names.
        """
        presets: dict[str, RetryPolicy] = {
            "none": cls(max_attempts=1),
            # Spec §3.5 preset table (attractor-spec.md:554-560)
            "standard": cls(max_attempts=5),  # delays 200,400,800,1600,3200 ms
            "aggressive": cls(
                max_attempts=5,
                backoff=BackoffConfig(initial_delay_ms=500),  # delays 500,1000,2000,4000,8000 ms
            ),
            "linear": cls(
                max_attempts=3,
                backoff=BackoffConfig(initial_delay_ms=500, backoff_factor=1.0),  # fixed 500ms
            ),
            "patient": cls(
                max_attempts=3,
                backoff=BackoffConfig(initial_delay_ms=2000, backoff_factor=3.0),  # delays 2000,6000,18000 ms
            ),
        }
        if name not in presets:
            raise ValueError(
                f"Unknown retry preset '{name}'. "
                f"Valid presets: {', '.join(sorted(presets))}"
            )
        return presets[name]

    @classmethod
    def from_node(cls, node: Node, graph: Graph) -> RetryPolicy:
        """Build a RetryPolicy from node and graph attributes.

        Resolution order:
        1. Node attribute ``max_retries`` (additional attempts beyond initial)
        2. Graph attribute ``default_max_retry`` (fallback)
        3. Built-in default: 0 (no retries)

        max_retries=N means max_attempts = N + 1.

        Spec Section 3.5.
        """
        max_retries = node.attrs.get("max_retries")
        if max_retries is None:
            max_retries = graph.default_max_retry
        if max_retries is None:
            max_retries = 0

        max_retries = int(max_retries)
        return cls(max_attempts=max_retries + 1)


async def execute_with_retry(
    handler: Any,
    node: Node,
    context: PipelineContext,
    graph: Graph,
    logs_root: str,
    policy: RetryPolicy,
    hooks: Any = None,
    engine: Any = None,
) -> Outcome:
    """Execute a handler with retry policy.

    Retries on RETRY outcomes and exceptions. Returns immediately on
    SUCCESS, PARTIAL_SUCCESS, FAIL, and SKIPPED.

    Spec Section 3.5: execute_with_retry algorithm.
    """
    last_outcome: Outcome | None = None

    for attempt in range(1, policy.max_attempts + 1):
        # Execute the handler
        try:
            outcome = await handler.execute(
                node, context, graph, logs_root, engine=engine
            )
        except Exception as e:
            logger.warning(
                "Node %s attempt %d/%d raised: %s",
                node.id,
                attempt,
                policy.max_attempts,
                e,
            )
            # M-18: Classify exception — terminal errors fail immediately
            if not should_retry(e):
                logger.info(
                    "Node %s: terminal error (not retryable): %s",
                    node.id,
                    type(e).__name__,
                )
                return Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=str(e),
                )
            if attempt < policy.max_attempts:
                await _sleep_backoff(policy.backoff, attempt)
                continue
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=str(e),
            )

        # SUCCESS or PARTIAL_SUCCESS — return immediately
        if outcome.status in (StageStatus.SUCCESS, StageStatus.PARTIAL_SUCCESS):
            return outcome

        # FAIL — return immediately, no retries
        if outcome.status == StageStatus.FAIL:
            return outcome

        # SKIPPED — return immediately; the engine's auto_status check may
        # promote this to SUCCESS when auto_status=true (spec §2.6 / Appendix C).
        # Do not retry a SKIPPED outcome — retrying would not produce a status.
        if outcome.status == StageStatus.SKIPPED:
            return outcome

        # RETRY — retry if attempts remain
        last_outcome = outcome
        if attempt < policy.max_attempts:
            logger.info(
                "Node %s attempt %d/%d returned RETRY, retrying...",
                node.id,
                attempt,
                policy.max_attempts,
            )
            if hooks is not None:
                from .pipeline_events import PIPELINE_STAGE_RETRYING

                await hooks.emit(
                    PIPELINE_STAGE_RETRYING,
                    {
                        "node_id": node.id,
                        "attempt": attempt,
                        "max_attempts": policy.max_attempts,
                        "delay_ms": policy.backoff.delay_for_attempt(attempt),
                    },
                )
            await _sleep_backoff(policy.backoff, attempt)
            continue

    # All retries exhausted
    if hooks is not None:
        from .pipeline_events import PIPELINE_STAGE_FAILED

        await hooks.emit(
            PIPELINE_STAGE_FAILED,
            {
                "node_id": node.id,
                "attempts": policy.max_attempts,
                "final_status": (
                    "partial_success" if node.attrs.get("allow_partial") else "fail"
                ),
            },
        )

    _ap = node.attrs.get("allow_partial")
    if _ap is True or str(_ap).lower() == "true":
        return Outcome(
            status=StageStatus.PARTIAL_SUCCESS,
            notes="Retries exhausted, partial accepted",
            failure_reason=last_outcome.failure_reason if last_outcome else None,
        )

    return Outcome(
        status=StageStatus.FAIL,
        failure_reason="Max retries exceeded",
    )


async def _sleep_backoff(backoff: BackoffConfig, attempt: int) -> None:
    """Sleep for the backoff delay (in seconds)."""
    delay_ms = backoff.delay_for_attempt(attempt)
    if delay_ms > 0:
        await asyncio.sleep(delay_ms / 1000.0)
