"""Unit tests for the must_write= node attribute (EXTENSIONS.md §27).

Adversarial battery covering:
  1. Narration-ending no-write       -> node FAIL
  2. Empty-final-message completion  -> node FAIL
  3. PLANTED content-bearing file    -> node FAIL (freshness floor)
  4. DELAYED-REPLANT (informational) -> FAIL under session attribution;
     under mtime-floor-only: informational PASS (documented residual)
  5. Write-first skeleton VERDICT: PENDING -> node PASSES
  6. No-attribute node, no-write     -> still SUCCEEDS (backward compat)

Plus:
  - Relative-path resolution (context.target_dir anchor)
  - Whitespace-only artifact is non-trivial-fail
  - Empty artifact is non-trivial-fail
  - Handler already-FAIL is not double-wrapped
  - Retry semantics: violations consume max_retries in-place
    (1 + max_retries handler calls), retry-then-write succeeds,
    allow_partial and continue_on_fail cannot soften the FAIL
"""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import time
from pathlib import Path

from amplifier_module_loop_pipeline.backend import _parse_outcome
from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NARRATION = (
    "Now let me do a final comprehensive check of the report before "
    "writing it out. The analysis looks complete and well-structured."
)


def _outcome_from(text: str) -> Outcome:
    try:
        return _parse_outcome(text)
    except Exception:
        return _parse_outcome(" ")


class NoWriteBackend:
    """Live shape 1: session ends on plain-text narration, no artifact."""

    def __init__(self):
        self.calls = 0

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls += 1
        return _outcome_from(NARRATION)


class EmptyFinalBackend:
    """Live shape 2 (observed live): the session ends with an EMPTY final message."""

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return _outcome_from("")


class WriterBackend:
    """The honest maker: a fresh, content-bearing write by this execution."""

    def __init__(self, path: Path, content: str):
        self.path = path
        self.content = content

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        Path(self.path).write_text(self.content)
        return _outcome_from("Report written as required.")


class ReplantBackend:
    """DELAYED-REPLANT: content-bearing write by external process after node
    start; the node itself never writes."""

    def __init__(self, path: Path):
        self.path = path

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        subprocess.run(
            ["sh", "-c", f"printf 'replanted by external process\\n' > '{self.path}'"],
            check=True,
        )
        return _outcome_from(NARRATION)


class AlreadyFailBackend:
    """Backend that returns an explicit FAIL outcome (must_write should not
    double-wrap it)."""

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return Outcome(
            status=StageStatus.FAIL,
            failure_reason="handler-level failure: upstream data missing",
        )


def _dot_for(artifact: str, declare: bool = True) -> str:
    attr = f'must_write="{artifact}", ' if declare else ""
    return f"""
digraph MustWriteTest {{
    graph [goal="must_write fixture"]
    start [shape=Mdiamond]
    done  [shape=Msquare]
    maker [shape=box, {attr}max_retries=0,
           prompt="Produce the declared artifact."]
    start -> maker
    maker -> done
}}
"""


def _run(
    backend,
    artifact: Path,
    declare: bool = True,
    plant: str | None = None,
    context: PipelineContext | None = None,
) -> Outcome:
    if plant is not None:
        artifact.write_text(plant)
    graph = parse_dot(_dot_for(str(artifact), declare))
    logs = tempfile.mkdtemp(prefix="must-write-unit-logs-")
    engine = PipelineEngine(
        graph=graph,
        context=context or PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=logs,
    )
    return asyncio.run(engine.run())


# ---------------------------------------------------------------------------
# Battery cases
# ---------------------------------------------------------------------------


def test_case1_narration_no_write_fails(tmp_path):
    """Case 1: session ends on narration without writing the artifact -> FAIL."""
    a = tmp_path / "case1.md"
    outcome = _run(NoWriteBackend(), a)
    assert outcome.status == StageStatus.FAIL, (
        "must_write node that completes on narration without its artifact must FAIL "
        "(the narration-ending critic shape observed live; silent success is the incident class under repair)"
    )
    assert (
        "must_write" in (outcome.failure_reason or "").lower()
        or "must_write" in (outcome.notes or "").lower()
    ), "failure_reason or notes should mention must_write"


