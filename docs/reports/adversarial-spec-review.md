# Adversarial Spec Review: Attractor Implementation vs NLSpec

**Date:** 2026-02-10
**Reviewers:** Three independent adversarial agents (coding-agent-loop-spec reviewer, attractor-spec reviewer, integration reviewer)
**Scope:** All source code in `amplifier-bundle-attractor/modules/` vs all three nlspec files in `specs/`
**Methodology:** Every MUST requirement traced to implementation file and line. Findings categorized as MISSING, PARTIAL, INCORRECT, UNTESTED, or DEVIATION.

---

> **STATUS — Historical (2026-02-10):** Many findings since resolved — H-11 fixed PR #36; fidelity fixed PR #42/#46; C-1 fixed PR #41. Preserved for architectural history.

---

## Executive Summary

The implementation has strong architectural foundations — 886 unit tests, real API calls proven across 2 providers, DOT parsing and pipeline traversal working. But the adversarial review found **70 total findings** (10 CRITICAL, 14 HIGH, 24 MEDIUM, 22 LOW) across three review dimensions:

| Spec | Implemented | Partial | Missing |
|---|---|---|---|
| coding-agent-loop-spec (155 requirements) | ~44% | ~17% | ~39% |
| attractor-spec (281 requirements) | ~55% | ~20% | ~25% |
| End-to-end integration | Agent loop works | Direct tool loop works | Spawn-based pipeline unproven |

**Three systemic themes:**
1. "Sessions all the way down" is architecturally sound but unproven end-to-end
2. The agent loop is ~60% of the spec — core works but many features partial/missing
3. Pipeline orchestration works for simple graphs but complex patterns have structural gaps

---

## CRITICAL Findings (10)

### C-1: `profiles={}` — Pipeline Can Never Route to the Right Provider Profile

**Source:** Integration reviewer
**Location:** `loop-pipeline/__init__.py:183-188`, consumed at `backend.py:114-116`
**Spec:** Pipeline should read `llm_provider` from each DOT node and spawn a child session with the matching provider profile.
**Implementation:** `_build_backend()` always constructs `AmplifierBackend(profiles={})`. The profiles dict is hardcoded empty. Every `session.spawn` call sends `agent_name=""`. There is no config pathway to populate this dict.
**Impact:** Per-node provider switching — a core architectural differentiator — is dead code. Every spawn attempt either crashes or creates a session with no profile.

### C-2: Three Incompatible Spawn Contracts

**Source:** Integration reviewer
**Locations:** `backend.py:204-229`, `subagent_tools.py:295`, `tool-delegate/__init__.py:853`
**Spec:** "Sessions all the way down" — pipeline spawns agent sessions, agents can spawn sub-agents.
**Implementation:** Three consumers call spawn with different signatures:

| Dimension | AmplifierBackend | SubagentManager | tool-delegate |
|---|---|---|---|
| Access | `get_capability("session.spawn")` | `coordinator.spawn()` | `get_capability("session.spawn")` |
| Return type | `dict` with output/session_id | `str` (plain text) | `dict` with output/session_id |
| Extra kwargs | agent_configs, orchestrator_config | Just instruction | tool_inheritance, hook_inheritance |

**Impact:** At least one consumer will crash at runtime due to return type mismatch.

### C-3: Spawn Path Has Zero Real E2E Coverage

**Source:** Integration reviewer
**Spec:** Pipeline spawns full agent sessions for each codergen node.
**Implementation:** All E2E tests use `DirectProviderBackend` (fallback). Zero tests exercise: pipeline → AmplifierBackend → real `session.spawn` → child loop-agent → real provider → tool execution → result propagation back to pipeline.
**Impact:** The intended architecture has never been run end-to-end.

### C-4: No Tool Output Truncation Wired Into Agent Loop

**Source:** Agent reviewer + Integration reviewer
**Spec Section 5:** Two-pass truncation (character then line) with per-tool limits. TOOL_CALL_END carries full output, LLM receives truncated.
**Implementation:** `hooks-tool-truncation` module exists with 34 tests, but no test wires it into an `AgentSession` and verifies the `tool:post` event fires and modifies output before it reaches the provider.
**Impact:** A single large file read (>50KB) can blow out the context window. Silent failure — no crash, just ballooning costs and eventual context length errors.

### C-5: No Abort Signal

**Source:** Agent reviewer
**Spec Section 2.4:** "The host can signal cancellation at any time. The current LLM stream is closed, running processes killed, session → CLOSED."
**Implementation:** `AgentSession` has `shutdown()` but no `abort()` method. No mechanism for the host to interrupt a running `process_input()` call. No `CancellationToken` or `asyncio.Event` checked during the loop.
**Impact:** Runaway agents cannot be stopped. Long tool executions or LLM calls block until natural completion.

