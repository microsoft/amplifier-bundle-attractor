# NLSpec DoD Gap Analysis — Attractor (attractor-next)

**Date:** 2026-02-20
**Scope:** Baseline DoD gap analysis across Attractor, Coding Agent Loop, and Unified LLM specs. Evidence limited to amplifier-bundle-attractor, unified-llm-client, amplifier-module-loop-agent, amplifier-module-loop-pipeline.
**Specs analyzed:**
- Attractor Spec (NLSpec)
- Coding Agent Loop Spec (NLSpec)
- Unified LLM Spec (NLSpec)

---

## Summary

| Spec | PASS | PARTIAL | FAIL | Total |
|------|------|---------|------|-------|
| Attractor (11.1–11.13) | 103 | 0 | 1 | 104 |
| Coding Agent Loop (9.1–9.13) | 109 | 2 | 0 | 111 |
| Unified LLM Client (8.1–8.10) | 118 | 2 | 1 | 121 |
| **TOTAL** | **330** | **4** | **2** | **336** |

---

## Attractor Spec (Sections 11.1–11.13)

### 11.1 DOT Parsing (10 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Parser accepts the supported DOT subset (digraph with graph/node/edge attribute blocks) | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/dot_parser.py:66-682`, `amplifier-module-loop-pipeline/tests/test_dot_parser.py:16-471`, `amplifier-module-loop-pipeline/tests/test_dot_interop.py:48-441` | 25+ parse tests, spec fixture parsing. |
| Graph-level attributes (`goal`, `label`, `model_stylesheet`) are extracted correctly | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/dot_parser.py:431-462`, `amplifier-module-loop-pipeline/tests/test_dot_parser.py:281-310` | `_set_graph_attr()` tested via `test_graph_level_attributes_bare`, `test_graph_attr_block`. |
| Node attributes are parsed including multi-line attribute blocks | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/dot_parser.py:327-388`, `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/dot_parser.py:487-554`, `amplifier-module-loop-pipeline/tests/test_dot_parser.py:56-96` | `_parse_node_stmt()`, `_find_closing_bracket()` tested. |
| Edge attributes (`label`, `condition`, `weight`) are parsed correctly | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/dot_parser.py:341-388`, `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/graph.py:208-238`, `amplifier-module-loop-pipeline/tests/test_dot_parser.py:56-96` | `Edge` dataclass fields verified. |
| Chained edges (`A -> B -> C`) produce individual edges for each pair | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/dot_parser.py:341-388`, `amplifier-module-loop-pipeline/tests/test_dot_parser.py:56-76` | `test_chained_edges_expanded`, `test_chained_edges_with_attributes`. |
| Node/edge default blocks apply to subsequent declarations | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/dot_parser.py:168-228`, `amplifier-module-loop-pipeline/tests/test_dot_parser.py:83-110` | `test_node_defaults`, `test_edge_defaults`. |
| Subgraph blocks are flattened (contents kept, wrapper removed) | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/dot_parser.py:229-260`, `amplifier-module-loop-pipeline/tests/test_dot_parser.py:113-160` | `test_subgraph_support`, `test_subgraph_class_derivation`. |
| `class` attribute on nodes merges in attributes from the stylesheet | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/dot_parser.py:262-272`, `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/stylesheet.py:175-198`, `amplifier-module-loop-pipeline/tests/test_stylesheet.py:136-165` | `_derive_class()` + `_selector_matches()` tested. |
| Quoted and unquoted attribute values both work | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/dot_parser.py:554-594`, `amplifier-module-loop-pipeline/tests/test_dot_parser.py:190-245` | `_parse_value()` handles quoted + bare. |
| Comments (`//` and `/* */`) are stripped before parsing | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/dot_parser.py:612-654`, `amplifier-module-loop-pipeline/tests/test_dot_parser.py:252-276` | `test_line_comments_stripped`, `test_block_comments_stripped`. |

---

### 11.2 Validation and Linting (10 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Exactly one start node (shape=Mdiamond) is required | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/validation.py:120-142`, `amplifier-module-loop-pipeline/tests/test_validation.py:53-75` | `test_missing_start_node`, `test_multiple_start_nodes`. |
| Exactly one exit node (shape=Msquare) is required | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/validation.py:144-169`, `amplifier-module-loop-pipeline/tests/test_validation.py:83-105` | `test_missing_exit_node`, `test_multiple_exit_nodes_error`. |
| Start node has no incoming edges | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/validation.py:197-213`, `amplifier-module-loop-pipeline/tests/test_validation.py:181-200` | `test_start_no_incoming`. |
| Exit node has no outgoing edges | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/validation.py:215-231`, `amplifier-module-loop-pipeline/tests/test_validation.py:202-221` | `test_exit_no_outgoing`. |
| All nodes are reachable from start (no orphans) | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/validation.py:233-275`, `amplifier-module-loop-pipeline/tests/test_validation.py:136-156` | BFS reachability via `_check_reachability()`. |
| All edges reference valid node IDs | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/validation.py:171-195`, `amplifier-module-loop-pipeline/tests/test_validation.py:158-179` | `_check_edge_targets()`. |
| Codergen nodes have non-empty `prompt` attribute (warning) | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/validation.py:298-324`, `amplifier-module-loop-pipeline/tests/test_validation.py:242-280` | `_check_prompt_on_llm_nodes()`. |
| Condition expressions on edges parse without errors | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/validation.py:326-391`, `amplifier-module-loop-pipeline/tests/test_validation.py:384-420` | `test_condition_syntax_valid_conditions`, `test_condition_syntax_invalid_condition_is_error`. |
| `validate_or_raise()` throws on error-severity violations | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/validation.py:103-114`, `amplifier-module-loop-pipeline/tests/test_validation.py:282-295` | `test_validate_or_raise_raises_on_errors`, `test_validate_or_raise_returns_warnings`. |
| Lint results include rule name, severity, node/edge ID, message | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/validation.py:40-52`, `amplifier-module-loop-pipeline/tests/test_validation.py:53-665` | `Diagnostic` dataclass fields validated throughout. |

---

### 11.3 Execution Engine (8 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Engine resolves the start node and begins execution there | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/engine.py:460-485`, `amplifier-module-loop-pipeline/tests/test_engine.py:276-355` | `_find_start_node()`, `test_start_node_fallback_to_id_start`. |
| Each node's handler is resolved via shape-to-handler-type mapping | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/validation.py:23-36`, `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/handlers/__init__.py:36-91`, `amplifier-module-loop-pipeline/tests/test_handlers.py:38-70` | `SHAPE_TO_HANDLER`, `HandlerRegistry.get()`. |
| Handler is called with (node, context, graph, logs_root) and returns an Outcome | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/handlers/__init__.py:20-33`, `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/outcome.py:28-48`, `amplifier-module-loop-pipeline/tests/test_outcome.py:12-90` | `NodeHandler` protocol + `Outcome` dataclass. |
| Outcome is written to `{logs_root}/{node_id}/status.json` | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/engine.py:658-680`, `amplifier-module-loop-pipeline/tests/test_run_directory.py:198-353` | `_write_node_status()`, `TestNodeStatusFiles`. |
| Edge selection follows the 5-step priority | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/edge_selection.py:24-83`, `amplifier-module-loop-pipeline/tests/test_edge_selection.py:23-230` | All 5 steps tested: condition > preferred_label > suggested_ids > weight > lexical. |
| Engine loops: execute > select edge > advance > repeat | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/engine.py:74-357`, `amplifier-module-loop-pipeline/tests/test_engine.py:62-105` | `test_simple_linear_pipeline`, `test_conditional_branching`. |
| Terminal node (shape=Msquare) stops execution | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/engine.py:74-357`, `amplifier-module-loop-pipeline/tests/test_engine.py:62-82` | Terminal detection in run loop verified. |
| Pipeline outcome is "success" if all goal_gate nodes succeeded | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/engine.py:487-552`, `amplifier-module-loop-pipeline/tests/test_goal_gates.py:71-322` | `_check_goal_gates()` with satisfied/unsatisfied/fail. |

---

### 11.4 Goal Gate Enforcement (4 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Nodes with `goal_gate=true` are tracked throughout execution | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/graph.py:181`, `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/engine.py:487-552`, `amplifier-module-loop-pipeline/tests/test_goal_gates.py:71-95` | `Node.goal_gate` promoted attr + `_check_goal_gates()`. |
| Engine checks all goal gate nodes before allowing exit | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/engine.py:487-552`, `amplifier-module-loop-pipeline/tests/test_goal_gates.py:124-155` | `test_unsatisfied_goal_gate_no_retry_target_fails`. |
| Engine routes to `retry_target` if unsatisfied | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/engine.py:487-552`, `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/graph.py:182-183`, `amplifier-module-loop-pipeline/tests/test_goal_gates.py:153-225` | Retry target resolution: node > node fallback > graph > graph fallback. |
| No retry_target and unsatisfied = pipeline fail | PASS | `amplifier-module-loop-pipeline/tests/test_goal_gates.py:124-155`, `amplifier-module-loop-pipeline/tests/test_goal_gates.py:322` | `test_unsatisfied_goal_gate_no_retry_target_fails`, `test_goal_gate_retry_bounded`. |