def test_case2_empty_final_message_fails(tmp_path):
    """Case 2: empty final message must not satisfy an artifact contract -> FAIL."""
    a = tmp_path / "case2.md"
    outcome = _run(EmptyFinalBackend(), a)
    assert outcome.status == StageStatus.FAIL, (
        "empty final message must not satisfy must_write contract "
        "(5th live instance: 'Child session completed with empty final message' accepted as success)"
    )


def test_case3_planted_file_fails(tmp_path):
    """Case 3: pre-existing artifact (planted before node start) -> FAIL (freshness floor)."""
    a = tmp_path / "case3.md"
    outcome = _run(
        NoWriteBackend(),
        a,
        plant="planted before node start\nVERDICT: SHIP\n",
    )
    assert outcome.status == StageStatus.FAIL, (
        "a pre-existing artifact must NOT pass: presence alone is the hole the contract closes "
        "(freshness floor: mtime after node start is REQUIRED)"
    )
    assert (
        "freshness" in (outcome.failure_reason or "").lower()
        or "predates" in (outcome.failure_reason or "").lower()
        or "planted" in (outcome.failure_reason or "").lower()
        or "mtime" in (outcome.failure_reason or "").lower()
    ), "failure_reason should mention freshness/mtime for planted-file case"


def test_case3b_equality_boundary_fails(tmp_path):
    """Case 3b: artifact mtime set to exactly node_start_wall -> FAIL.

    The freshness floor uses strictly-greater-than (mtime > node_start_wall).
    An artifact whose mtime equals node_start_wall must be rejected — the
    equality boundary is an adversarial bypass vector (set via os.utime or
    a coarse-resolution filesystem).

    This pins the boundary so a future ``<`` -> ``<=`` regression is caught
    immediately.
    """
    import os

    a = tmp_path / "case3b.md"

    # Capture the wall-clock time we'll use as the fake node_start_wall.
    fake_start = time.time()

    # Write a content-bearing artifact and force its mtime to exactly fake_start.
    a.write_text("content planted at exact start time\n")
    os.utime(a, (fake_start, fake_start))

    # Call _check_must_write directly with the equality-boundary start time.
    from amplifier_module_loop_pipeline.graph import Node
    from amplifier_module_loop_pipeline.outcome import Outcome, StageStatus

    node = Node(id="maker", attrs={"must_write": str(a)})
    success_outcome = Outcome(status=StageStatus.SUCCESS)

    # We need a minimal engine instance to call _check_must_write.
    import tempfile
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.engine import PipelineEngine
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.handlers import HandlerRegistry
    from amplifier_module_loop_pipeline.handlers.context import HandlerContext
    from amplifier_module_loop_pipeline.backend import _parse_outcome

    dot = f"""
digraph EqualityTest {{
    graph [goal="equality boundary test"]
    start [shape=Mdiamond]
    done  [shape=Msquare]
    maker [shape=box, must_write="{a}", max_retries=0, prompt="test"]
    start -> maker
    maker -> done
}}
"""
    graph = parse_dot(dot)
    logs = tempfile.mkdtemp(prefix="must-write-eq-logs-")

    class NoOpBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            return _parse_outcome("done")

    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=NoOpBackend())),
        logs_root=logs,
    )

    # Call _check_must_write directly with the equality-boundary timestamp.
    result = engine._check_must_write(node, success_outcome, fake_start)

    assert result is not None and result.status == StageStatus.FAIL, (
        "artifact mtime == node_start_wall must FAIL the freshness floor "
        "(equality is an adversarial bypass vector; strictly-greater-than is required). "
        f"Got: {result}"
    )
    # The failure reason should mention the mtime boundary
    reason = (result.failure_reason or "") + (result.notes or "")
    assert any(
        kw in reason.lower() for kw in ("mtime", "freshness", "planted", "node_start")
    ), f"failure_reason/notes should mention the freshness violation; got: {reason!r}"


