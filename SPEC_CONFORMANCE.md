# Spec Conformance Ledger

A living record of where this implementation (`amplifier-bundle-attractor`) is **off-spec**
relative to the upstream natural-language specs at [strongdm/attractor](https://github.com/strongdm/attractor),
and the chosen disposition for each gap. The goal is not 100% literal conformance — it is that
**every divergence is a deliberate, recorded choice**, trending over time toward one of:

- **ALIGN** — fix the implementation to fulfill the spec.
- **DIVERGE** — keep the implementation's behavior; record it as an intentional, documented
  divergence (and fold it into the spec / `specs/EXTENSIONS.md` so the spec stops lying).
- **IMPROVE** — implementation deliberately exceeds the spec; promote the idea upstream.

## Compatibility doctrine

Maintainer ruling, 2026-08-14. The four rules that decide every disposition in this file:

1. **Honor the nlspec design where possible.** The upstream natural-language spec is the design of
   record; "we'd have done it differently" is not a reason to diverge.
2. **100% support for community `.dot` files built against the nlspec.** A graph written to the
   canonical spec must run on this engine unmodified. This is the hard constraint **on
   extensions** — an extension that breaks a conforming graph is a bug, not an extension. It is
   not a claim that *nothing* can require an edit: the decided **divergences** under rule 4 are
   the enumerated exceptions, and each one names the graph shape it refuses and the one-line
   remedy. Today the divergences that can require touching a conforming graph are `EXTENSIONS.md`
   §16 (fail-fast routing — a graph relying on canonical "continue past FAIL on the best
   unconditional edge" must add `runs_on=always` or `continue_on_fail` to the intended successor)
   and §38 / ATX-13 (unknown-shape hard-fail — a decorative out-of-table shape must carry an
   explicit `type=`). Rule 2 bounds what an *extension* may do; it does not repeal rule 4.
3. **Extensions must be additive and non-interfering.** New attributes, shapes, and semantics may
   only add reachable behavior; they may not change what a spec-conformant graph does.
4. **Divergences only for safety, backed by measured evidence, and always LOUD.** A divergence must
   name the safety property it buys, cite the evidence (an incident, a measurement, a live run) that
   the spec-literal behavior actually failed, and fail loudly rather than silently — a divergence
   that resolves quietly toward "success" is the failure mode this doctrine exists to prevent. Every
   one is ledgered here and in `specs/EXTENSIONS.md`.

## How to use this file

1. Each gap has a stable ID (`ULM-*`, `CAL-*`, `ATX-*`), a spec reference, an impl reference,
   a **status**, and a **disposition**.
2. When you work a gap: update its status, add a dated line to the Changelog, and — if it's a
   DIVERGE/IMPROVE — make sure `specs/EXTENSIONS.md` documents the real behavior.
3. Keep evidence as `file:line`. Re-verify before flipping a status to DONE — a passing unit
   test of request translation is not the same as proven live end-to-end behavior; note which
   bar was met.

## Status legend

`OPEN` · `IN-PROGRESS` · `DONE` · `WONTFIX` (recorded divergence, no further action)

## Baseline

- Upstream specs read in full @ strongdm/attractor `fb57a55` (unified-llm 2169L, attractor 2090L,
  coding-agent-loop 1467L).
- Spec drift: our vendored `specs/` copies differ slightly from upstream (unified-llm −16L,
  attractor −2L, coding-agent-loop −16L). Only `specs/canonical/attractor-spec-canonical.md` is
  byte-identical to upstream. See `SYNC-1`.

---

## Summary

| Spec | Areas reviewed | Off-spec gaps | Resolved | Open |
|------|----------------|---------------|----------|------|
| unified-llm | ~35 | 13 | 4 (structured output, all providers) | 9 |
| coding-agent-loop | ~17 | 10 | 3 (bugs CAL-1, CAL-2, CAL-10) | 7 |
| attractor | ~30 | 12 | 9 (ATX-1, ATX-2, ATX-4, ATX-5, ATX-10, ATX-11, ATX-12, ATX-13, ATX-14) | 3 (ATX-3, ATX-6, ATX-7) |

The engine layer (attractor) is the strongest — substantially a **superset** of the spec. The
material weaknesses are concentrated in the LLM client's per-provider metadata and a small set
of deliberate omissions (tool-hooks, execution-environment). Resume is no longer among them:
ATX-2 shipped per §5.3 and coexists with the graph-owned idempotency pattern.

---

## 1. unified-llm-spec → `modules/unified-llm-client`

| ID | Area | Spec | Impl | Status | Disposition |
|----|------|------|------|--------|-------------|
| ULM-1 | Gemini native structured output (`responseMimeType`+`responseSchema`) | `:988` | `adapters/gemini.py` `_translate_request` | **DONE — LIVE-PROVEN** (gemini-2.5-flash-lite) | ALIGN |
| ULM-2 | Anthropic structured-output fallback (tool-extraction) | `:989` | `adapters/anthropic.py` + `generate.py` `generate_object` | **DONE — LIVE-PROVEN** (claude-haiku-4-5) | ALIGN |
| ULM-3 | OpenAI native structured output | `:987` | `adapters/openai.py:296-313`, `openai_compat.py:453-466` | **DONE — LIVE-PROVEN** (gpt-4o-mini) | ALIGN |
| ULM-4 | `generate_object` schema-validation depth (only required-keys + root type) | `§4.5` | `generate.py` `_validate_against_schema` | OPEN | ALIGN (secondary) |
| ULM-5 | `Response.raw` never populated on success | `:599`, `:1615` | all 4 adapters now via `with_raw_response` + `_serialize_raw()` | **DONE** | ALIGN |
| ULM-6 | `RateLimitInfo` / `x-ratelimit-*` never parsed | `:1616`, `:724` | OpenAI/Anthropic/openai_compat parse headers; **Gemini deferred (SDK exposes no headers)** | **DONE (partial; Gemini N/A)** | ALIGN |
| ULM-14 | Gemini rejects unsupported JSON-Schema keywords (`additionalProperties`, `$schema`, …) in `response_schema` | live 400 INVALID_ARGUMENT | `adapters/gemini.py` `_sanitize_gemini_schema()` | **DONE** (live-found + fixed) | ALIGN |
| ULM-15 | Smoke-test/catalog staleness: `claude-sonnet-4-20250514` 404s on gateway; reasoning-model smoke tests use `max_tokens=16` (0 output budget) | n/a (test fixtures + `models.json`) | catalog → `claude-sonnet-4-6` + `claude-haiku-4-5-20251001`; smoke `max_tokens` 16→512 | **DONE — LIVE-PROVEN** (smoke suite 9/9 green) | ALIGN (test/catalog hygiene) |
| ULM-16 | OpenAI strict structured-output mode rejects schemas with OPTIONAL fields (`required` must list every property; objects need `additionalProperties:false`) → live `400 invalid_json_schema`. Standard JSON Schema with optional fields works on Anthropic/Gemini but 400s on OpenAI. | live eval (S4) | `adapters/_openai_strict_schema.py` `make_openai_strict_schema()` (all-required + `additionalProperties:false` + optionals→nullable), applied in `openai.py` + `openai_compat.py` | **DONE — LIVE-PROVEN** (eval OpenAI 5/6→6/6) | ALIGN |
| ULM-17 | Gemini's `additionalProperties:false` is prompt-enforced only (ULM-14 sanitizer strips the keyword; OpenAI=API-strict, Anthropic=tool-schema). Adversarial follow-up: 3 genuinely tempting "extract everything" prompts × 3-field schemas, 9 live calls → **0/3 leaked on Gemini** (and OpenAI/Anthropic). Gemini treats `properties` as the authoritative allowed-key set without the keyword. Holds under adversarial pressure for flat schemas. | live adversarial eval (`eval_gemini_extra_keys.py`) | `adapters/gemini.py` `_sanitize_gemini_schema()` | **DONE — DIVERGE confirmed (no fix; holds)** | DIVERGE |
| ULM-7 | `reasoning_effort` no-op on Anthropic & Gemini | `:691`, `:701` | Anthropic → extended-thinking (`thinking` budget + beta hdr, temp=1, budget<max_tokens); Gemini → `ThinkingConfig(thinking_budget)`; effort→budget low/med/high=1024/8000/16000; only when explicitly set | **DONE — LIVE-PROVEN** (both providers accept + reason) | ALIGN |
| ULM-8 | Anthropic `reasoning_tokens` estimate from thinking blocks | `:697` | `anthropic.py` `_map_usage` never sets it | OPEN | ALIGN |
| ULM-9 | Error message-body classification (Quota/ContextLength/ContentFilter) | `:1394-1401` | `errors.py` `_classify_by_message()` promotes generic/unknown errors → `QuotaExceededError`/`ContextLengthError`/`ContentFilterError` by message substrings (conservative; doesn't override status-determined types) | **DONE** | ALIGN |
| ULM-10 | Audio/Document content parts silently dropped | `:2016` | all 4 adapters now `raise ConfigurationError` (naming provider + `ContentKind`) on unhandled content instead of dropping; full audio/doc support is a separate feature | **DONE** (fail-loud) | ALIGN |
| ULM-11 | Image local-file-path convenience + OpenAI `detail` hint | `:486`, `:488` | not handled | OPEN | ALIGN |
| ULM-12 | `StreamResult.partial_response` property | `:943` | missing | OPEN | ALIGN |
| ULM-13 | `AdapterTimeout` granularity (connect/request/stream_read); `stream_object` true partials | `:1043`, `:1004` | single timeout float; whole-buffer JSON parse | OPEN | ALIGN |

**Note on ULM-1/2/3:** request-translation is proven by deterministic SDK-mocked unit tests.
**Live end-to-end (does each provider actually honor the schema) is UNPROVEN** — needs real API
keys. Do not mark "live" until exercised against real providers (e.g. in a DTU with keys).

---

## 2. coding-agent-loop-spec → `modules/loop-agent`

| ID | Area | Spec | Impl | Status | Disposition |
|----|------|------|------|--------|-------------|
| CAL-1 | `max_tool_rounds_per_input` `0 = unlimited` | `:150`, `:231` | `config.py:39`, `agent_session.py:314` | **DONE** | ALIGN |
| CAL-2 | `ContextLengthError` → warn + continue (not CLOSED) | `:405`, `:1432` | `agent_session.py:348` | **DONE** | ALIGN |
| CAL-3 | ExecutionEnvironment abstraction (§4) — swappable Local/Docker/SSH exec seam | `:729-768` | none; tools self-execute | OPEN | **DECIDE** (ALIGN vs DIVERGE) |
| CAL-4 | Command timeouts (10s/600s, SIGTERM→SIGKILL) + env filtering wiring | `:558`, `:783-786` | config fields unread; `env_filter.py` unimported | OPEN | ALIGN / delegate-to-tools |
| CAL-5 | ProviderProfile + `provider_options` passthrough (§3) | `:471-488` | flattened into SessionConfig + bundle profiles | OPEN | DIVERGE (likely) |
| CAL-6 | Distinct `PROCESSING_END` event | `:422` | emits `session_end` only | OPEN | DIVERGE-or-ALIGN |
| CAL-7 | System prompt: recent commit messages + knowledge-cutoff line (§6.4) | `:1036`, `:1025` | omitted | OPEN | ALIGN (cheap) |
| CAL-8 | Subagents: start-then-`wait` semantics, default unlimited turns | `:1069`, `:1075` | lazy spawn, default 50, host-dependent | OPEN | DIVERGE-or-ALIGN |
| CAL-9 | Graceful shutdown closes active subagents + cancels in-flight stream | `:1436-1449` | partial | OPEN | ALIGN |
| CAL-10 | Tool output truncation (§5.1 MUST) silently discarded: `tool:post` hook `Modify` results (e.g. `hooks-tool-truncation`) never reached the LLM because the consumer gated on `post_result.action == "modify"`, a value amplifier-core's `HookRegistry.emit()` (`hooks.rs`) never returns to its caller (Modify only chains data between handlers inside `emit()`'s own dispatch loop; the returned action always collapses to `continue`) | `:852-854` (§5.1: truncation MUST happen before the LLM sees oversized output) | `agent_session.py:912-945` (fixed); `loop-pipeline/hook_bridge.py:190-224` (`wrap_tool_with_hooks`, same discard-class, unwired/dead in production) | **DONE — PROVEN (RED→GREEN)** | ALIGN |

---

## 3. attractor-spec → `modules/loop-pipeline` (+ supporting tool/hook modules)

| ID | Area | Spec | Impl | Status | Disposition |
|----|------|------|------|--------|-------------|
| ATX-1 | Node `timeout` unit mismatch (ms stored, consumed as seconds) | `timeout_seconds` | `engine.py:485`, `handlers/tool.py:105` | **DONE** | ALIGN |
| ATX-2 | Checkpoint-based RESUME (restore context/completed/retry, continue after `current_node`; `full`→`summary:high` degrade) | `§5.3`, DoD `:1857` | `checkpoint.py:load_checkpoint_for_resume` (validation ladder) + `engine.py:PipelineEngine.resume` + `runner.py:resume_pipeline` + `dot-runner resume <run_dir>`; checkpoint schema v2 is a superset keeping the six §5.3 fields at the §5.6 path. A fresh `run()` still never reads a checkpoint — no call path exists (`tests/test_no_implicit_resume.py`) | **DONE — PROVEN ON A REALLY-KILLED RUN** (`modules/pipeline-runner/tests/test_resume_e2e.py`: SIGKILLed subprocess, separate `dot-runner resume` invocation, equivalence vs a control run executed at gate runtime) | ALIGN |
| ATX-3 | Tool-call hooks `tool_hooks.pre`/`.post` (shell around each LLM tool call) | `§9.7` `:1650` | grep `tool_hooks`=0 | OPEN | **DECIDE** (ALIGN vs DIVERGE) |
| ATX-4 | HTTP server mode (REST + SSE) | `§9.5` (optional) | not present; programmatic tools + CLI instead. The absence is now asserted rather than merely described: matrix row `ATX-M-004n` (`specs/conformance/attractor-matrix.yaml`) fails if an HTTP surface appears | **WONTFIX — DECIDED (NOT-IMPLEMENTED)** | DIVERGE (spec-optional) |
| ATX-5 | `outcome=` condition resolves to `preferred_label` first | `§10.4 :1693` | `conditions.py:75` returns `preferred_label or status`; both halves asserted by matrix row `ATX-M-022` (`specs/conformance/attractor-matrix.yaml`) | **DONE — DECIDED** | DIVERGE (decided; ledgered — `specs/EXTENSIONS.md` §22) |
| ATX-6 | Retry on FAIL | spec self-contradicts: `§3.5 :519` (no) vs DoD `:1833` (yes) | retries RETRY only (`retry.py:238`) | OPEN | ALIGN-spec-first (reconcile the spec) |
| ATX-7 | `stack.child_workdir`; condition literal unquoting (`§10.5`) | `:1743` | not handled | OPEN | ALIGN (minor) |
| ATX-10 | Multi-match fan-out: non-component nodes with ≥2 simultaneously-matching conditional edges routed to ALL targets in parallel (unledgered dialect; never in spec §3.3) | `§3.3 :421-458` (`best_by_weight_then_lexical`) | `engine.py` (retired `select_all_matching_edges` gate; now routes through `select_edge()` only) | **DONE** | ALIGN — conformance restored (T0-4) |
| ATX-11 | Main-loop no-matching-edge hard-fail: a dead end with no matching outgoing edge always terminates the pipeline with `status=FAIL` + `PIPELINE_ERROR error_type=no_matching_edge`, regardless of the last outcome's status | `§3.2 step 6 :388-393` (dead end + non-FAIL outcome ⇒ `Outcome(status=SUCCESS)`) | `engine.py:853-867` (`terminate_pipeline()` + `PIPELINE_ERROR` emission); shipped since the engine's initial commit | **DONE — DECIDED** | DIVERGE (decided; ledgered — `specs/EXTENSIONS.md` §33) |
| ATX-12 | `loop_restart` is an **in-process reset**, not a terminate-and-relaunch: `$iteration`/`$loop_count` increment, completed nodes are cleared, the run directory is retained (gaining `iteration_N/`), and `context_updates` **survive** | `§2.7 :177` ("terminates the current run and re-launches with a fresh log directory"); `§3.2 step 7 :395-398` (`restart_run(...)` then `RETURN`) | `engine.py` `run()` loop-restart branch; design decision recorded at `docs/plans/2026-02-24-engine-enhancements-design.md:95-102` | **DONE — DECIDED** | DIVERGE (decided; ledgered — `specs/EXTENSIONS.md` §24) |
| ATX-13 | Unknown node shape hard-fails at dispatch instead of falling back to the default handler: `HandlerRegistry.get()` raises `ValueError` naming the shape, the node, the supported set, and the remedy | `§4.2 :603-607` (resolution order ends "3. Default handler (the codergen/LLM handler)"); `:628-629` (`RETURN default_handler`) | `handlers/__init__.py:116-121`; behavior contract `tests/test_no_silent_fallback.py`; both halves asserted by matrix row `ATX-M-F01` (`specs/conformance/attractor-matrix.yaml`) | **DONE — DECIDED** | DIVERGE (decided; ledgered — `specs/EXTENSIONS.md` §38; closes issue #234 F1) |
| ATX-14 | `reasoning_effort` unset-passthrough: no engine-injected default anywhere, so Appendix A's `"high"` does not hold — an omitted attribute reaches the provider as no reasoning parameter at all, and node attr / `model_stylesheet` / profile are the only value sources | `§2.6 :162` and Appendix A `:2020` (default `"high"`) | `graph.py:251` (`Node.reasoning_effort = None`), `backend.py:244` + `__init__.py:114` (None passthrough; spawn path omits the key), `transforms.py`/`stylesheet.py` (explicit resolution surface); asserted by matrix row `ATX-M-F04`; authoring-guide claim pinned by `tests/test_doc_consistency.py` D-243 | **DONE — DECIDED** | DIVERGE (decided; ledgered — `specs/EXTENSIONS.md` §39; closes issue #234 F4) |

**Shipped extensions (IMPROVE — ledgered in `specs/EXTENSIONS.md`):** skip-propagation contracts
(`requires=`/`outputs=`/`failed_outputs`,
`PIPELINE_NODE_SKIPPED`/`PIPELINE_NODE_CONTRACT_VIOLATION`); parallel `k_of_n`/`quorum`/`error_policy`;
human `freeform` mode + attachments; tool `parse_json`/`tool_env`/`tool.last_line`; `$param`/`${key}`
substitution beyond `$goal`. These are additive: they add reachable behavior without changing what a
spec-conformant graph does (doctrine rule 3).

**Fail-fast edge routing (`runs_on`/`continue_on_fail`) is a DIVERGE, not an IMPROVE.** It changes
what a conforming graph does on a FAIL outcome — canonical §3.3 step 4 selects the best unconditional
edge regardless of outcome status; this engine stops unless the target declares
`runs_on` ∈ {`always`, `failure`} or `continue_on_fail`. The load-bearing classification is
conformance-matrix row `ATX-M-016` (`specs/conformance/attractor-matrix.yaml`),
`disposition: DIVERGE-DECIDED`, ledgered at `specs/EXTENSIONS.md` §16 and asserted by
`tests/test_edge_selection_no_silent_fallthrough.py`.

---

## Cross-cutting

| ID | Item | Status | Disposition |
|----|------|--------|-------------|
| SYNC-1 | Re-sync vendored `specs/canonical/` to upstream byte-for-byte | **DONE** (canonical @ `fb57a55`) | ALIGN |
| DEAD-1 | Dead `SessionConfig` fields implying coverage that isn't wired (`tool_output_limits`, `tool_line_limits`, `default_command_timeout_ms`, `max_command_timeout_ms`, `get_max_tool_rounds`) | **DONE** | DIVERGE (all deleted + documented) |
| ATX-8 | DOT `response_schema` node attribute → per-provider structured output (NOT in canonical spec; §4.5 keeps output format at backend) | **DONE — LIVE-PROVEN** (all 3 providers via DOT pipeline) | IMPROVE (extension §23) |
| ATX-9 | DOT backends didn't recover Anthropic structured output from the `__structured_output__` tool call (only read `result.text`, which is empty on the tool-extraction path); live symptom `outcome.notes=''`; fixed in `loop-pipeline/__init__.py`, `backend.py` | **DONE** (live-found + fixed) | ALIGN |

---

## DECIDE items — context for a future decision

Deferred by owner; not decided yet. Captured context so the future call is well-informed.
Each is **OPEN** with disposition pending (ALIGN vs DIVERGE) unless its heading says otherwise:
an item that has since been decided keeps its subsection here as the record of *why* the call
went the way it did, with its heading and **Status** line carrying the outcome. The tables above
are the current state; these are the reasoning behind it.

### CAL-3 — ExecutionEnvironment abstraction (coding-agent-loop §4)
- **Spec wants:** a swappable `read_file/write_file/exec_command/grep/glob/list_directory` seam with
  Local/Docker/K8s/WASM/SSH implementations + platform metadata.
- **Reality now:** tools self-execute; no environment object is threaded; command timeouts + env
  filtering are owned by the (external) shell/bash tool. `environment.py` only builds the prompt's
  `<environment>` text block, not an execution seam.
- **Coupled to:** CAL-4 (command-timeout/env-filter wiring) and DEAD-1 (we deleted the
  command-timeout `SessionConfig` fields and pointed at the shell tool).
- **Decision hinges on:** do we need sandboxed/remote execution (Docker/SSH/WASM)? If all execution
  stays local via mounted tools → **DIVERGE** (document "tools own execution"). If we want isolation
  or remote targets → **ALIGN** (build the seam). Note Amplifier already provides isolation at a
  different layer (DTU), which may make a loop-level ExecutionEnvironment redundant.
- **Cost if ALIGN:** new abstraction crossing the tool boundary; touches every tool's call contract.

### ATX-2 — Checkpoint-based resume (attractor §5.3) — **DECIDED 2026-08-14: ALIGN (build it) — SHIPPED**
- **Spec wants:** load `checkpoint.json` → restore context/completed-nodes/retry counters → continue
  from the node after `current_node`; degrade `full`→`summary:high` one hop on resume.
- **Reality when this was written (pre-ship, superseded):** engine always restarted from the start
  node; `checkpoint.py` was an observability record (explicitly "not a resume marker");
  `load_checkpoint()` was never used to rehydrate. Idempotency was **graph-owned** only: handlers
  skip already-done work (see `examples/pipelines/12-graph-resume`).
- **Ruling (maintainer, 2026-08-14):** the missing engine-level resume is a **bug in our design**, not
  a defensible divergence. The spec's Definition of Done mandates it outright —
  `specs/canonical/attractor-spec-canonical.md:1857`: *"Resume from checkpoint: load checkpoint ->
  restore state -> continue from current_node"* — so this was never a spec-silent area we were free
  to fill differently. PR #66's premise, that *"the spec does not mandate engine-level resume,"* was
  **false as written**; the disposition recorded on the back of it is withdrawn.
- **Scope of the fix:** engine resume will **coexist with** graph-owned idempotency, not replace it.
  The `examples/pipelines/12-graph-resume` pattern stays supported and documented — handler-level
  "don't redo finished work" remains the right tool for expensive idempotent steps; engine resume
  covers the different problem of restoring accumulated *context* after a crash mid-pipeline.
- **Status: SHIPPED** (issue #224). Engine resume landed per §5.3 and the ATX-2 row above is
  **DONE — PROVEN ON A REALLY-KILLED RUN**: `dot-runner resume <run_dir>` /
  `resume_pipeline()` / `PipelineEngine.resume()`, opt-in only — a fresh `run()` has no code
  path to a checkpoint loader (`modules/loop-pipeline/tests/test_no_implicit_resume.py`).
  The coexistence condition held: the graph-owned pattern still parses, lints and executes its
  documented guard-skip semantics (`modules/loop-pipeline/tests/test_graph_owned_resume_coexists.py`).
  Design record: `docs/designs/2026-08-14-engine-checkpoint-resume.md`. See the 2026-08-14
  Changelog entry below for the full evidence.
- **Cost:** real state-serialization + rehydration surface; the `full`→`summary:high` degrade rule;
  correctness testing across partial-completion states. Accepted, and paid.

### ATX-3 — Tool-call hooks (attractor §9.7)
- **Spec wants:** `tool_hooks.pre` / `tool_hooks.post` shell commands wrapping every LLM tool call;
  a non-zero pre-hook exit skips the tool call.
- **Reality now:** not implemented (grep `tool_hooks` = 0). A separate `hooks-tool-truncation` module
  exists but implements output truncation, not this pre/post shell contract.
- **Decision hinges on:** do we want per-tool-call shell guards expressed at the *DOT* layer, or is
  this better served by Amplifier's existing kernel/bundle **hook** mechanism (code-decided lifecycle
  hooks)? If the kernel hook system covers the real need → **DIVERGE** (document the alternative). If
  DOT-author-level per-call guards are genuinely wanted → **ALIGN**.
- **Cost if ALIGN:** moderate; a new node/graph attribute + a guarded tool-call execution path.

---

## Changelog

### 2026-08-26 — CAL-10 DONE: tool:post hook modifications (truncation) reach the LLM again (amplifier-support#485)

- **CAL-10 NEW → DONE — ALIGN.** §5.1's MUST ("tool output exceeds the configured limit, it MUST be
  truncated before being sent to the LLM") was silently unmet in production: `agent_session.py`'s
  tool:post consumer gated on `post_result.action == "modify"`, a value amplifier-core's
  `HookRegistry.emit()` (`hooks.rs`) never returns to its caller — Modify only chains `current_data`
  between handlers *inside* `emit()`'s own dispatch loop; the action returned to the caller always
  collapses to `continue` (or deny/ask_user/inject_context if some handler in the chain requested one
  of those). Dead since `fc85653`. Every `hooks-tool-truncation` modification was a no-op; the LLM
  always received the raw, untruncated output.
  - **Fix (toward-spec, decision-matrix tier):** read `post_result.data` directly — it is the merged
    (possibly hook-modified) event data, delivered under `action="continue"` in the normal case — and
    guard for `"result"` being a `str` that differs from the raw output before using it, because
    Modify *replaces* `current_data` rather than merging it, so `"result"` is not guaranteed present.
    Fixed at both sites in the same class found by grep: `agent_session.py:912-945` (loop-agent's
    live tool-call path) and `loop-pipeline/hook_bridge.py:190-224`'s `wrap_tool_with_hooks` (same
    discard, more severe — it never read `post_result` at all; currently unwired in production, fixed
    as defense-in-depth so it isn't a landmine if wired in later).
  - **Loudness:** both sites now `logger.debug` original→modified char counts whenever a modification
    is actually applied — the incident was a modifier silently no-op'ing for months undetected.
  - **Tests:** `test_truncation_wiring.py` fakes rewritten to be faithful to the real kernel contract
    (`action="continue"`, data replaced) instead of fabricating `action="modify"` back to the caller —
    the old fakes are what let this bug ship silently; `test_tool_post_modification_reaches_llm` is
    RED at `origin/main` (proven: fails against the pre-fix file) and GREEN after the fix; 3 new guard
    tests (missing `"result"`, non-str `"result"`, identical `"result"` → no spurious log). New
    `test_truncation_e2e_real_hook.py` mounts the REAL `hooks-tool-truncation` module on a REAL
    `amplifier_core.HookRegistry` (not a fake) and proves oversized output is truncated to the
    configured `char_limit` before reaching the LLM.
  - **Live-run evidence:** a real `AgentSession` run against a live provider, oversized `bash` output,
    truncation hook mounted — event-stream slice saved outside the repo (see PR notes / report).
  - **EXTENSIONS.md:** not touched — this is a bug fix restoring documented §5.1 behavior, not a new
    or changed behavior surface.
  - loop-agent suite, hooks-tool-truncation suite, loop-pipeline suite, pipeline-runner suite all green
    after the fix (see PR notes for exact counts).

### 2026-08-16 — issue #234 decided: ATX-13 + ATX-14 ledgered as divergences (F1/F4 from the matrix build)

- **ATX-13 NEW — DECIDED — DIVERGE** — unknown node shape hard-fails at dispatch
  (`handlers/__init__.py:116-121`) where canonical §4.2's resolve() falls through to the default
  codergen handler. First ruling made under the decision matrix (`docs/VISION.md`, "Our relationship to the nlspec"; tolls in `docs/OPERATIONS.md` §3):
  drift tier, full toll paid — named safety property (no silent execution-class substitution),
  measured evidence (PR #19 / `aa44fca`: the spec-literal fallback silently ran `shape=diamond`
  conditional nodes as full LLM sessions when the shape was missing from the table; plus the
  in-process demonstration that lint emits 0 ERRORs for a typo'd shape, so dispatch is the only
  tripwire — spec-literal resolution would run a typo'd tool/human node as an LLM session that
  reports SUCCESS), loud behavior (the raise names shape, node, valid set, remedy), and
  `specs/EXTENSIONS.md` §38 + matrix row `ATX-M-F01` flipped OPEN-PINNED → DIVERGE-DECIDED in
  the same PR. No behavior change — the entry records the decision.
- **ATX-14 NEW — DECIDED — DIVERGE** — `reasoning_effort` has no engine-injected default;
  canonical §2.6 `:162` and Appendix A `:2020` say `"high"`. Drift tier, full toll paid — named
  safety property (no hidden engine default on a provider-mode-switching surface: a baked-in
  `high` would flip every Anthropic node into extended thinking with `temperature` force-set to
  1.0 and a 16000-token budget, send `reasoning` params to non-reasoning OpenAI models — live
  400s for graphs that run clean today — and defeat ULM-7's live-proven "only when explicitly
  set" wiring from above), honest absence rather than substitution (unset-in is unset-out: the
  spawn path omits the key; the direct path sends no reasoning field; `Response.raw` shows the
  request), and `specs/EXTENSIONS.md` §39 + matrix row `ATX-M-F04` flipped OPEN-PINNED →
  DIVERGE-DECIDED in the same PR. `docs/DOT-AUTHORING-GUIDE.md`'s node-attribute table — which
  repeated the spec's `high` as if it held here — corrected and pinned two-sided by
  `tests/test_doc_consistency.py` D-243. No behavior change — the entry records the decision.
- Both were OPEN-PINNED by the matrix (tranche 1) explicitly so this decision would be
  deliberate: the rows pinned today's behavior, refused to launder it into DIVERGE-DECIDED, and
  cited issue #234 as the pending decision. This PR is that decision landing; the issue closes
  with it.

### 2026-08-15 — ATX-4 / ATX-5 status cells reconciled with the conformance matrix (PR #235)

- **ATX-5 OPEN → DONE — DECIDED — DIVERGE.** The status cell said OPEN while the decision had in
  fact been made and ledgered: `specs/EXTENSIONS.md` §22 is the divergence record, it is explicit
  that the behavior is **not** behavior-neutral (a canonical graph matching `outcome=<status>`
  changes meaning once a node sets a `preferred_label`), and `conditions.py:75` has shipped
  `preferred_label or status` deliberately for months. Nothing about the behavior changed here —
  only the cell that had been mis-reporting a made decision as an unmade one. §22 is now cited as
  the decision record in the Disposition cell, matching the ATX-11 / ATX-12 convention.
- **ATX-4 OPEN → WONTFIX — DECIDED (NOT-IMPLEMENTED).** Canonical §9.5 is permissive ("*may* expose
  the pipeline engine as an HTTP service"), so not building it is conformant rather than divergent.
  The decision is: **not building it absent demand** — the bundle exposes programmatic tools and a
  CLI instead. Recorded as WONTFIX per this file's own status legend ("recorded divergence, no
  further action").
- **Evidence that these were stale cells and not new decisions.** PR #235's conformance matrix
  independently classified both rows from the spec text and the shipped code: `ATX-M-022` carries
  `disposition: DIVERGE-DECIDED` citing ATX-5 + EXTENSIONS §22, and `ATX-M-004n` carries
  `disposition: NOT-IMPLEMENTED-DECIDED` citing ATX-4. Two records of the same decision disagreed;
  the matrix was right and the ledger cells were behind. Surfaced by independent adversarial review
  of PR #235.
- **Both rows are now asserted, not merely described.** `ATX-M-022` pins both halves of the
  divergence (`outcome=` resolves to the label when set; to the status when not);  `ATX-M-004n` pins
  the *absence* of an HTTP surface, so shipping one becomes a ledger event rather than a quiet
  feature. The Impl cells cite the matrix rows, making the ledger↔matrix link bidirectional.
- **Dispositions unchanged.** Both cells keep their `DIVERGE` disposition, which is what the matrix
  coverage tripwire (`test_tripwire_every_diverge_atx_row_is_asserted`) reads — a DIVERGE-disposition
  `ATX-*` row must be cited by at least one matrix row. Verified green after this edit.

### 2026-08-14 — ATX-2 DONE: engine-level checkpoint resume shipped (issue #224)
- **ATX-2 DECIDED/IN-PROGRESS → DONE — ALIGN.** Spec §5.3 "Resume behavior" rules 1–6 and DoD
  `:1857` are implemented behind an explicit, opt-in entry point: `dot-runner resume <run_dir>` /
  `resume_pipeline()` / `PipelineEngine.resume()`. Checkpoint schema v2 is a strict superset —
  the six §5.3 fields keep their exact names and shapes at the §5.6
  `{logs_root}/checkpoint.json` location, plus `schema_version`, `run_state`, `node_outcomes`,
  `engine_state` and `graph` (fingerprint + embedded DOT source). `node_retries` is now actually
  populated (it was always written as `{}`).
  - **Bar met:** proven on a really-killed run, not a simulated interrupt — a subprocess SIGKILLed
    mid-graph after a checkpoint write, resumed by a genuinely separate `dot-runner resume`
    invocation, asserted equivalent to an uninterrupted control run executed at gate runtime
    (`modules/pipeline-runner/tests/test_resume_e2e.py`).
  - **The two PR #66 crash classes are designed out, not guarded against.** (1) Fresh runs cannot
    be poisoned by a checkpoint: `engine.py` contains no reference to any checkpoint loader at all,
    enforced by `modules/loop-pipeline/tests/test_no_implicit_resume.py`; graph identity is
    evaluated only inside the explicit resume ladder. (2) "No matching edge from resumed node":
    there is no fast-forward replay — the checkpoint records the last COMPLETED node and its real
    outcome, and resume runs edge selection exactly ONCE from those recorded inputs.
  - **Coexistence held (the ALIGN condition):** `examples/pipelines/12-graph-resume.{dot,md}` were
    left byte-unchanged by this PR and still parse, lint and execute their documented guard-skip
    semantics (`modules/loop-pipeline/tests/test_graph_owned_resume_coexists.py`). *(Later: the
    `.md`'s closing prose was reconciled to this coexistence ruling on 2026-08-15 — issue #229 —
    which the byte-pin had deliberately deferred. The `.dot` remains untouched.)* Engine resume answers
    "this process died mid-graph"; graph-owned skip-through answers "this work is already done on
    disk". Neither disables the other.
  - Design record: `docs/designs/2026-08-14-engine-checkpoint-resume.md`. Issue #224.

### 2026-08-14 — documentation/spec-alignment wave (maintainer rulings, in-session)
- **Compatibility doctrine adopted** — added as a header section above: honor the nlspec where
  possible · 100% support for community `.dot` files built against the nlspec · extensions additive
  and non-interfering · divergences only for safety, backed by measured evidence, always loud.
- **ATX-2 OPEN/DECIDE → DECIDED — ALIGN (build it)** — the spec DoD (`:1857`) mandates resume, so the
  gap is a bundle design bug, not a divergence; PR #66's "the spec does not mandate engine-level
  resume" premise was false as written. Engine resume will coexist with graph-owned idempotency
  (`examples/pipelines/12-graph-resume` stays supported). Implementation in flight via the feature
  pipeline, **issue #224**.
- **ATX-12 NEW — DECIDED — DIVERGE** — `loop_restart` is an in-process reset (context preserved,
  `iteration_N/` sub-tree, `$iteration` continuity), not spec §2.7's terminate-and-relaunch with a
  fresh log directory. Deliberate (`docs/plans/2026-02-24-engine-enhancements-design.md:95-102`
  quotes the spec, then chooses otherwise) and load-bearing for `feedback_from=` collection,
  `$iteration` continuity, and context survival. Refiled in `specs/EXTENSIONS.md` §24, which had
  claimed pure additivity.
- **EXTENSIONS §§1–7 retconned as ABSORBED UPSTREAM @ `fb57a55`** — upstream absorbed all seven
  item-for-item; each now carries a `status:` banner citing the canonical section that supersedes it,
  bodies retained verbatim for ledger contiguity. Entry Format amended to define the banner.
- **EXTENSIONS §18 usage check** — upstream removed `k_of_n`/`quorum`/`error_policy` from §4.8 at
  `fb57a55` (they are pure extensions now). Shipped-graph usage: `k_of_n` = 0, `quorum` = 0,
  `error_policy` = 5. `k_of_n`/`quorum` recorded as a subtraction candidate; no code removed.
- **Stale working spec copies retired** — `specs/attractor-spec.md`, `specs/coding-agent-loop-spec.md`,
  `specs/unified-llm-spec.md` contradicted the synced `specs/canonical/*` (five-phase lifecycle,
  `k_of_n`, `preferred_next_label`); replaced with pointer stubs and the one code reader
  (`test_doc_consistency.py::test_spec_default_max_retry_table_is_zero`) repointed at canonical.
- **Docs corrected to match the shipped engine** — `docs/ROUTING-REFERENCE.md` §§3–4 no longer teach
  the "silent alphabetical fallback" (the engine hard-fails `no_matching_edge`; canonical §3.3 returns
  NONE); `README.md` backend table no longer claims `DirectProviderBackend` runs with "no tools".

### 2026-06-24
- **CAL-1 DONE** — `max_tool_rounds_per_input` `0 = unlimited`: loop guard now `_max_rounds <= 0 or round_count < _max_rounds` (`agent_session.py:314`); default aligned to spec `0` (`config.py:39`). Tests added; loop-agent suite 493 passed.
- **CAL-2 DONE** — `ContextLengthError` now emits `AGENT_CONTEXT_WARNING` and returns to IDLE (session stays usable) instead of `fatal_error()` → CLOSED (`agent_session.py:348`). Tests added.
- **ATX-1 DONE** — node `timeout` unit fix: consumers divide parser-ms by 1000 (`engine.py:485`, `handlers/tool.py:105`); `max_pipeline_duration` (ms) untouched. New `tests/test_node_timeout_units.py` (7 tests); loop-pipeline suite 1330 passed.
- **ULM-1/2/3 DONE (translation)** — per-provider structured output: Gemini native `response_mime_type`+`response_schema`; Anthropic tool-based extraction (`__structured_output__` forced tool_choice) with `generate_object` reading tool args; OpenAI confirmed. Fail-loud (`ConfigurationError`) on Anthropic json-without-schema. 9 tests added; unified-llm suite 636 passed. **Live end-to-end remains UNPROVEN (needs real API keys).**
- **ULM-7 / ULM-9 / ULM-10 DONE** ("silent-drop" PR) — three fail-loud/no-op fixes: **ULM-7** `reasoning_effort` now wired to Anthropic extended-thinking (`thinking` budget + beta header, temp=1, budget<max_tokens) and Gemini `ThinkingConfig` (effort→budget 1024/8000/16000), only when explicitly set — **live-proven** both providers accept it and reason (Gemini: correct bat-and-ball reasoning; Anthropic: thinking block in raw, correct answer; honest note: Gemini's `reasoning_tokens` usage field is variable for flash-lite — request-translation asserted by unit tests). **ULM-9** `errors.py::_classify_by_message()` promotes generic errors → `QuotaExceededError`/`ContextLengthError`/`ContentFilterError` by substring. **ULM-10** all 4 adapters now `raise ConfigurationError` on unhandled Audio/Document content (provider + kind named) instead of silently dropping. +38 unit tests; unified-llm suite 724 green; structured-output eval 6/6×3 no regression (re-verified by my own run).
- **ULM-17 DONE — DIVERGE confirmed (no fix)** — ran an adversarial extra-keys eval (3 "extract everything" prompts × 3-field `additionalProperties:false` schemas, 9 live calls): **0/3 leaked on Gemini** (and OpenAI/Anthropic). Gemini's structured-output mode treats `properties` as the authoritative allowed-key set even though our sanitizer strips the keyword. The "stripping causes leakage" hypothesis is falsified for flat schemas under adversarial pressure; the sanitizer trade-off is benign in practice. Documented as an accepted divergence; no code change. (Minor side-note, parked: when a schema's JSON-Schema `title` *metadata* shares a name with a `title` *property*, OpenAI may echo the metadata — a test-schema-design artifact, not a product bug.)
- **ULM-16 DONE — LIVE-PROVEN** — eval found OpenAI strict mode 400s on any schema with optional fields. Added `adapters/_openai_strict_schema.py::make_openai_strict_schema()` (deep-copy transform: every object → `additionalProperties:false` + `required`=all keys; originally-optional fields widened to nullable), applied in `openai.py` + `openai_compat.py` strict path; never mutates caller schema. +10 mocked tests; unified-llm suite 686 green. Live structured-output eval: OpenAI 5/6→**6/6** (Anthropic/Gemini 6/6, DOT 2/2) — re-verified by my own run.
- **ULM-17 logged (OPEN)** — eval surfaced that Gemini's `additionalProperties:false` is prompt-enforced only (ULM-14 sanitizer strips the keyword); didn't leak in the eval but the structural guarantee is absent. Disposition DIVERGE (documented sanitizer trade-off); follow-up = an adversarial extra-keys test.
- **ULM-15 DONE — LIVE-PROVEN** — refreshed Anthropic catalog (`claude-sonnet-4-20250514`→`claude-sonnet-4-6`, `claude-3-5-haiku-20241022`→`claude-haiku-4-5-20251001`; verified live on gateway); bumped smoke `max_tokens` 16→512 (reasoning models starved at 16); updated stale-id refs in catalog/resolver unit tests. The repo's **live** integration smoke suite now passes 9/9 (was 7 failing); mocked suite 676 green. `get_latest_model("anthropic")` → `claude-sonnet-4-6`.
- **DEAD-1 DONE** — investigated wiring `tool_output_limits`/`tool_line_limits` into `hooks-tool-truncation`: NO clean seam (the hook reads its own config; the `tool:post` event payload has no limits slot; wiring would need a new cross-module channel). Per "wire-it-or-delete-it / don't invent a channel," **deleted all 5 fields** (+ getters + orphaned constants) and documented the real control points in `config.py` (truncation → `hooks-tool-truncation` config; command timeouts → shell tool, see CAL-3/CAL-4). loop-agent 478 passed; 0 references remain in source/tests. (Plan said "wire the truncation pair"; reality said no clean seam, so delete+document — the honest outcome.)
- **ATX-8 DONE (wiring)** — `response_schema` node attribute added as backward-compatible extension (EXTENSIONS §23). Promoted in `graph.py`; resolved (inline JSON or file path) in `transforms.py`; fail-loud validation; threaded to `unified_llm.generate(response_format=ResponseFormat(json_schema=...))` on the direct-LLM path (`backend.py`, `__init__.py`); spawned-agent path **fails loud** ("only supported on direct-LLM nodes yet"). Structured result stored as `outcome.notes` + parsed into `context_updates[node.id]`. 30 tests; loop-pipeline 1360 passed. **Live end-to-end UNPROVEN (needs API keys).**
- **SYNC-1 DONE** — refreshed all three `specs/canonical/*-canonical.md` to upstream `fb57a55` (byte-identical). Working `specs/*.md` left as-is (they carry local edits documented in EXTENSIONS.md).
- **Extensions documented** — added `specs/EXTENSIONS.md` §16–22 for shipped-but-unspec'd attractor features (fail-fast routing/`runs_on`, I/O contracts `requires`/`outputs`, parallel `k_of_n`/`quorum`, human `freeform`, tool `parse_json`/`tool_env`, `$param`/`${key}`, `outcome`→`preferred_label`). ATX-5 cross-referenced.
- Ledger created.