---

### 11.5 Retry Logic (5 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Nodes with `max_retries > 0` are retried on RETRY or FAIL outcomes | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/retry.py:167-273`, `amplifier-module-loop-pipeline/tests/test_retry.py:84-160` | `execute_with_retry()`, `test_retry_on_retry_outcome`. |
| Retry count is tracked per-node and respects the configured limit | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/retry.py:101-164`, `amplifier-module-loop-pipeline/tests/test_retry.py:221-240` | `RetryPolicy.max_attempts`, `test_retry_policy_from_node_max_retries`. |
| Backoff between retries works (constant, linear, or exponential) | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/retry.py:77-98`, `amplifier-module-loop-pipeline/tests/test_retry.py:260-290` | `BackoffConfig.delay_for_attempt()` with all modes tested. |
| Jitter is applied to backoff delays when configured | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/retry.py:77-98`, `amplifier-module-loop-pipeline/tests/test_retry.py:286-295` | `test_backoff_with_jitter`. |
| After retry exhaustion, the node's final outcome is used for edge selection | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/retry.py:167-273`, `amplifier-module-loop-pipeline/tests/test_retry.py:144-160`, `amplifier-module-loop-pipeline/tests/test_failure_routing.py:58-230` | Final outcome returned on exhaustion, verified in failure routing. |

---

### 11.6 Node Handlers (9 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Start handler: Returns SUCCESS immediately | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/handlers/start.py:15-26`, `amplifier-module-loop-pipeline/tests/test_handlers.py:82-92` | `test_start_handler_returns_success`. |
| Exit handler: Returns SUCCESS immediately | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/handlers/exit.py:15-26`, `amplifier-module-loop-pipeline/tests/test_handlers.py:94-104` | `test_exit_handler_returns_success`. |
| Codergen handler: Expands `$goal`, calls backend, writes files | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/handlers/codergen.py:33-94`, `amplifier-module-loop-pipeline/tests/test_handlers.py:134-170` | `test_codergen_handler_calls_backend`, `test_codergen_writes_stage_files`. |
| Wait.human handler: Presents choices, returns preferred_label | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/handlers/human.py:54-237`, `amplifier-module-loop-pipeline/tests/test_human.py:247-510` | `TestHumanGateHandler`, event emission, accelerator keys. |
| Conditional handler: Passes through | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/handlers/conditional.py:16-27`, `amplifier-module-loop-pipeline/tests/test_handlers.py:106-118` | `test_conditional_handler_is_noop`. |
| Parallel handler: Fan-out concurrent | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/handlers/parallel.py:36-325`, `amplifier-module-loop-pipeline/tests/test_parallel.py:64-399`, `amplifier-module-loop-pipeline/tests/test_parallel_policies.py:61-507` | Fan-out, context cloning, join policies, bounded parallelism. |
| Fan-in handler: Waits for all branches to complete | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/handlers/fan_in.py:48-155`, `amplifier-module-loop-pipeline/tests/test_parallel.py:399-520` | `FanInHandler`, fan-in selection, no-results fail. |
| Tool handler: Executes command and returns result | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/handlers/tool.py:18-102`, `amplifier-module-loop-pipeline/tests/test_handlers.py:211-305` | `test_tool_handler_runs_command`, `test_tool_handler_respects_node_timeout`. |
| Custom handlers can be registered by type string | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/handlers/__init__.py:36-91`, `amplifier-module-loop-pipeline/tests/test_handlers.py:307-330` | `test_registry_custom_handler_registration`. |

---

### 11.7 State and Context (6 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Context is a key-value store accessible to all handlers | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/context.py:24-80`, `amplifier-module-loop-pipeline/tests/test_context.py:15-172` | `PipelineContext` with get/set/update/snapshot/clone. 14 tests. |
| Handlers can return `context_updates` in the Outcome | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/outcome.py:28-48`, `amplifier-module-loop-pipeline/tests/test_engine.py:137-172` | `Outcome.context_updates` field, `test_context_updates_propagate`. |
| Context updates are merged after each node execution | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/engine.py:74-357`, `amplifier-module-loop-pipeline/tests/test_engine.py:137-172` | Merge verified in run loop propagation test. |
| Checkpoint is saved after each node completion | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/checkpoint.py:17-82`, `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/engine.py:606-635`, `amplifier-module-loop-pipeline/tests/test_checkpoint.py:32-262` | `Checkpoint` dataclass, `save_checkpoint()`, model + serialization + integration. |
| Resume from checkpoint: load > restore > continue | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/engine.py:554-604`, `amplifier-module-loop-pipeline/tests/test_checkpoint.py:262+` | `_try_resume_from_checkpoint()`, `TestResumeFromCheckpoint`. |
| Artifacts are written to `{logs_root}/{node_id}/` | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/artifacts.py:37-216`, `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/engine.py:658-680`, `amplifier-module-loop-pipeline/tests/test_run_directory.py:71-451`, `amplifier-module-loop-pipeline/tests/test_artifacts.py:13-251` | `ArtifactStore` + status.json, manifest, artifacts dir. |

---

### 11.8 Human-in-the-Loop (6 items)

| DoD Item | Status | Evidence | Notes | Interpretation |
|---|---|---|---|---|
| Interviewer interface works: `ask(question) -> Answer` | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/interviewer.py:81-102`, `amplifier-module-loop-pipeline/tests/test_human.py:58-90` | `Interviewer` abstract class with `ask()`, `ask_multiple()`, `inform()`. | |
| Question supports types: SINGLE_SELECT, MULTI_SELECT, FREE_TEXT, CONFIRM | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/interviewer.py:18-27`, `amplifier-module-loop-pipeline/tests/test_human.py:58-90` | Impl uses YES_NO, MULTIPLE_CHOICE, FREEFORM, CONFIRMATION. | Spec type names differ from implementation names (SINGLE_SELECT->YES_NO, MULTI_SELECT->MULTIPLE_CHOICE, FREE_TEXT->FREEFORM, CONFIRM->CONFIRMATION). Functionally equivalent; treated as naming convention difference, not a functional gap. |
| AutoApproveInterviewer always selects the first option | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/interviewer.py:105-124`, `amplifier-module-loop-pipeline/tests/test_human.py:111-146` | `TestAutoApproveInterviewer`. | |
| ConsoleInterviewer prompts in terminal | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/interviewer.py:169-227`, `amplifier-module-loop-pipeline/tests/test_human.py` | `ConsoleInterviewer` tested at integration level. | |
| CallbackInterviewer delegates to a provided function | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/interviewer.py:149-166`, `amplifier-module-loop-pipeline/tests/test_human.py:171-185` | `TestCallbackInterviewer`. | |
| QueueInterviewer reads from a pre-filled answer queue | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/interviewer.py:127-146`, `amplifier-module-loop-pipeline/tests/test_human.py:148-169` | `TestQueueInterviewer`. | |

---

### 11.9 Condition Expressions (7 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| `=` (equals) operator works for string comparison | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/conditions.py:39-57`, `amplifier-module-loop-pipeline/tests/test_conditions.py:11-18` | `test_outcome_equals`. |
| `!=` (not equals) operator works | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/conditions.py:39-57`, `amplifier-module-loop-pipeline/tests/test_conditions.py:19-25` | `test_not_equals`. |
| `&&` (AND) conjunction works with multiple clauses | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/conditions.py:14-36`, `amplifier-module-loop-pipeline/tests/test_conditions.py:36-50` | `test_and_clauses`. |
| `outcome` variable resolves to current node's outcome status | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/conditions.py:60-91`, `amplifier-module-loop-pipeline/tests/test_conditions.py:137-146` | `test_outcome_status_values`. |
| `preferred_label` variable resolves to outcome's preferred label | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/conditions.py:60-91`, `amplifier-module-loop-pipeline/tests/test_conditions.py:81-97` | `test_preferred_label_equals`, `test_preferred_label_not_equals`. |
| `context.*` variables resolve to context values | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/conditions.py:60-91`, `amplifier-module-loop-pipeline/tests/test_conditions.py:27-35` | `test_context_lookup`. |
| Empty condition always evaluates to true | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/conditions.py:14-36`, `amplifier-module-loop-pipeline/tests/test_conditions.py:52-60` | `test_empty_condition_is_true`. |

---