def test_case4_delayed_replant_informational(tmp_path):
    """Case 4: delayed-replant is informational.

    Under mtime-floor-only: the external write happens after node_start_wall,
    so mtime >= start — this is the documented accepted residual.  Under
    session attribution (future) it would FAIL.  We do not gate on this case.
    """
    a = tmp_path / "case4.md"
    outcome = _run(ReplantBackend(a), a)
    # Informational: either result is acceptable; just print the result
    # so the test log captures the residual honestly.
    print(
        f"case4 delayed-replant: {'PASS (mtime-floor residual)' if outcome.is_success else 'FAIL (session attribution active)'}"
    )


def test_case5_write_first_skeleton_passes(tmp_path):
    """Case 5: fresh, authored, non-trivial skeleton -> PASSES must_write.

    Presence and quality are separate contracts — a VERDICT: PENDING skeleton
    satisfies every must_write= axis but carries no verdict.  This is why the
    3-way verdict classification does NOT retire.
    """
    a = tmp_path / "case5.md"
    outcome = _run(
        WriterBackend(a, "# Critique skeleton\nVERDICT: PENDING\n"),
        a,
    )
    assert outcome.is_success, (
        "a fresh, authored, non-trivial skeleton must PASS must_write= "
        "(presence != quality; the 3-way verdict classification does not retire)"
    )


def test_case6_no_attribute_control(tmp_path):
    """Case 6: node without must_write= is untouched — backward compat."""
    a = tmp_path / "case6.md"
    outcome = _run(NoWriteBackend(), a, declare=False)
    assert outcome.is_success, (
        "nodes without must_write= must be completely untouched — the contract is opt-in"
    )


# ---------------------------------------------------------------------------
# Non-trivial semantics
# ---------------------------------------------------------------------------


def test_empty_artifact_fails(tmp_path):
    """An artifact that is written but empty fails the non-trivial check."""
    a = tmp_path / "empty.md"

    class EmptyWriterBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            Path(a).write_text("")
            return _outcome_from("wrote empty file")

    outcome = _run(EmptyWriterBackend(), a)
    assert outcome.status == StageStatus.FAIL, (
        "an empty artifact must not satisfy the non-trivial requirement"
    )


def test_whitespace_only_artifact_fails(tmp_path):
    """An artifact with only whitespace fails the non-trivial check."""
    a = tmp_path / "whitespace.md"

    class WhitespaceWriterBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            Path(a).write_text("   \n\t\n   \n")
            return _outcome_from("wrote whitespace file")

    outcome = _run(WhitespaceWriterBackend(), a)
    assert outcome.status == StageStatus.FAIL, (
        "a whitespace-only artifact must not satisfy the non-trivial requirement"
    )


def test_minimal_content_passes(tmp_path):
    """A single non-whitespace byte satisfies the non-trivial requirement."""
    a = tmp_path / "minimal.md"

    class MinimalWriterBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            Path(a).write_text("x")
            return _outcome_from("wrote minimal content")

    outcome = _run(MinimalWriterBackend(), a)
    assert outcome.is_success, (
        "a single non-whitespace byte must satisfy the non-trivial requirement"
    )


# ---------------------------------------------------------------------------
# Path resolution: relative paths anchored to context.target_dir
# ---------------------------------------------------------------------------


