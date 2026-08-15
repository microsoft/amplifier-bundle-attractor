# Design: The Spec Conformance Matrix

**Repo:** `microsoft/amplifier-bundle-attractor` (design written against `origin/main` @ `0241d85`)
**Commissioned:** 2026-08-15 — *"what do we have in place to explicitly prevent drift — this domain's
equivalent of a linting tool, but considering more the semantics/spec of the original nlspec?"*
**Status:** **TRANCHE 1 SHIPPED.** Feasibility of the three hardest rows was verified by executed
probes before implementation (§10; 5/5 passing in 0.22s against a `/tmp` worktree of `origin/main`);
tranche 1 then landed as `specs/conformance/attractor-matrix.yaml` (38 rows) plus
`modules/loop-pipeline/tests/test_spec_conformance_matrix.py` (198 passed, 24 skipped, 0.3s). The
document below is the design as written; where construction trued a number up, an
**implementation note** says so inline rather than rewriting the design after the fact.

**Implementation notes (2026-08-15):**

- **Row count: 38, not 35.** §7's own table B lists 24 rows under a "23 rows" header — the
  arithmetic in the design was off by one, and the built matrix carries all 24. The other two are
  the F1 and F4 rows from §13, which the design named as required tranche-1 work without counting
  them in the §7 tally. Final: 1 SYNC + 7 divergence/extension + 4 OPEN-PINNED/absence + 2 findings
  + 24 conformances.
- **Schema addition: `spec.also`.** An optional list of `{section, quote}`, verified verbatim
  exactly like `spec.quote`. Earned by ATX-M-012, whose divergence spans two non-contiguous spec
  statements (§2.7's "fresh log directory" and §3.2 step 7's `restart_run(); RETURN`); anchoring
  only one would leave half the assertion uncited. Used by three rows.