### 11.10 Model Stylesheet (6 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Stylesheet is parsed from graph's `model_stylesheet` attribute | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/stylesheet.py:77-129`, `amplifier-module-loop-pipeline/tests/test_stylesheet.py:16-62` | `parse_stylesheet()` with all selector type tests. |
| Selectors by shape name work | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/stylesheet.py:175-198`, `amplifier-module-loop-pipeline/tests/test_stylesheet.py:119-134` | `test_apply_universal_rule` (shape=box). |
| Selectors by class name work | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/stylesheet.py:175-198`, `amplifier-module-loop-pipeline/tests/test_stylesheet.py:136-149` | `test_apply_class_selector`. |
| Selectors by node ID work | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/stylesheet.py:175-198`, `amplifier-module-loop-pipeline/tests/test_stylesheet.py:151-164` | `test_apply_id_selector`. |
| Specificity order: universal < shape < class < ID | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/stylesheet.py:62-74`, `amplifier-module-loop-pipeline/tests/test_stylesheet.py:166-220` | `StyleRule.specificity`: *=0, shape=1, .class=2, #id=3. |
| Stylesheet properties are overridden by explicit node attributes | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/stylesheet.py:132-172`, `amplifier-module-loop-pipeline/tests/test_stylesheet.py:203-219` | `test_explicit_node_attrs_override_stylesheet`. |

---

### 11.11 Transforms and Extensibility (5 items)

| DoD Item | Status | Evidence | Notes | Interpretation |
|---|---|---|---|---|
| AST transforms can modify the Graph between parsing and validation | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/transforms.py:97-132`, `amplifier-module-loop-pipeline/tests/test_transforms.py:158-260` | `apply_transforms()` ordered pipeline, `TestApplyTransforms`. | |
| Transform interface: `transform(graph) -> graph` | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/transforms.py:52-62`, `amplifier-module-loop-pipeline/tests/test_transforms.py:158-260` | `Transform` protocol with `apply(graph)`. | |
| Built-in variable expansion transform replaces `$goal` in prompts | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/transforms.py:30-94`, `amplifier-module-loop-pipeline/tests/test_transforms.py:48-155`, `amplifier-module-loop-pipeline/tests/test_param_expansion.py` | `expand_goal_variable()`, `expand_variables()`. | |
| Custom transforms can be registered and run in order | PASS | `amplifier-module-loop-pipeline/amplifier_module_loop_pipeline/transforms.py:97-132`, `amplifier-module-loop-pipeline/tests/test_transforms.py:158-260` | `extra_transforms` parameter tested. | |
| HTTP server mode: POST /run, GET /status, POST /answer | FAIL | — | Spec marks this as "(if implemented)". No HTTP server endpoints exist. `PipelineRunTool` provides tool-based invocation but not HTTP endpoints. | The spec explicitly marks this requirement as conditional ("if implemented"). No HTTP server mode has been implemented. The `PipelineRunTool` (in the bundle) provides programmatic pipeline invocation but not the HTTP API surface described in spec. Marked FAIL per the hard rule that no evidence = FAIL, even though the spec's conditional phrasing suggests this is acceptable. |

---

### 11.12 Cross-Feature Parity Matrix (22 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Parse a simple linear pipeline (start -> A -> B -> done) | PASS | `amplifier-module-loop-pipeline/tests/test_pipeline_e2e.py:174` | `TestSimpleLinear`. |
| Parse pipeline with graph-level attributes (goal, label) | PASS | `amplifier-module-loop-pipeline/tests/test_dot_parser.py:281-310`, `amplifier-module-loop-pipeline/tests/test_dot_interop.py:48` | `TestSpecSimpleLinear`. |
| Parse multi-line node attributes | PASS | `amplifier-module-loop-pipeline/tests/test_dot_parser.py:56-96` | Attribute block parsing. |
| Validate: missing start node -> error | PASS | `amplifier-module-loop-pipeline/tests/test_validation.py:53-62` | Validated. |
| Validate: missing exit node -> error | PASS | `amplifier-module-loop-pipeline/tests/test_validation.py:83-92` | Validated. |
| Validate: orphan node -> warning | PASS | `amplifier-module-loop-pipeline/tests/test_validation.py:136-156` | Validated. |
| Execute a linear 3-node pipeline end-to-end | PASS | `amplifier-module-loop-pipeline/tests/test_engine.py:62-80`, `amplifier-module-loop-pipeline/tests/test_pipeline_e2e.py:174` | Two independent test files. |
| Execute with conditional branching | PASS | `amplifier-module-loop-pipeline/tests/test_engine.py:104-135`, `amplifier-module-loop-pipeline/tests/test_pipeline_e2e.py:325` | `TestConditionalBranch`. |
| Execute with retry on failure (max_retries=2) | PASS | `amplifier-module-loop-pipeline/tests/test_retry.py:84-160`, `amplifier-module-loop-pipeline/tests/test_failure_routing.py:58-230` | Retry + failure routing. |
| Goal gate blocks exit when unsatisfied | PASS | `amplifier-module-loop-pipeline/tests/test_goal_gates.py:124-155` | Validated. |
| Goal gate allows exit when all satisfied | PASS | `amplifier-module-loop-pipeline/tests/test_goal_gates.py:71-95` | Validated. |
| Wait.human presents choices and routes on selection | PASS | `amplifier-module-loop-pipeline/tests/test_human.py:247-378` | Validated. |
| Edge selection: condition match wins over weight | PASS | `amplifier-module-loop-pipeline/tests/test_edge_selection.py:207-218` | Validated. |
| Edge selection: weight breaks ties | PASS | `amplifier-module-loop-pipeline/tests/test_edge_selection.py:81-90` | Validated. |
| Edge selection: lexical tiebreak as final fallback | PASS | `amplifier-module-loop-pipeline/tests/test_edge_selection.py:91-100` | Validated. |
| Context updates from one node are visible to the next | PASS | `amplifier-module-loop-pipeline/tests/test_engine.py:137-172` | Validated. |
| Checkpoint save and resume produces same result | PASS | `amplifier-module-loop-pipeline/tests/test_checkpoint.py:213-310` | `TestCheckpointEngineIntegration`, `TestResumeFromCheckpoint`. |
| Stylesheet applies model override to nodes by shape | PASS | `amplifier-module-loop-pipeline/tests/test_stylesheet.py:237-305` | `test_spec_example`. |
| Prompt variable expansion ($goal) works | PASS | `amplifier-module-loop-pipeline/tests/test_transforms.py:48-155` | Validated. |
| Parallel fan-out and fan-in complete correctly | PASS | `amplifier-module-loop-pipeline/tests/test_parallel.py:64-520`, `amplifier-module-loop-pipeline/tests/test_parallel_policies.py` | Comprehensive parallel tests. |
| Custom handler registration and execution works | PASS | `amplifier-module-loop-pipeline/tests/test_handlers.py:307-330` | Validated. |
| Pipeline with 10+ nodes completes without errors | PASS | `amplifier-module-loop-pipeline/tests/test_dot_integration.py:874-1080` | `TestSemportPipeline` (11+ nodes), `TestConsensusPipeline`. |

---

### 11.13 Integration Smoke Test (6 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Parse: graph.goal extracted, 5 nodes, 6 edges | PASS | `amplifier-module-loop-pipeline/tests/test_dot_interop.py:333-418`, `amplifier-module-loop-pipeline/tests/fixtures/spec_smoke_test.dot` | `TestSpecSmokeTestDOT`. |
| Validate: no error-severity results | PASS | `amplifier-module-loop-pipeline/tests/test_dot_interop.py:429-441` | `test_all_spec_fixtures_validate`. |
| Execute with LLM callback: outcome is "success" | PASS | `amplifier-module-loop-pipeline/tests/test_pipeline_e2e.py:553`, `amplifier-module-loop-pipeline/tests/test_dot_integration.py:249-500` | `TestSpecSmokeTest`, multi-step integration. |
| Artifacts exist for plan, implement, review | PASS | `amplifier-module-loop-pipeline/tests/test_run_directory.py:354-422` | `TestCodergenFiles`, `TestArtifactsDirectory`. |
| Goal gate satisfied for "implement" node | PASS | `amplifier-module-loop-pipeline/tests/test_pipeline_e2e.py:439` | `TestGoalGate`. |
| Checkpoint: current_node == "done", completed_nodes correct | PASS | `amplifier-module-loop-pipeline/tests/test_checkpoint.py:213-262` | `TestCheckpointEngineIntegration`. |

---

## Coding Agent Loop Spec (Sections 9.1–9.13)

### 9.1 Core Loop (8 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Session can be created with a ProviderProfile and ExecutionEnvironment | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/__init__.py:33-153`, `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:84-115`, `amplifier-module-loop-agent/tests/test_mount.py:1-48` | `AgentOrchestrator` creates `AgentSession`. |
| `process_input()` runs the agentic loop | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:279-463`, `amplifier-module-loop-agent/tests/test_agent_session.py:1-430` | 14 session tests. |
| Natural completion: model responds with text only -> loop exits | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:279-463`, `amplifier-module-loop-agent/tests/test_agent_session.py` | Natural completion detection tested. |
| Round limits: `max_tool_rounds_per_input` stops the loop | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/config.py:30-80`, `amplifier-module-loop-agent/tests/test_agent_session.py` | Round limit verified. |
| Session turn limits: `max_turns` stops the loop across all inputs | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/config.py:30-80`, `amplifier-module-loop-agent/tests/test_agent_session.py` | Turn limit state verified. |
| Abort signal: cancellation -> CLOSED | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:965-983`, `amplifier-module-loop-agent/amplifier_module_loop_agent/state.py:22-101`, `amplifier-module-loop-agent/tests/test_cancellation.py:1-215` | 5 cancellation checkpoint tests. |
| Loop detection: consecutive identical patterns -> warning SteeringTurn | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/loop_detection.py:16-68`, `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:1054-1067`, `amplifier-module-loop-agent/tests/test_loop_detection.py:1-268` | 12 tests (patterns, window, SteeringTurn injection). |
| Multiple sequential inputs work | PASS | `amplifier-module-loop-agent/tests/test_agent_session.py` | Session persistence across calls, round count reset. |