def test_relative_path_resolves_against_target_dir(tmp_path):
    """Relative must_write= paths resolve against context.target_dir."""
    target_dir = tmp_path / "repo"
    target_dir.mkdir()
    artifact_rel = ".ai/postmortem/report.md"
    artifact_abs = target_dir / artifact_rel

    class RelativeWriterBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            artifact_abs.parent.mkdir(parents=True, exist_ok=True)
            artifact_abs.write_text("postmortem content\n")
            return _outcome_from("wrote report")

    # Use relative path in DOT, set context.target_dir
    dot = f"""
digraph RelPathTest {{
    graph [goal="relative path test"]
    start [shape=Mdiamond]
    done  [shape=Msquare]
    maker [shape=box, must_write="{artifact_rel}", max_retries=0,
           prompt="Write the postmortem report."]
    start -> maker
    maker -> done
}}
"""
    graph = parse_dot(dot)
    context = PipelineContext()
    context.set("context.target_dir", str(target_dir))
    logs = tempfile.mkdtemp(prefix="must-write-relpath-logs-")
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=HandlerRegistry(
            HandlerContext(backend=RelativeWriterBackend())
        ),
        logs_root=logs,
    )
    outcome = asyncio.run(engine.run())
    assert outcome.is_success, (
        f"relative must_write= path should resolve against context.target_dir "
        f"({target_dir}); outcome={outcome.status}, reason={outcome.failure_reason}"
    )


def test_relative_path_no_target_dir_uses_cwd(tmp_path, monkeypatch):
    """Relative must_write= paths fall back to os.getcwd() when target_dir unset."""
    monkeypatch.chdir(tmp_path)
    artifact_rel = "output.md"
    artifact_abs = tmp_path / artifact_rel

    class CwdWriterBackend:
        async def run(self, node, prompt, context, incoming_edge=None, graph=None):
            artifact_abs.write_text("output content\n")
            return _outcome_from("wrote output")

    dot = f"""
digraph CwdPathTest {{
    graph [goal="cwd fallback test"]
    start [shape=Mdiamond]
    done  [shape=Msquare]
    maker [shape=box, must_write="{artifact_rel}", max_retries=0,
           prompt="Write the output."]
    start -> maker
    maker -> done
}}
"""
    graph = parse_dot(dot)
    context = PipelineContext()  # no target_dir set
    logs = tempfile.mkdtemp(prefix="must-write-cwd-logs-")
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=HandlerRegistry(HandlerContext(backend=CwdWriterBackend())),
        logs_root=logs,
    )
    outcome = asyncio.run(engine.run())
    assert outcome.is_success, (
        f"relative must_write= should fall back to cwd when target_dir unset; "
        f"outcome={outcome.status}, reason={outcome.failure_reason}"
    )


# ---------------------------------------------------------------------------
# Handler already-FAIL is not double-wrapped
# ---------------------------------------------------------------------------


def test_handler_fail_not_double_wrapped(tmp_path):
    """If the handler already returns FAIL, must_write= does not double-wrap it."""
    a = tmp_path / "never_written.md"
    outcome = _run(AlreadyFailBackend(), a)
    assert outcome.status == StageStatus.FAIL
    # The original failure_reason must be preserved, not replaced by must_write logic
    assert "handler-level failure" in (outcome.failure_reason or ""), (
        "must_write= must not overwrite an existing FAIL outcome's failure_reason"
    )


# ---------------------------------------------------------------------------
# must_write= does not fire on already-FAIL outcome (skip check)
# ---------------------------------------------------------------------------


def test_must_write_skipped_when_handler_already_fails(tmp_path):
    """must_write= check is skipped when handler already returned FAIL.

    The check only intercepts non-FAIL outcomes — it must not run when the
    handler itself produced a FAIL (would double-wrap and lose the original
    failure_reason).
    """
    a = tmp_path / "skip_check.md"
    # AlreadyFailBackend returns FAIL without writing the file
    outcome = _run(AlreadyFailBackend(), a)
    # Confirm it failed (the handler's reason), and the must_write check did
    # not fire (which would produce a different failure_reason)
    assert "handler-level failure" in (outcome.failure_reason or ""), (
        "must_write= check must be skipped when handler already returned FAIL"
    )


# ---------------------------------------------------------------------------
# continue_on_fail must NOT suppress a must_write= FAIL (regression test)
# ---------------------------------------------------------------------------


