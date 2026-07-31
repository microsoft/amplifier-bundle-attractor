# T0-4 Multi-Match Fan-Out Inventory

**Date:** 2026-07-31
**Task:** T0-4 — Restore pick-one-best-edge edge-selection conformance
**Purpose:** Mandatory investigation evidence required before engine edits.
The task requires a credible inventory of every shipped `.dot`, fixture graph,
and test that would behave differently under single-edge selection.

---

## Method

Swept all 46 `.dot` files across the three shipped-graph roots — `examples/`
(25 files), `modules/loop-pipeline/tests/fixtures/` (18 files), and
`modules/pipeline-runner/tests/fixtures/` (3 files) — for nodes with two or more
conditional outgoing edges.  For each such node, assessed whether conditions could
simultaneously match (i.e., are non-mutually-exclusive).

Reproduce the file census with:
`find examples modules/loop-pipeline/tests/fixtures modules/pipeline-runner/tests/fixtures -name '*.dot' | wc -l`
→ 46.

The stale-label multi-match hazard requires:
1. A bare `context.tool.last_line=X` edge (no `&& outcome=success`), AND
2. An `outcome=fail` edge on the same source node.

On the second visit after a failure, `tool.last_line` retains the prior success
value, so both conditions match simultaneously.

---

## Files Scanned

All 46 `.dot` files:

**`examples/patterns/`** (6 files):
- `demo-conversational-gates.dot`
- `demo-combined.dot`
- `demo-convergence-factory.dot`
- `conversational-gate.dot`
- `convergence-factory.dot`
- `task-runner.dot`

**`examples/pipelines/`** (19 files, including `practical/` and the
`11-manager-child-dotfile-hitl/` pair):
- `01-simple-linear.dot`
- `02-plan-implement-test.dot`
- `03-conditional-routing.dot`
- `04-retry-with-fallback.dot`
- `05-parallel-fan-out.dot`
- `06-model-stylesheet.dot`
- `07-fidelity-modes.dot`
- `08-human-gate.dot`
- `09-manager-supervisor.dot`
- `10-full-attractor.dot`
- `11-manager-child-dotfile-hitl/child-with-gate.dot`
- `11-manager-child-dotfile-hitl/parent.dot`
- `12-graph-resume.dot`
- `practical/bug-fix.dot`
- `practical/feature-build.dot`
- `practical/multi-lens-review.dot`
- `practical/pr-review.dot`
- `practical/refactor.dot`
- `practical/test-gen.dot`

**`modules/loop-pipeline/tests/fixtures/`** (18 files, including the 7 under
`integration/`):
- `parent_with_child.dot`
- `goal_gate.dot`
- `report_outcome_convergence.dot`
- `spec_simple_linear.dot`
- `spec_stylesheet.dot`
- `child_pipeline.dot`
- `conditional_branch.dot`
- `simple_linear.dot`
- `spec_branching.dot`
- `spec_smoke_test.dot`
- `spec_human_gate.dot`
- `integration/unified_llm_multi_step.dot`
- `integration/unified_llm_model_routing.dot`
- `integration/semport.dot`
- `integration/consensus_task.dot`
- `integration/unified_llm_parallel.dot`
- `integration/unified_llm_simple.dot`
- `integration/unified_llm_conditional.dot`

**`modules/pipeline-runner/tests/fixtures/`** (3 files):
- `fixture_box_writes_cwd.dot`
- `fixture_tool_reads_param.dot`
- `fixture_human_gate.dot`

---

## Nodes with 2+ Conditional Out-Edges

The following nodes have two or more conditional outgoing edges (potential
multi-match candidates).  Each is analyzed for whether conditions can
simultaneously match.

