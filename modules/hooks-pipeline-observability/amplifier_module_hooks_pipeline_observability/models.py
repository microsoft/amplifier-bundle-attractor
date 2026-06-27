"""Pipeline observability data model.

The PipelineRunState is the centerpiece of the observability system.
Every consumer reads from it: the status bar hook, the progress hook,
the query tool, and future REST API endpoints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class NodeInfo:
    """Static information about a graph node."""

    id: str
    label: str = ""
    shape: str = "box"
    type: str = ""
    prompt: str = ""


@dataclass
class EdgeInfo:
    """Static information about a graph edge."""

    from_node: str
    to_node: str
    label: str = ""
    condition: str = ""
    weight: int = 0


@dataclass
class NodeRun:
    """A single execution of a node (supports retries — multiple runs per node)."""

    status: str  # "running" | "success" | "fail" | "timeout" | "partial_success"
    attempt: int  # 1-based
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int = 0
    outcome_notes: str | None = None
    llm_calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0


@dataclass
class EdgeDecision:
    """Record of an edge routing decision at a node."""

    from_node: str
    evaluated_edges: list[Any]  # all candidate edges with conditions + match result
    selected_edge: EdgeInfo
    reason: str  # "condition matched" | "weight priority" | "default"


@dataclass
class GoalGateCheck:
    """Record of a goal gate evaluation at an exit node."""

    timestamp: datetime
    satisfied: list[str]  # node IDs that passed
    unsatisfied: list[str]  # node IDs that failed
    action: str  # "complete" | "retry" | "fail"


@dataclass
class BranchInfo:
    """A single branch in a parallel fan-out."""

    branch_id: str
    target_node: str
    status: str  # "pending" | "running" | "success" | "fail"
    started_at: datetime
    completed_at: datetime | None = None
    duration_ms: int = 0
    outcome_notes: str | None = None


@dataclass
class HumanInteraction:
    """Record of a human gate interaction."""

    node_id: str
    question: str
    options: list[str]
    selected: str
    wait_time_ms: int = 0


@dataclass
class SupervisorCycle:
    """Record of a manager-supervisor observe/steer/wait cycle."""

    cycle_number: int
    observation: str
    steering_message: str
    wait_result: str


@dataclass
class PipelineRunState:
    """Comprehensive pipeline execution state.

    Maintained by the state aggregator hook, updated on every pipeline event.
    This is the centerpiece that every consumer reads from.
    """

    # Identity
    pipeline_id: str
    dot_source: str
    goal: str

    # Graph structure (populated from pipeline:start)
    nodes: dict[str, NodeInfo] = field(default_factory=dict)
    edges: list[EdgeInfo] = field(default_factory=list)

    # Execution progress
    status: str = "pending"  # "pending" | "running" | "complete" | "failed"
    current_node: str | None = None
    execution_path: list[str] = field(default_factory=list)
    branches_taken: list[EdgeInfo] = field(default_factory=list)

    # Per-node execution detail (supports retries/loops — list per node)
    node_runs: dict[str, list[NodeRun]] = field(default_factory=dict)

    # Edge routing decisions (for conditional visualization)
    edge_decisions: list[EdgeDecision] = field(default_factory=list)

    # Loop/retry tracking
    loop_iterations: dict[str, int] = field(default_factory=dict)
    goal_gate_checks: list[GoalGateCheck] = field(default_factory=list)

    # Parallel execution tracking
    parallel_branches: dict[str, list[BranchInfo]] = field(default_factory=dict)

    # Subgraph execution (recursive — for nested DOT subgraphs)
    subgraph_runs: dict[str, Any] = field(default_factory=dict)

    # Model token/glob resolutions: raw llm_model pattern -> concrete served id
    resolved_models: dict[str, str] = field(default_factory=dict)

    # Human gate interactions
    human_interactions: list[HumanInteraction] = field(default_factory=list)

    # Manager-supervisor cycles
    supervisor_cycles: dict[str, list[SupervisorCycle]] = field(default_factory=dict)

    # Aggregate metrics
    total_elapsed_ms: int = 0
    total_llm_calls: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    total_tokens_cached: int = 0
    total_tokens_reasoning: int = 0
    nodes_completed: int = 0
    nodes_total: int = 0

    # Per-node timing breakdown
    timing: dict[str, int] = field(default_factory=dict)

    # Error info
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dictionary.

        Converts datetime objects to ISO format strings.
        Converts dataclass instances to plain dicts recursively.
        """
        return _serialize(self)


def _serialize(obj: Any) -> Any:
    """Recursively serialize dataclass instances and datetimes to JSON-safe types."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dataclass_fields__"):
        result = {}
        for field_name in obj.__dataclass_fields__:
            result[field_name] = _serialize(getattr(obj, field_name))
        return result
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    return obj
