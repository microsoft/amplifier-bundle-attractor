"""AC-4 guard: a fresh run is INERT to any checkpoint that happens to exist.

The regression this exists to prevent is concrete. The pre-#66 implementation
called a checkpoint loader implicitly from ``run()``, so its graph-identity
guard executed on runs that never asked to resume; the resulting
CheckpointMismatchError forced a downstream repository to delete
``checkpoint.json`` between runs to escape it. That removal is what made
checkpoints observability-only.

The fix is not a better guard — it is the absence of the call. Two levels of
proof:

  (a) CONSTRUCTION: ``engine.py`` contains no reference to any checkpoint
      loader at all. ``PipelineEngine.resume()`` takes an ALREADY-LOADED,
      already-validated Checkpoint; the engine cannot read one even if a
      future edit wanted it to. Inertness is a property of the call graph.

  (b) BEHAVIOR: a stale, foreign, corrupt, or v1 ``checkpoint.json`` planted
      where a fresh run can see it produces records identical to a clean-dir
      control run's, and is simply overwritten.
"""

import ast
import json
from pathlib import Path
from typing import Any

import pytest

from amplifier_module_loop_pipeline.context import PipelineContext
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.engine import PipelineEngine
from amplifier_module_loop_pipeline.graph import Node
from amplifier_module_loop_pipeline.handlers import HandlerRegistry
from amplifier_module_loop_pipeline.handlers.context import HandlerContext
from amplifier_module_loop_pipeline.validation import validate_or_raise

ENGINE_PATH = (
    Path(__file__).resolve().parents[1]
    / "amplifier_module_loop_pipeline"
    / "engine.py"
)

LOADER_NAMES = {"load_checkpoint", "load_checkpoint_for_resume"}

DOT = """
digraph fresh {
    start [shape=Mdiamond]
    a [prompt="A"]
    b [prompt="B"]
    exit [shape=Msquare]
    start -> a -> b -> exit
}
"""


class RecordingBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def run(
        self,
        node: Node,
        prompt: str,
        context: PipelineContext,
        incoming_edge=None,
        graph=None,
    ) -> str:
        self.calls.append(node.id)
        return "ok"


def _engine(logs_root, backend) -> PipelineEngine:
    graph = parse_dot(DOT)
    validate_or_raise(graph)
    return PipelineEngine(
        graph=graph,
        context=PipelineContext(),
        handler_registry=HandlerRegistry(HandlerContext(backend=backend)),
        logs_root=str(logs_root),
    )


# ---------------------------------------------------------------------------
# (a) Construction-level: the engine cannot load a checkpoint
# ---------------------------------------------------------------------------


def test_engine_module_never_references_a_checkpoint_loader():
    tree = ast.parse(ENGINE_PATH.read_text())

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)

    assert not (imported & LOADER_NAMES), (
        f"engine.py imports a checkpoint loader ({imported & LOADER_NAMES}). "
        "The engine must never load a checkpoint: resume() takes an "
        "already-validated Checkpoint from the explicit entry point. This is "
        "the guard that keeps a fresh run inert by construction."
    )

    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    } | {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not (called & LOADER_NAMES), (
        f"engine.py calls a checkpoint loader ({called & LOADER_NAMES})."
    )


def test_run_never_reaches_resume_or_a_loader():
    """Walk run()'s in-module call graph; nothing resume-flavored is reachable."""
    tree = ast.parse(ENGINE_PATH.read_text())
    cls = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "PipelineEngine"
    )
    methods = {
        n.name: n
        for n in cls.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    seen: set[str] = set()
    frontier = ["run"]
    while frontier:
        name = frontier.pop()
        if name in seen or name not in methods:
            continue
        seen.add(name)
        for call in ast.walk(methods[name]):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                if (
                    isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "self"
                ):
                    frontier.append(call.func.attr)

    assert "resume" not in seen, "run() must never reach resume()"
    assert not (seen & LOADER_NAMES)
    # Sanity: the walk actually explored something real.
    assert "_run_loop" in seen and "_save_checkpoint" in seen


# ---------------------------------------------------------------------------
# (b) Behavioral: a planted checkpoint changes nothing
# ---------------------------------------------------------------------------


def _foreign_v2_checkpoint() -> dict[str, Any]:
    return {
        "current_node": "some_other_graphs_node",
        "completed_nodes": ["some_other_graphs_node"],
        "context": {"graph.goal": "a completely different run"},
        "timestamp": "2020-01-01T00:00:00Z",
        "node_retries": {"some_other_graphs_node": 7},
        "logs": ["from another run entirely"],
        "schema_version": 2,
        "run_state": "in_flight",
        "node_outcomes": {
            "some_other_graphs_node": {"status": "success", "is_explicit": True}
        },
        "engine_state": {"iteration_count": 99, "node_execution_counts": {"x": 5}},
        "graph": {"fingerprint": "sha256:" + "0" * 64, "dot_source": "digraph {}"},
    }


PLANTED = {
    "foreign_v2": lambda: json.dumps(_foreign_v2_checkpoint()),
    "stale_v1": lambda: json.dumps(
        {
            "current_node": "b",
            "completed_nodes": ["start", "a", "b"],
            "context": {"graph.goal": "old goal"},
            "timestamp": "2020-01-01T00:00:00Z",
            "node_retries": {},
            "logs": [],
        }
    ),
    "corrupt": lambda: '{"current_node": "a", "completed_no',
    "empty": lambda: "",
}


@pytest.mark.parametrize("kind", sorted(PLANTED))
@pytest.mark.asyncio
async def test_fresh_run_is_inert_to_a_planted_checkpoint(tmp_path, kind):
    control_logs = tmp_path / "control"
    planted_logs = tmp_path / "planted"
    control_logs.mkdir()
    planted_logs.mkdir()
    (planted_logs / "checkpoint.json").write_text(PLANTED[kind]())

    control_backend = RecordingBackend()
    control_outcome = await _engine(control_logs, control_backend).run()

    planted_backend = RecordingBackend()
    planted_outcome = await _engine(planted_logs, planted_backend).run()

    # Same work, same result — the planted file did not fail the run either.
    assert planted_outcome.status == control_outcome.status
    assert planted_backend.calls == control_backend.calls == ["a", "b"]

    def _records(root: Path) -> list[dict]:
        return [
            {k: v for k, v in json.loads(ln).items() if k not in ("ts", "duration_ms")}
            for ln in (root / "trace.jsonl").read_text().splitlines()
            if ln.strip()
        ]

    assert _records(planted_logs) == _records(control_logs)

    def _checkpoint(root: Path) -> dict:
        data = json.loads((root / "checkpoint.json").read_text())
        data.pop("timestamp", None)
        return data

    # The planted file was simply overwritten by the run that owns the dir.
    assert _checkpoint(planted_logs) == _checkpoint(control_logs)