def _dot_for_continue_on_fail(artifact: str) -> str:
    """Graph with a must_write node that also sets continue_on_fail=true."""
    return f"""
digraph ContinueOnFailTest {{
    graph [goal="continue_on_fail bypass regression"]
    start [shape=Mdiamond]
    done  [shape=Msquare]
    maker [shape=box, must_write="{artifact}", continue_on_fail="true",
           max_retries=0, prompt="Produce the declared artifact."]
    start -> maker
    maker -> done
}}
"""


def test_continue_on_fail_does_not_suppress_must_write_fail(tmp_path):
    """continue_on_fail=true must NOT override a must_write= FAIL.

    Regression: the continue_on_fail block originally ran unconditionally
    after the must_write check injected a FAIL, silently voiding the artifact
    contract.  The guarantee is now by ORDERING: the engine runs the
    must_write check as the final backstop, after the continue_on_fail
    override, so any non-FAIL outcome without a fresh artifact is failed.

    A node that declares must_write=<path>, produces no artifact, AND sets
    continue_on_fail=true must still record FAIL — the fail-closed guarantee
    is non-overridable by pipeline-level attributes.
    """
    a = tmp_path / "case7.md"
    # NoWriteBackend returns SUCCESS-like outcome without writing the artifact
    graph = parse_dot(_dot_for_continue_on_fail(str(a)))
    logs = tempfile.mkdtemp(prefix="must-write-cof-logs-")
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=NoWriteBackend())),
        logs_root=logs,
    )
    outcome = asyncio.run(engine.run())
    assert outcome.status == StageStatus.FAIL, (
        "must_write= FAIL must not be suppressed by continue_on_fail=true. "
        "The fail-closed artifact contract is non-overridable. "
        f"Got status={outcome.status}, failure_reason={outcome.failure_reason!r}"
    )
    assert (
        "must_write" in (outcome.failure_reason or "").lower()
        or "must_write" in (outcome.notes or "").lower()
    ), (
        "failure_reason or notes should mention must_write "
        f"(got: failure_reason={outcome.failure_reason!r}, notes={outcome.notes!r})"
    )


# ---------------------------------------------------------------------------
# max_retries retries must_write violations IN-PLACE (EXTENSIONS.md §27)
# ---------------------------------------------------------------------------


def _dot_for_max_retries(
    artifact: str, max_retries: int = 2, extra_attrs: str = ""
) -> str:
    """Graph with must_write and max_retries > 0 to verify retry semantics."""
    return f"""
digraph MaxRetriesTest {{
    graph [goal="max_retries must_write regression"]
    start [shape=Mdiamond]
    done  [shape=Msquare]
    maker [shape=box, must_write="{artifact}", max_retries={max_retries},
           {extra_attrs}prompt="Produce the declared artifact."]
    start -> maker
    maker -> done
}}
"""


class WriteOnNthCallBackend:
    """Flaky maker: completes without writing until the Nth invocation."""

    def __init__(self, path: Path, content: str, write_on_call: int):
        self.path = path
        self.content = content
        self.write_on_call = write_on_call
        self.calls = 0

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls += 1
        if self.calls >= self.write_on_call:
            Path(self.path).write_text(self.content)
        return _outcome_from("Report written as required.")


def _run_max_retries_graph(backend, dot: str) -> Outcome:
    graph = parse_dot(dot)
    logs = tempfile.mkdtemp(prefix="must-write-maxretries-logs-")
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=logs,
    )
    return asyncio.run(engine.run())


