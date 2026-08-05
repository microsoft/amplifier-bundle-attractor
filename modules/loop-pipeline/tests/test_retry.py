"""Tests for retry logic with configurable policy.

Spec coverage: RETRY-001–011, FAIL-001, Section 3.5–3.6.
"""

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.graph import Graph, Node
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus
from amplifier_module_loop_pipeline.retry import (
    BackoffConfig,
    RetryPolicy,
    execute_with_retry,
)


class MockHandler:
    """Handler that returns outcomes from a pre-set sequence."""

    def __init__(self, outcomes: list[Outcome]):
        self._outcomes = list(outcomes)
        self.call_count = 0

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine=None,
    ) -> Outcome:
        self.call_count += 1
        if self._outcomes:
            return self._outcomes.pop(0)
        return Outcome(status=StageStatus.FAIL, failure_reason="no more outcomes")


class RaisingHandler:
    """Handler that raises a retryable exception on the first N calls.

    Uses TimeoutError (retryable per M-18) to test transient failure retry.
    """

    def __init__(self, fail_count: int, then: Outcome):
        self._fail_count = fail_count
        self._then = then
        self.call_count = 0

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
        *,
        engine=None,
    ) -> Outcome:
        self.call_count += 1
        if self.call_count <= self._fail_count:
            raise TimeoutError(f"Transient error #{self.call_count}")
        return self._then


def _make_node(**kwargs) -> Node:
    defaults = {"id": "step", "prompt": "Do work"}
    defaults.update(kwargs)
    return Node(**defaults)


def _make_graph() -> Graph:
    return Graph(
        name="test",
        nodes={"start": Node(id="start", shape="Mdiamond")},
        edges=[],
    )


# --- Basic retry behavior ---


@pytest.mark.asyncio
async def test_success_on_first_try():
    """No retries needed when first attempt succeeds."""
    handler = MockHandler([Outcome(status=StageStatus.SUCCESS)])
    policy = RetryPolicy(max_attempts=3)
    result = await execute_with_retry(
        handler, _make_node(), PipelineContext(), _make_graph(), "/tmp", policy
    )
    assert result.status == StageStatus.SUCCESS
    assert handler.call_count == 1


@pytest.mark.asyncio
async def test_retry_on_retry_outcome():
    """RETRY outcome triggers additional attempts."""
    handler = MockHandler(
        [
            Outcome(status=StageStatus.RETRY),
            Outcome(status=StageStatus.RETRY),
            Outcome(status=StageStatus.SUCCESS),
        ]
    )
    policy = RetryPolicy(max_attempts=3, backoff=BackoffConfig(initial_delay_ms=0))
    result = await execute_with_retry(
        handler, _make_node(), PipelineContext(), _make_graph(), "/tmp", policy
    )
    assert result.status == StageStatus.SUCCESS
    assert handler.call_count == 3
    # support#379: Outcome.attempt_count must reflect the real attempt that
    # finally succeeded (3rd try), not a hardcoded 1.
    assert result.attempt_count == 3


@pytest.mark.asyncio
async def test_fail_not_retried():
    """FAIL outcome returns immediately — no retries (RETRY-006)."""
    handler = MockHandler(
        [Outcome(status=StageStatus.FAIL, failure_reason="bad input")]
    )
    policy = RetryPolicy(max_attempts=3, backoff=BackoffConfig(initial_delay_ms=0))
    result = await execute_with_retry(
        handler, _make_node(), PipelineContext(), _make_graph(), "/tmp", policy
    )
    assert result.status == StageStatus.FAIL
    assert handler.call_count == 1
    assert result.failure_reason == "bad input"


@pytest.mark.asyncio
async def test_success_not_retried():
    """SUCCESS outcome returns immediately."""
    handler = MockHandler([Outcome(status=StageStatus.SUCCESS)])
    policy = RetryPolicy(max_attempts=5, backoff=BackoffConfig(initial_delay_ms=0))
    result = await execute_with_retry(
        handler, _make_node(), PipelineContext(), _make_graph(), "/tmp", policy
    )
    assert result.status == StageStatus.SUCCESS
    assert handler.call_count == 1