---

### 9.2 Provider Profiles (6 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| OpenAI profile provides codex-rs-aligned tools (apply_patch v4a) | PASS | `amplifier-module-loop-agent/tests/test_provider_aligned_tools.py`, `amplifier-bundle-attractor/modules/tool-apply-patch/`, `amplifier-bundle-attractor/profiles/attractor-profile-openai.yaml` | Profile config + tool implementation + mount tests. |
| Anthropic profile provides Claude Code-aligned tools (edit_file) | PASS | `amplifier-module-loop-agent/tests/test_provider_aligned_tools.py`, `amplifier-bundle-attractor/profiles/attractor-profile-anthropic.yaml` | Profile config + mount tests. |
| Gemini profile provides gemini-cli-aligned tools | PASS | `amplifier-module-loop-agent/tests/test_provider_aligned_tools.py`, `amplifier-bundle-attractor/profiles/attractor-profile-gemini.yaml` | Profile config + mount tests. |
| Each profile produces a provider-specific system prompt | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/system_prompt.py:36-84`, `amplifier-module-loop-agent/tests/test_system_prompt.py:1-200` | `build_system_prompt()` 5-layer assembly. 12 tests. |
| Custom tools can be registered on top of any profile | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/tool_registry.py:16-86`, `amplifier-module-loop-agent/tests/test_tool_registry.py:1-156` | `ToolRegistry.register()`, 12 registry tests. |
| Tool name collisions: custom overrides profile defaults | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/tool_registry.py:16-86`, `amplifier-module-loop-agent/tests/test_tool_registry.py` | Latest-wins semantics tested. |

---

### 9.3 Tool Execution (5 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Tool calls dispatched through the ToolRegistry | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:589-621`, `amplifier-module-loop-agent/amplifier_module_loop_agent/tool_registry.py:16-86` | `_execute_tool_calls()` via registry. |
| Unknown tool calls return error result (not exception) | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:623-638`, `amplifier-module-loop-agent/tests/test_error_handling.py` | Error ToolResult, session stays open. |
| Tool argument JSON is parsed and validated | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:640-699`, `amplifier-module-loop-agent/tests/test_arg_validation.py:1-224` | `_validate_tool_arguments()`, 8+ validation tests. |
| Tool execution errors caught and returned as error results | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:701-799`, `amplifier-module-loop-agent/tests/test_error_handling.py` | Exception -> ToolResult(is_error=True), session open. |
| Parallel tool execution when `supports_parallel_tool_calls` is true | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:589-621`, `amplifier-module-loop-agent/tests/test_parallel_gating.py:1-279` | `asyncio.gather`, 7 tests (config, sequential, parallel, timing). |

---

### 9.4 Execution Environment (6 items)

| DoD Item | Status | Evidence | Notes | Interpretation |
|---|---|---|---|---|
| `LocalExecutionEnvironment` implements all file and command operations | PARTIAL | `amplifier-module-loop-agent/amplifier_module_loop_agent/environment.py:17-125`, `amplifier-module-loop-agent/tests/test_environment.py:1-119` | Implementation provides env context builder + config. Actual file/command tools are mounted externally via bundle tool modules. | The spec describes a `LocalExecutionEnvironment` class. The implementation uses a `build_environment_context()` function + externally mounted tool modules (in bundle) instead of a single class. Functionally equivalent — all operations are available — but the architectural pattern differs from spec. Marked PARTIAL because the named class does not exist. |
| Command timeout default is 10 seconds | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/config.py:40-41`, `amplifier-module-loop-agent/tests/test_config.py` | `default_command_timeout_ms = 10_000`. | |
| Command timeout is overridable per-call via shell tool's `timeout_ms` | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/config.py:40-41`, `amplifier-bundle-attractor/modules/tool-shell/` | Config provides `max_command_timeout_ms` cap; shell tool accepts per-call timeout. | |
| Timed-out commands: SIGTERM -> SIGKILL after 2 seconds | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:929-959`, `amplifier-module-loop-agent/tests/test_process_tracking.py:1-185` | 8 tests (register, shutdown, SIGTERM->SIGKILL, timeout). | |
| Environment variable filtering excludes sensitive variables | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/env_filter.py:16-59`, `amplifier-module-loop-agent/tests/test_env_filtering.py:1-94` | `sanitize_env()`, 12 tests (API_KEY, TOKEN, SECRET, case insensitive). | |
| `ExecutionEnvironment` interface is implementable by consumers | PARTIAL | `amplifier-module-loop-agent/amplifier_module_loop_agent/environment.py:17-125` | Tools are mounted via `ToolRegistry`; no formal `ExecutionEnvironment` Protocol class exists. | The spec describes an `ExecutionEnvironment` interface for custom environments (Docker, K8s, WASM, SSH). The implementation uses implicit duck-typing via `ToolRegistry` mounting rather than a formal Protocol class. Consumers can register custom tools, achieving the same extensibility, but the named interface does not exist. |

---

### 9.5 Tool Output Truncation (6 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Character-based truncation runs FIRST | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/config.py:14-28`, `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:748-786`, `amplifier-bundle-attractor/modules/hooks-tool-truncation/` | Per-tool char limits + tool:post hook. |
| Line-based truncation runs SECOND | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/config.py:68-80`, `amplifier-module-loop-agent/tests/test_config.py:1-173` | `get_tool_line_limit()`, per-tool line limits. |
| Truncation inserts a visible marker | PASS | `amplifier-module-loop-agent/tests/test_truncation_wiring.py:1-210`, `amplifier-bundle-attractor/modules/hooks-tool-truncation/tests/test_truncation.py` | Marker text in truncated output. |
| Full untruncated output available in `TOOL_CALL_END` event | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:748-786`, `amplifier-module-loop-agent/tests/test_truncation_wiring.py` | Full output in tool_call_end event. |
| Default character limits match spec Section 5.2 | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/config.py:14-28`, `amplifier-module-loop-agent/tests/test_config.py` | read_file=50k, shell=30k, grep=20k, etc. |
| Both character and line limits overridable via `SessionConfig` | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/config.py:47-48`, `amplifier-module-loop-agent/tests/test_config.py` | `tool_output_limits`, `tool_line_limits` overrides. |

---

### 9.6 Steering (4 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| `steer()` queues a message injected after current tool round | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/steering.py:17-43`, `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:1016-1025`, `amplifier-module-loop-agent/tests/test_steering.py:1-309` | 12 tests (steer, drain, timing). |
| `follow_up()` queues a message processed after current input completes | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/steering.py:46-70`, `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:1027-1048`, `amplifier-module-loop-agent/tests/test_steering.py` | Follow-up ordering, processing tests. |
| Steering messages appear as SteeringTurn in the history | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/turns.py:53-58`, `amplifier-module-loop-agent/tests/test_steering.py` | SteeringTurn verified in history. |
| SteeringTurns converted to user-role messages for the LLM | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/messages.py:36-79`, `amplifier-module-loop-agent/tests/test_messages.py:1-295` | 15 message conversion tests. |

---

### 9.7 Reasoning Effort (3 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| `reasoning_effort` is passed through to the LLM SDK Request | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:330`, `amplifier-module-loop-agent/amplifier_module_loop_agent/config.py:42`, `amplifier-module-loop-agent/tests/test_parity_matrix.py:398-423` | reasoning_effort="high" passed x3 providers. |
| Changing `reasoning_effort` mid-session takes effect on next LLM call | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:330`, `amplifier-module-loop-agent/tests/test_parity_matrix.py:398-423` | Read from config each iteration. S11 parametrized. |
| Valid values: "low", "medium", "high", null | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/config.py:42`, `amplifier-module-loop-agent/tests/test_config.py:16-26` | Nullable field, default is None. |

---