- **Schema addition: `ledger.issue`.** A third legal cite form, permitted **only** on `OPEN-PINNED`
  rows. F1 and F4 are unledgered by definition — that is the finding — so they cite the issue
  carrying the pending decision (#234) instead of a ledger row that does not exist yet. The runner
  rejects `ledger.issue` on any other disposition: a decided disposition owes a real ledger entry.
- **F4 resolved empirically, and it does not hold.** §13 predicted the Appendix A
  `reasoning_effort="high"` default would fail. Confirmed: `Node.reasoning_effort` defaults to
  `None` and `None` reaches the backend, so the provider's own default applies. Pinned as
  `ATX-M-F04`; filed with F1 as issue #234.
- **The tripwire's banner detector reads BOLD declaratives only.** A naive `DIVERG` substring
  search over entry banners flags §§24, 25, 29, 32, 33, 35 — but §§29/32/35 merely use the word in
  `upstream action:` boilerplate. Matching `**...DIVERG(ES|ENCE)...**` inside the banner yields
  exactly §§24, 25, 33, which is the true set. The precision is regression-tested against synthetic
  positive and negative banners.

---

## 1. The problem, stated precisely

The repo has three drift defenses today, and a hole between them:

| Defense | What it pins | What it cannot see |
|---|---|---|
| Doc guards (`test_doc_consistency.py`, `test_engine_semantics_doc_guard.py`, `test_explainer_doc_guard.py`) | Numbers/strings in *our docs* against *our code* | The upstream spec's semantics |
| Ledgers (`SPEC_CONFORMANCE.md` ATX/CAL/ULM rows; `specs/EXTENSIONS.md` §§1–36) | Every *decided* disposition, in prose | Nothing — they are human-maintained records with no executable teeth |
| The test suite (~2,000 tests) | Engine behavior | Which spec sentence each behavior answers to, and which ledger entry licenses each divergence |

**Drift** = any behavioral movement not recorded in the ledger, **in either direction**:

1. **Away from spec, unledgered** — the classic case (ATX-11 lived this way for months: the
   dead-end hard-fail shipped in the initial commit, was load-bearing for
   `bug-fix.dot`'s `escalated` node, and was only ledgered by a later audit).
2. **Back toward spec, unledgered** — *un-diverging silently is drift too.* If someone
   "fixes" the engine to match spec §3.2 step 6 (dead end → SUCCESS), `EXTENSIONS.md` §33 and
   ATX-11 become lies, and `bug-fix.dot` silently reports success on its designed failure path.
   The ledger lying is the same incident class as the engine lying.

The matrix makes the ledger **load-bearing**: an executable, CI-run mapping from the canonical
spec's normative statements to assertions against the real engine, where decided divergences are
asserted as firmly as conformances. A flipped assertion fails with a message naming the exact
ledger entry that must move with the behavior.

---

## 2. Decisions at a glance

| Question | Decision |
|---|---|
| Unit (row) | YAML row: `id` + `spec{section, quote, line(info-only), conflict?}` + `disposition` (6-value vocabulary incl. **OPEN-PINNED**) + `ledger{conformance, extensions}` + `assertion{kind: probe\|indexed\|absence\|none, …}` + `justification` |
| Mechanics | Declarative data file `specs/conformance/attractor-matrix.yaml` consumed by one parametrized module `modules/loop-pipeline/tests/test_spec_conformance_matrix.py`. Structural integrity parametrized per row; behavioral probes are named functions keyed by row id, cross-checked both ways |
| Divergences | Every DIVERGE-DECIDED row asserts (a) our documented behavior occurs AND (b) the spec's behavior does **not** occur, where cheap. Both halves carry the same flip message |
| Anchoring | Verbatim spec **quotes**, runner-verified byte-for-byte against the canonical file, + a pinned **sha256** of the canonical file recording upstream `fb57a55`. Line numbers stored but *informational only* — never asserted |
| Coverage | ~154 rows total: ~123 INDEXED to existing tests, ~21 new probes (incl. 4 absence assertions + the SYNC row), ~10 NOT-ASSERTABLE with per-row justification |
| Probes | All tranche-1 probes run in-process against `PipelineEngine` + `MockBackend`-style doubles: deterministic, no LLM, no network, milliseconds each. Verified by execution (§10) |
| Scope | v1 = attractor spec only. ULM/CAL named as tranche 3 with the same schema (§12) |

---

## 3. Q1 — The unit: row schema

The commissioned proposal is close; four amendments, each earned by something found in the repo.

```yaml
# specs/conformance/attractor-matrix.yaml  (illustrative row — the ATX-11 divergence)
- id: ATX-M-011                      # stable forever; never renumbered, never reused
  title: dead-end hard-fail (main loop)
  spec:
    section: "3.2"                   # canonical § anchor
    heading: "Core Execution Loop"
    quote: |                         # verbatim; runner-verified against canonical bytes
      IF next_edge is NONE:
          IF outcome.status == FAIL:
              RETURN outcome
          RETURN Outcome(status=SUCCESS, notes="Pipeline completed")
    line: 390                        # informational only — NEVER asserted (see §9)
  disposition: DIVERGE-DECIDED
  ledger:
    conformance: ATX-11              # SPEC_CONFORMANCE.md row id (checked to exist)
    extensions: 33                   # specs/EXTENSIONS.md § number (checked to exist)
  assertion:
    kind: probe
    probe: test_row_atx_m_011        # function in the matrix module (checked to exist)
    indexed:                         # existing coverage this row ALSO indexes (checked to exist)
      - modules/loop-pipeline/tests/test_engine.py::test_no_matching_edge_returns_fail
      - modules/loop-pipeline/tests/test_pipeline_events.py::TestErrorEvents::test_emits_error_on_no_edge
  notes: >
    Load-bearing for examples/pipelines/practical/bug-fix.dot ('escalated' node).
    Paired doc guard: test_engine_semantics_doc_guard.py D-200/D-202.
```

**Field semantics and the four amendments:**

1. **`disposition` gains `OPEN-PINNED`** (full vocabulary: `CONFORM | DIVERGE-DECIDED |
   EXTENSION | NOT-IMPLEMENTED-DECIDED | OPEN-PINNED | NOT-ASSERTABLE`). The ledger has
   honest OPEN items with pending dispositions (ATX-6 retry-on-FAIL, ATX-7 literal
   unquoting, ATX-3 tool_hooks, CAL-3). The matrix must not launder them into
   "DIVERGE-DECIDED" — that would forge a decision the maintainer hasn't made. OPEN-PINNED
   asserts *current behavior* and cites the open ledger item; its flip message says "either
   this PR is the ALIGN decision (update ATX-n to DONE and this row to CONFORM) or it is an
   accident (revert)." Undecided ≠ unpinned.
2. **`assertion.kind: absence`** — for decided/open *omissions* (tool_hooks §9.7, HTTP mode
   §9.5, extended condition operators §10.7). The assertion is that the feature is *absent*
   (e.g. `grep tool_hooks` over `loop-pipeline` source = 0 hits). This is the "un-diverging
   silently is drift" mechanism for the not-implemented direction: someone who ships
   tool_hooks without touching ATX-3 gets a red matrix, not a quiet green.
3. **`spec.conflict`** (optional second `{section, quote}`) — the canonical spec contradicts
   *itself* in at least three places (§3.5 `:519` vs DoD `:1833` on retry-on-FAIL; §7.2
   reachability=ERROR vs DoD 11.12 "orphan → warning"; §4.6 pseudocode returns
   `suggested_next_ids` vs DoD 11.6 "returns selected label as preferred_label"). A row
   pinning one side must name the other side, or the matrix inherits the spec's ambiguity.
4. **`failure_contract` is generated, not hand-written.** One helper renders the flip
   message from row fields (§8). Hand-written per-row prose rots; the ledger-guard tests'
   great failure messages work because the *shape* is uniform. Rows may append a
   row-specific hint via `notes`, which the renderer includes.

`ledger` is required (non-null) for every disposition except `CONFORM` and `NOT-ASSERTABLE`;
`justification` is required for `NOT-ASSERTABLE` and `OPEN-PINNED`. `assertion.kind: none` is
legal **only** for `NOT-ASSERTABLE`.

---

## 4. Q2 — Mechanics: YAML table + one parametrized module

**Decision: declarative data file + parametrized pytest module.** Specifically:

```
specs/conformance/attractor-matrix.yaml            # the matrix (a document, reviewed as one)
modules/loop-pipeline/tests/test_spec_conformance_matrix.py   # the runner (one module)
```

**Why not a pure-Python table:** the maintainer must be able to read the matrix *like a
document* — next to `specs/EXTENSIONS.md`, in the same directory family, one row per YAML
block, diffs that read as "row ATX-M-011 changed disposition." A Python dict-of-dicts is the
same information wearing a worse reading costume, and it drags every row edit through code
review conventions (imports, formatting, lint) that have nothing to say about dispositions.
The repo's own precedent agrees: the ledgers are Markdown documents and the guard tests
*read* them (`test_extensions_ledger_integrity.py` regex-walks `EXTENSIONS.md`); the matrix
is the same pattern with the arrow reversed (the document drives the tests).

**Why YAML and not Markdown-table:** rows carry multi-line verbatim quotes and lists of
node ids; YAML block scalars handle both losslessly. A Markdown table would force quote
mangling — and the quote is the load-bearing anchor (§9).

**The runner module does exactly four things:**

1. **Structural integrity, parametrized per row** (this is the ledger made load-bearing):
   - `spec.quote` appears **verbatim** in `specs/canonical/attractor-spec-canonical.md`
     (whitespace-normalized within lines, line-structure preserved for block quotes);
   - `ledger.conformance` id exists as a row in `SPEC_CONFORMANCE.md`; `ledger.extensions`
     number exists as a `## N.` heading in `specs/EXTENSIONS.md` (reusing
     `test_extensions_ledger_integrity.py`'s heading regex);
   - every `assertion.indexed` entry resolves: the file exists and the named
     test/class-method exists in it — verified by **AST parse, never import** (indexed
     cites cross module venv boundaries, e.g. `pipeline-runner`'s resume e2e; the repo's CI
     runs each module's suite in its own venv, so the matrix must not import across them);
   - disposition vocabulary, id uniqueness, required-field presence, and both directions of
     the probe cross-check (every `kind: probe` row names an existing function; every
     `test_row_*` function corresponds to a row).
2. **Behavioral probes**: plain async pytest functions named `test_row_<id>`, in-process
   engine runs with mock backends (§10 proves the pattern), each ending in asserts whose
   messages are rendered by the flip-message helper (§8).
3. **The SYNC row** (§9): sha256 of the canonical file matches the pin.
4. **A coverage tripwire**: every `## N.` entry in `EXTENSIONS.md` whose banner says
   DIVERGES must be cited by ≥1 matrix row, and every `ATX-*` ledger row with disposition
   DIVERGE must be cited by ≥1 row — so a *future* divergence cannot be ledgered without
   also being asserted. (The reverse — new spec sections — is handled by the SYNC row.)

**Repo-discovery and style** follow the guard-test precedents exactly: `BUNDLE_ROOT =
Path(__file__).parent.parent.parent.parent`, module docstring naming the incident class it
guards, "Honest limits" section, RED/GREEN regression proofs for the *checker logic itself*
(synthetic matrix rows with a wrong quote / missing ledger cite / dangling indexed cite must
be flagged). One deliberate departure: the guard tests' skip-if-absent discipline applies to
*optional* docs; the matrix file is **not optional** — a missing or unparseable
`attractor-matrix.yaml` is a hard failure, because a silently-skipped matrix is a matrix
that isn't load-bearing.

**CI cost:** one module, in-process probes, no LLM, no network (`test_no_network_dep.py`
discipline applies). Probe corpus at tranche-1 scale measured at ~0.25s (§10); structural
checks are file reads + AST parses over ~40 files. Budget: well under 10s inside the
existing `uv run pytest -q` loop-pipeline job. No new CI job needed.

---

## 5. Q3 — Asserting divergences: the pattern, fully worked

**The rule:** a DIVERGE-DECIDED row must prove **(a)** the engine does our documented
behavior, and **(b)** the spec's behavior does *not* occur — where (b) is cheap, i.e. an
extra assert on state already in hand, never a second engine run. Half (b) is what makes
un-diverging loud: a PR that re-aligns to spec keeps (a) failing, but (b) is the assert that
*names the spec behavior as the thing that just appeared*.

**Fully worked: ATX-M-011, the dead-end hard-fail (ATX-11 / EXTENSIONS §33 vs spec §3.2
step 6).** This exact code executed green against `origin/main` (§10, probe P1); only the
message rendering is added here.

```python
# in modules/loop-pipeline/tests/test_spec_conformance_matrix.py

class SuccessNoMatchBackend:
    """Explicit SUCCESS whose preferred_label matches no edge -> dead end."""
    async def run(self, node, prompt, context, incoming_edge=None, graph=None):
        return Outcome(status=StageStatus.SUCCESS, preferred_label="nomatch",
                       is_explicit=True)

@pytest.mark.asyncio
async def test_row_atx_m_011(tmp_path):
    row = MATRIX["ATX-M-011"]
    dot = """
    digraph M011 {
        start [shape=Mdiamond]
        exit  [shape=Msquare]
        work  [prompt="do work"]
        sink  [prompt="unreached"]
        start -> work
        work -> sink [condition="outcome=fail"]   // exists but will not match
        sink -> exit
    }
    """
    hooks = EventCollector()
    engine = build_engine(dot, SuccessNoMatchBackend(), tmp_path, hooks=hooks)
    outcome = await engine.run()

    # (a) OUR ledgered behavior: hard-fail, traceable, loud.
    assert outcome.status == StageStatus.FAIL, flip(row,
        observed=f"pipeline outcome {outcome.status} on a dead end",
        expected="a dead end ALWAYS terminates with status=FAIL")
    assert outcome.failure_reason, flip(row,
        observed="empty failure_reason",
        expected="terminate_pipeline() carries a traceable failure_reason")
    assert any(e.get("error_type") == "no_matching_edge"
               for e in hooks.get(PIPELINE_ERROR)), flip(row,
        observed="no PIPELINE_ERROR error_type=no_matching_edge event",
        expected="the hard-fail emits PIPELINE_ERROR error_type=no_matching_edge")

    # (b) The SPEC behavior must NOT occur: spec §3.2 step 6 says a dead end
    # after a non-FAIL outcome returns Outcome(SUCCESS, "Pipeline completed").
    assert outcome.status != StageStatus.SUCCESS and \
           "Pipeline completed" not in (outcome.notes or ""), flip(row,
        observed="dead end resolved to the spec's SUCCESS 'Pipeline completed'",
        expected="the spec-literal dead-end-to-SUCCESS behavior stays absent",
        direction="UN-DIVERGENCE")
```

And the rendered failure when a future PR re-aligns to spec (see §8 for the contract):

```
SPEC-CONFORMANCE MATRIX FLIP — row ATX-M-011 "dead-end hard-fail (main loop)"
  spec:        §3.2 Core Execution Loop — "IF next_edge is NONE: ... RETURN
               Outcome(status=SUCCESS, notes=\"Pipeline completed\")"
  disposition: DIVERGE-DECIDED  (ledgered: SPEC_CONFORMANCE.md ATX-11;
               specs/EXTENSIONS.md §33)
  direction:   UN-DIVERGENCE — the engine now matches the spec text the ledger
               says we deliberately diverge from.
  observed:    dead end resolved to the spec's SUCCESS 'Pipeline completed'
  expected:    the spec-literal dead-end-to-SUCCESS behavior stays absent

  A dead-ended graph silently reporting success is the incident class this
  divergence exists to prevent (EXTENSIONS.md §33; the 2026-07-28 false-green
  run). examples/pipelines/practical/bug-fix.dot ('escalated') depends on the
  hard-fail. Two legal exits — in THIS PR, not a follow-up:
    1. Revert the behavior change. The ledger is the record of decided behavior.
    2. Keep the change AND move the record with it: update SPEC_CONFORMANCE.md
       ATX-11 (DIVERGE -> ALIGN, dated changelog line), rewrite EXTENSIONS.md §33,
       update this matrix row (disposition + assertion), and fix the paired doc
       guards (test_engine_semantics_doc_guard.py D-200/D-202,
       context/engine-semantics.md §3).
  Doing neither means main carries a ledger that lies. That is drift.
```

The same two-direction shape applies to every tranche-1 divergence: ATX-12 asserts
in-process reset **and** that no fresh log-directory root appears; §25 asserts the gate
rejects a status-only SUCCESS **and** (control) accepts an explicit one; §16 asserts FAIL
stops at unconditional edges **and** (control, already in
`test_edge_selection_no_silent_fallthrough.py`) SUCCESS traverses them; ATX-5 asserts
`outcome=` matches the label when `preferred_label` is set **and** falls back to status when
unset (both directions already exist in `test_conditions.py` — that row is INDEXED, no new
code).

---

## 6. Q4 — Coverage strategy and the row inventory

**Granularity rule:** one row per *normative statement cluster* — the smallest spec text a
PR could violate independently. Not per sentence (the §3.3 pseudocode is one algorithm, but
its five steps flip independently → five rows); not per section (§4.5 contains at least
eight independently-violable claims).

**NOT-ASSERTABLE is legal but earned per row.** Legitimate members found in the walk:
design-principles prose (§1.3), "single-threaded traversal simplifies reasoning" (§3.8
rationale — the *isolation* claims beside it are assertable and asserted), the §5.4
fidelity token-budget approximations ("~600 tokens" — we assert the mode vocabulary and
preamble shapes instead), manager-loop steer-cooldown prose, the observer-vs-stream event
consumption patterns (§9.6 tail), and §11's checklists *as such* (the matrix is their
operationalization; asserting "the checklist exists" would be circular). Each such row
carries a `justification` and `assertion.kind: none`. Ten rows total — ~6% of the matrix,
which is the honest size of the spec's aspirational prose.

**The inventory** (per-section counts; "indexed" names the suites that already carry the
behavior — the matrix *indexes* them, never duplicates them):

| Spec section | Rows | Indexed (existing suites) | New probes | Not-assertable |
|---|--:|--:|--:|--:|
| §1 Overview/principles | 2 | 0 | 0 | 2 |
| §2.1–2.4 grammar, constraints, value types | 4 | 4 — `test_dot_parser.py`, `test_node_timeout_units.py` (ATX-1) | 0 | 0 |
| §2.5 graph attrs + defaults | 3 | 3 — `test_retry.py::test_retry_policy_default_zero`, `test_fidelity.py::test_resolve_fidelity_default_is_compact`, `test_doc_consistency.py` | 0 | 0 |
| §2.6 node attrs | 6 | 5 — `test_retry.py` (arithmetic/inheritance), `test_spec_conformance_batch.py` (A1 auto_status, A2 quoted booleans), must_write/allow_partial suites | 1 (Appendix-A defaults spot-check, incl. `reasoning_effort` default — see F4 §13) | 0 |
| §2.7 edge attrs (`loop_restart`) | 2 | 1 — `test_fidelity.py` (edge fidelity/thread_id overrides) | 1 (**P2**: ATX-12 dual-direction) | 0 |
| §2.8 + §4.2 shape/handler dispatch | 3 | 1 — `test_no_silent_fallback.py::test_known_shapes_still_dispatch_correctly` | 2 (full 9-shape table parametrized; unknown-shape row — see F1 §13) | 0 |
| §2.9–2.13 chained edges, subgraphs, default blocks, class | 4 | 4 — `test_dot_parser.py`, `test_stylesheet.py` | 0 | 0 |
| §3.1 lifecycle phases | 1 | 0 | 0 | 1 |
| §3.2 core loop | 7 | 4 — `test_engine.py` (start resolution, terminal stop), `test_checkpoint.py::TestCheckpointEngineIntegration` (save-after-node), `test_pipeline_events.py` | 3 (**P1** dead-end dual-direction; step-4 built-in context keys; goal-gate CONTINUE jump) | 0 |
| §3.3 edge selection (+ §16 divergence) | 7 | 7 — `test_edge_selection.py` (25 tests: priority, normalization, tiebreaks, determinism), `test_edge_selection_no_silent_fallthrough.py` (§16 + runs_on), ATX-10 restoration | 0 | 0 |
| §3.4 goal gates (+ §25 gate rung) | 5 | 3 — `test_goal_gates.py` (GOAL-001…006), `test_engine.py::test_goal_gate_unsatisfied_returns_fail`, `test_goal_gate_retry_clears_failures.py` | 2 (**P3** is_explicit gate rung; 4-rung retry-target ladder incl. graph-level rungs) | 0 |
| §3.5–3.6 retry (+ ATX-6 pin) | 8 | 7 — `test_retry.py` (policy, backoff math, jitter, `TestShouldRetry` ×10), `test_spec_conformance_batch.py` (B6 preset table) | 1 (ATX-6: FAIL executes exactly once — OPEN-PINNED) | 0 |
| §3.7 failure routing | 2 | 2 — `test_failure_routing.py`, `test_folder_node_failure_routing.py` | 0 | 0 |
| §3.8 concurrency | 3 | 2 — `test_parallel.py`, `test_parallel_branch_nested_isolation.py`, `test_parallel_ignore_does_not_populate_failed_outputs.py` | 0 | 1 |
| §4.1–4.2 handler interface/registry | 3 | 2 — `test_handlers.py`, `test_validation.py::test_explicit_tool_type_wins_over_codergen_node_type` | 1 (re-register replaces) | 0 |
| §4.3–4.4 start/exit no-ops | 2 | 2 — `test_handlers.py` | 0 | 0 |
| §4.5 codergen (+ §25 parser rung) | 8 | 6 — `test_backend.py` (string→SUCCESS wrap for non-gates, `_parse_outcome` ladder), `test_engine_artifacts.py`, `test_run_directory.py`, `test_codergen_failure_capture.py` | 2 (**P3-prose**; simulation-mode text) | 0 |
| §4.6 human gate | 6 | 5 — `test_human.py` (61 tests: choices, timeout→default, SKIPPED→FAIL, accelerator table), `test_p2_conversational_gate.py` | 1 (unmatched-answer→first-choice fallback, if uncovered on audit) | 0 |
| §4.7 conditional handler | 1 | 1 — `test_conditional_handler.py` | 0 | 0 |
| §4.8 parallel (+ §18 extension) | 6 | 5 — `test_parallel.py`, `test_parallel_policies.py` (25), `test_parallel_early_exit.py`, `test_parallel_fanout_contract.py` | 0 | 1 |
| §4.9 fan-in | 4 | 4 — `test_fan_in_bfs.py`, `test_spec_conformance_batch.py` (B7 `best_outcome` key), handler tests | 0 | 0 |
| §4.10 tool handler | 4 | 4 — `test_handlers.py`, `test_node_timeout_units.py` (ATX-1), `test_p10_tool_env.py`, `test_tool_failure_capture.py` | 0 | 0 |
| §4.11 manager loop | 3 | 2 — `test_manager_loop.py`, `test_manager_steer_structured.py` | 0 | 1 |
| §4.12 custom handlers | 2 | 2 — `test_handlers.py`, `test_retry.py::RaisingHandler` (exception→FAIL) | 0 | 0 |
| §5.1 context | 4 | 2 — `test_context.py` (14), `test_handler_context.py` | 1 (engine-set built-in keys: `outcome`, `preferred_label`, `graph.goal`, `internal.retry_count.*`) | 1 |
| §5.2 outcome model | 2 | 2 — `test_outcome.py`, must_write SKIPPED tests | 0 | 0 |
| §5.3 checkpoint + resume | 6 | 6 — `test_checkpoint.py` (v2 superset keeps the six §5.3 fields), `test_engine_resume.py` (incl. `test_resume_degrades_first_full_hop_once` — rule 6), `test_resume_validation.py`, `test_no_implicit_resume.py`, `pipeline-runner/tests/test_resume_e2e.py` (SIGKILL) | 0 | 0 |
| §5.4 fidelity | 5 | 4 — `test_fidelity.py` (precedence, thread rungs, modes), `test_backend_fidelity.py`, `test_backend_full_continuity.py` | 0 | 1 |
| §5.5 artifact store | 3 | 3 — `test_artifacts.py` (27), `test_engine_artifacts.py` | 0 | 0 |
| §5.6 run directory | 2 | 2 — `test_run_directory.py` (19) | 0 | 0 |
| §6 interviewer protocol | 4 | 4 — `test_human.py` (Queue/AutoApprove/Callback/timeout) | 0 | 0 |
| §7 validation | 5 | 5 — `test_validation.py` (41: every §7.2 rule, `validate_or_raise`, diagnostic fields), `test_topological_lint.py` | 0 | 0 |
| §8 stylesheet | 4 | 4 — `test_stylesheet.py` (25: selectors, specificity, explicit-wins) | 0 | 0 |
| §9.1–9.4 transforms/composition | 3 | 3 — `test_transforms.py`, `test_subgraph_runner.py`, `test_pipeline_handler.py` | 0 | 0 |
| §9.5 HTTP server mode | 1 | 0 | 1 (absence — NOT-IMPLEMENTED-DECIDED, ATX-4) | 0 |
| §9.6 events | 3 | 2 — `test_pipeline_events.py` (34), `hooks-pipeline-observability` suites | 0 | 1 |
| §9.7 tool call hooks | 1 | 0 | 1 (absence — OPEN-PINNED, ATX-3) | 0 |
| §10 conditions (+ ATX-5, ATX-7, §10.7) | 8 | 6 — `test_conditions.py` (39: ops, missing-key→"", bare-key truthiness, ATX-5 both directions, `context.` prefix retry) | 2 (ATX-7 literal-unquoting pin; §10.7 extended-operators absence) | 0 |
| §11 DoD as such | 1 | 0 | 0 | 1 |
| Appendix A attribute reference | 2 | 1 — doc guards | 1 (defaults spot-check, shared with §2.6 probe) | 0 |
| Appendix C status-file contract | 2 | 2 — `test_spec_conformance_batch.py` (A1 synthesis), `test_run_directory.py`, `test_outputs_attribute.py` | 0 | 0 |
| Appendix D error categories | 1 | 1 — `test_retry.py::TestShouldRetry` | 0 | 0 |
| SYNC canonical pin | 1 | 0 | 1 (sha256) | 0 |
| **Totals** | **154** | **123** | **21** | **10** |

Counts are the design's estimate at cluster granularity; tranche construction trues them up
(rows may split, never silently vanish — id retirement requires a matrix-file comment naming
the successor rows).

---

## 7. Q5 — Tranche 1: the 35 load-bearing rows

Membership rule: **every decided divergence (non-negotiable), every OPEN ledger item
pinned, the SYNC row, and the behaviors a community `.dot` file stakes its correctness on**
(Compatibility-doctrine rule 2). Quotes abbreviated here; the matrix file carries them
verbatim.

**A. Divergences and extensions with teeth (11 rows — all mandatory):**

| id | Spec ref (quote gist) | Disposition | Ledger | Assertion |
|---|---|---|---|---|
| ATX-M-011 | §3.2 step 6 `:388-393` — dead end + non-FAIL ⇒ `Outcome(SUCCESS, "Pipeline completed")` | DIVERGE-DECIDED | ATX-11; EXT §33 | **probe** (P1 ✅): FAIL + `PIPELINE_ERROR error_type=no_matching_edge` + spec-SUCCESS absent; indexed: `test_engine.py::test_no_matching_edge_returns_fail`, `test_pipeline_events.py::TestErrorEvents::test_emits_error_on_no_edge` |
| ATX-M-012 | §2.7 `:177` + §3.2 step 7 `:395-398` — `loop_restart` "terminates the current run and re-launches with a fresh log directory"; `restart_run(...); RETURN` | DIVERGE-DECIDED | ATX-12; EXT §24 | **probe** (P2 ✅): in-process reset — `$iteration` 0→1, `context_updates` survive, work re-executes, `iteration_1/` under the SAME `logs_root`, no fresh root, `run()` returns normally; indexed: `test_convergence_observability.py` |
| ATX-M-025a | §4.5 `:695-704` — codergen wraps any string response as `Outcome(SUCCESS, "Stage completed: …")` | DIVERGE-DECIDED (goal_gate scope only) | EXT §25 | **probe** (P3-prose ✅): plain prose on a `goal_gate=true` node does NOT exit success; **control**: non-gate prose→SUCCESS preserved (indexed: `test_backend.py::test_backend_plain_text_returns_success`, `test_fail_closed_outcomes.py::test_fc001/fc002/fc010`) |
| ATX-M-025b | §3.4 `:471-477` — `check_goal_gates`: status ∈ {SUCCESS, PARTIAL_SUCCESS} satisfies the gate | DIVERGE-DECIDED (gate additionally requires `is_explicit`) | EXT §25 | **probe** (P3 ✅): status-only `Outcome(SUCCESS, is_explicit=False)` on a gate ⇒ pipeline FAIL; control `is_explicit=True` ⇒ SUCCESS; indexed: `test_fail_closed_outcomes.py::test_fc008` |
| ATX-M-016 | §3.3 step 4 `:448-451` — unconditional edges selected by weight *regardless of outcome status* | DIVERGE-DECIDED (FAIL is fail-fast) | EXT §16 | indexed both directions: `test_edge_selection_no_silent_fallthrough.py::test_fail_outcome_does_not_traverse_unconditional_edges` + `::test_success_outcome_traverses_unconditional_edges_as_before` + `runs_on` routes |
| ATX-M-022 | §10.4 `:1693-1697` — `resolve_key("outcome")` RETURNS `outcome.status`; `preferred_label` is a separate key | DIVERGE-DECIDED | ATX-5; EXT §22 | indexed both directions: `test_conditions.py::test_outcome_resolves_preferred_label_custom_value` + `::test_outcome_resolves_status_when_no_preferred_label` (+`_fail` variant) |
| ATX-M-036 | §4.5 `:718` — backend internals implementer-delegated (spec-silent on provider serviceability) | EXTENSION (fail-loud preflight; behavior change vs silent fallback) | EXT §36 | indexed: `test_provider_preflight.py`, `pipeline-runner/tests/test_provider_preflight_drive_engine.py`, `test_profile_routing.py` (no-fallback resolution) |
| ATX-M-006o | §3.5 `:519-520` — `IF outcome.status == FAIL: RETURN outcome` (no retry) — **conflicts** with DoD `:1833` "retried on RETRY or FAIL outcomes" | OPEN-PINNED (ATX-6, disposition pending: reconcile-the-spec) | ATX-6 | **probe**: handler FAIL with `max_retries=2` ⇒ exactly 1 execution; `spec.conflict` cites DoD 11.5; flip message names ATX-6 |
| ATX-M-007o | §10.5 `:1743-1747` — `parse_literal` strips double quotes from condition literals | OPEN-PINNED (ATX-7) | ATX-7 | **probe**: pin today's behavior (quoted literal compared raw vs unquoted — probe records which; flip demands the ATX-7 decision) |
| ATX-M-003o | §9.7 `:1650-1657` — `tool_hooks.pre`/`.post` shell commands around each tool call | OPEN-PINNED (ATX-3, DECIDE pending) | ATX-3 | **absence**: `grep -r tool_hooks modules/loop-pipeline/amplifier_module_loop_pipeline/` = 0 — implementing it without deciding ATX-3 flips this row |
| ATX-M-004n | §9.5 `:1589-1607` — "Implementations **may** expose … HTTP service" | NOT-IMPLEMENTED-DECIDED | ATX-4 | **absence**: no HTTP-server entry point in the bundle; justification: spec-optional, programmatic tools instead |

**B. Load-bearing conformances (23 rows):**

| id | Spec ref | Disposition | Assertion |
|---|---|---|---|
| ATX-M-101 | §3.2 — `find_start_node`: shape=Mdiamond, then id `start`/`Start`; error if absent | CONFORM | indexed: `test_engine.py`, `test_validation.py::test_missing_start_node`/`test_multiple_start_nodes` |
| ATX-M-102 | §3.2 step 4 — engine merges `context_updates`, sets `context["outcome"]`=status, sets `preferred_label` only when non-empty | CONFORM | **probe** (new): run 2-node graph, inspect context between/after nodes |
| ATX-M-103 | §3.2 step 5 / §5.3 — checkpoint saved after each node completion | CONFORM | indexed: `test_checkpoint.py::TestCheckpointEngineIntegration`, `TestEngineWritesV2` |
| ATX-M-104 | §3.2 step 1 — terminal node stops execution; gate check runs first | CONFORM | indexed: `test_goal_gates.py`, `test_engine.py` |
| ATX-M-105 | §3.3 step 1 — condition-matched edges win; best by weight-then-lexical among them | CONFORM | indexed: `test_edge_selection.py::test_condition_matching_takes_priority`, `::test_condition_beats_preferred_label`, `::test_condition_edges_sorted_by_weight` |
| ATX-M-106 | §3.3 step 2 — preferred-label match on *unconditional* edges only; normalization lowercases/trims/strips `[Y] `-style accelerators | CONFORM | indexed: `::test_preferred_label_match`, `::test_step2_skips_conditional_edge_with_matching_label`, `::test_label_normalization*` (×3) |
| ATX-M-107 | §3.3 step 3 — `suggested_next_ids` first match, unconditional only | CONFORM | indexed: `::test_suggested_next_ids*` (×3), `::test_preferred_label_beats_suggested_ids` |
| ATX-M-108 | §3.3 steps 4–5 — weight desc, lexical tiebreak; deterministic | CONFORM | indexed: `::test_weight_tiebreak`, `::test_lexical_tiebreak`, `::test_deterministic_with_same_inputs` |
| ATX-M-109 | §3.3 — one edge selected, never fan-out for non-component nodes (ATX-10 restoration) | CONFORM | indexed: `test_edge_selection.py` (retired multi-match gate), T0-4 note EXT |
| ATX-M-110 | §3.4 — SUCCESS and PARTIAL_SUCCESS satisfy gates | CONFORM | indexed: `test_goal_gates.py::test_satisfied_goal_gates_allow_exit`, `::test_partial_success_satisfies_goal_gate` |
| ATX-M-111 | §3.4 rule 3 — retry-target ladder: node → node fallback → graph → graph fallback | CONFORM | **probe** (new, parametrized over the 4 rungs) + indexed partial: `test_goal_gates.py` |
| ATX-M-112 | §3.4 rule 4 — no target at any level ⇒ pipeline FAIL | CONFORM | indexed: `test_engine.py::test_goal_gate_unsatisfied_returns_fail` |
| ATX-M-113 | §3.5 — `max_retries=3` ⇒ 4 executions; node inherits `default_max_retries` (+legacy alias); built-in default 0 | CONFORM | indexed: `test_retry.py::test_retry_policy_from_node_max_retries`, `::test_retry_policy_from_graph_default`, `::test_retry_policy_default_zero` |
| ATX-M-114 | §3.5 — RETRY: increment, backoff, exhaust ⇒ `allow_partial` ? PARTIAL : FAIL | CONFORM | indexed: `test_retry.py`, `test_engine_must_write.py::test_retry_exhaustion_allow_partial_*` |
| ATX-M-115 | §3.6 — preset policy table values | CONFORM | indexed: `test_spec_conformance_batch.py` (B6) |
| ATX-M-116 | §3.6 — default `should_retry`: transient=retryable, auth/400/validation=terminal | CONFORM | indexed: `test_retry.py::TestShouldRetry` (10 tests) |
| ATX-M-117 | §3.7 — failure routing order: fail edge → retry_target → fallback → terminate | CONFORM | indexed: `test_failure_routing.py` |
| ATX-M-118 | §2.8/App B — 9-shape → handler-type table | CONFORM | **probe** (new, parametrized over the table) + indexed: `test_no_silent_fallback.py::test_known_shapes_still_dispatch_correctly` |
| ATX-M-119 | §4.12 — handler exceptions caught ⇒ FAIL outcome | CONFORM | indexed: `test_retry.py` (RaisingHandler terminal path) |
| ATX-M-120 | §5.3 — checkpoint carries the six fields at `{logs_root}/checkpoint.json` (v2 = superset) | CONFORM (schema superset = EXTENSION note) | indexed: `test_checkpoint.py::TestCheckpointKeyShape`, `::TestCheckpointV2Schema` |
| ATX-M-121 | §5.3 resume rules 1–5 — restore context/completed/retries; continue after `current_node`; edge selection once from recorded outcome | CONFORM | indexed: `test_engine_resume.py` (×4 incl. control-run equivalence), `test_resume_validation.py` ladder, `pipeline-runner/tests/test_resume_e2e.py` (really-killed run) |
| ATX-M-122 | §5.3 rule 6 — `full` degrades to `summary:high` for exactly one resumed hop | CONFORM | indexed: `test_engine_resume.py::test_resume_degrades_first_full_hop_once`, `::test_cap_never_leaks_into_a_checkpoint_or_a_later_hop`, `::test_no_degrade_when_the_first_hop_is_not_full` |
| ATX-M-123 | §5.4 — fidelity precedence edge > node > graph > `compact` | CONFORM | indexed: `test_fidelity.py` (×6) |
| ATX-M-124 | §7.1/§7.3 — ERROR diagnostics refuse execution; `validate_or_raise` throws; §7.2 rule table (conflict note: DoD 11.12 "orphan → warning" — pinned to §7.2 ERROR) | CONFORM | indexed: `test_validation.py` (per-rule tests + `::test_validate_or_raise_raises_on_errors`) |

**Plus** ATX-M-000 (SYNC row, §9) = **35 rows**: 11 divergence/pin/absence + 24 conformance
+ SYNC. All probe-kind rows verified feasible by the executed pattern in §10.

**Tranche 2+** (the remaining ~119 rows, by section): §2 parser rows; §3.2 residue
(goal-gate CONTINUE, mirror-attributes); §3.8; §4.1–4.4, §4.6–4.12 handler rows; §5.1–5.2,
§5.4–5.6 residue; §6; §8; §9.1–9.4, §9.6; §10 residue (bare-key truthiness, `context.`
prefix retry, §10.7 absence); Appendices A/C/D; NOT-ASSERTABLE rows with justifications.
Tranche 3 = ULM/CAL matrices (§12).

---

## 8. Q6 — The drift-flip experience

**Scenario:** a future PR changes `engine.py` so a dead end after SUCCESS completes
successfully ("aligning to spec"). The developer runs the suite (or CI does) and sees, from
`test_spec_conformance_matrix.py::test_row_atx_m_011`, exactly the message rendered in §5.

**The message contract** — every flip message MUST contain, in order:

1. **The banner** `SPEC-CONFORMANCE MATRIX FLIP — row <id> "<title>"` (greppable).
2. **The spec anchor**: section + heading + the verbatim quote (the developer should not
   need to open the spec to know which sentence is in play).
3. **The current disposition + every ledger cite** (`SPEC_CONFORMANCE.md ATX-n`,
   `specs/EXTENSIONS.md §n`).
4. **The direction**: `REGRESSION` (moved off our documented behavior),
   `UN-DIVERGENCE` (moved onto the spec text we ledgered away from), or
   `UNDECIDED-MOVEMENT` (OPEN-PINNED row moved before its decision).
5. **observed / expected**, one line each, concrete.
6. **The two legal exits**, mirroring the Compatibility doctrine, with the *complete*
   same-PR checklist for exit 2: ledger row (+ dated changelog line), EXTENSIONS entry,
   matrix row, and any paired doc guards named in the row's `notes`.
7. **The closing invariant**: *"Doing neither means main carries a ledger that lies. That
   is drift."*

Rendered by one `flip(row, observed, expected, direction)` helper so the shape cannot decay
per-row. The structural-integrity failures (quote no longer in canonical, dangling ledger
cite, dangling indexed test) use the same banner with direction `MATRIX-INTEGRITY`, so a
`grep "MATRIX FLIP"` over CI logs finds every conformance event of any kind.

**What the developer does next** is mechanical, not archaeological: exit 1 is `git revert`;
exit 2 touches four named files in one PR, and the matrix's structural checks verify the
ledger edits landed (an EXTENSIONS banner flipped to ABSORBED/withdrawn without renumbering
still satisfies the integrity guard; a deleted ATX row fails the cite check — the ledger's
append-only discipline is preserved by construction).

---

## 9. Q7 — Upstream-sync hook and the anchoring decision

**The SYNC row (ATX-M-000):** the matrix file header pins

```yaml
canonical:
  path: specs/canonical/attractor-spec-canonical.md
  upstream: strongdm/attractor @ fb57a55
  sha256: "<computed at matrix creation>"
```

and the runner asserts the file's sha256 matches. On mismatch the flip message is not "fix
the hash" — it is: *"specs/canonical has been re-synced. Every matrix row quotes this file;
a re-sync is a full-matrix re-review event. Re-verify every row against the new text, update
dispositions/ledger entries that the new upstream text absorbs or invalidates (see the
EXTENSIONS 'ABSORBED UPSTREAM' banner protocol), then update the pinned sha and `upstream:`
in the same PR."* This turns SYNC-1-style refreshes from a quiet vendoring commit into a
demanded review — which is exactly what the 2026-08-14 `fb57a55` sync required by hand
(EXTENSIONS §§1–7 retconned ABSORBED; §18's `k_of_n`/`quorum` discovered *removed* upstream).

**Line numbers vs anchors — decided: verbatim quotes are the binding anchor; line numbers
are stored but informational.** The prompt's observation is correct: byte-pinning makes line
numbers *stable between SYNC events*, so they are not brittle day-to-day. But they carry no
meaning — a line number can only ever fail *because the sha row already failed*, at which
point 154 stale integers would demand a mass renumbering edit that reviews nothing. Quotes
do strictly more work: (a) they make each row self-verifying against the spec's *text*, (b)
after a re-sync, quote checks fail **only where the normative text actually changed** —
an automatic, targeted diff of "which rows does the new upstream touch," which is the
review the SYNC row demands, (c) they make the matrix readable standalone (the row shows
the sentence it answers to). So: quotes runner-verified; `line:` kept as a reading aid
(and regenerable by a trivial script); the sha row catches everything else including
edits that don't touch any quoted sentence.

---

## 10. Q8 — Probes: feasibility, verified by execution

**Claim:** every tranche-1 probe runs in-process against `PipelineEngine` with a
backend double — deterministic, no LLM, no network, no subprocess. The seams are already
canonical in the suite: `MockBackend`/`CountingBackend` (`test_goal_gates.py`),
`CountingRestartBackend` (`test_convergence_observability.py`), `MockHooks` event capture
(`test_pipeline_events.py`), direct handler construction (`test_handlers.py`), and pure
functions (`select_edge`, `evaluate_condition`). Any row that seems to need a live LLM is
mis-specified — the spec's normative statements are about the *engine's* contract with the
backend, and that contract is exactly the mock seam. (The one genuinely live-ish row class —
"resume survives a real SIGKILL" — already exists as
`pipeline-runner/tests/test_resume_e2e.py` and is INDEXED, not duplicated.)

**Verification: the three hardest rows were probed for real** in a `/tmp` worktree at
`origin/main` (`git worktree add /tmp/cm/wt origin/main`; file
`modules/loop-pipeline/tests/test_probe_matrix.py`; `uv run pytest` in the module venv):

```
tests/test_probe_matrix.py::test_p1_dead_end_after_success_hard_fails_with_event PASSED
tests/test_probe_matrix.py::test_p2_loop_restart_is_in_process_reset_not_relaunch PASSED
tests/test_probe_matrix.py::test_p3_goal_gate_rejects_non_explicit_success PASSED
tests/test_probe_matrix.py::test_p3_control_explicit_success_satisfies_gate PASSED
tests/test_probe_matrix.py::test_p3_plain_prose_on_goal_gate_does_not_exit_success PASSED
============================== 5 passed in 0.22s ==============================
```

- **P1 (ATX-M-011)** — dead end after explicit SUCCESS (edges exist, none match):
  `run()` → FAIL with non-empty `failure_reason`; `PIPELINE_ERROR` event with
  `error_type="no_matching_edge"` captured via the hooks seam; spec's
  `SUCCESS/"Pipeline completed"` asserted absent. One engine construction, ~40 lines.
  (Import gotcha found and recorded: the events module is
  `amplifier_module_loop_pipeline.pipeline_events`, not `.events`.)
- **P2 (ATX-M-012)** — `work -> work [loop_restart=true]` graph, backend stops on call 2:
  `$iteration` seen as `["0", "1"]` inside the backend; `context_updates` from iteration 0
  (`finding=attempt-0-found-X`) visible in iteration 1; `work` executed twice (completed
  set cleared); `logs/iteration_1/` exists under the **same** `logs_root`; no sibling
  run-root created (spec's "fresh log directory" absent); `run()` returned a normal final
  outcome in-process (spec's `restart_run(); RETURN` absent). No infinite-loop hazard: the
  backend's stop condition is the loop bound, same pattern as `CountingRestartBackend`.
- **P3 (ATX-M-025a/b)** — `goal_gate=true` node: `Outcome(SUCCESS, is_explicit=False)`
  (the spawn status-only shape) ⇒ pipeline FAIL with "gate" in the failure reason — the
  spec's §3.4 status-only satisfaction demonstrably does not hold; control with
  `is_explicit=True` ⇒ SUCCESS; plain-prose string return (which spec §4.5 wraps as
  SUCCESS) ⇒ pipeline does not exit success.

Total probe cost for the three hardest: 0.22s including the two control tests. The
remaining ~16 tranche-1/2 probes are strictly simpler shapes of the same pattern.

---

## 11. Q9 — Liftability: what other repos can take

**Portable as-is** (candidate for a shared `spec-conformance` skill/bundle doc):

- **The row schema** (§3) — nothing in it is attractor-specific except the ledger file
  names, which are two config keys.
- **The disposition vocabulary** — especially the two inventions earned here:
  `OPEN-PINNED` (pin behavior without forging a decision) and `absence` assertions
  (make "we don't implement §X" an executable claim). Any repo with a contract has
  undecided gaps and decided omissions.
- **The flip-message contract** (§8) — banner, anchor-with-quote, disposition+cite,
  direction, observed/expected, two-legal-exits, closing invariant. This is the actual
  product; everything else is plumbing for it.
- **The anchoring rule** (§9) — verbatim quotes runner-verified + one content-hash SYNC row
  demanding full re-review; positional references informational only. Works for any
  byte-pinned upstream artifact (a vendored spec, an OpenAPI schema, a protocol RFC).
- **The two-direction divergence rule** (§5) and the **coverage tripwire** (every ledgered
  divergence must be cited by ≥1 row).

**Attractor-specific, do not lift:** the deterministic seams (MockBackend, hooks capture,
`select_edge`) — every engine has its own; the per-module-venv constraint that forces
AST-verification of cross-module cites; the ledger *formats* (ATX row grammar, EXTENSIONS
`## N.` banners) — though the integrity-guard pairing generalizes; the specific
NOT-ASSERTABLE judgments.

The honest lift-limit: the matrix's value density comes from the *ledger discipline already
existing here*. A repo without a decided-divergence ledger gets a conformance suite from
this pattern, not a drift detector — the pattern's first demand on such a repo is "write the
ledger."

---

## 12. Scope call: the other two canonical specs

Named decision: **v1 scopes to the attractor spec.** The other two canonical specs are
assertable against this repo's modules, and the schema is deliberately spec-agnostic, but
they are tranche 3, as separate data files with runner twins in their own module test trees
(per-module venv reality):

- **unified-llm** (`modules/unified-llm-client`): already has a spec-section-mapped DoD
  suite (`tests/dod/test_8_1…8_10*.py`) — effectively a proto-matrix without dispositions.
  A `ulm-matrix.yaml` would mostly INDEX it, add the ledger cites (ULM-1…17), and pin the
  one decided divergence (ULM-17 Gemini `additionalProperties` sanitizer) plus the OPEN
  rows (ULM-4/8/11/12/13) as OPEN-PINNED. Cheap, high value, second.
- **coding-agent-loop** (`modules/loop-agent`): ledger is OPEN-heavy (CAL-3…9 pending
  decisions), so a `cal-matrix.yaml` would be mostly OPEN-PINNED rows — legitimate but
  low-signal until the DECIDE items move. Two rows are assertable CONFORM today and worth
  seeding: CAL-1 (`0 = unlimited tool rounds` — `test_config.py`/`test_agent_session.py`)
  and CAL-2 (`ContextLengthError` → warn + continue — `test_error_handling.py`). Third.

---

## 13. Honest notes: findings, and what resists the treatment

**Findings the inventory itself surfaced** (evidence the matrix pays for its construction —
each needs a maintainer decision during tranche 1, filed as ledger work, not silently
encoded):

- **F1 — unknown-shape hard error is an unledgered candidate divergence.** Spec §4.2's
  registry pseudocode falls through to the *default handler* for an unknown shape; our
  engine raises `ValueError` listing supported shapes
  (`test_no_silent_fallback.py`, whose docstring argues from §2.8's finite table). It is
  the right behavior under doctrine rule 4 — but it is exactly ATX-11's biography:
  correct, load-bearing, undocumented. Tranche 1 must either ledger it (recommended:
  DIVERGE, new ATX row + EXTENSIONS entry) or align it; the matrix row exists either way.
- **F2 — spec self-contradictions get `spec.conflict` rows**: retry-on-FAIL (§3.5 vs DoD
  11.5 — the ATX-6 pin); reachability severity (§7.2 ERROR vs DoD 11.12 warning — we
  implement ERROR, matching §7.2); human-gate routing signal (§4.6 pseudocode
  `suggested_next_ids` — which our handler implements — vs DoD 11.6 "preferred_label").
  The matrix pins one side *by named choice*; it cannot make the spec coherent.
- **F3 — §9.6 event names**: the spec's `PipelineStarted(name, id)` vocabulary vs our
  `pipeline:start`-family events. The row asserts *semantic* event coverage (each spec
  lifecycle event has a named counterpart carrying the specified data), disposition
  CONFORM-with-note, not name-literal equality — asserting the literal names would be
  conformance theater against a spec that presents them as a typed-event sketch.
- **F4 — Appendix A defaults spot-check** will likely catch at least `reasoning_effort`
  default `"high"` (ours is profile/stylesheet-resolved with no unconditional high
  default). Probe first, then ledger or align — same F1 protocol.

**What genuinely resists the treatment** (and the design's honest answer):

- **Aspirational/approximate prose** — §5.4 token budgets, §1 design principles, §3.8
  rationale: NOT-ASSERTABLE rows with justifications. ~10 rows, each individually argued.
- **Randomness at the edge** — backoff jitter is asserted as *bounds* (`0.5x..1.5x`,
  existing `test_backoff_with_jitter`), not distribution. The spec's `random_uniform` is
  not further assertable without pretending.
- **"Others may be cancelled"** (§4.8 first_success) — permissive spec language ("may")
  can only be pinned as *our* choice, never as conformance/divergence; such rows are
  CONFORM with the permissiveness noted in the quote itself.
- **The spec's own checklists (§11)** — the matrix *is* their operationalization;
  a row asserting the checklist would be self-reference. One NOT-ASSERTABLE meta-row
  records this so the section isn't silently unaccounted for.
- **Structural-integrity limits** (inherited honestly from the guard precedents): quote
  verification proves the *text* is still in the spec, not that our reading of it is
  right; AST-verified indexed cites prove the test *exists*, not that it still asserts
  what the row claims — the paired suite's own green is what carries that, and a renamed
  test fails the cite check loudly (which is the designed behavior).

---

## 14. Build plan (when implementation is commissioned)

1. **PR 1 — skeleton + divergences:** matrix file with header/SYNC pin + the 11 tranche-1A
   rows; runner with structural checks, flip renderer, RED/GREEN checker self-tests, and
   the 6 new probe functions (P1/P2/P3 land nearly verbatim from §10). File F1 as a ledger
   decision item in the same PR.
2. **PR 2 — tranche-1B conformances:** the 24 rows, 4 new probes (context keys, gate
   ladder, shape table, ATX-6 pin), coverage tripwire on.
3. **PR 3+ — tranche 2 by section;** then ULM/CAL matrices (§12). Each PR trues up the §6
   inventory counts in this doc.

Definition of done for the matrix itself: flipping any asserted behavior in a scratch
branch produces the §8 message naming the right ledger entry — tested for the three
divergence classes by mutation (revert the dead-end hard-fail, make the gate accept
non-explicit SUCCESS, strip a ledger cite) before PR 1 merges.