@pytest.mark.asyncio
async def test_partial_success_not_retried():
    """PARTIAL_SUCCESS returns immediately."""
    handler = MockHandler([Outcome(status=StageStatus.PARTIAL_SUCCESS)])
    policy = RetryPolicy(max_attempts=3, backoff=BackoffConfig(initial_delay_ms=0))
    result = await execute_with_retry(
        handler, _make_node(), PipelineContext(), _make_graph(), "/tmp", policy
    )
    assert result.status == StageStatus.PARTIAL_SUCCESS
    assert handler.call_count == 1


# --- Retry exhaustion ---


@pytest.mark.asyncio
async def test_retries_exhausted_returns_fail():
    """All retries exhausted returns FAIL (RETRY-005)."""
    handler = MockHandler([Outcome(status=StageStatus.RETRY)] * 3)
    policy = RetryPolicy(max_attempts=3, backoff=BackoffConfig(initial_delay_ms=0))
    result = await execute_with_retry(
        handler, _make_node(), PipelineContext(), _make_graph(), "/tmp", policy
    )
    assert result.status == StageStatus.FAIL
    assert "max retries exceeded" in (result.failure_reason or "").lower()
    assert handler.call_count == 3


@pytest.mark.asyncio
async def test_allow_partial_on_exhaustion():
    """allow_partial=true -> PARTIAL_SUCCESS after retries exhausted (RETRY-005)."""
    handler = MockHandler([Outcome(status=StageStatus.RETRY)] * 3)
    policy = RetryPolicy(max_attempts=3, backoff=BackoffConfig(initial_delay_ms=0))
    node = _make_node(attrs={"allow_partial": True})
    result = await execute_with_retry(
        handler, node, PipelineContext(), _make_graph(), "/tmp", policy
    )
    assert result.status == StageStatus.PARTIAL_SUCCESS
    assert handler.call_count == 3


# --- Default behavior (max_retries=0) ---


@pytest.mark.asyncio
async def test_default_no_retries():
    """Default max_retries=0 means max_attempts=1 — no retries."""
    handler = MockHandler([Outcome(status=StageStatus.RETRY)])
    policy = RetryPolicy(max_attempts=1, backoff=BackoffConfig(initial_delay_ms=0))
    result = await execute_with_retry(
        handler, _make_node(), PipelineContext(), _make_graph(), "/tmp", policy
    )
    assert result.status == StageStatus.FAIL
    assert handler.call_count == 1


# --- Exception handling ---


@pytest.mark.asyncio
async def test_exception_retried():
    """Exceptions are caught and retried (transient failures)."""
    handler = RaisingHandler(
        fail_count=2,
        then=Outcome(status=StageStatus.SUCCESS),
    )
    policy = RetryPolicy(max_attempts=3, backoff=BackoffConfig(initial_delay_ms=0))
    result = await execute_with_retry(
        handler, _make_node(), PipelineContext(), _make_graph(), "/tmp", policy
    )
    assert result.status == StageStatus.SUCCESS
    assert handler.call_count == 3
    # support#379: real attempt count threaded through the exception-retry path too.
    assert result.attempt_count == 3


@pytest.mark.asyncio
async def test_exception_exhausted_returns_fail():
    """All retries exhausted by exceptions returns FAIL."""
    handler = RaisingHandler(
        fail_count=5,
        then=Outcome(status=StageStatus.SUCCESS),
    )
    policy = RetryPolicy(max_attempts=3, backoff=BackoffConfig(initial_delay_ms=0))
    result = await execute_with_retry(
        handler, _make_node(), PipelineContext(), _make_graph(), "/tmp", policy
    )
    assert result.status == StageStatus.FAIL
    assert "Transient error" in (result.failure_reason or "")
    assert handler.call_count == 3


# --- RetryPolicy construction ---


def test_retry_policy_from_node_max_retries():
    """Build policy from node's max_retries attribute."""
    node = _make_node(attrs={"max_retries": 3})
    graph = _make_graph()
    policy = RetryPolicy.from_node(node, graph)
    # max_retries=3 means max_attempts=4 (1 initial + 3 retries)
    assert policy.max_attempts == 4


@pytest.mark.parametrize("value", [-1, "-1", True, 1.5, "1.5", "invalid"])
def test_retry_policy_rejects_the_same_invalid_values_as_validation(value):
    """Runtime retry coercion cannot reintroduce values structural validation rejects."""
    node = _make_node()
    node.max_retries = value

    with pytest.raises(ValueError, match="non-negative integer"):
        RetryPolicy.from_node(node, _make_graph())