### 9.8 System Prompts (6 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| System prompt includes provider-specific base instructions | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/system_prompt.py:36-84`, `amplifier-module-loop-agent/tests/test_system_prompt.py:1-200` | Layer 1 in 5-layer assembly. |
| System prompt includes environment context | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/environment.py:17-56`, `amplifier-module-loop-agent/tests/test_environment.py:1-119`, `amplifier-module-loop-agent/tests/test_system_prompt_wiring.py:1-311` | Platform, date, git, provider, model. |
| System prompt includes tool descriptions from active profile | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:572-583`, `amplifier-module-loop-agent/tests/test_system_prompt_wiring.py` | `_get_tool_definitions()`. |
| Project documentation files discovered and included | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/system_prompt.py:87-147`, `amplifier-module-loop-agent/amplifier_module_loop_agent/system_prompt.py:26-33`, `amplifier-module-loop-agent/tests/test_system_prompt.py` | AGENTS.md, CLAUDE.md, GEMINI.md, .codex/instructions.md. |
| User instruction overrides are appended last | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/system_prompt.py:36-84`, `amplifier-module-loop-agent/tests/test_system_prompt_wiring.py` | User override as layer 5 (highest priority). |
| Only relevant provider files loaded | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/system_prompt.py:26-33`, `amplifier-module-loop-agent/tests/test_system_prompt.py` | Anthropic->CLAUDE.md, OpenAI->.codex/instructions.md. Exclusion tested. |

---

### 9.9 Subagents (6 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Subagents spawned via `spawn_agent` tool | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/subagent_tools.py:118-188`, `amplifier-module-loop-agent/tests/test_subagent_tools.py:1-633` | `SpawnAgentTool`, 22+ spawn tests. |
| Subagents share parent's execution environment | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/subagent_tools.py:257-379`, `amplifier-module-loop-agent/tests/test_subagent_tools.py` | Spawn via coordinator, same filesystem. |
| Subagents maintain independent conversation history | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/subagent_tools.py:257-379`, `amplifier-module-loop-agent/tests/test_subagent_tools.py` | Separate session via spawn. |
| Depth limiting prevents recursive spawning (default max depth: 1) | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/subagent_tools.py:118-188`, `amplifier-module-loop-agent/amplifier_module_loop_agent/config.py:52`, `amplifier-module-loop-agent/tests/test_subagents.py:1-139` | `max_subagent_depth`, 7 depth tests. |
| Subagent results returned as tool results | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/subagent_tools.py:257-379`, `amplifier-module-loop-agent/tests/test_subagent_tools.py` | WaitTool returns result, cached. |
| `send_input`, `wait`, and `close_agent` tools work correctly | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/subagent_tools.py:196-421`, `amplifier-module-loop-agent/tests/test_subagent_tools.py` | `SendInputTool`, `WaitTool`, `CloseAgentTool` — all 4 tools tested. |

---

### 9.10 Event System (4 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| All event kinds listed in Section 2.9 are emitted | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/events.py:17-44`, `amplifier-module-loop-agent/tests/test_events.py:1-340` | 15 event constants, 12 emission tests + ordering. |
| Events delivered via async iterator | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:198-273`, `amplifier-module-loop-agent/tests/test_streaming.py:1-374` | Streaming async generator, 9 tests. |
| `TOOL_CALL_END` events carry full untruncated output | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:748-786`, `amplifier-module-loop-agent/tests/test_truncation_wiring.py` | Full output in tool_call_end event. |
| SESSION_START/SESSION_END bracket the session | PASS | `amplifier-module-loop-agent/tests/test_events.py`, `amplifier-module-loop-agent/tests/test_session_end_timing.py:1-176` | session_start first, session_end last, exactly once. |

---

### 9.11 Error Handling (5 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Tool execution errors -> error result sent to LLM | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:701-799`, `amplifier-module-loop-agent/tests/test_error_handling.py:1-353` | Exception -> ToolResult(is_error=True), session stays open. |
| LLM transient errors (429, 500-503) -> retry with backoff (via Unified LLM) | PASS | `unified-llm-client/unified_llm/retry.py:37-85`, `amplifier-module-loop-agent/tests/test_error_handling.py` | RateLimit re-raised (retryable=True), delegated to ULC retry. |
| Authentication errors -> immediate, no retry, session -> CLOSED | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:337-352`, `amplifier-module-loop-agent/tests/test_error_handling.py` | Auth error -> CLOSED + re-raised. |
| Context window overflow -> emit warning event | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:1073-1112`, `amplifier-module-loop-agent/tests/test_context_window.py:1-193` | `_check_context_usage()` 80% threshold, 5 tests. |
| Graceful shutdown: abort -> cancel LLM -> kill processes -> flush -> SESSION_END | PASS | `amplifier-module-loop-agent/amplifier_module_loop_agent/agent_session.py:901-959`, `amplifier-module-loop-agent/tests/test_error_handling.py`, `amplifier-module-loop-agent/tests/test_cancellation.py` | `shutdown()`, `_terminate_tracked_processes()`, CLOSED + session_end. |

---

### 9.12 Cross-Provider Parity Matrix (15 x 3 = 45 items)

All 45 cells covered by parametrized tests in a single test file: `amplifier-module-loop-agent/tests/test_parity_matrix.py:1-619` (15 scenarios x 3 providers: OpenAI, Anthropic, Gemini).

| Test Case | OpenAI | Anthropic | Gemini | Evidence |
|---|---|---|---|---|
| Simple file creation task | PASS | PASS | PASS | S1 parametrized |
| Read file, then edit it | PASS | PASS | PASS | S2 parametrized |
| Multi-file edit in one session | PASS | PASS | PASS | S3 parametrized |
| Shell command execution | PASS | PASS | PASS | S4 parametrized |
| Shell command timeout handling | PASS | PASS | PASS | S5 parametrized |
| Grep + glob to find files | PASS | PASS | PASS | S6 parametrized |
| Multi-step task (read -> analyze -> edit) | PASS | PASS | PASS | S7 parametrized |
| Tool output truncation (large file) | PASS | PASS | PASS | S8 parametrized |
| Parallel tool calls (if supported) | PASS | PASS | PASS | S9 parametrized |
| Steering mid-task | PASS | PASS | PASS | S10 parametrized |
| Reasoning effort change | PASS | PASS | PASS | S11 parametrized |
| Subagent spawn and wait | PASS | PASS | PASS | S12 parametrized |
| Loop detection triggers warning | PASS | PASS | PASS | S13 parametrized |
| Error recovery (tool fails, model retries) | PASS | PASS | PASS | S14 parametrized |
| Provider-specific editing format works | PASS | PASS | PASS | S15 parametrized |

---

### 9.13 Integration Smoke Test (7 items)

All 7 items covered by `amplifier-module-loop-agent/tests/test_integration_smoke.py` — full sequence test on single orchestrator.

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Simple file creation: file exists and contains expected content | PASS | `amplifier-module-loop-agent/tests/test_integration_smoke.py` | Step 1: file creation via write_file. |
| Read and edit: file contains both original and new content | PASS | `amplifier-module-loop-agent/tests/test_integration_smoke.py` | Step 2: read+edit with persistence. |
| Shell execution: command was executed (verified via event stream) | PASS | `amplifier-module-loop-agent/tests/test_integration_smoke.py` | Step 3: shell with output capture. |
| Truncation verification: TOOL_CALL_END has full 100k chars; ToolResult has marker | PASS | `amplifier-module-loop-agent/tests/test_integration_smoke.py` | Step 4: 100k chars handled. |
| Steering: agent adjusts approach after steer() call | PASS | `amplifier-module-loop-agent/tests/test_integration_smoke.py` | Step 5: steering injection verified. |
| Subagent: subagent tool calls appear in event stream | PASS | `amplifier-module-loop-agent/tests/test_integration_smoke.py` | Step 6: subagent delegation. |
| Timeout handling: command times out and agent handles gracefully | PASS | `amplifier-module-loop-agent/tests/test_integration_smoke.py` | Step 7: timeout + LLM recovery. |

---

## Unified LLM Client Spec (Sections 8.1–8.10)

### 8.1 Core Infrastructure (8 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| `Client` can be constructed from environment variables (`Client.from_env()`) | PASS | `unified-llm-client/unified_llm/client.py:88-130`, `unified-llm-client/tests/dod/test_8_1_core_infra.py:91-98`, `unified-llm-client/tests/unit/test_client.py:195` | Auto-detect API keys. `TestFromEnv`. |
| `Client` can be constructed programmatically with explicit adapters | PASS | `unified-llm-client/unified_llm/client.py:28-36`, `unified-llm-client/tests/unit/test_client.py:88` | `Client.__init__()`, `TestClientConstruction`. |
| Provider routing: requests dispatched to correct adapter based on `provider` | PASS | `unified-llm-client/unified_llm/client.py:38-52`, `unified-llm-client/tests/dod/test_8_1_core_infra.py:113-122`, `unified-llm-client/tests/unit/test_client.py:107` | `_resolve_adapter()`. |
| Default provider used when `provider` is omitted | PASS | `unified-llm-client/unified_llm/client.py:40`, `unified-llm-client/tests/dod/test_8_1_core_infra.py:125-132` | `default_provider` field. |
| `ConfigurationError` raised when no provider configured and no default | PASS | `unified-llm-client/unified_llm/client.py:38-52`, `unified-llm-client/unified_llm/errors.py:203-208`, `unified-llm-client/tests/dod/test_8_1_core_infra.py:135-145` | `ConfigurationError`. |
| Middleware chain executes in correct order | PASS | `unified-llm-client/unified_llm/middleware.py:19-61`, `unified-llm-client/tests/dod/test_8_1_core_infra.py:147-171`, `unified-llm-client/tests/unit/test_middleware.py:1-139` | Registration order (request), reverse order (response). |
| Module-level default client works | PASS | `unified-llm-client/unified_llm/client.py:133-144`, `unified-llm-client/tests/dod/test_8_1_core_infra.py:174-186`, `unified-llm-client/tests/unit/test_client.py:255` | `set_default_client()`, `get_default_client()`. |
| Model catalog populated; `get_model_info()` / `list_models()` return correct data | PASS | `unified-llm-client/unified_llm/catalog.py:18-92`, `unified-llm-client/unified_llm/data/models.json`, `unified-llm-client/tests/dod/test_8_1_core_infra.py:189-205`, `unified-llm-client/tests/unit/test_catalog.py:1-72` | Shipped catalog data. |