### C-6: No Tool Argument Validation

**Source:** Agent reviewer
**Spec Section 4.1, Step 2:** "VALIDATE arguments against the tool's JSON Schema."
**Implementation:** Tool arguments from the LLM are passed directly to `tool.execute()` without schema validation. Invalid arguments reach the tool's execute method unchecked.
**Impact:** Malformed tool arguments cause unpredictable tool behavior instead of clean error messages back to the LLM.

### C-7: AWAITING_INPUT State Is Dead Code

**Source:** Agent reviewer
**Spec Section 2.3:** State machine includes PROCESSING → AWAITING_INPUT when model asks a question (text-only, question-like response).
**Implementation:** `SessionState.AWAITING_INPUT` exists in the enum and transition table, but no code path ever triggers the `await_input()` transition. When the model responds with text only (no tool calls), the session always goes IDLE, never AWAITING_INPUT.
**Impact:** The host application can never distinguish between "agent completed" and "agent is asking a question."

### C-8: Streaming Drops Reasoning Content

**Source:** Agent reviewer
**Location:** `agent_session.py` streaming path
**Spec:** Response captures `reasoning` field from LLM output.
**Implementation:** The streaming path (`_call_provider_streaming`) assembles text from chunks but does not capture ThinkingBlock content. The `_extract_reasoning()` helper is only called in the non-streaming path.
**Impact:** For thinking-enabled models (Claude with extended thinking, OpenAI o-series), reasoning content is silently lost during streaming.

### C-9: No ProviderProfile / Tool Registry

**Source:** Agent reviewer
**Spec Section 3:** ProviderProfile interface with `tool_registry`, `build_system_prompt()`, `tools()`, `provider_options()`, capability flags.
**Implementation:** No ProviderProfile class exists. Tools come from whatever the bundle mounts. System prompt assembly is hardcoded in `agent_session.py`. No capability flags (`supports_parallel_tool_calls`, `context_window_size`).
**Impact:** The agent can't adapt behavior based on provider capabilities. All providers treated identically regardless of their actual features.

### C-10: 5 of 13 Validation Lint Rules Missing

**Source:** Pipeline reviewer
**Spec Section 7.2:** 13 lint rules defined, including 2 at ERROR level.
**Implementation:** 8 rules implemented. Missing: `condition_syntax` (ERROR), `stylesheet_syntax` (ERROR), `type_known` (WARNING), `fidelity_valid` (WARNING), `retry_target_exists` (WARNING).
**Impact:** Invalid condition expressions and stylesheet syntax pass validation and crash at runtime.

---

## HIGH Findings (14)

### H-1: SESSION_END Timing Wrong

**Location:** `agent_session.py`
**Spec:** SESSION_END emitted only when BOTH the loop exits AND the follow-up queue is empty.
**Implementation:** SESSION_END emitted before follow-up processing.
**Impact:** Host apps get premature completion signal.

### H-2: No ExecutionEnvironment Abstraction

**Spec Section 4:** Abstract `ExecutionEnvironment` interface with `read_file()`, `write_file()`, `exec_command()`, etc.
**Implementation:** Tools execute directly on the local filesystem. No abstraction layer for Docker/K8s/WASM/SSH.
**Impact:** Cannot run agents in isolated execution environments.

### H-3: Forced Parallel Tool Execution

**Spec Section 4.1:** Parallel only when `provider_profile.supports_parallel_tool_calls` is true.
**Implementation:** Always uses `asyncio.gather()` for multiple tool calls regardless of provider.
**Impact:** Providers that don't support parallel tool calls may receive results in unexpected order.

### H-4: `loop_restart` Edge Attribute Unimplemented

**Spec Section 3.2, Step 7:** "IF next_edge has loop_restart=true: restart_run()"
**Location:** `engine.py:244-272`
**Implementation:** After edge selection, engine just advances to next node. No check for `loop_restart`, no restart logic.
**Impact:** Cycle-based pipeline patterns silently don't work.

### H-5: Manager Loop Wrong Architecture

**Spec Section 4.11:** Manager loads `stack.child_dotfile` and runs a separate child pipeline.
**Implementation:** `manager_loop.py:103-116` runs a subgraph of the SAME graph via outgoing edges.
**Impact:** The supervisor pattern is fundamentally different from spec — re-runs part of the same graph instead of a separate pipeline.

### H-6: Per-Node Failure Routing Missing Outside Goal Gates

