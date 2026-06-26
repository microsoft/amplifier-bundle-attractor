"""Tests for the response_schema structured-output extension (EXTENSIONS.md §23).

Covers:
  - DOT parsing promotes response_schema into Node.response_schema
  - apply_transforms resolves inline JSON and file-path forms to dicts
  - Invalid inline JSON → loud ValueError at transform time
  - Missing / invalid JSON file → loud ValueError at transform time
  - Validation rule flags any unresolved string that slips past transforms
  - AmplifierBackend._run_with_tool_loop passes ResponseFormat to generate()
    when response_schema is set, and does NOT when it is absent
  - AmplifierBackend._run_with_spawn returns FAIL with clear message when
    response_schema is set (spawn path does not support structured output yet)
  - DirectProviderBackend.run passes ResponseFormat to generate() when set

All LLM calls are mocked — no live API keys required.
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Dependency stubs (must precede module imports)
# ---------------------------------------------------------------------------

if "amplifier_foundation" not in sys.modules:

    @dataclass
    class _StubProviderPreference:
        provider: str = ""
        model: str = ""

    _stub_foundation = types.ModuleType("amplifier_foundation")
    _stub_foundation.ProviderPreference = _StubProviderPreference  # type: ignore[attr-defined]
    sys.modules["amplifier_foundation"] = _stub_foundation

if "amplifier_core" not in sys.modules:

    @dataclass
    class _StubMessage:
        role: str = "user"
        content: Any = ""
        tool_call_id: str | None = None
        name: str | None = None
        metadata: dict | None = None

    @dataclass
    class _StubChatRequest:
        messages: list = field(default_factory=list)
        tools: list | None = None
        tool_choice: str | None = None
        reasoning_effort: str | None = None

    _stub_core = types.ModuleType("amplifier_core")
    _stub_core.Message = _StubMessage  # type: ignore[attr-defined]
    _stub_core.ChatRequest = _StubChatRequest  # type: ignore[attr-defined]
    sys.modules["amplifier_core"] = _stub_core

    _stub_msg = types.ModuleType("amplifier_core.message_models")
    sys.modules["amplifier_core.message_models"] = _stub_msg

unified_llm = pytest.importorskip("unified_llm")

from amplifier_module_loop_pipeline.backend import AmplifierBackend  # noqa: E402
from amplifier_module_loop_pipeline.context import PipelineContext  # noqa: E402
from amplifier_module_loop_pipeline.dot_parser import parse_dot  # noqa: E402
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node  # noqa: E402
from amplifier_module_loop_pipeline.outcome import StageStatus  # noqa: E402
from amplifier_module_loop_pipeline.transforms import (  # noqa: E402
    apply_transforms,
    resolve_response_schemas,
)
from amplifier_module_loop_pipeline.validation import (  # noqa: E402
    validate_or_raise,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_INLINE_SCHEMA_DICT: dict[str, Any] = {
    "type": "object",
    "properties": {"name": {"type": "string"}},
    "required": ["name"],
}

_INLINE_SCHEMA_STR: str = json.dumps(_INLINE_SCHEMA_DICT)

# Minimum valid DOT: escape double-quotes inside the JSON for the DOT string.
# We build the attribute value without backslash-in-f-string (Python 3.11 limit).
_SCHEMA_DOT_VALUE = _INLINE_SCHEMA_STR.replace('"', '\\"')
_DOT_WITH_SCHEMA = (
    "digraph test {\n"
    "    start [shape=Mdiamond]\n"
    f'    extract [shape=box, prompt="Extract the thing", response_schema="{_SCHEMA_DOT_VALUE}"]\n'
    "    end [shape=Msquare]\n"
    "    start -> extract\n"
    "    extract -> end\n"
    "}"
)

_DOT_WITHOUT_SCHEMA = """
digraph test {
    start [shape=Mdiamond]
    work [shape=box, prompt="Do the work"]
    end [shape=Msquare]
    start -> work
    work -> end
}
"""


def _make_node_with_schema(schema: Any = None, **kwargs: Any) -> Node:
    defaults: dict[str, Any] = {
        "id": "extract",
        "prompt": "Extract",
        "llm_model": "test-model",
        "response_schema": schema,
    }
    defaults.update(kwargs)
    return Node(**defaults)


def _make_node_no_schema(**kwargs: Any) -> Node:
    defaults: dict[str, Any] = {
        "id": "work",
        "prompt": "Do the work",
        "llm_model": "test-model",
    }
    defaults.update(kwargs)
    return Node(**defaults)


def _make_context() -> PipelineContext:
    return PipelineContext()


def _make_minimal_graph(node: Node, source_dir: str = "") -> Graph:
    """Minimal two-node graph wrapping *node* for transform/validation tests."""
    exit_node = Node(id="exit", shape="Msquare")
    start_node = Node(id="start", shape="Mdiamond")
    return Graph(
        name="test",
        nodes={"start": start_node, node.id: node, "exit": exit_node},
        edges=[
            Edge(from_node="start", to_node=node.id),
            Edge(from_node=node.id, to_node="exit"),
        ],
        source_dir=source_dir,
    )


def _make_generate_result(text: str = "") -> "unified_llm.GenerateResult":  # type: ignore[name-defined]
    """Build a minimal GenerateResult for mocking."""
    response = unified_llm.Response(
        id="r1",
        model="test-model",
        provider="test",
        message=unified_llm.Message.assistant(text),
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=unified_llm.Usage(input_tokens=5, output_tokens=10, total_tokens=15),
    )
    from unified_llm.types import StepResult

    step = StepResult(
        text=text,
        tool_calls=[],
        tool_results=[],
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=unified_llm.Usage(input_tokens=5, output_tokens=10, total_tokens=15),
        response=response,
        warnings=[],
    )
    return unified_llm.GenerateResult(
        text=text,
        finish_reason=unified_llm.FinishReason(reason="stop"),
        usage=unified_llm.Usage(input_tokens=5, output_tokens=10, total_tokens=15),
        total_usage=unified_llm.Usage(
            input_tokens=5, output_tokens=10, total_tokens=15
        ),
        steps=[step],
        response=response,
    )


class _MockUnifiedClient:
    """Records complete() requests for assertion."""

    def __init__(self, text: str = "{}") -> None:
        self._text = text
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> Any:
        self.requests.append(request)
        return unified_llm.Response(
            id="r1",
            model="test-model",
            provider="test",
            message=unified_llm.Message.assistant(self._text),
            finish_reason=unified_llm.FinishReason(reason="stop"),
            usage=unified_llm.Usage(input_tokens=5, output_tokens=10, total_tokens=15),
        )


class _MockCoordinator:
    """Coordinator that exposes session.spawn."""

    session = MagicMock()
    session.config = {}
    # Include 'attractor-anthropic' with a non-pipeline session.orchestrator
    # so the identity recursion guard in _run_with_spawn does not fire.
    config: dict[str, Any] = {
        "agents": {
            "attractor-anthropic": {
                "session": {"orchestrator": {"module": "loop-agent"}},
            },
        }
    }

    def __init__(self, spawn_result: dict[str, Any] | None = None) -> None:
        self._spawn_result = spawn_result or {"output": "done", "session_id": "c-1"}
        self.spawn_called = False
        self.last_spawn_kwargs: dict[str, Any] = {}

    def get_capability(self, name: str) -> Any:
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs: Any) -> Any:
        self.spawn_called = True
        self.last_spawn_kwargs = kwargs
        return self._spawn_result


# ---------------------------------------------------------------------------
# 1. Graph / parser layer: response_schema promoted into Node
# ---------------------------------------------------------------------------


class TestGraphPromotion:
    """response_schema is promoted like other _NODE_PROMOTED_ATTRS."""

    def test_node_field_default_none(self) -> None:
        node = Node(id="n", prompt="p")
        assert node.response_schema is None

    def test_node_accepts_dict_directly(self) -> None:
        """When constructed programmatically with a dict, it is stored."""
        node = Node(id="n", prompt="p", response_schema={"type": "object"})
        assert node.response_schema == {"type": "object"}

    def test_node_attrs_promote_string_value(self) -> None:
        """response_schema from DOT attrs is promoted to the Node field."""
        node = Node(id="n", prompt="p", attrs={"response_schema": '{"type":"object"}'})
        # The field holds the raw string (pre-transform state)
        assert node.response_schema == '{"type":"object"}'
        # Backward compat: _NodeAttrsProxy proxies the field back through attrs.get()
        assert node.attrs.get("response_schema") == '{"type":"object"}'

    def test_dot_parser_passes_through_response_schema(self) -> None:
        """parse_dot creates a node with response_schema from the DOT source."""
        graph = parse_dot(_DOT_WITH_SCHEMA)
        extract = graph.nodes["extract"]
        # raw string from DOT is stored on the field
        assert extract.response_schema is not None
        assert isinstance(extract.response_schema, str)
        assert extract.response_schema.strip().startswith("{")

    def test_dot_parser_node_without_schema_has_none(self) -> None:
        graph = parse_dot(_DOT_WITHOUT_SCHEMA)
        work = graph.nodes["work"]
        assert work.response_schema is None


# ---------------------------------------------------------------------------
# 2. apply_transforms resolves inline JSON
# ---------------------------------------------------------------------------


class TestInlineJsonResolution:
    def test_resolve_simple_inline_schema(self) -> None:
        node = _make_node_with_schema(_INLINE_SCHEMA_STR)
        graph = _make_minimal_graph(node)
        resolve_response_schemas(graph)
        assert graph.nodes["extract"].response_schema == _INLINE_SCHEMA_DICT

    def test_resolve_inline_already_dict_is_noop(self) -> None:
        """If the field is already a dict (programmatic), it is left unchanged."""
        node = _make_node_with_schema(_INLINE_SCHEMA_DICT)
        graph = _make_minimal_graph(node)
        resolve_response_schemas(graph)
        assert graph.nodes["extract"].response_schema == _INLINE_SCHEMA_DICT

    def test_apply_transforms_calls_resolution(self) -> None:
        """apply_transforms() resolves response_schema as part of its pipeline."""
        node = _make_node_with_schema(_INLINE_SCHEMA_STR)
        graph = _make_minimal_graph(node)
        apply_transforms(graph, _make_context())
        result = graph.nodes["extract"].response_schema
        assert isinstance(result, dict)
        assert result["type"] == "object"

    def test_none_schema_untouched(self) -> None:
        node = _make_node_no_schema()
        graph = _make_minimal_graph(node)
        resolve_response_schemas(graph)
        assert graph.nodes["work"].response_schema is None


# ---------------------------------------------------------------------------
# 3. apply_transforms resolves file-path form
# ---------------------------------------------------------------------------


class TestFilePathResolution:
    def test_resolve_schema_from_file(self, tmp_path: Path) -> None:
        schema_file = tmp_path / "schema.json"
        schema_file.write_text(json.dumps(_INLINE_SCHEMA_DICT), encoding="utf-8")

        node = _make_node_with_schema("schema.json")
        graph = _make_minimal_graph(node, source_dir=str(tmp_path))
        resolve_response_schemas(graph)
        assert graph.nodes["extract"].response_schema == _INLINE_SCHEMA_DICT

    def test_resolve_absolute_path(self, tmp_path: Path) -> None:
        schema_file = tmp_path / "abs_schema.json"
        schema_file.write_text(json.dumps({"type": "array"}), encoding="utf-8")

        node = _make_node_with_schema(str(schema_file))
        graph = _make_minimal_graph(node, source_dir="")
        resolve_response_schemas(graph)
        assert graph.nodes["extract"].response_schema == {"type": "array"}

    def test_relative_path_falls_back_to_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        schema_file = tmp_path / "rel_schema.json"
        schema_file.write_text(json.dumps({"type": "boolean"}), encoding="utf-8")

        monkeypatch.chdir(tmp_path)
        node = _make_node_with_schema("rel_schema.json")
        graph = _make_minimal_graph(node, source_dir="")
        resolve_response_schemas(graph)
        assert graph.nodes["extract"].response_schema == {"type": "boolean"}


# ---------------------------------------------------------------------------
# 4. Invalid values → loud error
# ---------------------------------------------------------------------------


class TestFailLoud:
    def test_invalid_inline_json_raises(self) -> None:
        node = _make_node_with_schema("{this is not json}")
        graph = _make_minimal_graph(node)
        with pytest.raises(
            ValueError, match="response_schema is not valid inline JSON"
        ):
            resolve_response_schemas(graph)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        node = _make_node_with_schema("nonexistent_schema.json")
        graph = _make_minimal_graph(node, source_dir=str(tmp_path))
        with pytest.raises(ValueError, match="could not be read"):
            resolve_response_schemas(graph)

    def test_file_with_invalid_json_raises(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{not valid", encoding="utf-8")

        node = _make_node_with_schema("bad.json")
        graph = _make_minimal_graph(node, source_dir=str(tmp_path))
        with pytest.raises(ValueError, match="does not contain valid JSON"):
            resolve_response_schemas(graph)

    def test_non_object_json_array_treated_as_file_path_raises(
        self, tmp_path: Path
    ) -> None:
        """[1,2,3] does not start with '{', so it is treated as a file path.
        The resolution fails loudly — the error is about a missing file, not
        about JSON structure, because the heuristic is '{'-based."""
        node = _make_node_with_schema("[1, 2, 3]")
        graph = _make_minimal_graph(node, source_dir=str(tmp_path))
        with pytest.raises(ValueError, match="could not be read"):
            resolve_response_schemas(graph)

    def test_apply_transforms_propagates_error(self) -> None:
        node = _make_node_with_schema("{broken")
        graph = _make_minimal_graph(node)
        with pytest.raises(
            ValueError, match="response_schema is not valid inline JSON"
        ):
            apply_transforms(graph, _make_context())

    def test_error_message_includes_node_id(self) -> None:
        """Error messages from resolution include the node id for easy diagnosis."""
        node = _make_node_with_schema("{bad json}", id="my_node")
        graph = _make_minimal_graph(node)
        with pytest.raises(ValueError, match="my_node"):
            resolve_response_schemas(graph)

    def test_error_message_includes_file_path(self, tmp_path: Path) -> None:
        """Error message when file is missing includes the path for diagnosis."""
        node = _make_node_with_schema("ghost.json", id="node_x")
        graph = _make_minimal_graph(node, source_dir=str(tmp_path))
        with pytest.raises(ValueError, match="ghost.json"):
            resolve_response_schemas(graph)


# ---------------------------------------------------------------------------
# 5. Validation rule: unresolved string is an ERROR
# ---------------------------------------------------------------------------


class TestValidationRule:
    def test_unresolved_string_is_error(self) -> None:
        """If transforms weren't run and schema is still a string, validate() ERRORs."""
        from amplifier_module_loop_pipeline.validation import validate

        node = _make_node_with_schema('{"type":"object"}')
        graph = _make_minimal_graph(node)
        # Deliberately skip resolve_response_schemas so the string stays
        diags = validate(graph)
        errors = [
            d
            for d in diags
            if d.severity == "ERROR" and d.rule == "response_schema_valid"
        ]
        assert len(errors) == 1, (
            f"Expected one response_schema_valid ERROR, got: {diags}"
        )
        assert "extract" in errors[0].message  # references the node id

    def test_resolved_dict_passes_validation(self) -> None:
        node = _make_node_with_schema(_INLINE_SCHEMA_DICT)
        graph = _make_minimal_graph(node)
        # Dict is already valid — should not raise
        validate_or_raise(graph)

    def test_none_schema_passes_validation(self) -> None:
        node = _make_node_no_schema()
        graph = _make_minimal_graph(node)
        validate_or_raise(graph)


