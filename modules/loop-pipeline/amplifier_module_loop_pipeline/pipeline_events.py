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

#: Emitted when a node finishes execution (success or failure).
#:
#: Payload fields (always present):
#:   node_id          — ID of the node
#:   status           — StageStatus value string ("success", "fail", etc.)
#:   duration_ms      — wall-clock milliseconds for the handler
#:   notes            — optional human-readable notes from the handler
#:   failure_reason   — optional short failure description string
#:   session_id       — optional child Amplifier session ID (backend nodes)
#:   execution_index  — graph-level visit count for this node
#:
#: Issue 10 / analog of WS-4 Sub-fix C — field added for tool-node failures:
#:   failed_step      — structured tool-invocation payload (None for success
#:                      or non-tool nodes).  Shape when present:
#:     command        — resolved shell command (≤500 chars)
#:     exit_code      — subprocess return code
#:     duration_s     — wall-clock seconds for the subprocess
#:     stdout_tail    — stdout output (empty string when stdout was empty)
#:     stderr_tail    — stderr output (empty string when stderr was empty)
#:     verification_gap.log_filtered — True when 8 KiB cap truncated the payload
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
# Resume (spec §5.3 "Resume behavior")
# ---------------------------------------------------------------------------

#: Emitted once by ``PipelineEngine.resume()``, after the checkpoint has passed
#: the whole validation ladder and engine state has been restored, immediately
#: before the walk continues.  This is the run's own durable record that a
#: resume happened at all.
#:
#: Payload fields:
#:   checkpoint_node   — the checkpoint's current_node (LAST COMPLETED node)
#:   completed_count   — number of nodes restored as already complete
#:   iteration_count   — restored $iteration / loop_restart cycle number
#:   fidelity_degrade_armed — True when the next executed node will be checked
#:                            for the one-shot §5.3 rule-6 degrade
PIPELINE_RESUME: str = "pipeline:resume"

#: Emitted when the first node executed after a resume resolves to
#: ``fidelity=full`` and is therefore capped to ``summary:high`` for that one
#: hop (spec §5.3 rule 6 — in-memory LLM sessions cannot be serialized, so the
#: transcript that ``full`` would replay does not survive the process boundary).
#: Subsequent nodes may resolve ``full`` again and emit nothing.
#:
#: Payload fields:
#:   node_id — the single degraded hop
#:   from    — always "full"
#:   to      — always "summary:high"
PIPELINE_RESUME_FIDELITY_DEGRADE: str = "pipeline:resume_fidelity_degrade"


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
# Emitted when a node's llm_model family-token/glob is resolved to a concrete
# served model id. Records {raw, resolved, provider, pattern} for audit /
# eval reproducibility. Fires once per distinct resolution per run.
MODEL_RESOLVED: str = "model:resolved"

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