**Spec Section 3.7:** On FAIL: try fail edge → retry_target → fallback_retry_target → terminate.
**Location:** `engine.py:244-260`
**Implementation:** On `edge is None`, immediately returns FAIL. Does NOT check retry_target or fallback_retry_target.
**Impact:** Nodes that fail and have no matching edge terminate the pipeline even if they have retry targets configured.

### H-7: Transform Ordering Wrong

**Spec Section 9.1:** "Transforms applied after parsing and before validation."
**Implementation:** `__init__.py:243-246` validates FIRST, then `engine.py:94-95` applies transforms during `run()`. Order is parse → validate → execute(transform → ...) instead of parse → transform → validate → execute.
**Impact:** A stylesheet that fixes an invalid model config would still fail validation.

### H-8: Multi-Class Stylesheet Matching Broken

**Spec Section 2.12:** Classes are comma-separated, selectable individually with dot-prefix.
**Location:** `stylesheet.py:174-175`
**Implementation:** `sel[1:] == node_class` does exact string equality. `.code` won't match `class="code,critical"`.
**Impact:** Multi-class nodes can never be matched by individual class selectors.

### H-9: Fidelity Not Wired Into Engine

**Spec Section 5.4:** Fidelity resolution at each node transition.
**Implementation:** Engine never calls `resolve_fidelity()`. Only works in `AmplifierBackend` path, which has zero E2E coverage (see C-3).
**Impact:** In the working code path (DirectProviderBackend), fidelity is completely bypassed.

### H-10: Pipeline Events Missing Entire Categories

**Spec Section 9.6:** 14+ event types across 4 categories.
**Implementation:** 8 event constants. Missing all parallel events (4), human events (3), retry-specific events (2).
**Impact:** No observability for parallel execution, human interaction, or retry attempts.

### H-11: `subgraph_runner` Never Wired

**Location:** `handlers/__init__.py:62-73`
**Implementation:** `HandlerRegistry` constructed with only `backend=`. `ParallelHandler` and `ManagerLoopHandler` read `kwargs.get("subgraph_runner")` which is always `None`.
**Impact:** Parallel and manager-loop nodes cannot execute subgraphs. Silently fail.

### H-12: Standalone Module Repos Diverged From Bundle Copies

**Location:** `amplifier-module-loop-agent/` vs `amplifier-bundle-attractor/modules/loop-agent/`
**Implementation:** Bundle copy has 713-line agent_session.py with streaming, provider resolution, 5-layer prompt. Standalone has 499 lines without any of that. Standalone is missing subagent_tools.py entirely.
**Impact:** Anyone installing from standalone git URL gets a broken, 214-line-shorter version.

### H-13: E2E Profile Missing System Prompt Context

**Location:** `profiles/attractor-e2e-anthropic.yaml`
**Implementation:** No `context:` section. E2E tests run with hardcoded fallback: "You are a coding agent." (5 words) instead of the 65-line provider-aligned prompt.
**Impact:** E2E tests don't validate the real system prompt works correctly.

### H-14: Pipeline E2E Tests Don't Exercise DOT Fixtures

**Location:** `tests/e2e/run_e2e.sh`
**Implementation:** All 3 bash E2E tests use `--mode single` (agent-only). The pipeline profile and DOT fixtures exist but are never invoked by any E2E test.
**Impact:** Pipeline orchestration never tested end-to-end by the test infrastructure.

---

## MEDIUM Findings (24)

