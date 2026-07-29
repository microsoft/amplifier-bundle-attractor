# Spec Conformance Ledger

A living record of where this implementation (`amplifier-bundle-attractor`) is **off-spec**
relative to the upstream natural-language specs at [strongdm/attractor](https://github.com/strongdm/attractor),
and the chosen disposition for each gap. The goal is not 100% literal conformance — it is that
**every divergence is a deliberate, recorded choice**, trending over time toward one of:

- **ALIGN** — fix the implementation to fulfill the spec.
- **DIVERGE** — keep the implementation's behavior; record it as an intentional, documented
  divergence (and fold it into the spec / `specs/EXTENSIONS.md` so the spec stops lying).
- **IMPROVE** — implementation deliberately exceeds the spec; promote the idea upstream.

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
| coding-agent-loop | ~17 | 9 | 2 (bugs CAL-1, CAL-2) | 7 |
| attractor | ~30 | 8 | 2 (ATX-1, ATX-10) | 6 |

The engine layer (attractor) is the strongest — substantially a **superset** of the spec. The
material weaknesses are concentrated in the LLM client's per-provider metadata and a small set
of deliberate omissions (resume, tool-hooks, execution-environment).

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
| ULM-18 | §7.10 `OpenAICompatibleAdapter` (vLLM/Ollama named explicitly) was implemented but **never registered** — `Client.from_env()` (`client.py:89`) only checked the three cloud API keys, so a local OpenAI-compatible endpoint was unreachable and `from_env()` raised when no cloud key was present. Adapter also hardcoded its `name` in 6 places, so `Response.provider` reported `"openai_compat"` regardless of the key it was registered under and `provider_options[<name>]` was silently dropped. | `:1781` (§7.10), `:1777` (§7.9 step 7) | `client.py::_openai_compat_from_env()` registers from `OPENAI_COMPAT_BASE_URL` (+ optional `_PROVIDER_NAME`, `_API_KEY`), keyed on base-URL not API key since local endpoints have none; registered LAST so it never steals `default_provider` from a cloud key (§2.2). `OpenAICompatAdapter` gained `name=`. | **DONE — LIVE-PROVEN** | ALIGN |

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

---

## 3. attractor-spec → `modules/loop-pipeline` (+ supporting tool/hook modules)

| ID | Area | Spec | Impl | Status | Disposition |
|----|------|------|------|--------|-------------|
| ATX-1 | Node `timeout` unit mismatch (ms stored, consumed as seconds) | `timeout_seconds` | `engine.py:485`, `handlers/tool.py:105` | **DONE** | ALIGN |
| ATX-2 | Checkpoint-based RESUME (restore context/completed/retry, continue after `current_node`; `full`→`summary:high` degrade) | `§5.3`, DoD `:1857` | engine always restarts from start; `load_checkpoint()` never rehydrates | OPEN | **DECIDE** (ALIGN vs DIVERGE) |
| ATX-3 | Tool-call hooks `tool_hooks.pre`/`.post` (shell around each LLM tool call) | `§9.7` `:1650` | grep `tool_hooks`=0 | OPEN | **DECIDE** (ALIGN vs DIVERGE) |
| ATX-4 | HTTP server mode (REST + SSE) | `§9.5` (optional) | not present; programmatic tools instead | OPEN | DIVERGE (spec-optional) |
| ATX-5 | `outcome=` condition resolves to `preferred_label` first | `§10.4 :1693` | `conditions.py:75` returns `preferred_label or status` | OPEN | DIVERGE (intentional; load-bearing for report_outcome routing) |
| ATX-6 | Retry on FAIL | spec self-contradicts: `§3.5 :519` (no) vs DoD `:1833` (yes) | retries RETRY only (`retry.py:238`) | OPEN | ALIGN-spec-first (reconcile the spec) |
| ATX-7 | `stack.child_workdir`; condition literal unquoting (`§10.5`) | `:1743` | not handled | OPEN | ALIGN (minor) |
| ATX-10 | Multi-match fan-out: non-component nodes with ≥2 simultaneously-matching conditional edges routed to ALL targets in parallel (unledgered dialect; never in spec §3.3) | `§3.3 :421-458` (`best_by_weight_then_lexical`) | `engine.py` (retired `select_all_matching_edges` gate; now routes through `select_edge()` only) | **DONE** | ALIGN — conformance restored (T0-4) |

**Shipped extensions (IMPROVE — fold into `specs/EXTENSIONS.md`):** fail-fast edge routing with
`runs_on`/`continue_on_fail`; skip-propagation contracts (`requires=`/`outputs=`/`failed_outputs`,
`PIPELINE_NODE_SKIPPED`/`PIPELINE_NODE_CONTRACT_VIOLATION`); parallel `k_of_n`/`quorum`/`error_policy`;
human `freeform` mode + attachments; tool `parse_json`/`tool_env`/`tool.last_line`; `$param`/`${key}`
substitution beyond `$goal`.

---

## Cross-cutting

| ID | Item | Status | Disposition |
|----|------|--------|-------------|
| SYNC-1 | Re-sync vendored `specs/canonical/` to upstream byte-for-byte | **DONE** (canonical @ `fb57a55`) | ALIGN |
| DEAD-1 | Dead `SessionConfig` fields implying coverage that isn't wired (`tool_output_limits`, `tool_line_limits`, `default_command_timeout_ms`, `max_command_timeout_ms`, `get_max_tool_rounds`) | **DONE** | DIVERGE (all deleted + documented) |
| ATX-8 | DOT `response_schema` node attribute → per-provider structured output (NOT in canonical spec; §4.5 keeps output format at backend) | **DONE — LIVE-PROVEN** (all 3 providers via DOT pipeline) | IMPROVE (extension §23) |
| ATX-9 | DOT backends didn't recover Anthropic structured output from the `__structured_output__` tool call (only read `result.text`, which is empty on the tool-extraction path) | live: `outcome.notes=''` | `loop-pipeline/__init__.py`, `backend.py` | **DONE** (live-found + fixed) | ALIGN |

---

## DECIDE items — context for a future decision

Deferred by owner; not decided yet. Captured context so the future call is well-informed.
Each is currently **OPEN** with disposition pending (ALIGN vs DIVERGE).

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

### ATX-2 — Checkpoint-based resume (attractor §5.3)
- **Spec wants:** load `checkpoint.json` → restore context/completed-nodes/retry counters → continue
  from the node after `current_node`; degrade `full`→`summary:high` one hop on resume.
- **Reality now:** engine always restarts from the start node; `checkpoint.py` is an observability
  record (explicitly "not a resume marker"); `load_checkpoint()` is never used to rehydrate.
  Idempotency is **graph-owned**: handlers skip already-done work (see `examples/pipelines/12-graph-resume`).
- **Decision hinges on:** is true crash-resume *with in-memory context restoration* needed, or is
  handler-level idempotency ("don't redo finished work") sufficient? If the latter → **DIVERGE**
  (document the graph-owned-idempotency model as the intentional design). If we need resume-after-crash
  that restores accumulated context mid-pipeline → **ALIGN**.
- **Cost if ALIGN:** real state-serialization + rehydration surface; the `full`→`summary:high`
  degrade rule; correctness testing across partial-completion states.

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

### 2026-07-29
- **ULM-18 DONE — LIVE-PROVEN** — §7.10's `OpenAICompatibleAdapter` shipped complete and fully unit-tested but was imported by **nothing**: `Client.from_env()` registered only the three cloud API keys, so a local OpenAI-compatible endpoint (vLLM, Ollama, llama.cpp, LM Studio, Docker Model Runner) was unreachable and `from_env()` **raised** in a local-only environment. Added `_openai_compat_from_env()` (`client.py`), keyed on `OPENAI_COMPAT_BASE_URL` — **not** an API key, because local endpoints have none and keying on one makes them structurally unreachable. Optional `OPENAI_COMPAT_PROVIDER_NAME` (default `"local"`, and the string a caller puts in `Request.provider` / a DOT node puts in `llm_provider`) and `OPENAI_COMPAT_API_KEY` (default `"not-needed"`, matching `provider-chat-completions`' existing convention). Registered **last** so it never steals `default_provider` from a cloud key already present (§2.2). Also gave `OpenAICompatAdapter` a `name=` parameter: it previously hardcoded `"openai_compat"` in **6** sites, so `Response.provider` misreported which endpoint served a request (breaking the audit trail that data-boundary claims rest on) and `provider_options[<registered-name>]` was silently discarded. A declared-list form for N named endpoints was written, reviewed, and **cut** as speculative — one endpoint serving many models is the real shape; per-node model choice comes from `llm_model`. +5 unit tests; unified-llm-client suite **282 passed**. Live: two-node DOT pipeline through `DirectProviderBackend` against Docker Model Runner with **all cloud keys unset** → `providers=['docker']`, `adapter.name='docker'`, status `success`; negative control (dead port) → status `fail`, `failure_reason="Connection error."`, exit 1.

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