---

### 8.2 Provider Adapters (10 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Adapter uses provider's native API (not compatibility shim) | PASS | `unified-llm-client/unified_llm/adapters/openai.py:72-79`, `unified-llm-client/unified_llm/adapters/anthropic.py:67-74`, `unified-llm-client/unified_llm/adapters/gemini.py:66-75`, `unified-llm-client/tests/dod/test_8_2_provider_adapters.py:57-80` | All 3 providers use native APIs (Responses API, Messages API, Gemini API). |
| Authentication works (API key from env var or explicit config) | PASS | `unified-llm-client/unified_llm/adapters/openai.py:42-62`, `unified-llm-client/unified_llm/adapters/anthropic.py:40-57`, `unified-llm-client/unified_llm/adapters/gemini.py:43-56`, `unified-llm-client/tests/dod/test_8_2_provider_adapters.py:88-109` | `TestAuthentication`. |
| `complete()` sends request and returns correctly populated `Response` | PASS | `unified-llm-client/unified_llm/adapters/openai.py:72-79`, `unified-llm-client/unified_llm/adapters/anthropic.py:67-74`, `unified-llm-client/unified_llm/adapters/gemini.py:66-75`, `unified-llm-client/tests/dod/test_8_2_provider_adapters.py:117-193` | `TestCompleteReturnsResponse`. |
| `stream()` returns async iterator of correctly typed `StreamEvent` objects | PASS | `unified-llm-client/unified_llm/adapters/openai.py:81-216`, `unified-llm-client/unified_llm/adapters/anthropic.py:76-190`, `unified-llm-client/unified_llm/adapters/gemini.py:77-181`, `unified-llm-client/tests/adapter/test_openai.py:946`, `unified-llm-client/tests/adapter/test_anthropic.py:977`, `unified-llm-client/tests/adapter/test_gemini.py:1027` | Streaming tests per provider. |
| System messages extracted/handled per provider convention | PASS | `unified-llm-client/unified_llm/adapters/openai.py:238-243`, `unified-llm-client/unified_llm/adapters/anthropic.py:212-217`, `unified-llm-client/unified_llm/adapters/gemini.py:210-215`, `unified-llm-client/tests/adapter/test_openai.py:39`, `unified-llm-client/tests/adapter/test_anthropic.py:39`, `unified-llm-client/tests/adapter/test_gemini.py:35` | SYSTEM/DEVELOPER -> instructions/system/system_instruction per provider. |
| All 5 roles (SYSTEM, USER, ASSISTANT, TOOL, DEVELOPER) translated correctly | PASS | `unified-llm-client/unified_llm/types.py:20-27`, `unified-llm-client/tests/dod/test_8_2_provider_adapters.py:200-242` | `TestRoleTranslation`. |
| `provider_options` escape hatch passes through | PASS | `unified-llm-client/unified_llm/adapters/openai.py:306-309`, `unified-llm-client/unified_llm/adapters/anthropic.py:258-264`, `unified-llm-client/unified_llm/adapters/gemini.py:258-261`, `unified-llm-client/tests/dod/test_8_2_provider_adapters.py:250-271` | `TestProviderOptionsPassthrough`. |
| Beta headers are supported (especially Anthropic) | PASS | `unified-llm-client/unified_llm/adapters/anthropic.py:40-57`, `unified-llm-client/unified_llm/adapters/anthropic.py:410-443`, `unified-llm-client/tests/adapter/test_anthropic.py:1163-1235` | `test_beta_header_included`, `test_beta_header_merged_with_existing`. |
| HTTP errors translated to correct error hierarchy types | PASS | `unified-llm-client/unified_llm/adapters/openai.py:541-593`, `unified-llm-client/unified_llm/adapters/anthropic.py:530-576`, `unified-llm-client/unified_llm/adapters/gemini.py:492-531`, `unified-llm-client/tests/dod/test_8_2_provider_adapters.py:279-312` | `TestErrorTranslation`. |
| `Retry-After` headers parsed and set on error object | PASS | `unified-llm-client/unified_llm/adapters/openai.py:567-575`, `unified-llm-client/unified_llm/adapters/anthropic.py:550-558`, `unified-llm-client/tests/dod/test_8_2_provider_adapters.py:320-339` | `TestRetryAfterHeader`. |

---

### 8.3 Message & Content Model (7 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Messages with text-only content work across all providers | PASS | `unified-llm-client/unified_llm/types.py:130-194`, `unified-llm-client/tests/dod/test_8_3_content_model.py:75-86` | Text convenience on `Message`. |
| Image input works: URL, base64, and local file path | PASS | `unified-llm-client/unified_llm/types.py:53-60`, `unified-llm-client/unified_llm/adapters/openai.py:313-331`, `unified-llm-client/tests/dod/test_8_3_content_model.py:94-149` | `ImageData`, URL + base64 tested. |
| Audio and document content parts handled | PASS | `unified-llm-client/unified_llm/types.py:63-79`, `unified-llm-client/tests/dod/test_8_3_content_model.py:157-181` | `AudioData`, `DocumentData`. |
| Tool call content parts round-trip correctly | PASS | `unified-llm-client/unified_llm/types.py:82-100`, `unified-llm-client/tests/dod/test_8_3_content_model.py:189-225` | `ToolCallData`, `ToolResultData`. |
| Thinking blocks (Anthropic) preserved and round-tripped with signatures | PASS | `unified-llm-client/unified_llm/types.py:103-109`, `unified-llm-client/unified_llm/adapters/anthropic.py:449-498`, `unified-llm-client/tests/dod/test_8_3_content_model.py:233-268` | `ThinkingData` with signature. |
| Redacted thinking blocks passed through verbatim | PASS | `unified-llm-client/unified_llm/types.py:35-45`, `unified-llm-client/tests/dod/test_8_3_content_model.py:270-276` | `ContentKind.REDACTED_THINKING`. |
| Multimodal messages (text + images in same message) work | PASS | `unified-llm-client/unified_llm/types.py:112-127`, `unified-llm-client/tests/dod/test_8_3_content_model.py:276-296` | `ContentPart` list in Message. |

---

### 8.4 Generation (10 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| `generate()` works with a simple text `prompt` | PASS | `unified-llm-client/unified_llm/generate.py:159-345`, `unified-llm-client/tests/dod/test_8_4_generation.py:107-114`, `unified-llm-client/tests/unit/test_generate.py:79` | generate() entry point. |
| `generate()` works with a full `messages` list | PASS | `unified-llm-client/unified_llm/generate.py:353-374`, `unified-llm-client/tests/dod/test_8_4_generation.py:116-130` | `_build_messages()`. |
| `generate()` rejects when both `prompt` and `messages` provided | PASS | `unified-llm-client/unified_llm/generate.py:159-345`, `unified-llm-client/tests/dod/test_8_4_generation.py:132-145` | Validation check. |
| `stream()` yields `TEXT_DELTA` events that concatenate to full text | PASS | `unified-llm-client/unified_llm/generate.py:464-602`, `unified-llm-client/tests/dod/test_8_4_generation.py:158-180` | stream() function. |
| `stream()` yields `STREAM_START` and `FINISH` events | PASS | `unified-llm-client/tests/dod/test_8_4_generation.py:182-211` | Metadata verified. |
| Streaming follows start/delta/end pattern | PASS | `unified-llm-client/unified_llm/stream_validation.py:16-150`, `unified-llm-client/tests/unit/test_stream_validation.py:1-214` | Stream protocol validator. |
| `generate_object()` returns parsed, validated structured output | PASS | `unified-llm-client/unified_llm/generate.py:628-698`, `unified-llm-client/tests/dod/test_8_4_generation.py:220-255` | generate_object(). |
| `generate_object()` raises `NoObjectGeneratedError` on failure | PASS | `unified-llm-client/unified_llm/errors.py:195-200`, `unified-llm-client/tests/unit/test_generate.py:1032` | `TestGenerateObject`. |
| Cancellation via abort signal works for both generate() and stream() | PASS | `unified-llm-client/unified_llm/generate.py:43-85`, `unified-llm-client/tests/dod/test_8_4_generation.py:264-276`, `unified-llm-client/tests/unit/test_generate.py:1388` | `AbortSignal`, `AbortController`. |
| Timeouts work (total timeout and per-step timeout) | PASS | `unified-llm-client/unified_llm/generate.py:99-157`, `unified-llm-client/unified_llm/types.py:585-599`, `unified-llm-client/tests/dod/test_8_4_generation.py:284-306`, `unified-llm-client/tests/unit/test_generate.py:1502` | `TimeoutConfig`, `AdapterTimeout`. |