def test_max_retries_retries_must_write_violation_in_place(tmp_path):
    """max_retries DOES re-invoke the handler for a must_write= violation.

    The contract is checked per-attempt inside execute_with_retry(): a
    completed attempt without the declared artifact consumes a retry attempt
    exactly like a RETRY outcome (the fail-closed goal-gate verdict shape,
    EXTENSIONS.md §25).  max_retries=2 on a never-writes node must therefore
    produce exactly 1 + 2 = 3 backend calls before the loud FAIL.

    This pins the documented behavior in EXTENSIONS.md §27 "Retries" — the
    reviewer-requested integration test (BACKEND_CALLS == 1 + max_retries).
    """
    artifact = tmp_path / "case_max_retries.md"
    backend = NoWriteBackend()
    outcome = _run_max_retries_graph(
        backend, _dot_for_max_retries(str(artifact), max_retries=2)
    )
    assert outcome.status == StageStatus.FAIL, (
        "A must_write= node that never produces its artifact must FAIL even "
        f"with max_retries=2. Got status={outcome.status}"
    )
    assert backend.calls == 3, (
        "max_retries must re-invoke the handler for a must_write= violation "
        "(per-attempt check inside the retry ladder). Expected 1 + max_retries "
        f"= 3 backend calls, got {backend.calls}."
    )


def test_must_write_retry_then_write_succeeds(tmp_path):
    """An in-place retry that DOES produce the artifact converts to SUCCESS.

    The point of checking within the retry-attempt boundary: a no-write
    completion is a flaky-failure class where re-invoking the handler helps.
    Attempt 1 completes without writing; attempt 2 writes — the node succeeds
    with exactly 2 backend calls and no graph-level failure routing.
    """
    artifact = tmp_path / "flaky_write.md"
    backend = WriteOnNthCallBackend(
        artifact, "# Report\nreal content\n", write_on_call=2
    )
    outcome = _run_max_retries_graph(
        backend, _dot_for_max_retries(str(artifact), max_retries=2)
    )
    assert outcome.status == StageStatus.SUCCESS, (
        "A retry attempt that satisfies the must_write= contract must let the "
        f"node succeed. Got status={outcome.status}, "
        f"failure_reason={outcome.failure_reason!r}"
    )
    assert backend.calls == 2, (
        f"Expected the node to succeed on the 2nd attempt, got {backend.calls} calls."
    )


def test_allow_partial_does_not_soften_must_write_failure(tmp_path):
    """allow_partial=true must NOT downgrade a must_write= FAIL to PARTIAL.

    The retry ladder's exhaustion path converts RETRY exhaustion to
    PARTIAL_SUCCESS under allow_partial — but a must_write= violation is
    fail-closed: exhaustion returns the loud FAIL directly.
    """
    artifact = tmp_path / "allow_partial.md"
    backend = NoWriteBackend()
    outcome = _run_max_retries_graph(
        backend,
        _dot_for_max_retries(
            str(artifact), max_retries=1, extra_attrs='allow_partial="true", '
        ),
    )
    assert outcome.status == StageStatus.FAIL, (
        "allow_partial must not soften a must_write= violation "
        f"(fail-closed). Got status={outcome.status}"
    )
    assert backend.calls == 2, (
        f"Expected 1 + max_retries = 2 backend calls, got {backend.calls}."
    )


def test_continue_on_fail_handler_fail_without_artifact_still_fails(tmp_path):
    """continue_on_fail=true cannot resurrect a must_write node whose handler
    FAILED and whose artifact was never written.

    The engine's final backstop runs AFTER the continue_on_fail override:
    handler FAIL → continue_on_fail flips it to SUCCESS → the backstop
    re-checks the artifact contract → no fresh artifact → FAIL.  Without the
    backstop ordering, this combination completed as a silent success without
    the declared artifact (the exact hole the contract exists to close).
    """
    artifact = tmp_path / "cof_handler_fail.md"
    graph = parse_dot(f"""
digraph ContinueOnFailHandlerFail {{
    graph [goal="continue_on_fail + handler FAIL + must_write"]
    start [shape=Mdiamond]
    done  [shape=Msquare]
    maker [shape=box, must_write="{artifact}", continue_on_fail="true",
           max_retries=0, prompt="Produce the declared artifact."]
    start -> maker
    maker -> done
}}
""")
    logs = tempfile.mkdtemp(prefix="must-write-cof-hf-logs-")
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=AlreadyFailBackend())),
        logs_root=logs,
    )
    outcome = asyncio.run(engine.run())
    assert outcome.status == StageStatus.FAIL, (
        "A must_write node whose handler failed and whose artifact was never "
        "written must stay FAILED even under continue_on_fail=true. "
        f"Got status={outcome.status}, failure_reason={outcome.failure_reason!r}"
    )