def test_retry_policy_from_graph_default():
    """Build policy from graph default_max_retry when node has none."""
    node = _make_node(attrs={})
    graph = Graph(
        name="test",
        nodes={"start": Node(id="start", shape="Mdiamond")},
        edges=[],
        default_max_retry=2,
    )
    policy = RetryPolicy.from_node(node, graph)
    # graph default_max_retry=2 -> max_attempts=3
    assert policy.max_attempts == 3


def test_retry_policy_default_zero():
    """When no max_retries anywhere, default is 0 retries (1 attempt)."""
    node = _make_node(attrs={})
    graph = Graph(
        name="test",
        nodes={"start": Node(id="start", shape="Mdiamond")},
        edges=[],
        default_max_retry=0,
    )
    policy = RetryPolicy.from_node(node, graph)
    assert policy.max_attempts == 1


# --- BackoffConfig ---


def test_backoff_delay_exponential():
    """Exponential backoff: delay doubles each attempt."""
    config = BackoffConfig(initial_delay_ms=200, backoff_factor=2.0, jitter=False)
    assert config.delay_for_attempt(1) == 200.0
    assert config.delay_for_attempt(2) == 400.0
    assert config.delay_for_attempt(3) == 800.0


def test_backoff_delay_capped():
    """Delay is capped at max_delay_ms."""
    config = BackoffConfig(
        initial_delay_ms=1000, backoff_factor=10.0, max_delay_ms=5000, jitter=False
    )
    assert config.delay_for_attempt(1) == 1000.0
    assert config.delay_for_attempt(2) == 5000.0  # capped
    assert config.delay_for_attempt(3) == 5000.0  # still capped


def test_backoff_linear():
    """Linear backoff (factor=1.0) has fixed delay."""
    config = BackoffConfig(initial_delay_ms=500, backoff_factor=1.0, jitter=False)
    assert config.delay_for_attempt(1) == 500.0
    assert config.delay_for_attempt(2) == 500.0
    assert config.delay_for_attempt(3) == 500.0


def test_backoff_with_jitter():
    """Jitter adds randomness within [0.5, 1.5] of the delay."""
    config = BackoffConfig(initial_delay_ms=1000, backoff_factor=1.0, jitter=True)
    delays = [config.delay_for_attempt(1) for _ in range(100)]
    # All delays should be between 500 and 1500
    assert all(500.0 <= d <= 1500.0 for d in delays)
    # Not all the same (very unlikely with 100 samples)
    assert len(set(delays)) > 1