# ---------------------------------------------------------------------------
# 6. Backend: _run_with_tool_loop passes ResponseFormat to generate()
# ---------------------------------------------------------------------------


class TestBackendToolLoopWiring:
    @pytest.mark.asyncio
    async def test_response_format_passed_when_schema_set(self) -> None:
        """When response_schema is set, generate() receives ResponseFormat(json_schema=...)."""
        schema = _INLINE_SCHEMA_DICT
        json_response = json.dumps({"name": "Alice"})
        mock_client = _MockUnifiedClient(text=json_response)

        node = _make_node_with_schema(schema)
        backend = AmplifierBackend(
            coordinator=MagicMock(
                get_capability=lambda _: None,
                config={"agents": {}},
                session=MagicMock(),
            ),
            profiles={},
            provider=MagicMock(),
            unified_client=mock_client,
        )

        await backend._run_with_tool_loop(
            node=node,
            instruction="Extract the thing",
            reasoning_effort=None,
        )

        # Verify the request sent to complete() carried response_format
        assert len(mock_client.requests) >= 1
        req = mock_client.requests[-1]
        assert req.response_format is not None
        assert req.response_format.type == "json_schema"
        assert req.response_format.json_schema == schema
        assert req.response_format.strict is True

    @pytest.mark.asyncio
    async def test_response_format_absent_when_no_schema(self) -> None:
        """When response_schema is None, generate() receives no response_format."""
        json_response = json.dumps({"status": "success"})
        mock_client = _MockUnifiedClient(text=json_response)

        node = _make_node_no_schema()
        backend = AmplifierBackend(
            coordinator=MagicMock(
                get_capability=lambda _: None,
                config={"agents": {}},
                session=MagicMock(),
            ),
            profiles={},
            provider=MagicMock(),
            unified_client=mock_client,
        )

        await backend._run_with_tool_loop(
            node=node,
            instruction="Do the work",
            reasoning_effort=None,
        )

        assert len(mock_client.requests) >= 1
        req = mock_client.requests[-1]
        # response_format must be absent (None) when no schema is set
        assert req.response_format is None

    @pytest.mark.asyncio
    async def test_structured_output_returned_as_success_outcome(self) -> None:
        """Structured output JSON stored as notes; parsed dict in context_updates[node_id]."""
        schema = _INLINE_SCHEMA_DICT
        json_response = json.dumps({"name": "Alice"})
        mock_client = _MockUnifiedClient(text=json_response)

        node = _make_node_with_schema(schema)
        backend = AmplifierBackend(
            coordinator=MagicMock(
                get_capability=lambda _: None,
                config={"agents": {}},
                session=MagicMock(),
            ),
            profiles={},
            provider=MagicMock(),
            unified_client=mock_client,
        )

        outcome = await backend._run_with_tool_loop(
            node=node,
            instruction="Extract",
            reasoning_effort=None,
        )

        assert outcome.status == StageStatus.SUCCESS
        # JSON text is stored as notes
        assert outcome.notes == json_response
        # Parsed object stashed under node.id in context_updates
        assert outcome.context_updates is not None
        assert outcome.context_updates[node.id] == {"name": "Alice"}

    @pytest.mark.asyncio
    async def test_structured_output_with_invalid_json_still_succeeds(self) -> None:
        """If provider returns non-JSON text despite response_format, outcome is SUCCESS
        and raw text is preserved (the pipeline can route on it downstream)."""
        schema = _INLINE_SCHEMA_DICT
        bad_response = "NOT JSON"
        mock_client = _MockUnifiedClient(text=bad_response)

        node = _make_node_with_schema(schema)
        backend = AmplifierBackend(
            coordinator=MagicMock(
                get_capability=lambda _: None,
                config={"agents": {}},
                session=MagicMock(),
            ),
            profiles={},
            provider=MagicMock(),
            unified_client=mock_client,
        )

        outcome = await backend._run_with_tool_loop(
            node=node,
            instruction="Extract",
            reasoning_effort=None,
        )

        assert outcome.status == StageStatus.SUCCESS
        assert outcome.notes == bad_response
        # node.id key should NOT be present when JSON parse failed
        assert outcome.context_updates is not None
        assert node.id not in outcome.context_updates


