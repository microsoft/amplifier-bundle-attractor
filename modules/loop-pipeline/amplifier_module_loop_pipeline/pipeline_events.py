"""Pipeline event constants and helpers.

Defines the event names emitted by the pipeline engine at key execution
points.  The engine calls ``await hooks.emit(event_name, data)`` when a
hooks object is provided.

Spec coverage: EVT-001–008, Section 9.6.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Pipeline lifecycle
# ---------------------------------------------------------------------------
PIPELINE_START: str = "pipeline:start"
PIPELINE_COMPLETE: str = "pipeline:complete"

# ---------------------------------------------------------------------------
# Node lifecycle
# ---------------------------------------------------------------------------
PIPELINE_NODE_START: str = "pipeline:node_start"
PIPELINE_NODE_COMPLETE: str = "pipeline:node_complete"

# ---------------------------------------------------------------------------
# Edge selection
# ---------------------------------------------------------------------------
PIPELINE_EDGE_SELECTED: str = "pipeline:edge_selected"

# ---------------------------------------------------------------------------
# Checkpoint
# ---------------------------------------------------------------------------
PIPELINE_CHECKPOINT: str = "pipeline:checkpoint"

# ---------------------------------------------------------------------------
# Goal gates
# ---------------------------------------------------------------------------
PIPELINE_GOAL_GATE_CHECK: str = "pipeline:goal_gate_check"

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
PIPELINE_ERROR: str = "pipeline:error"

# ---------------------------------------------------------------------------
# Parallel execution (spec Section 9.6)
# ---------------------------------------------------------------------------
PIPELINE_PARALLEL_STARTED: str = "pipeline:parallel_started"
PIPELINE_PARALLEL_BRANCH_STARTED: str = "pipeline:parallel_branch_started"
PIPELINE_PARALLEL_BRANCH_COMPLETED: str = "pipeline:parallel_branch_completed"
PIPELINE_PARALLEL_COMPLETED: str = "pipeline:parallel_completed"

# ---------------------------------------------------------------------------
# Human interaction (spec Section 9.6)
# ---------------------------------------------------------------------------
PIPELINE_INTERVIEW_STARTED: str = "pipeline:interview_started"
PIPELINE_INTERVIEW_COMPLETED: str = "pipeline:interview_completed"
PIPELINE_INTERVIEW_TIMEOUT: str = "pipeline:interview_timeout"

# ---------------------------------------------------------------------------
# Retry lifecycle (spec Section 9.6)
# ---------------------------------------------------------------------------
PIPELINE_STAGE_RETRYING: str = "pipeline:stage_retrying"
PIPELINE_STAGE_FAILED: str = "pipeline:stage_failed"

# ---------------------------------------------------------------------------
# Provider-level events (LLM call observability)
# ---------------------------------------------------------------------------
PROVIDER_REQUEST: str = "provider:request"
PROVIDER_RESPONSE: str = "provider:response"
PROVIDER_ERROR: str = "provider:error"

# ---------------------------------------------------------------------------
# Subgraph execution (nested pipeline nodes)
# ---------------------------------------------------------------------------
PIPELINE_SUBGRAPH_START: str = "pipeline:subgraph_start"
PIPELINE_SUBGRAPH_COMPLETE: str = "pipeline:subgraph_complete"

# ---------------------------------------------------------------------------
# R12 M3: Node failure propagation (skip + contract violation)
# ---------------------------------------------------------------------------

#: Emitted when the engine skips a node because at least one of its
#: referenced context keys was produced by a failed/skipped predecessor.
#:
#: Payload fields:
#:   node_id                     — ID of the skipped node
#:   cause                       — always "predecessor_failed"
#:   references                  — list of {key, producer_node_id} dicts
#:   missing_keys                — list of key names that were missing
#:   failure_mode                — always "predecessor_failed" (D1 taxonomy)
#:   failure_mode_taxonomy_version — always 1 (CR-4)
PIPELINE_NODE_SKIPPED: str = "PIPELINE_NODE_SKIPPED"

#: Emitted when a producer node succeeded but did not emit all of its
#: declared ``outputs=`` keys (pipeline-author contract violation).
#:
#: Payload fields:
#:   node_id                     — ID of the producer node
#:   declared                    — list of declared output keys
#:   emitted                     — list of keys actually written to context
#:   missing                     — list of declared keys that were not emitted
#:   failure_mode                — always "software" (D1 taxonomy)
#:   failure_mode_taxonomy_version — always 1 (CR-4)
PIPELINE_NODE_CONTRACT_VIOLATION: str = "PIPELINE_NODE_CONTRACT_VIOLATION"