@pytest.mark.asyncio
async def test_retry_emits_retrying_event():
    """execute_with_retry must emit StageRetrying events on retry."""
    from amplifier_module_loop_pipeline.pipeline_events import (
        PIPELINE_STAGE_RETRYING,
    )

    emitted: list[tuple[str, dict]] = []

    class MockHooks:
        async def emit(self, event_name, data):
            emitted.append((event_name, data))

    call_count = 0

    class RetryThenSucceedHandler:
        async def execute(self, node, context, graph, logs_root, *, engine=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return Outcome(status=StageStatus.RETRY, failure_reason="not yet")
            return Outcome(status=StageStatus.SUCCESS)

    node = Node(id="work", shape="box", prompt="do work")
    graph = Graph(
        name="test",
        nodes={"work": node},
        edges=[],
    )
    ctx = PipelineContext()
    policy = RetryPolicy(max_attempts=3, backoff=BackoffConfig(initial_delay_ms=0))

    await execute_with_retry(
        RetryThenSucceedHandler(),
        node,
        ctx,
        graph,
        "/tmp/test",
        policy,
        hooks=MockHooks(),
    )

    event_names = [e[0] for e in emitted]
    assert PIPELINE_STAGE_RETRYING in event_names


@pytest.mark.asyncio
async def test_retry_emits_stage_failed_on_exhaustion():
    """execute_with_retry must emit StageFailed when retries exhausted."""
    from amplifier_module_loop_pipeline.pipeline_events import (
        PIPELINE_STAGE_FAILED,
    )

    emitted: list[tuple[str, dict]] = []

    class MockHooks:
        async def emit(self, event_name, data):
            emitted.append((event_name, data))

    handler = MockHandler([Outcome(status=StageStatus.RETRY)] * 3)
    node = Node(id="work", shape="box", prompt="do work")
    graph = Graph(
        name="test",
        nodes={"work": node},
        edges=[],
    )
    ctx = PipelineContext()
    policy = RetryPolicy(max_attempts=3, backoff=BackoffConfig(initial_delay_ms=0))

    await execute_with_retry(
        handler,
        node,
        ctx,
        graph,
        "/tmp/test",
        policy,
        hooks=MockHooks(),
    )

    event_names = [e[0] for e in emitted]
    assert PIPELINE_STAGE_FAILED in event_names


# --- M-18: Error classification ---


class TestShouldRetry:
    """M-18: should_retry classifies exceptions as retryable or terminal."""

    def test_timeout_error_is_retryable(self):
        from amplifier_module_loop_pipeline.retry import should_retry

        assert should_retry(TimeoutError("timed out")) is True

    def test_connection_error_is_retryable(self):
        from amplifier_module_loop_pipeline.retry import should_retry

        assert should_retry(ConnectionError("reset")) is True

    def test_oserror_is_retryable(self):
        from amplifier_module_loop_pipeline.retry import should_retry

        assert should_retry(OSError("network down")) is True

    def test_rate_limit_like_error_is_retryable(self):
        """Exceptions with 429 or 'rate limit' in message are retryable."""
        from amplifier_module_loop_pipeline.retry import should_retry

        assert should_retry(Exception("rate limit exceeded")) is True
        assert should_retry(Exception("HTTP 429 Too Many Requests")) is True

    def test_server_error_like_is_retryable(self):
        """Exceptions mentioning 5xx status codes are retryable."""
        from amplifier_module_loop_pipeline.retry import should_retry

        assert should_retry(Exception("HTTP 500 Internal Server Error")) is True
        assert should_retry(Exception("HTTP 502 Bad Gateway")) is True
        assert should_retry(Exception("HTTP 503 Service Unavailable")) is True

    def test_value_error_is_terminal(self):
        from amplifier_module_loop_pipeline.retry import should_retry

        assert should_retry(ValueError("bad input")) is False

    def test_type_error_is_terminal(self):
        from amplifier_module_loop_pipeline.retry import should_retry

        assert should_retry(TypeError("wrong type")) is False

    def test_key_error_is_terminal(self):
        from amplifier_module_loop_pipeline.retry import should_retry

        assert should_retry(KeyError("missing")) is False

    def test_auth_error_like_is_terminal(self):
        """Exceptions mentioning 401 or 403 are terminal."""
        from amplifier_module_loop_pipeline.retry import should_retry

        assert should_retry(Exception("HTTP 401 Unauthorized")) is False
        assert should_retry(Exception("HTTP 403 Forbidden")) is False

    def test_bad_request_is_terminal(self):
        """Exceptions mentioning 400 are terminal."""
        from amplifier_module_loop_pipeline.retry import should_retry

        assert should_retry(Exception("HTTP 400 Bad Request")) is False


@pytest.mark.asyncio
async def test_terminal_exception_not_retried():
    """M-18: Terminal exceptions (ValueError etc.) propagate immediately."""

    class TerminalThenSuccessHandler:
        def __init__(self):
            self.call_count = 0

        async def execute(self, node, context, graph, logs_root, *, engine=None):
            self.call_count += 1
            if self.call_count == 1:
                raise ValueError("invalid config")
            return Outcome(status=StageStatus.SUCCESS)

    handler = TerminalThenSuccessHandler()
    policy = RetryPolicy(max_attempts=3, backoff=BackoffConfig(initial_delay_ms=0))
    result = await execute_with_retry(
        handler, _make_node(), PipelineContext(), _make_graph(), "/tmp", policy
    )
    # ValueError is terminal — should NOT retry, should fail immediately
    assert result.status == StageStatus.FAIL
    assert handler.call_count == 1
    assert "invalid config" in (result.failure_reason or "")


@pytest.mark.asyncio
async def test_retryable_exception_is_retried():
    """M-18: Retryable exceptions (TimeoutError etc.) are retried."""

    class TimeoutThenSuccessHandler:
        def __init__(self):
            self.call_count = 0

        async def execute(self, node, context, graph, logs_root, *, engine=None):
            self.call_count += 1
            if self.call_count == 1:
                raise TimeoutError("connection timed out")
            return Outcome(status=StageStatus.SUCCESS)

    handler = TimeoutThenSuccessHandler()
    policy = RetryPolicy(max_attempts=3, backoff=BackoffConfig(initial_delay_ms=0))
    result = await execute_with_retry(
        handler, _make_node(), PipelineContext(), _make_graph(), "/tmp", policy
    )
    # TimeoutError is retryable — should retry and eventually succeed
    assert result.status == StageStatus.SUCCESS
    assert handler.call_count == 2


# --- Exhaustion telemetry truth (council amendment) ---


class _EventCollector:
    """Minimal hooks stand-in: collects emitted (event, payload) pairs."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def emit(self, event: str, payload: dict) -> None:
        self.events.append((event, payload))


def _failed_events(collector: _EventCollector) -> list[dict]:
    from amplifier_module_loop_pipeline.pipeline_events import (
        PIPELINE_STAGE_FAILED,
    )

    return [p for (e, p) in collector.events if e == PIPELINE_STAGE_FAILED]


@pytest.mark.asyncio
async def test_exhaustion_event_matches_return_for_string_false():
    """allow_partial="false" (string, as DOT attrs are) must NOT emit a
    partial_success failure event while returning FAIL.

    Regression: the event once used raw truthiness of the attr while the
    return path used a strict check — event and return disagreed.
    """
    handler = MockHandler(
        [Outcome(status=StageStatus.RETRY), Outcome(status=StageStatus.RETRY)]
    )
    policy = RetryPolicy(max_attempts=2, backoff=BackoffConfig(initial_delay_ms=0))
    collector = _EventCollector()
    result = await execute_with_retry(
        handler,
        _make_node(attrs={"allow_partial": "false"}),
        PipelineContext(),
        _make_graph(),
        "/tmp",
        policy,
        hooks=collector,
    )
    assert result.status == StageStatus.FAIL
    failed = _failed_events(collector)
    assert len(failed) == 1
    assert failed[0]["final_status"] == "fail", (
        "Event must match the returned outcome: allow_partial='false' returns "
        f"FAIL, but the event claimed {failed[0]['final_status']!r}."
    )


@pytest.mark.asyncio
async def test_exhaustion_event_partial_success_when_partial_returned():
    """allow_partial="true": event says partial_success AND partial is returned."""
    handler = MockHandler(
        [Outcome(status=StageStatus.RETRY), Outcome(status=StageStatus.RETRY)]
    )
    policy = RetryPolicy(max_attempts=2, backoff=BackoffConfig(initial_delay_ms=0))
    collector = _EventCollector()
    result = await execute_with_retry(
        handler,
        _make_node(attrs={"allow_partial": "true"}),
        PipelineContext(),
        _make_graph(),
        "/tmp",
        policy,
        hooks=collector,
    )
    assert result.status == StageStatus.PARTIAL_SUCCESS
    failed = _failed_events(collector)
    assert len(failed) == 1
    assert failed[0]["final_status"] == "partial_success"


@pytest.mark.asyncio
async def test_exhaustion_event_fail_when_must_write_vetoes_partial(tmp_path):
    """allow_partial="true" + must_write= violated at exhaustion: the
    manufactured partial is vetoed by the artifact contract (EXTENSIONS §27)
    and the failure event reports the actual final status: fail.
    """
    artifact = tmp_path / "never_written.md"
    handler = MockHandler(
        [Outcome(status=StageStatus.RETRY), Outcome(status=StageStatus.RETRY)]
    )
    policy = RetryPolicy(max_attempts=2, backoff=BackoffConfig(initial_delay_ms=0))
    collector = _EventCollector()
    result = await execute_with_retry(
        handler,
        _make_node(attrs={"allow_partial": "true", "must_write": str(artifact)}),
        PipelineContext(),
        _make_graph(),
        "/tmp",
        policy,
        hooks=collector,
    )
    assert result.status == StageStatus.FAIL
    assert "must_write" in (result.failure_reason or "")
    failed = _failed_events(collector)
    assert len(failed) == 1
    assert failed[0]["final_status"] == "fail", (
        "must_write vetoed the manufactured partial; the event must say fail, "
        f"got {failed[0]['final_status']!r}."
    )