# ---------------------------------------------------------------------------
# 7. Backend: spawn path FAIL-LOUD when response_schema is set
# ---------------------------------------------------------------------------


class TestBackendSpawnPathFail:
    @pytest.mark.asyncio
    async def test_spawn_path_fails_when_schema_set(self) -> None:
        """AmplifierBackend returns FAIL with clear message on spawn path + response_schema."""
        coordinator = _MockCoordinator(
            spawn_result={"output": "done", "session_id": "c-1"}
        )
        backend = AmplifierBackend(
            coordinator=coordinator,
            profiles={"anthropic": "attractor-anthropic"},
        )
        backend.ensure_spawn_resolved()

        node = _make_node_with_schema(_INLINE_SCHEMA_DICT, id="extract_spawn")
        outcome = await backend.run(node, "Extract the thing", _make_context())

        assert outcome.status == StageStatus.FAIL
        assert "response_schema" in (outcome.failure_reason or "")
        assert "spawned-agent" in (outcome.failure_reason or "")
        # Spawn should NOT have been called
        assert not coordinator.spawn_called

    @pytest.mark.asyncio
    async def test_spawn_path_ok_without_schema(self) -> None:
        """AmplifierBackend spawn path proceeds normally when no response_schema."""
        coordinator = _MockCoordinator(
            spawn_result={
                "output": json.dumps({"status": "success", "notes": "done"}),
                "session_id": "c-1",
            }
        )
        backend = AmplifierBackend(
            coordinator=coordinator,
            profiles={"anthropic": "attractor-anthropic"},
        )
        backend.ensure_spawn_resolved()

        node = _make_node_no_schema(id="work_spawn")
        outcome = await backend.run(node, "Do work", _make_context())

        assert outcome.status == StageStatus.SUCCESS
        assert coordinator.spawn_called