---

### 8.5 Reasoning Tokens (6 items)

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| OpenAI reasoning models return `reasoning_tokens` in Usage | PASS | `unified-llm-client/unified_llm/adapters/openai.py:502-535`, `unified-llm-client/tests/dod/test_8_5_reasoning.py:48-86`, `unified-llm-client/tests/adapter/test_openai.py:1059` | `_extract_usage()`, `TestReasoningTokens`. |
| `reasoning_effort` parameter passed through to OpenAI | PASS | `unified-llm-client/unified_llm/adapters/openai.py:302-303`, `unified-llm-client/tests/dod/test_8_5_reasoning.py:77-86` | reasoning.effort in request. |
| Anthropic thinking blocks returned as THINKING content parts | PASS | `unified-llm-client/unified_llm/adapters/anthropic.py:449-498`, `unified-llm-client/tests/dod/test_8_5_reasoning.py:94-149` | `TestAnthropicThinkingBlocks`. |
| Thinking block `signature` field preserved for round-tripping | PASS | `unified-llm-client/unified_llm/types.py:103-109`, `unified-llm-client/tests/dod/test_8_5_reasoning.py:94-149` | `ThinkingData.signature` preservation. |
| Gemini thinking tokens mapped to `reasoning_tokens` in Usage | PASS | `unified-llm-client/unified_llm/adapters/gemini.py:448-475`, `unified-llm-client/tests/dod/test_8_5_reasoning.py:157-180` | `thoughts_token_count` -> `reasoning_tokens`. |
| Usage reports `reasoning_tokens` as distinct from `output_tokens` | PASS | `unified-llm-client/unified_llm/types.py:204-230`, `unified-llm-client/tests/dod/test_8_5_reasoning.py:188-202` | `TestUsageDistinction`. |

---

### 8.6 Prompt Caching (9 items)

| DoD Item | Status | Evidence | Notes | Interpretation |
|---|---|---|---|---|
| OpenAI: caching works automatically via Responses API | PASS | `unified-llm-client/unified_llm/adapters/openai.py:524-527`, `unified-llm-client/tests/dod/test_8_6_caching.py:46-72` | `TestOpenAICaching`. | |
| OpenAI: `Usage.cache_read_tokens` populated | PASS | `unified-llm-client/unified_llm/adapters/openai.py:524-527`, `unified-llm-client/tests/dod/test_8_6_caching.py:46-72` | `input_tokens_details.cached_tokens`. | |
| Anthropic: `cache_control` breakpoints injected automatically | PASS | `unified-llm-client/unified_llm/adapters/anthropic.py:410-443`, `unified-llm-client/tests/dod/test_8_6_caching.py:80-141`, `unified-llm-client/tests/adapter/test_anthropic.py:1126` | `_inject_cache_control()`, `TestPromptCaching`. | |
| Anthropic: `prompt-caching-2024-07-31` beta header included | PASS | `unified-llm-client/unified_llm/adapters/anthropic.py:410-443`, `unified-llm-client/tests/adapter/test_anthropic.py:1163-1175` | `test_beta_header_included`. | |
| Anthropic: `cache_read_tokens` and `cache_write_tokens` populated | PASS | `unified-llm-client/unified_llm/adapters/anthropic.py:511-524`, `unified-llm-client/tests/dod/test_8_6_caching.py:80-141` | `_map_usage()`. | |
| Anthropic: auto caching can be disabled via `provider_options` | PASS | `unified-llm-client/unified_llm/adapters/anthropic.py:267-272`, `unified-llm-client/tests/dod/test_8_6_caching.py:123-141` | `auto_cache` option. | |
| Gemini: automatic prefix caching works | PASS | `unified-llm-client/unified_llm/adapters/gemini.py:465-467`, `unified-llm-client/tests/dod/test_8_6_caching.py:149-172` | `TestGeminiCaching`. | |
| Gemini: `cache_read_tokens` populated | PASS | `unified-llm-client/unified_llm/adapters/gemini.py:465-467`, `unified-llm-client/tests/dod/test_8_6_caching.py:149-172` | `cached_content_token_count`. | |
| Multi-turn session: cache_read_tokens >50% at turn 5+ | FAIL | — | No multi-turn cache efficiency test exists. Per-provider cache token extraction is unit-tested, but no integration test verifies the >50% threshold across 5+ turns. | This requirement specifies a quantitative integration test across all 3 providers with real API keys. The underlying cache token tracking is implemented and unit-tested per provider. The missing piece is the multi-turn agentic integration test that verifies the efficiency threshold. This would require real API keys and is inherently an integration-level verification. |

---

### 8.7 Tool Calling (11 items)

| DoD Item | Status | Evidence | Notes | Interpretation |
|---|---|---|---|---|
| Active tools (execute handler) trigger automatic tool execution loops | PASS | `unified-llm-client/unified_llm/generate.py:280-309`, `unified-llm-client/tests/dod/test_8_7_tool_calling.py:137-169`, `unified-llm-client/tests/unit/test_generate.py:291` | Tool execution loop in generate(). | |
| Passive tools (no handler) return tool calls to caller | PASS | `unified-llm-client/unified_llm/generate.py:304-305`, `unified-llm-client/tests/dod/test_8_7_tool_calling.py:178-201` | No execute -> return. | |
| `max_tool_rounds` is respected | PASS | `unified-llm-client/unified_llm/generate.py:239,306-307`, `unified-llm-client/tests/dod/test_8_7_tool_calling.py:210-254` | Round counting. | |
| `max_tool_rounds = 0` disables automatic execution | PASS | `unified-llm-client/tests/dod/test_8_7_tool_calling.py:263-284` | Validated. | |
| Parallel tool calls: N calls -> N concurrent executions | PASS | `unified-llm-client/unified_llm/generate.py:870-907`, `unified-llm-client/tests/dod/test_8_7_tool_calling.py:293-329` | `asyncio.gather`. | |
| Parallel results sent back in single continuation request | PASS | `unified-llm-client/unified_llm/generate.py:280-309`, `unified-llm-client/tests/dod/test_8_7_tool_calling.py:293-329` | All results batched. | |
| Tool execution errors sent as error results (not exceptions) | PASS | `unified-llm-client/unified_llm/generate.py:888-903`, `unified-llm-client/tests/dod/test_8_7_tool_calling.py:338-408` | catch -> ToolResult(is_error=True). | |
| Unknown tool calls send error result (not exception) | PASS | `unified-llm-client/tests/dod/test_8_7_tool_calling.py:398-408` | Validated. | |
| `ToolChoice` modes (auto, none, required, named) translated per provider | PASS | `unified-llm-client/unified_llm/types.py:380-385`, `unified-llm-client/unified_llm/adapters/openai.py:410-420`, `unified-llm-client/unified_llm/adapters/gemini.py:533-547`, `unified-llm-client/tests/dod/test_8_7_tool_calling.py:416-434` | `TestToolChoiceModes`. | |
| Tool call argument JSON is parsed and validated before passing to handlers | PARTIAL | `unified-llm-client/unified_llm/generate.py:870-907` | JSON parsing occurs in `_execute_tools()`, but no explicit schema validation against tool parameter definitions. No dedicated test for argument validation. | The spec says "parsed and validated against the tool's parameter schema." JSON parsing happens (malformed JSON would error), but there is no explicit schema validation step that checks arguments against the tool's JSON schema definition. The loop-agent layer (`_validate_tool_arguments()`) does provide this validation, but at the ULC level it's only JSON parsing. |
| `StepResult` objects track each step's tool calls, results, and usage | PASS | `unified-llm-client/unified_llm/types.py:550-561`, `unified-llm-client/tests/dod/test_8_7_tool_calling.py:443-483` | `StepResult` dataclass. | |

---

### 8.8 Error Handling & Retry (9 items)