| File | Node | Edges | Can Simultaneously Match? | Behavior Delta |
|------|------|-------|--------------------------|----------------|
| `examples/patterns/conversational-gate.dot` | `check` | `preferred_label=scored`, `preferred_label=need_more` | No — only one preferred_label at a time | None |
| `examples/patterns/convergence-factory.dot` | `check` | `preferred_label=converged`, `preferred_label=refine` | No — only one preferred_label at a time | None |
| `examples/patterns/task-runner.dot` | `setup` | `last_line=ok`, `last_line=missing` | No — only one last_line value at a time | None |
| `examples/patterns/task-runner.dot` | `verify` | `last_line=green && outcome=success`, `outcome=fail`, `last_line=exhausted && outcome=success` | No — outcome=success and outcome=fail are mutually exclusive; both last_line edges require outcome=success | None |
| `examples/patterns/task-runner.dot` | `triage` | `last_line=novel`, `last_line=repeat`, `last_line=exhausted` | No — only one last_line value at a time | None |
| `examples/patterns/task-runner.dot` | `diagnose_gate` | `last_line=continue`, `last_line=blocked` | No — only one last_line value at a time | None |
| `examples/patterns/task-runner.dot` | `verdict` | `last_line=ship && outcome=success`, `outcome=fail`, `last_line=stall && outcome=success` | No — outcome=success and outcome=fail are mutually exclusive | None |
| `examples/patterns/task-runner.dot` | `ship_check` | `last_line=shipped`, `last_line=dirty` | No — only one last_line value at a time | None |
| `examples/pipelines/09-manager-supervisor.dot` | `gate` | `last_line=pass`, `last_line=fail` | No — only one last_line value at a time | None |
| `examples/pipelines/12-graph-resume.dot` | `check_smells` | `last_line=todo`, `last_line=done` | No — only one last_line value at a time | None |
| `examples/pipelines/12-graph-resume.dot` | `check_plan` | `last_line=todo`, `last_line=done` | No — only one last_line value at a time | None |
| `examples/pipelines/12-graph-resume.dot` | `check_snapshot` | `last_line=todo`, `last_line=done` | No — only one last_line value at a time | None |
| `examples/pipelines/12-graph-resume.dot` | `check_tests_done` | `last_line=todo`, `last_line=done` | No — only one last_line value at a time | None |
| `examples/pipelines/12-graph-resume.dot` | `test_gate` | `last_line=pass`, `last_line=fail` | No — only one last_line value at a time | None |
| `examples/pipelines/10-full-attractor.dot` | `test_gate` | `last_line=pass`, `last_line=fail` | No — only one last_line value at a time | None |
| `examples/pipelines/03-conditional-routing.dot` | `gate` | `last_line=pass`, `last_line=fail` | No — only one last_line value at a time | None |
| `examples/pipelines/practical/feature-build.dot` | `test_gate` | `last_line=pass`, `last_line=fail` | No — only one last_line value at a time | None |
| `examples/pipelines/practical/bug-fix.dot` | `test_gate` | `last_line=exhausted && outcome=success`, `last_line=pass && outcome=success`, `last_line=fail` | No — different last_line values; the two `outcome=success` edges are mutually exclusive with the `outcome=fail` edge | None |
| `examples/pipelines/practical/bug-fix.dot` | `triage` | `last_line=novel`, `last_line=repeat` | No — only one last_line value at a time | None |
| `examples/pipelines/practical/bug-fix.dot` | `verdict_gate` | `last_line=ship && outcome=success`, `last_line=iterate && outcome=success` | No — only one last_line value at a time | None |
| `examples/pipelines/practical/refactor.dot` | `test_gate` | `last_line=pass`, `last_line=fail` | No — only one last_line value at a time | None |
| `examples/pipelines/practical/test-gen.dot` | `test_gate` | `last_line=pass`, `last_line=fail` | No — only one last_line value at a time | None |
| `modules/loop-pipeline/tests/fixtures/goal_gate.dot` | `implement` | `outcome=success`, `outcome=fail` | No — mutually exclusive | None |
| `modules/loop-pipeline/tests/fixtures/goal_gate.dot` | `review` | `outcome=success`, `outcome=fail` | No — mutually exclusive | None |
| `modules/loop-pipeline/tests/fixtures/report_outcome_convergence.dot` | `assess` | `outcome=converged`, `outcome=refine` | No — only one outcome at a time | None |
| `modules/loop-pipeline/tests/fixtures/conditional_branch.dot` | `test` | `outcome=success`, `outcome=fail` | No — mutually exclusive | None |
| `modules/loop-pipeline/tests/fixtures/spec_branching.dot` | `validate` | `outcome=success`, `outcome!=success` | No — mutually exclusive | None |
| `modules/loop-pipeline/tests/fixtures/spec_smoke_test.dot` | `implement` | `outcome=success`, `outcome=fail` | No — mutually exclusive | None |
| `modules/loop-pipeline/tests/fixtures/spec_smoke_test.dot` | `review` | `outcome=success`, `outcome=fail` | No — mutually exclusive | None |
| `modules/loop-pipeline/tests/fixtures/integration/semport.dot` | `TestValidate` | `outcome=yes`, `outcome=retry` | No — only one outcome at a time | None |
| `modules/loop-pipeline/tests/fixtures/integration/semport.dot` | `FetchUpstreamSonnet` | `outcome=process`, `outcome=done` | No — only one outcome at a time | None |
| `modules/loop-pipeline/tests/fixtures/integration/semport.dot` | `AnalyzePlanSonnet` | `outcome=port`, ... | No — only one outcome at a time | None |
| `modules/loop-pipeline/tests/fixtures/integration/consensus_task.dot` | `CheckDoD` | `outcome=needs_dod` (×3), `outcome=has_dod` (×3) | **YES** — three edges shared the same condition; under the old dialect all three matching targets fanned out in parallel | **Behavior changed — fixture MIGRATED in this change.** See migration note below. |