# ---------------------------------------------------------------------------
# 8. DirectProviderBackend.run wiring (via __init__.py)
# ---------------------------------------------------------------------------


class TestDirectProviderBackendWiring:
    """DirectProviderBackend.run (defined in __init__.py) also wires response_format."""

    @pytest.mark.asyncio
    async def test_direct_backend_passes_response_format(self) -> None:
        """When response_schema is set, DirectProviderBackend passes ResponseFormat."""
        from amplifier_module_loop_pipeline import DirectProviderBackend

        schema = _INLINE_SCHEMA_DICT
        json_response = json.dumps({"name": "Bob"})
        mock_client = _MockUnifiedClient(text=json_response)

        node = _make_node_with_schema(schema)
        backend = DirectProviderBackend(
            provider=MagicMock(),
            unified_client=mock_client,
        )

        outcome = await backend.run(node, "Extract please", _make_context())

        assert len(mock_client.requests) >= 1
        req = mock_client.requests[-1]
        assert req.response_format is not None
        assert req.response_format.type == "json_schema"
        assert req.response_format.json_schema == schema

        assert outcome.status == StageStatus.SUCCESS
        assert outcome.notes == json_response

    @pytest.mark.asyncio
    async def test_direct_backend_no_response_format_when_no_schema(self) -> None:
        """When response_schema is absent, DirectProviderBackend passes no response_format."""
        from amplifier_module_loop_pipeline import DirectProviderBackend

        json_response = json.dumps({"status": "success"})
        mock_client = _MockUnifiedClient(text=json_response)

        node = _make_node_no_schema()
        backend = DirectProviderBackend(
            provider=MagicMock(),
            unified_client=mock_client,
        )

        await backend.run(node, "Do work", _make_context())

        assert len(mock_client.requests) >= 1
        req = mock_client.requests[-1]
        assert req.response_format is None
