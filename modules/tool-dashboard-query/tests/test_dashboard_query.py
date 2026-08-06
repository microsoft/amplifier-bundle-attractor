"""Tests for dashboard_query tool."""

import hashlib
import json
import os
import subprocess
import sys

import httpx
import pytest
from amplifier_module_tool_dashboard_query import (
    _OPERATIONS_SORTED as _MODULE_OPERATIONS_SORTED,
)
from amplifier_module_tool_dashboard_query import VALID_OPERATIONS, DashboardQueryTool

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_response(data: dict | list, status_code: int = 200) -> httpx.Response:
    """Build an httpx.Response with JSON body."""
    return httpx.Response(
        status_code=status_code,
        json=data,
    )


def _make_tool(handler) -> DashboardQueryTool:
    """Create a DashboardQueryTool with a mock transport."""
    tool = DashboardQueryTool(config={"dashboard_url": "http://test"})
    tool._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )
    return tool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio(loop_scope="session")
async def test_list_pipelines():
    """GET /api/pipelines returns a list of pipelines."""
    pipelines = [{"id": "p1", "status": "running"}, {"id": "p2", "status": "done"}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines"
        assert request.method == "GET"
        return _json_response(pipelines)

    tool = _make_tool(handler)
    result = await tool.execute({"operation": "list_pipelines"})

    assert result.success
    assert result.output == pipelines


@pytest.mark.asyncio(loop_scope="session")
async def test_get_pipeline():
    """GET /api/pipelines/{id} returns pipeline detail."""
    pipeline = {"id": "p1", "status": "running", "nodes": []}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines/p1"
        assert request.method == "GET"
        return _json_response(pipeline)

    tool = _make_tool(handler)
    result = await tool.execute({"operation": "get_pipeline", "pipeline_id": "p1"})

    assert result.success
    assert result.output == pipeline


@pytest.mark.asyncio(loop_scope="session")
async def test_get_node():
    """GET /api/pipelines/{pid}/nodes/{nid} returns node detail."""
    node = {"id": "build", "status": "complete", "output": "ok"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines/p1/nodes/build"
        assert request.method == "GET"
        return _json_response(node)

    tool = _make_tool(handler)
    result = await tool.execute(
        {"operation": "get_node", "pipeline_id": "p1", "node_id": "build"}
    )

    assert result.success
    assert result.output == node


@pytest.mark.asyncio(loop_scope="session")
async def test_submit_pipeline():
    """POST /api/pipelines submits a new pipeline."""
    created = {"id": "p3", "status": "pending"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["dot_source"] == "digraph { a -> b }"
        assert body["goal"] == "test goal"
        return _json_response(created)

    tool = _make_tool(handler)
    result = await tool.execute(
        {
            "operation": "submit_pipeline",
            "dot_source": "digraph { a -> b }",
            "goal": "test goal",
        }
    )

    assert result.success
    assert result.output == created


@pytest.mark.asyncio(loop_scope="session")
async def test_submit_pipeline_without_goal():
    """POST /api/pipelines works without an optional goal."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "goal" not in body
        return _json_response({"id": "p4", "status": "pending"})

    tool = _make_tool(handler)
    result = await tool.execute(
        {"operation": "submit_pipeline", "dot_source": "digraph { x -> y }"}
    )

    assert result.success


@pytest.mark.asyncio(loop_scope="session")
async def test_cancel_pipeline():
    """POST /api/pipelines/{id}/cancel cancels a pipeline."""
    cancelled = {"id": "p1", "status": "cancelled"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines/p1/cancel"
        assert request.method == "POST"
        return _json_response(cancelled)

    tool = _make_tool(handler)
    result = await tool.execute({"operation": "cancel_pipeline", "pipeline_id": "p1"})

    assert result.success
    assert result.output == cancelled


@pytest.mark.asyncio(loop_scope="session")
async def test_get_questions():
    """GET /api/pipelines/{id}/questions returns pending questions."""
    questions = [{"id": "q1", "text": "Approve deploy?"}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines/p1/questions"
        assert request.method == "GET"
        return _json_response(questions)

    tool = _make_tool(handler)
    result = await tool.execute({"operation": "get_questions", "pipeline_id": "p1"})

    assert result.success
    assert result.output == questions


@pytest.mark.asyncio(loop_scope="session")
async def test_answer_question():
    """POST /api/pipelines/{pid}/questions/{qid}/answer submits an answer."""
    answered = {"id": "q1", "status": "answered"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/pipelines/p1/questions/q1/answer"
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["answer"] == "yes"
        return _json_response(answered)

    tool = _make_tool(handler)
    result = await tool.execute(
        {
            "operation": "answer_question",
            "pipeline_id": "p1",
            "question_id": "q1",
            "answer": "yes",
        }
    )

    assert result.success
    assert result.output == answered


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_operation():
    """Missing operation field returns error."""
    tool = DashboardQueryTool(config={})
    result = await tool.execute({})

    assert not result.success
    assert "operation" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_pipeline_id():
    """get_pipeline without pipeline_id returns error."""
    tool = DashboardQueryTool(config={})
    result = await tool.execute({"operation": "get_pipeline"})

    assert not result.success
    assert "pipeline_id" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_node_id():
    """get_node without node_id returns error."""
    tool = DashboardQueryTool(config={})
    result = await tool.execute({"operation": "get_node", "pipeline_id": "p1"})

    assert not result.success
    assert "node_id" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_dot_source():
    """submit_pipeline without dot_source returns error."""
    tool = DashboardQueryTool(config={})
    result = await tool.execute({"operation": "submit_pipeline"})

    assert not result.success
    assert "dot_source" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_missing_answer_fields():
    """answer_question without question_id and answer returns error."""
    tool = DashboardQueryTool(config={})
    result = await tool.execute({"operation": "answer_question", "pipeline_id": "p1"})

    assert not result.success
    assert "question_id" in result.error["message"]


@pytest.mark.asyncio(loop_scope="session")
async def test_tool_name_and_schema():
    """Tool has correct name, description, and input schema structure."""
    tool = DashboardQueryTool(config={})

    assert tool.name == "dashboard_query"
    assert "pipeline" in tool.description.lower()

    schema = tool.input_schema
    assert schema["type"] == "object"
    assert "operation" in schema["properties"]
    assert "operation" in schema["required"]
    assert "pipeline_id" in schema["properties"]
    assert "node_id" in schema["properties"]
    assert "dot_source" in schema["properties"]
    assert "goal" in schema["properties"]
    assert "question_id" in schema["properties"]
    assert "answer" in schema["properties"]


@pytest.mark.asyncio(loop_scope="session")
async def test_close_shuts_down_client():
    """close() closes the underlying httpx client and resets to None."""

    def handler(request: httpx.Request) -> httpx.Response:
        return _json_response({"ok": True})

    tool = _make_tool(handler)
    # Force client creation
    client = tool._get_client()
    assert client is not None

    await tool.close()
    assert tool._client is None


@pytest.mark.asyncio(loop_scope="session")
async def test_close_when_no_client_is_noop():
    """close() on a tool that never created a client is a safe no-op."""
    tool = DashboardQueryTool(config={})
    assert tool._client is None

    await tool.close()  # should not raise
    assert tool._client is None


# -- Schema determinism (prompt-cache stability) ----------------------------
#
# Regression guard for the S4 structural pattern (two independent `enum`
# call sites that must never drift apart). Full story:
# docs/designs/RECURRING-BUG-CLASSES.md (S4). See
# test_schema_serialization_is_deterministic_across_processes below for the
# actual cross-process proof.


def test_schema_enum_matches_canonical_order():
    """The enum must be exactly the module's canonical sorted order."""
    tool = DashboardQueryTool(config={})
    assert tool.input_schema["properties"]["operation"]["enum"] == list(
        _MODULE_OPERATIONS_SORTED
    )


def test_schema_enum_contains_expected_members():
    """Regression guard: sorting must never silently drop or add a value."""
    tool = DashboardQueryTool(config={})
    enum = tool.input_schema["properties"]["operation"]["enum"]
    assert set(enum) == VALID_OPERATIONS
    assert len(enum) == 7


_SCHEMA_PROBE = (
    "import json\n"
    "from amplifier_module_tool_dashboard_query import DashboardQueryTool\n"
    "tool = DashboardQueryTool(config={})\n"
    "print(json.dumps(tool.input_schema, sort_keys=False, separators=(',', ':')))\n"
)


def test_schema_serialization_is_deterministic_across_processes():
    """The REAL eval: the serialized input_schema must be byte-identical across
    independent interpreter starts.

    A same-process test cannot catch this bug class because PYTHONHASHSEED is
    fixed for the lifetime of one interpreter -- frozenset iteration order
    only varies *between* process starts. This spawns N independent
    subprocesses (matching how the real production incident manifested: a
    fresh schema hash at every process restart) and asserts they all produce
    the identical serialized schema.
    """
    n_procs = 8
    env = dict(os.environ)
    env.pop("PYTHONHASHSEED", None)  # let each subprocess pick its own random seed

    canon_outputs: list[str] = []
    for _ in range(n_procs):
        result = subprocess.run(
            [sys.executable, "-c", _SCHEMA_PROBE],
            capture_output=True,
            text=True,
            timeout=5,
            env=env,
            check=False,
        )
        assert result.returncode == 0, (
            f"Probe subprocess failed (rc={result.returncode}): "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        canon_outputs.append(result.stdout.strip())

    hashes = {hashlib.sha256(c.encode()).hexdigest()[:16] for c in canon_outputs}
    distinct_orders = sorted(set(canon_outputs))
    assert len(hashes) == 1, (
        f"Expected 1 distinct input_schema serialization across {n_procs} "
        f"independent processes, got {len(hashes)}. The schema's byte "
        "representation is not stable across interpreter restarts (a "
        "PYTHONHASHSEED-dependent frozenset iteration order), which "
        "invalidates the ENTIRE Anthropic prompt cache on every process "
        f"restart.\nDistinct serializations observed:\n" + "\n".join(distinct_orders)
    )