| # | Finding | Location | Issue |
|---|---|---|---|
| M-1 | Missing response_id capture | agent_session.py | AssistantTurn.response_id always None |
| M-2 | Loop detection message format wrong | loop_detection.py | Different text than spec's exact wording |
| M-3 | Missing TOOL_CALL_OUTPUT_DELTA event | events.py | No incremental tool output streaming |
| M-4 | Config max_tool_rounds default 200 but spec says per-provider | config.py | Single default vs per-provider |
| M-5 | No ToolRegistry class | agent_session.py | Tools accessed via dict, not registry |
| M-6 | Missing env-var filtering in tool execution | agent_session.py | Env vars not scrubbed in shell output |
| M-7 | Shallow shutdown (no SIGTERM to running processes) | agent_session.py | Only sets state, doesn't kill processes |
| M-8 | No `model` field on spawn_agent tool | subagent_tools.py | Can't override model per subagent |
| M-9 | DOT parser more permissive than spec (accepts space-separated attrs) | dot_parser.py | Commas not enforced between attributes |
| M-10 | Node attributes not first-class fields (12 of 17 in generic dict) | graph.py | Loses type safety and defaults |
| M-11 | Terminal node allows multiple exits (spec says exactly one) | validation.py | Validates >=1 instead of ==1 |
| M-12 | Human handler returns preferred_label instead of suggested_next_ids | human.py | Different routing mechanism than spec |
| M-13 | Human handler SKIPPED returns SUCCESS instead of FAIL | human.py | Spec says SKIPPED → FAIL |
| M-14 | Manager loop default max_cycles 10 vs spec's 1000 | manager_loop.py | 100x lower than spec default |
| M-15 | Manager loop steering is superficial (context key, not file write) | manager_loop.py | Spec says write to stage directory |
| M-16 | Tool handler ignores node timeout attribute | tool.py | Commands run without timeout |
| M-17 | Fan-in handler has no LLM-based evaluation path | fan_in.py | Always heuristic, never LLM judge |
| M-18 | Retry doesn't classify errors as retryable/terminal | retry.py | All exceptions retried equally |
| M-19 | Status.json fields don't match spec (status vs outcome, missing fields) | codergen.py, engine.py | External tools expecting spec format break |
| M-20 | No formal Transform interface (custom transforms impossible) | transforms.py | Hardcoded two transforms only |
| M-21 | Shape-name selectors missing from stylesheet | stylesheet.py | Only *, .class, #id — no bare shape |
| M-22 | Fidelity mode validation silently degrades (typos become compact) | fidelity.py | No warning on invalid mode |
| M-23 | Checkpoint resume doesn't degrade fidelity (spec says full → summary:high) | engine.py | Crash-resume tries full fidelity with no session |
| M-24 | Missing ConsoleInterviewer and RecordingInterviewer | interviewer.py | Only 3 of 5 spec'd implementations |

---

## LOW Findings (22)

Including: DOT float parsing edge case (.5 not parsed), subgraph class derivation missing, undirected edge detection fragile, edge attributes not first-class, graph attributes not first-class, extra_rules parameter missing on validate(), bare key truthiness not in BNF grammar, start node fallback to id="start" missing, checkpoint completed_nodes is dict vs spec's list, checkpoint missing logs field, auto_status attribute not implemented, artifact uses name vs artifact_id, artifact missing has/remove/clear methods, artifact store not thread-safe, condition_syntax check not implemented standalone, fan-in ranking includes SKIPPED status, variable expansion duplicated in 3 places, handler fallback masks config errors, E2E script hardcoded path, Interviewer missing ask_multiple/inform, HTTP server mode missing, pre/post tool call hooks missing.

---

## What the E2E Tests Actually Proved

| Proven | Method |
|---|---|
| loop-agent creates files via real Anthropic API | E2E Test 1 |
| loop-agent reads + edits files (multi-tool chain) | E2E Test 2 |
| loop-agent executes shell commands | E2E Test 3 |
| Pipeline parses DOT, walks graph, calls real API via direct tool loop | E2E Test 4 |
| Multi-node pipeline with real validation (implement + validate) | E2E Test 5 |
| Pipeline works with second provider (OpenAI gpt-4.1-mini) | E2E Test 6 |

| NOT Proven | Why |
|---|---|
| Pipeline spawns real child sessions (sessions all the way down) | Used DirectProviderBackend fallback |
| Provider profile switching per pipeline node | profiles={} means this is dead code |
| Streaming events flow during real usage | E2E tests didn't check streaming events |
| Tool truncation fires during real usage | Hook wiring never validated in integration |
| Subagent tools work with real sessions | Mocked coordinator only |
| Abort/cancellation works | No abort mechanism exists |
| AWAITING_INPUT surfaced to host | Dead code |
| Complex pipeline patterns (parallel, manager, conditional retry) | Unit tests with mocks only |

---

## Recommended Fix Order

### Phase A: Unblock Spawn-Based Pipeline (the intended architecture)
1. Fix `profiles={}` — wire profile mapping from config or registered providers
2. Reconcile spawn contracts — standardize return type and access pattern
3. Add one real-provider spawn E2E test
4. Wire `subgraph_runner` for parallel/manager handlers

### Phase B: Agent Loop Spec Compliance
5. Wire tool truncation into agent loop
6. Add abort signal (CancellationToken or asyncio.Event)
7. Add tool argument validation
8. Implement AWAITING_INPUT detection
9. Fix streaming to capture reasoning
10. Fix SESSION_END timing

### Phase C: Pipeline Spec Compliance
11. Fix transform ordering (transform before validate)
12. Fix multi-class stylesheet matching
13. Add missing validation rules (condition_syntax, stylesheet_syntax)
14. Implement loop_restart edge handling
15. Implement per-node failure routing
16. Add missing event categories

### Phase D: Testing & Infrastructure
17. Add system prompt context to E2E profiles
18. Add pipeline E2E tests to test script
19. Sync standalone repos with bundle copies (or deprecate standalone)
20. Fix E2E script hardcoded paths