# ---------------------------------------------------------------------------
# Council amendment battery: RETRY-exhaustion path, SKIPPED semantics,
# manufactured-verdict backstop
# ---------------------------------------------------------------------------


class AlwaysRetryBackend:
    """Backend that always returns RETRY (drives the ladder to exhaustion).

    Optionally writes the artifact on every call — models a worker that DID
    produce its artifact but kept asking for another attempt.
    """

    def __init__(self, write_path: Path | None = None, content: str = ""):
        self.write_path = write_path
        self.content = content
        self.calls = 0

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        self.calls += 1
        if self.write_path is not None:
            Path(self.write_path).write_text(self.content)
        return Outcome(status=StageStatus.RETRY, failure_reason="not ready yet")


class SkipBackend:
    """Backend that returns SKIPPED (handler-side skip; node did not execute)."""

    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return Outcome(status=StageStatus.SKIPPED, notes="nothing to do")


def _run_graph_with_engine(backend, dot: str):
    graph = parse_dot(dot)
    logs = tempfile.mkdtemp(prefix="must-write-council-logs-")
    engine = PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=logs,
    )
    outcome = asyncio.run(engine.run())
    return outcome, engine


def test_retry_exhaustion_allow_partial_no_artifact_fails(tmp_path):
    """RETRY exhaustion + allow_partial + NO artifact must NOT become partial.

    The exhaustion tail manufactures PARTIAL_SUCCESS("Retries exhausted,
    partial accepted") under allow_partial — before this amendment that
    manufactured verdict bypassed the ladder's must_write check entirely.
    No artifact means there is nothing to accept partially: loud FAIL.
    """
    artifact = tmp_path / "exhausted_no_artifact.md"
    backend = AlwaysRetryBackend()  # never writes
    _outcome, engine = _run_graph_with_engine(
        backend,
        _dot_for_max_retries(
            str(artifact), max_retries=1, extra_attrs='allow_partial="true", '
        ),
    )
    maker = engine.node_outcomes["maker"]
    assert maker.status == StageStatus.FAIL, (
        "Retries exhausted + allow_partial + no artifact must be a loud FAIL, "
        f"not partial acceptance. Got {maker.status}, notes={maker.notes!r}"
    )
    assert "must_write" in (maker.failure_reason or ""), (
        f"FAIL should name the violated artifact contract, got "
        f"failure_reason={maker.failure_reason!r}"
    )
    assert backend.calls == 2, (
        f"Expected 1 + max_retries = 2 backend calls, got {backend.calls}."
    )


def test_retry_exhaustion_allow_partial_with_artifact_partial_accepted(tmp_path):
    """Positive case: artifact present + exhausted + allow_partial → partial.

    When the exhausted node DID write a fresh, non-trivial artifact, the
    manufactured PARTIAL_SUCCESS passes the artifact contract and partial
    acceptance stands (both in the ladder and at the engine's backstop).
    """
    artifact = tmp_path / "exhausted_with_artifact.md"
    backend = AlwaysRetryBackend(write_path=artifact, content="# Real report\n")
    _outcome, engine = _run_graph_with_engine(
        backend,
        _dot_for_max_retries(
            str(artifact), max_retries=1, extra_attrs='allow_partial="true", '
        ),
    )
    maker = engine.node_outcomes["maker"]
    assert maker.status == StageStatus.PARTIAL_SUCCESS, (
        "Artifact present + exhausted + allow_partial should accept partial. "
        f"Got {maker.status}, failure_reason={maker.failure_reason!r}"
    )
    assert "partial accepted" in (maker.notes or ""), (
        f"Expected the manufactured partial-acceptance notes, got {maker.notes!r}"
    )