### Migration note: `consensus_task.dot` (completed in this change)

`consensus_task.dot` originally had six outgoing conditional edges from
`CheckDoD` — three with `outcome=needs_dod` and three with `outcome=has_dod`.
This was the multi-match fan-out pattern the engine has now retired: it was
the ONE shipped `.dot` whose runtime behavior relied on the retired dialect.
The graph's intent is to fan out to three parallel LLM agents (`Gemini`,
`GPT`, `Opus`) for consensus; under single-edge selection the old topology
would have silently degraded to a single lexical-winner agent.

**Migration (done in this change, not deferred):** the fixture now expresses
its three-agent intent via the spec-sanctioned explicit-parallelism
constructs — `shape=component` fan-out nodes (`DefineDoD_FanOut`,
`Plan_FanOut`, `Review_FanOut`) that dispatch all three agent branches via
`ParallelHandler`, converging on `shape=tripleoctagon` fan-in nodes
(`DefineDoD_Join`, `Plan_Join`, `Review_Join`). Conditional routing from
`CheckDoD` is now single-edge per outcome, exactly as spec §3.3 prescribes.
The migration also extends the three-agent intent to the planning-after-DoD
and review stages, which under the OLD engine silently ran only one agent
(unconditional multi-edges never fanned out — the old dialect only applied
to conditional edges), even though the fixture's own consolidation prompts
read all three agents' outputs (`.ai/plan_*.md`, `.ai/review_*.md`). The
fixture now delivers its documented consensus intent at every stage.

The seven `TestConsensusPipeline` execution/parse tests in
`test_dot_integration.py` were updated in lockstep (node inventory 17 → 23,
mock-response sequences, and assertions that ALL three agents run at each
fan-out stage). Verified green in an environment with `unified_llm`
installed: `uv run --extra remote pytest tests/test_dot_integration.py`
— 34 passed.

---

## Stale-Label Hazard Check

Specifically checked for nodes with a bare `context.tool.last_line=X` edge
(no `&& outcome=success`) co-present with an `outcome=fail` edge on the same
source node — the pattern that would simultaneously match on the second visit
after a failure:

**Result: NONE found.**

All shipped `.dot` files that use `context.tool.last_line=X` edges either:
- Use different `last_line` values on all outgoing edges (mutually exclusive
  by value — only one can be set at a time), OR
- Apply the `&& outcome=success` conjunction on all `last_line` edges that
  share a source with an `outcome=fail` edge.

---

## Test Suite Check

Checked `modules/loop-pipeline/tests/` for tests that specifically asserted
multi-match fan-out behavior for non-component nodes:

| Test | File | Prior behavior | Updated behavior |
|------|------|----------------|------------------|
| `test_non_component_multi_edge_fanout_still_works` | `test_engine_bug_g_parallel_component_fanout.py` | Asserted both b1 and b2 run (fan-out) | Renamed to `test_non_component_single_edge_selection`; asserts only b1 runs (lexical winner) |
| `test_non_component_fanout_respects_max_parallel` | `test_parallel_fanout_contract.py` | Asserted 4 branches all run with max_parallel=2 bounded concurrency | Renamed to `test_non_component_multi_match_selects_one_edge`; asserts only Branch1 runs (lexical winner) |
| `test_g6b_multiedge_fanout_branch_outcomes_reach_parent` | `test_parallel_branch_nested_isolation.py` | Asserted branch outcomes in engine.node_outcomes (non-component fan-out path) | Updated to assert parallel.results populated by ParallelHandler (shape=component path, which _make_multiedge_graph already uses) |
| `TestConsensusPipeline` (7 tests: parse, provider identification, has_dod, needs_dod, review_pass, review_retry, provider events) | `test_dot_integration.py` | Described and exercised the conditional three-way fan-out from `CheckDoD` (fixture relied on the retired dialect) | Fixture migrated to `shape=component` explicit parallelism; tests updated in lockstep (node inventory, mock-response sequences, all-three-agents assertions) — see migration note above |

`select_all_matching_edges` in `edge_selection.py:113` is no longer called
from the engine's main dispatch path (retired by T0-4).  It remains in the
module as a test/analysis utility, exercised by its own unit tests in
`test_edge_selection.py:336-414` and used in `test_dot_parser.py:657`.
It is not reachable as an unledgered fan-out capability from the engine's
main loop.  The engine-level `_execute_parallel_fan_out` helper that backed
the retired dialect has been deleted outright (its only call site was the
retired multi-match gate); explicit parallelism runs through
`ParallelHandler` (`run_subgraph` per branch), and the engine keeps only the
`_find_fan_in_node` convergence discovery used by the component-node path.

---

## Decision: Direct Fix vs Flag-Gated

**Decision: Direct fix, plus migration of the one shipped graph that relied
on the dialect.**

Evidence:
1. Exactly ONE shipped `.dot` file relied on multi-match fan-out:
   `modules/loop-pipeline/tests/fixtures/integration/consensus_task.dot`
   (`CheckDoD`, three edges per outcome value). Every other
   multi-conditional edge pair in the 46 scanned files is mutually
   exclusive by design (see table above).
2. That fixture has been migrated in this same change to spec-sanctioned
   `shape=component` explicit parallelism (see migration note above), so no
   shipped graph behaves differently after the fix in a way its tests do
   not now assert intentionally.
3. Tests that asserted the old fan-out behavior — three unit/contract tests
   plus the seven `TestConsensusPipeline` integration tests — have all been
   updated to assert the new spec-conformant behavior (single-edge
   selection for conditional multi-match; explicit component fan-out where
   parallelism is intended).
4. The conjunction discipline (`&& outcome=success`) was applied everywhere
   the stale-label hazard was present — so those graphs already work correctly
   under single-edge selection.

A flag-gated escape hatch is not needed.  No graph retains the retired
dialect, so there is nothing to ledger as an opt-in extension.  The direct
fix plus in-change migration is the honest shape.
