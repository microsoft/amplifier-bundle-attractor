"""Dashboard Query Tool Module for Amplifier.

Allows an agent to query and manage Attractor pipelines via the dashboard
HTTP API. Supports listing pipelines, checking status, submitting new
pipelines, cancelling, and interacting with human approval gates.
"""

# Amplifier module metadata
__amplifier_module_type__ = "tool"

import logging
from typing import Any

import httpx

__all__ = ["DashboardQueryTool", "mount"]

logger = logging.getLogger(__name__)

# Operations that require specific parameters
_REQUIRES_PIPELINE_ID = frozenset(
    {"get_pipeline", "get_node", "cancel_pipeline", "get_questions", "answer_question"}
)
_REQUIRES_NODE_ID = frozenset({"get_node"})
_REQUIRES_DOT_SOURCE = frozenset({"submit_pipeline"})
_REQUIRES_QUESTION_ID = frozenset({"answer_question"})
_REQUIRES_ANSWER = frozenset({"answer_question"})

VALID_OPERATIONS = frozenset(
    {
        "list_pipelines",
        "get_pipeline",
        "get_node",
        "submit_pipeline",
        "cancel_pipeline",
        "get_questions",
        "answer_question",
    }
)

# Canonical, stable ordering of VALID_OPERATIONS, read by BOTH the schema
# `enum` and the error-message join below so they can never drift apart
# (S4 -- partial-coverage symmetry; not a live bug here, since `sorted()`
# over a frozenset is already deterministic -- this closes the structural
# recurrence, not a live cache-invalidation bug). A tuple, not a list, so
# the shared source of truth can't be mutated through a consumer. Do NOT
# reorder and do NOT inline `sorted(VALID_OPERATIONS)` at either consumer
# again. Full story: docs/designs/RECURRING-BUG-CLASSES.md (S4).
# Regression guard: test_schema_serialization_is_deterministic_across_processes.
_OPERATIONS_SORTED: tuple[str, ...] = tuple(sorted(VALID_OPERATIONS))


class DashboardQueryTool:
    """Query and manage Attractor pipelines via the dashboard HTTP API.

    Provides a single tool with an ``operation`` parameter that dispatches
    to the appropriate API endpoint. The HTTP client is created lazily on
    the first call.
    """

    name = "dashboard_query"
    description = (
        "Query and manage Attractor pipelines via the dashboard HTTP API. "
        "Supports listing pipelines, checking status, submitting new pipelines, "
        "cancelling, and interacting with human gates."
    )

    def __init__(self, config: dict[str, Any] | None = None, coordinator: Any = None):
        """Initialize DashboardQueryTool.

        Args:
            config: Module configuration. Supports ``dashboard_url`` key
                (default ``http://localhost:8050``).
            coordinator: Optional module coordinator.
        """
        self.config = config or {}
        self.coordinator = coordinator
        self._base_url: str = self.config.get("dashboard_url", "http://localhost:8050")
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        """Return the shared httpx client, creating it on first use."""
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client, releasing connections."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def input_schema(self) -> dict:
        """Return JSON schema for tool parameters."""
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": list(_OPERATIONS_SORTED),
                    "description": "Operation to perform",
                },
                "pipeline_id": {
                    "type": "string",
                    "description": (
                        "Pipeline ID (required for get_pipeline, get_node, "
                        "cancel_pipeline, get_questions, answer_question)"
                    ),
                },
                "node_id": {
                    "type": "string",
                    "description": "Node ID (required for get_node)",
                },
                "dot_source": {
                    "type": "string",
                    "description": "DOT digraph source (required for submit_pipeline)",
                },
                "goal": {
                    "type": "string",
                    "description": "Pipeline goal (optional for submit_pipeline)",
                },
                "question_id": {
                    "type": "string",
                    "description": "Question ID (required for answer_question)",
                },
                "answer": {
                    "type": "string",
                    "description": "Answer text (required for answer_question)",
                },
            },
            "required": ["operation"],
        }

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require(input: dict, field: str) -> str | None:
        """Return an error message if *field* is missing from *input*."""
        value = input.get(field)
        if not value:
            return f"'{field}' is required for this operation"
        return None

    # ------------------------------------------------------------------
    # Execute
    # ------------------------------------------------------------------

    async def execute(self, input: dict[str, Any]) -> Any:
        """Execute a dashboard query operation.

        Args:
            input: Parameters including required ``operation`` field.

        Returns:
            ToolResult with the JSON response from the dashboard API.
        """
        from amplifier_core import ToolResult

        operation = input.get("operation")
        if not operation:
            error_msg = "'operation' parameter is required"
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )

        if operation not in VALID_OPERATIONS:
            error_msg = (
                f"Invalid operation: {operation!r}. "
                f"Must be one of: {', '.join(_OPERATIONS_SORTED)}"
            )
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )

        # Validate required fields per operation
        for field, ops in [
            ("pipeline_id", _REQUIRES_PIPELINE_ID),
            ("node_id", _REQUIRES_NODE_ID),
            ("dot_source", _REQUIRES_DOT_SOURCE),
            ("question_id", _REQUIRES_QUESTION_ID),
            ("answer", _REQUIRES_ANSWER),
        ]:
            if operation in ops:
                err = self._require(input, field)
                if err:
                    return ToolResult(success=False, output=err, error={"message": err})

        # Dispatch
        try:
            client = self._get_client()
            pid = input.get("pipeline_id", "")
            nid = input.get("node_id", "")
            qid = input.get("question_id", "")

            if operation == "list_pipelines":
                resp = await client.get("/api/pipelines")
            elif operation == "get_pipeline":
                resp = await client.get(f"/api/pipelines/{pid}")
            elif operation == "get_node":
                resp = await client.get(f"/api/pipelines/{pid}/nodes/{nid}")
            elif operation == "submit_pipeline":
                body: dict[str, Any] = {"dot_source": input["dot_source"]}
                if input.get("goal"):
                    body["goal"] = input["goal"]
                resp = await client.post("/api/pipelines", json=body)
            elif operation == "cancel_pipeline":
                resp = await client.post(f"/api/pipelines/{pid}/cancel")
            elif operation == "get_questions":
                resp = await client.get(f"/api/pipelines/{pid}/questions")
            elif operation == "answer_question":
                resp = await client.post(
                    f"/api/pipelines/{pid}/questions/{qid}/answer",
                    json={"answer": input["answer"]},
                )
            else:  # pragma: no cover
                error_msg = f"Unhandled operation: {operation!r}"
                return ToolResult(
                    success=False, output=error_msg, error={"message": error_msg}
                )

            resp.raise_for_status()
            data = resp.json()
            return ToolResult(success=True, output=data)

        except httpx.HTTPStatusError as exc:
            error_msg = f"HTTP {exc.response.status_code}: {exc.response.text}"
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )
        except httpx.HTTPError as exc:
            error_msg = f"HTTP error: {exc}"
            return ToolResult(
                success=False, output=error_msg, error={"message": error_msg}
            )


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the dashboard_query tool.

    Args:
        coordinator: Module coordinator for registering tools.
        config: Module configuration.
    """
    config = config or {}
    tool = DashboardQueryTool(config, coordinator)
    await coordinator.mount("tools", tool, name=tool.name)
    logger.info("Mounted dashboard_query tool")