def test_backstop_rejects_manufactured_partial_without_artifact(tmp_path):
    """The shared backstop check rejects the exact manufactured verdict shape.

    Feeds the ladder's manufactured PARTIAL_SUCCESS("Retries exhausted,
    partial accepted") directly to check_must_write (the same function the
    engine's final backstop delegates to): no artifact → FAIL; fresh
    artifact → contract satisfied (None).
    """
    from amplifier_module_loop_pipeline.graph import Node
    from amplifier_module_loop_pipeline.must_write import check_must_write

    artifact = tmp_path / "manufactured.md"
    node = Node(id="maker", attrs={"must_write": str(artifact)})
    manufactured = Outcome(
        status=StageStatus.PARTIAL_SUCCESS,
        notes="Retries exhausted, partial accepted",
    )
    start = time.time()

    fail = check_must_write(node, manufactured, start, PipelineContext())
    assert fail is not None and fail.status == StageStatus.FAIL, (
        "Manufactured partial without an artifact must fail the backstop."
    )

    artifact.write_text("# Real content\n")  # mtime strictly after `start`
    import os as _os

    _os.utime(artifact, (start + 5, start + 5))
    ok = check_must_write(node, manufactured, start, PipelineContext())
    assert ok is None, (
        f"Fresh, non-trivial artifact must satisfy the backstop, got {ok!r}"
    )


def test_skipped_outcome_passes_through_unconverted(tmp_path):
    """DECISION (EXTENSIONS.md §27): SKIPPED means the node did not execute.

    A handler-side SKIPPED on a must_write= node passes through unconverted
    — the artifact contract applies only to completed executions.  (The
    executed-without-artifact direction is pinned by
    test_case1_narration_no_write_fails and the exhaustion tests above.)
    """
    artifact = tmp_path / "skipped_node.md"
    _outcome, engine = _run_graph_with_engine(SkipBackend(), _dot_for(str(artifact)))
    maker = engine.node_outcomes["maker"]
    assert maker.status == StageStatus.SKIPPED, (
        "A legitimately-SKIPPED must_write node must stay SKIPPED, not be "
        f"converted to FAIL for an artifact it was never asked to produce. "
        f"Got {maker.status}, failure_reason={maker.failure_reason!r}"
    )


def test_skipped_unit_check_returns_none(tmp_path):
    """Unit direction of the SKIPPED decision: check_must_write exempts SKIPPED."""
    from amplifier_module_loop_pipeline.graph import Node
    from amplifier_module_loop_pipeline.must_write import check_must_write

    artifact = tmp_path / "never_written.md"  # deliberately absent
    node = Node(id="maker", attrs={"must_write": str(artifact)})
    skipped = Outcome(status=StageStatus.SKIPPED, notes="nothing to do")
    assert check_must_write(node, skipped, time.time(), PipelineContext()) is None


def test_auto_status_promoted_skip_is_a_completed_execution(tmp_path):
    """The deliberate asymmetry: auto_status=true promotion runs BEFORE the
    engine's final backstop, so a promoted SKIPPED→SUCCESS node counts as a
    completed execution and the artifact contract applies — a node that ran,
    wrote no status, and wrote no artifact is exactly the
    narration-without-artifact class this contract exists to catch.
    """
    artifact = tmp_path / "auto_status_promoted.md"
    dot = f"""
digraph AutoStatusPromotion {{
    graph [goal="auto_status + must_write asymmetry"]
    start [shape=Mdiamond]
    done  [shape=Msquare]
    maker [shape=box, must_write="{artifact}", auto_status=true,
           max_retries=0, prompt="Produce the declared artifact."]
    start -> maker
    maker -> done
}}
"""
    _outcome, engine = _run_graph_with_engine(SkipBackend(), dot)
    maker = engine.node_outcomes["maker"]
    assert maker.status == StageStatus.FAIL, (
        "auto_status-promoted SKIPPED counts as a completed execution: "
        "no artifact must FAIL at the backstop. "
        f"Got {maker.status}, notes={maker.notes!r}"
    )