| DoD Item | Status | Evidence | Notes | Interpretation |
|---|---|---|---|---|
| All errors in hierarchy raised for correct HTTP status codes | PASS | `unified-llm-client/unified_llm/errors.py:215-279`, `unified-llm-client/tests/dod/test_8_8_error_handling.py:48-129` | `_STATUS_MAP`, `error_from_status_code()`. 12 status codes tested. | |
| `retryable` flag set correctly on each error type | PASS | `unified-llm-client/unified_llm/errors.py:20-208`, `unified-llm-client/tests/dod/test_8_8_error_handling.py:137-169` | `TestRetryableFlag`. | |
| Exponential backoff with jitter works | PASS | `unified-llm-client/unified_llm/retry.py:16-34`, `unified-llm-client/tests/dod/test_8_8_error_handling.py:177-207` | `TestExponentialBackoff`. | |
| `Retry-After` header overrides calculated backoff | PASS | `unified-llm-client/unified_llm/retry.py:64-73`, `unified-llm-client/tests/dod/test_8_8_error_handling.py:215-261` | `TestRetryAfterHeader`. | |
| `max_retries = 0` disables automatic retries | PASS | `unified-llm-client/unified_llm/retry.py:61-62`, `unified-llm-client/tests/dod/test_8_8_error_handling.py:270-283` | Validated. | |
| Rate limit errors (429) retried transparently | PASS | `unified-llm-client/tests/dod/test_8_8_error_handling.py:292-314` | Validated. | |
| Non-retryable errors (401, 403, 404) raised immediately | PASS | `unified-llm-client/unified_llm/retry.py:57-58`, `unified-llm-client/tests/dod/test_8_8_error_handling.py:322-336` | Validated. | |
| Retries apply per-step, not to entire operation | PASS | `unified-llm-client/unified_llm/generate.py:247-251`, `unified-llm-client/tests/dod/test_8_8_error_handling.py:344-393` | Retry wraps each step. | |
| Streaming does not retry after partial data delivered | PARTIAL | `unified-llm-client/unified_llm/generate.py:528-538`, `unified-llm-client/tests/unit/test_generate.py:740-755` | `test_stream_retry_on_initial_connection` tests retry on initial failure (not after partial). The test verifies initial connection retry but does not explicitly verify that partial-data streams are NOT retried. | The implementation correctly retries only on initial connection failure. The test `test_stream_retry_on_initial_connection` verifies retries work for initial failures. However, there is no dedicated test that explicitly delivers partial stream data and then verifies NO retry occurs. The behavior is correct by code inspection (`generate.py:528-538` only wraps initial connection), but the negative test case (partial data -> no retry) is not explicitly verified. Marked PARTIAL for conservative assessment. |

---

### 8.9 Cross-Provider Parity Matrix (15 x 3 = 45 items)

All 45 cells covered by parametrized tests in a single test file: `unified-llm-client/tests/dod/test_8_9_cross_provider_parity.py:1-755` (15 test cases x 3 providers: OpenAI, Anthropic, Gemini).

| Test Case | OpenAI | Anthropic | Gemini | Evidence |
|---|---|---|---|---|
| Simple text generation | PASS | PASS | PASS | Parametrized |
| Streaming text generation | PASS | PASS | PASS | Parametrized |
| Image input (base64) | PASS | PASS | PASS | Parametrized |
| Image input (URL) | PASS | PASS | PASS | Parametrized |
| Single tool call + execution | PASS | PASS | PASS | Parametrized |
| Multiple parallel tool calls | PASS | PASS | PASS | Parametrized |
| Multi-step tool loop (3+ rounds) | PASS | PASS | PASS | Parametrized |
| Streaming with tool calls | PASS | PASS | PASS | Parametrized |
| Structured output (generate_object) | PASS | PASS | PASS | Parametrized |
| Reasoning/thinking token reporting | PASS | PASS | PASS | Parametrized |
| Error handling (invalid API key -> 401) | PASS | PASS | PASS | Parametrized |
| Error handling (rate limit -> 429) | PASS | PASS | PASS | Parametrized |
| Usage token counts are accurate | PASS | PASS | PASS | Parametrized |
| Prompt caching (cache_read_tokens > 0 on turn 2+) | PASS | PASS | PASS | Parametrized |
| Provider-specific options pass through | PASS | PASS | PASS | Parametrized |

---

### 8.10 Integration Smoke Test (6 items)

All 6 items covered by `unified-llm-client/tests/dod/test_8_10_integration_smoke.py` (gated by `@pytest.mark.integration`, requires real API keys).

| DoD Item | Status | Evidence | Notes |
|---|---|---|---|
| Basic generation across all providers: text not empty, usage > 0, finish_reason == "stop" | PASS | `unified-llm-client/tests/dod/test_8_10_integration_smoke.py:44-74` | Validated. |
| Streaming: text chunks concatenate to full response text | PASS | `unified-llm-client/tests/dod/test_8_10_integration_smoke.py:76-108` | Validated. |
| Tool calling with parallel execution: 2+ steps, result mentions both cities | PASS | `unified-llm-client/tests/dod/test_8_10_integration_smoke.py:110-171` | Validated. |
| Image input: response text is not empty | PASS | `unified-llm-client/tests/dod/test_8_10_integration_smoke.py:173-229` | Validated. |
| Structured output: parsed object matches expected values | PASS | `unified-llm-client/tests/dod/test_8_10_integration_smoke.py:231-266` | Validated. |
| Error handling: nonexistent model raises NotFoundError | PASS | `unified-llm-client/tests/dod/test_8_10_integration_smoke.py:268-288` | Validated. |

---

## Consolidated Gaps

6 items total: 2 FAIL + 4 PARTIAL across all three specs.

### FAIL Items (2)

| Spec | Section | DoD Item | Gap Description | Interpretation |
|---|---|---|---|---|
| Attractor | 11.11 | HTTP server mode: POST /run, GET /status, POST /answer | Spec marks this as "(if implemented)". Not implemented. `PipelineRunTool` provides tool-based invocation, not HTTP endpoints. | The spec explicitly marks this requirement as conditional ("if implemented"). No HTTP server mode has been implemented. The `PipelineRunTool` (in the bundle) provides programmatic pipeline invocation but not the HTTP API surface described in spec. Marked FAIL per the hard rule that no evidence = FAIL, even though the spec's conditional phrasing suggests this is acceptable. |
| Unified LLM Client | 8.6 | Multi-turn session: cache_read_tokens >50% at turn 5+ | No integration test verifying cache efficiency threshold. Per-provider cache token extraction is unit-tested, but no multi-turn agentic test exists. | This requirement specifies a quantitative integration test across all 3 providers with real API keys. The underlying cache token tracking is implemented and unit-tested per provider. The missing piece is the multi-turn agentic integration test that verifies the efficiency threshold. This would require real API keys and is inherently an integration-level verification. |

### PARTIAL Items (4)

| Spec | Section | DoD Item | Gap Description | Interpretation |
|---|---|---|---|---|
| Coding Agent Loop | 9.4 | `LocalExecutionEnvironment` implements all file and command operations | No formal `LocalExecutionEnvironment` class. Env context builder + externally mounted tools provide equivalent functionality. | The spec describes a `LocalExecutionEnvironment` class. The implementation uses a `build_environment_context()` function + externally mounted tool modules (in bundle) instead of a single class. Functionally equivalent — all operations are available — but the architectural pattern differs from spec. Marked PARTIAL because the named class does not exist. |
| Coding Agent Loop | 9.4 | `ExecutionEnvironment` interface is implementable by consumers | No formal Protocol class. Tools mounted via ToolRegistry (implicit duck-typing). Consumers CAN extend, but via different mechanism. | The spec describes an `ExecutionEnvironment` interface for custom environments (Docker, K8s, WASM, SSH). The implementation uses implicit duck-typing via `ToolRegistry` mounting rather than a formal Protocol class. Consumers can register custom tools, achieving the same extensibility, but the named interface does not exist. |
| Unified LLM Client | 8.7 | Tool call argument JSON parsed and validated against schema | JSON parsing occurs at ULC layer, but no schema validation against tool parameter definitions. Loop-agent provides schema validation. | The spec says "parsed and validated against the tool's parameter schema." JSON parsing happens (malformed JSON would error), but there is no explicit schema validation step that checks arguments against the tool's JSON schema definition. The loop-agent layer (`_validate_tool_arguments()`) does provide this validation, but at the ULC level it's only JSON parsing. |
| Unified LLM Client | 8.8 | Streaming does not retry after partial data delivered | Implementation correct by code inspection. Initial connection retry tested. Negative case (partial data -> no retry) not explicitly tested. | The implementation correctly retries only on initial connection failure. The test `test_stream_retry_on_initial_connection` verifies retries work for initial failures. However, there is no dedicated test that explicitly delivers partial stream data and then verifies NO retry occurs. The behavior is correct by code inspection (`generate.py:528-538` only wraps initial connection), but the negative test case (partial data -> no retry) is not explicitly verified. Marked PARTIAL for conservative assessment. |

### Naming Convention Note

One additional item warranting note (counted as PASS with interpretation):

| Spec | Section | DoD Item | Note |
|---|---|---|---|
| Attractor | 11.8 | Question type names: SINGLE_SELECT, MULTI_SELECT, FREE_TEXT, CONFIRM | Implementation uses YES_NO, MULTIPLE_CHOICE, FREEFORM, CONFIRMATION. Functionally equivalent naming convention difference — all four question types are supported with identical behavior. |

---

## Errata

**2026-08-03 — §11.5 Retry Logic, row 1.** The row "Nodes with `max_retries > 0` are
retried on RETRY or FAIL outcomes | PASS" misstates the implemented behavior: the retry
ladder (`execute_with_retry()`) retries RETRY outcomes, retryable-classified exceptions
(classified by exception type and message content), and — since EXTENSIONS.md §27 —
`must_write=` artifact-contract violations. A plain FAIL outcome is returned
immediately and is never retried in place (`test_fail_not_retried`; the fail-fast
choice is deliberate). Graph-level `retry_target` and `condition="outcome=fail"` edges
are the mechanism for re-attempting failures. The DoD line's "or FAIL" does not hold in
this engine, and the PASS verdict should have flagged the divergence.
