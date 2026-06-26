# Spec Gap Analysis v2 — Canonical Spec Comparison

**Date:** 2026-02-13
**Scope:** Comparison of three canonical specifications against the current Amplifier bundle implementation
**Specs analyzed:**
- Unified LLM Client Specification (`unified-llm-spec-canonical.md`)
- Coding Agent Loop Specification (`coding-agent-loop-spec-canonical.md`)
- Attractor Pipeline Specification (`attractor-spec-canonical.md`)

---

## Part 1: Architectural Alignment Assessment

### The Spec's 3-Layer Architecture

The three canonical specs define a layered system:

```
Layer 3: Attractor Pipeline     DOT graph orchestration, node handlers, model stylesheet
          ─────────────────────────────────────────────────────────────────────
Layer 2: Coding Agent Loop      Agentic loop, provider profiles, tool registry, execution env
          ─────────────────────────────────────────────────────────────────────
Layer 1: Unified LLM Client     Provider adapters, streaming, retry, middleware, data model
```

### Amplifier's Architecture

Amplifier organizes the same concerns differently:

```
Bundles:        attractor-profile-{anthropic,openai,gemini}    Provider-specific agent configs
                attractor (top-level)                          Pipeline orchestrator bundle
                ─────────────────────────────────────────────────────────────────────
Modules:        loop-pipeline                                  Pipeline engine (Attractor Layer 3)
                loop-agent                                     Agentic loop (Coding Agent Layer 2)
                tool-filesystem, tool-bash, tool-search, etc.  Tool implementations
                provider-anthropic, provider-openai, etc.      LLM provider adapters
                ─────────────────────────────────────────────────────────────────────
Kernel:         amplifier-core                                 Session, Coordinator, Provider protocol,
                                                               ChatRequest/ChatResponse, tool dispatch
```

### Where the Mapping Is Clean

| Spec Concept | Amplifier Equivalent | Alignment |
|---|---|---|
| `ProviderAdapter.complete()` | `Provider.complete(ChatRequest) → ChatResponse` | **Excellent.** Same contract, same semantics. |
| `ProviderProfile` (tools + system prompt + model) | Bundle YAML (provider + tools + context includes) | **Excellent.** Bundles achieve the same composition declaratively. |
| Provider-aligned toolsets (codex-rs, Claude Code, gemini-cli) | Profile-specific YAMLs with different tool modules and context | **Excellent.** Each profile has its own tool set and system prompt. |
| `CodergenBackend.run()` | `AmplifierBackend.run()` in `backend.py` | **Excellent.** Direct implementation of the spec interface. |
| Node handler registry | Handler registry in `loop-pipeline` module | **Good.** Shape-to-handler mapping implemented. |
| `ToolDefinition` / `RegisteredTool` | Amplifier tool modules with schema + execute | **Good.** Different structure but same contract. |
| Agentic loop (LLM → tool calls → loop) | `loop-agent` orchestrator module | **Good.** Core loop semantics match. |
| Pipeline execution engine | `loop-pipeline` orchestrator module | **Good.** DOT parsing, traversal, edge selection all implemented. |
| Session state and history | Amplifier Session with message history | **Good.** Kernel provides session lifecycle. |
| Event system | Amplifier hooks system | **Partial.** Hooks cover some events but not the full spec taxonomy. |

### Where the Mapping Diverges

#### 1. Single Unified Client vs. Separate Provider Modules

**Spec vision:** A single `Client` object holds multiple registered provider adapters and routes per-request based on the `provider` field. You write `client.complete(Request(provider="anthropic", ...))` and the same client can also handle `provider="openai"`.

**Amplifier reality:** Each provider is a separate module. A session has ONE active provider. Routing between providers happens at the **bundle/session level** (the pipeline backend spawns different child sessions with different profiles), not at the request level.

**Impact:** For the Coding Agent Loop (Layer 2), this is fine — each agent session uses one provider. For the Attractor Pipeline (Layer 3), the spec's model stylesheet expects per-node provider routing, which the backend already supports by resolving `llm_provider` from node attributes and spawning the correct profile. The architectural mismatch is real but the functional result is equivalent.

#### 2. `ProviderProfile` as Python Class vs. Bundle Composition

**Spec vision:** `ProviderProfile` is a runtime object with methods like `build_system_prompt()`, `tools()`, `provider_options()`, and capability flags.

**Amplifier reality:** The same information is declared in YAML bundle files. The system prompt comes from `context/system-*.md` includes. Tools come from tool module declarations. Provider options come from provider module config.

**Impact:** This is a **valid architectural equivalence**, not a gap. Amplifier's declarative approach is arguably better for composability and version control. The only functional gap is that there's no runtime `build_system_prompt()` method that dynamically includes environment context (platform, git status, date) — this would need to be handled by the orchestrator module.

#### 3. Middleware/Interceptor Chain

**Spec vision:** The `Client` supports middleware that wraps provider calls for logging, caching, cost tracking, rate limiting, etc.

**Amplifier reality:** Amplifier has a hooks system that provides pre/post processing on sessions, but the Provider protocol itself has no middleware chain. Cross-cutting concerns are handled at the module level (e.g., separate hook modules for approval, logging, redaction).

**Impact:** Amplifier's hook modules cover the same use cases but through a different mechanism. This is an ecosystem-level concern, not an Attractor-specific gap.

#### 4. Per-Request Provider Routing Within a Session

**Spec vision:** `Request.provider` routes each individual call to a different adapter.

**Amplifier reality:** A session is bound to one provider. To route to a different provider, you spawn a different session.

**Impact:** For the pipeline use case, this works — each node spawns its own session. For a hypothetical single-session multi-provider scenario, this would be a gap. In practice, the session-spawn approach is more robust because it isolates provider state.

---

## Part 2: Bundle Architecture Gap — Multi-Provider Pipeline

### The Core Architectural Intent

The spec's model stylesheet (Section 8) enables a **single pipeline** to route different nodes to different providers:

```dot
graph [model_stylesheet="
    * { llm_model: claude-sonnet-4-6; llm_provider: anthropic; }
    .code { llm_model: claude-opus-4-6; llm_provider: anthropic; }
    #critical_review { llm_model: gpt-5.2; llm_provider: openai; reasoning_effort: high; }
"]
```

### What Exists Today

**The engine supports multi-provider routing.** `backend.py` lines 116-121 read `llm_provider` from node attributes, look up the corresponding profile, and spawn a child session with that provider:

```python
provider = node.attrs.get("llm_provider", "anthropic")
profile_name = self._profiles.get(provider, next(iter(self._profiles.values()), ""))
```

**The e2e pipeline profile demonstrates the pattern.** `attractor-e2e-pipeline-anthropic.yaml` shows how to wire a pipeline orchestrator with a child agent profile:

```yaml
agents:
  attractor-anthropic:
    description: Anthropic coding agent (Claude Code aligned)
    session:
      orchestrator:
        module: loop-agent
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
        config:
          default_command_timeout_ms: 120000
session:
  orchestrator:
    module: loop-pipeline
    config:
      profiles:
        anthropic: attractor-anthropic
```

### What's Missing

**No single multi-provider pipeline bundle exists.** The e2e profile only wires Anthropic. To run a pipeline that routes nodes to different providers, you'd need a bundle that declares all three providers as available agents and maps them in the `profiles` config.

**The top-level `bundle.md` exposes provider-specific agents, not a pipeline entry point.** The bundle declares three separate agents (`attractor-profile-anthropic`, `attractor-profile-openai`, `attractor-profile-gemini`) but no pipeline agent that can use all three.

### The Fix: New Multi-Provider Pipeline Bundle

Create `profiles/attractor-pipeline-multi.yaml`:

```yaml
bundle:
  name: attractor-pipeline-multi
  version: 0.1.0
  description: >
    Multi-provider Attractor pipeline. Routes pipeline nodes to different
    providers based on the model stylesheet in the DOT file.

includes:
  - bundle: attractor:behaviors/attractor-core

agents:
  attractor-anthropic:
    description: Anthropic coding agent for pipeline nodes
    session:
      orchestrator:
        module: loop-agent
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
        config: {max_tool_rounds_per_input: 50, default_command_timeout_ms: 120000}
  attractor-openai:
    description: OpenAI coding agent for pipeline nodes
    session:
      orchestrator:
        module: loop-agent
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
        config: {max_tool_rounds_per_input: 50, default_command_timeout_ms: 10000}
  attractor-gemini:
    description: Gemini coding agent for pipeline nodes
    session:
      orchestrator:
        module: loop-agent
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
        config: {max_tool_rounds_per_input: 50, default_command_timeout_ms: 10000}

providers:
  - module: provider-anthropic
    source: git+https://github.com/microsoft/amplifier-module-provider-anthropic@main
  - module: provider-openai
    source: git+https://github.com/microsoft/amplifier-module-provider-openai@main
  - module: provider-gemini
    source: git+https://github.com/microsoft/amplifier-module-provider-gemini@main

session:
  orchestrator:
    module: loop-pipeline
    source: ./modules/loop-pipeline
    config:
      profiles:
        anthropic: attractor-anthropic
        openai: attractor-openai
        gemini: attractor-gemini

tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main
```

Additionally, update `bundle.md` to expose the pipeline as a primary persona alongside the per-provider agents.

---

## Part 3: Unified LLM Spec Gaps (~41% DoD Coverage)

### Assessment Methodology

The Unified LLM Spec's Definition of Done (Section 8) contains 80+ individual checklist items across 10 subsections. Amplifier's provider modules and kernel address some of these; others are out of scope for Attractor specifically.

Items are graded:
- **PASS** — Fully implemented
- **PARTIAL** — Implemented with limitations
- **FAIL** — Not implemented
- **N/A-AMPLIFIER** — Handled at a different Amplifier architectural layer (not an Attractor concern)

### 8.1 Core Infrastructure

| DoD Item | Status | Notes |
|---|---|---|
| `Client.from_env()` construction | N/A-AMPLIFIER | Amplifier uses bundle YAML + env vars for provider config |
| Programmatic client construction | N/A-AMPLIFIER | Coordinator/Session API serves this role |
| Provider routing by `provider` field | FAIL | No per-request provider routing; session-level only |
| Default provider when `provider` omitted | PASS | Bundle declares one active provider |
| `ConfigurationError` when no provider | PASS | Session fails to start without provider |
| Middleware chain execution order | FAIL | No middleware/interceptor on Provider protocol |
| Module-level default client | N/A-AMPLIFIER | Amplifier uses Coordinator pattern instead |
| Model catalog with `get_model_info()` | FAIL | No model catalog; model strings used directly |

### 8.2 Provider Adapters (per provider)

| DoD Item | Status | Notes |
|---|---|---|
| Native API usage (not compat shim) | PASS | Each provider module uses native API |
| Authentication from env/config | PASS | Provider modules handle auth |
| `complete()` returns correct Response | PASS | Provider protocol implemented |
| `stream()` returns async StreamEvent iterator | FAIL | Provider protocol has no `stream()` method |
| System message handling per convention | PASS | Providers handle system message extraction |
| All 5 roles translated correctly | PARTIAL | DEVELOPER role not universally supported |
| `provider_options` escape hatch | FAIL | No `provider_options` field on ChatRequest |
| Beta headers (Anthropic) | PARTIAL | Some beta headers set; not configurable per-request |
| HTTP error → error hierarchy | PARTIAL | Basic error handling; not full hierarchy |
| `Retry-After` header parsing | FAIL | Not parsed or exposed |

### 8.3 Message & Content Model

| DoD Item | Status | Notes |
|---|---|---|
| Text-only messages across all providers | PASS | Core functionality works |
| Image input (URL, base64, file path) | PARTIAL | Base64 support varies by provider module |
| Audio/document content parts | FAIL | Not in ChatRequest/ChatResponse model |
| Tool call round-trip | PASS | Tool calls and results work |
| Thinking blocks (Anthropic) | PARTIAL | Extended thinking supported but not all fields |
| Redacted thinking round-trip | FAIL | Not handled |
| Multimodal messages | PARTIAL | Limited support |

### 8.4 Generation

| DoD Item | Status | Notes |
|---|---|---|
| `generate()` with text prompt | N/A-AMPLIFIER | High-level API; Attractor uses `complete()` directly |
| `generate()` with messages list | N/A-AMPLIFIER | Same |
| `stream()` yields TEXT_DELTA events | FAIL | No streaming on Provider protocol |
| `stream()` yields STREAM_START/FINISH | FAIL | No streaming |
| start/delta/end pattern for text | FAIL | No unified StreamEvent taxonomy |
| `generate_object()` structured output | FAIL | No structured output API |
| `generate_object()` raises NoObjectGeneratedError | FAIL | No structured output |
| Cancellation via abort signal | PARTIAL | Session abort exists but not per-request |
| Timeouts (total + per-step) | PARTIAL | Provider-level timeouts only |

### 8.5 Reasoning Tokens

| DoD Item | Status | Notes |
|---|---|---|
| OpenAI reasoning_tokens in Usage | PARTIAL | Depends on provider module implementation |
| `reasoning_effort` parameter pass-through | PASS | Supported on ChatRequest |
| Anthropic thinking blocks as THINKING parts | PARTIAL | Basic support |
| Thinking block signature preservation | FAIL | Not explicitly handled |
| Gemini thoughtsTokenCount mapping | PARTIAL | Depends on provider module |
| Usage reports reasoning vs output tokens | PARTIAL | Not all providers distinguish |

### 8.6 Prompt Caching

| DoD Item | Status | Notes |
|---|---|---|
| OpenAI automatic caching (Responses API) | PARTIAL | Depends on provider module API choice |
| OpenAI cache_read_tokens reported | FAIL | Not in Usage model |
| Anthropic auto cache_control injection | FAIL | No automatic breakpoint injection |
| Anthropic caching beta header | PARTIAL | Some beta headers present |
| Anthropic cache_read/write_tokens | FAIL | Not in Usage model |
| Gemini automatic caching | PARTIAL | Depends on provider module |
| Gemini cache_read_tokens reported | FAIL | Not in Usage model |
| Multi-turn cache verification | FAIL | No cache metrics tracking |

### 8.7 Tool Calling

| DoD Item | Status | Notes |
|---|---|---|
| Active tools trigger execution loops | PASS | loop-agent handles this |
| Passive tools return calls to caller | PASS | Provider returns tool calls |
| `max_tool_rounds` respected | PASS | Configurable loop limits |
| Parallel tool execution | PASS | Concurrent tool dispatch |
| Tool errors → error results (not exceptions) | PASS | Error results sent to model |
| Unknown tool calls → error result | PASS | Handled gracefully |
| ToolChoice modes translated per provider | PARTIAL | Basic auto/none; not all modes |
| StepResult tracking | PARTIAL | Not full step-by-step tracking |

### 8.8 Error Handling & Retry

| DoD Item | Status | Notes |
|---|---|---|
| Error hierarchy for HTTP status codes | FAIL | Flat error handling, no typed hierarchy |
| `retryable` flag on errors | FAIL | No retryability classification |
| Exponential backoff with jitter | FAIL | No retry/backoff system in provider layer |
| `Retry-After` header override | FAIL | Not implemented |
| `max_retries = 0` disables retries | FAIL | No retry configuration |
| Rate limit (429) transparent retry | FAIL | 429 raises exception, no retry |
| Non-retryable errors raised immediately | PARTIAL | Errors raised but not classified |
| Per-step retries (not whole operation) | FAIL | No per-step retry logic |
| Streaming no-retry after partial data | FAIL | No streaming |

### Critical Gap Summary

| Gap | Severity | Where It Goes | Effort |
|---|---|---|---|
| No `stream()` on Provider protocol | Critical | Upstream (amplifier-core) | L |
| No retry/backoff system | Critical | Upstream (shared utility or provider-level) | M |
| No per-request provider routing | Medium | Architectural (works via session spawning) | N/A |
| No middleware/interceptor chain | Medium | Upstream (amplifier-core) | L |
| No `generate_object()` / structured output | Medium | Upstream (amplifier-core or module) | M |
| No unified StreamEvent taxonomy | Medium | Upstream (amplifier-core) | L |
| No `provider_options` on ChatRequest | Medium | Upstream (amplifier-core) | S |
| No model catalog | Low | Could be Attractor module or shared | S |
| No error hierarchy with retryability | Medium | Upstream (amplifier-core) | M |
| No prompt caching metrics in Usage | Medium | Upstream (amplifier-core + providers) | M |

**Ecosystem vs. Attractor classification:** Most Unified LLM Spec gaps are **ecosystem-level** concerns that belong in amplifier-core and the provider modules. They would benefit the entire Amplifier ecosystem, not just Attractor. Attractor should not build its own retry system or streaming layer — these should be pushed upstream.

---

## Part 4: Coding Agent Loop Spec Gaps (~85% DoD Coverage)

The Coding Agent Loop spec is the best-covered layer. The `loop-agent` orchestrator module, tool modules, and provider profiles together implement the majority of the spec.

### 9.1 Core Loop — Mostly PASS

| DoD Item | Status | Notes |
|---|---|---|
| Session created with ProviderProfile + ExecutionEnvironment | PASS | Bundle YAML achieves this |
| `process_input()` runs agentic loop | PASS | loop-agent implements this |
| Natural completion (text-only → exit) | PASS | Implemented |
| Round limits (`max_tool_rounds_per_input`) | PASS | Configurable |
| Session turn limits (`max_turns`) | PASS | Configurable |
| Abort signal → CLOSED | PASS | Session cancellation works |
| Loop detection with warning | PASS | Implemented |
| Sequential inputs work | PASS | Session supports multiple inputs |

### 9.2 Provider Profiles — PASS

| DoD Item | Status | Notes |
|---|---|---|
| OpenAI profile with codex-rs tools (apply_patch) | PASS | `attractor-profile-openai.yaml` + tool-apply-patch |
| Anthropic profile with Claude Code tools (edit_file) | PASS | `attractor-profile-anthropic.yaml` + tool-filesystem |
| Gemini profile with gemini-cli tools | PASS | `attractor-profile-gemini.yaml` + tool-web |
| Provider-specific system prompts | PASS | `context/system-*.md` files |
| Custom tool registration on profiles | PARTIAL | Tools via YAML; no runtime `register()` API |
| Tool name collision → latest wins | FAIL | Amplifier tool registry rejects duplicates |

### 9.3 Tool Execution — Mostly PASS

| DoD Item | Status | Notes |
|---|---|---|
| Tool dispatch through registry | PASS | Tool modules handle dispatch |
| Unknown tool calls → error result | PASS | Graceful error handling |
| JSON argument validation | PARTIAL | Basic parsing; not full JSON Schema validation |
| Tool errors → `is_error = true` results | PASS | Implemented |
| Parallel tool execution | PASS | Concurrent tool calls supported |

### 9.4 Execution Environment — PARTIAL

| DoD Item | Status | Notes |
|---|---|---|
| `LocalExecutionEnvironment` | PASS | tool-bash and tool-filesystem operate locally |
| Command timeout default 10s | PASS | Configurable per profile |
| Timeout overridable per-call | PASS | Shell tool accepts timeout parameter |
| SIGTERM → wait → SIGKILL on timeout | PASS | tool-bash handles process lifecycle |
| Env var filtering (secrets excluded) | PARTIAL | Basic filtering; not the full spec pattern |
| `ExecutionEnvironment` interface implementable | FAIL | No abstract ExecutionEnvironment interface |

**Key gap:** The spec defines an `ExecutionEnvironment` abstraction (Section 4.1) with implementations for Docker, K8s, WASM, and SSH. Amplifier's tool modules are hardcoded to local execution. There is no pluggable execution environment interface that would allow the same tools to run in a container or over SSH.

### 9.5 Tool Output Truncation — PARTIAL

| DoD Item | Status | Notes |
|---|---|---|
| Character-based truncation runs first | PASS | Truncation implemented |
| Line-based truncation runs second | PARTIAL | Not all tools have line limits |
| Truncation marker inserted | PASS | Warning message included |
| Full output in TOOL_CALL_END event | PARTIAL | Events system limited |
| Per-tool character limits match spec table | FAIL | Uniform 50k limit vs spec's per-tool table |
| Limits overridable via SessionConfig | PARTIAL | Some configurability |

**Per-tool truncation defaults gap:** The spec defines specific limits per tool:

| Tool | Spec Default | Current Implementation |
|---|---|---|
| read_file | 50,000 chars | ~50,000 (close) |
| shell | 30,000 chars | 50,000 (too generous) |
| grep | 20,000 chars | 50,000 (too generous) |
| glob | 20,000 chars | 50,000 (too generous) |
| edit_file | 10,000 chars | 50,000 (too generous) |
| write_file | 1,000 chars | 50,000 (way too generous) |

### 9.6 Steering — PASS

| DoD Item | Status | Notes |
|---|---|---|
| `steer()` queues injection | PASS | Amplifier session supports steering |
| `follow_up()` post-completion processing | PASS | Follow-up queue supported |
| SteeringTurn in history | PASS | Messages injected into history |
| Converted to user-role for LLM | PASS | Correct role mapping |

### 9.7 Reasoning Effort — PASS

| DoD Item | Status | Notes |
|---|---|---|
| Passed through to LLM SDK Request | PASS | ChatRequest.reasoning_effort |
| Mid-session change takes effect | PASS | Per-request configuration |
| Valid values: low/medium/high/null | PASS | Supported |

### 9.8 System Prompts — PARTIAL

| DoD Item | Status | Notes |
|---|---|---|
| Provider-specific base instructions | PASS | `context/system-*.md` files |
| Environment context (platform, git, date) | PARTIAL | Some context but not full spec block |
| Tool descriptions from profile | PASS | Auto-generated from tool schemas |
| Project docs (AGENTS.md, CLAUDE.md, etc.) | PARTIAL | AGENTS.md loaded; provider-specific not filtered |
| User instruction override (Layer 5) | FAIL | No explicit user override layer in system prompt |
| Provider-specific file filtering | PARTIAL | Not fully implemented |

### 9.9 Subagents — PASS

| DoD Item | Status | Notes |
|---|---|---|
| Spawn with scoped task | PASS | Amplifier delegate/spawn mechanism |
| Shared execution environment | PASS | Same filesystem |
| Independent conversation history | PASS | Separate sessions |
| Depth limiting | PASS | Configurable |
| Results returned as tool results | PASS | Outcome integration |
| send_input, wait, close_agent | PARTIAL | Basic spawn/wait; not full lifecycle API |

### 9.10 Event System — PARTIAL

| DoD Item | Status | Notes |
|---|---|---|
| All EventKinds emitted at correct times | PARTIAL | Core events present; not full taxonomy |
| Events via async iterator | PASS | Hook/event system |
| TOOL_CALL_END carries full untruncated output | PARTIAL | Not guaranteed |
| SESSION_START/SESSION_END bracket session | PASS | Session lifecycle events |
| TOOL_CALL_OUTPUT_DELTA (streaming tools) | FAIL | Not emitted |

### Remaining Gaps After Assessment

| Gap | Severity | Where It Goes | Effort |
|---|---|---|---|
| ToolRegistry latest-wins override | Medium | Attractor loop-agent module | S |
| Per-tool truncation defaults per spec table | Medium | Attractor tool config or loop-agent | S |
| TOOL_CALL_OUTPUT_DELTA event | Low | Attractor loop-agent module | S |
| System prompt Layer 5 user override | Medium | Attractor loop-agent module | S |
| ExecutionEnvironment abstraction | Low (long-term) | Upstream architectural change | L |
| Full JSON Schema validation for tool args | Low | Attractor or upstream | M |

---

## Part 5: Attractor Pipeline Spec Gaps

### 11.1 DOT Parsing — PASS

All parsing items are implemented: digraph parsing, attribute extraction, chained edges, subgraphs, default blocks, class attributes, comments.

### 11.2 Validation and Linting — PASS

Start/exit node validation, reachability, edge targets, condition syntax — all implemented.

### 11.3 Execution Engine — PASS

Core traversal loop, handler dispatch, edge selection algorithm, terminal node handling — all implemented.

### 11.4 Goal Gate Enforcement — PASS

Goal gate tracking, exit blocking, retry target routing — all implemented.

### 11.5 Retry Logic — PASS

Per-node retry, backoff configuration, retry exhaustion handling — all implemented.

### 11.6 Node Handlers — MOSTLY PASS

| Handler | Status | Notes |
|---|---|---|
| Start handler | PASS | No-op entry |
| Exit handler | PASS | No-op with goal gate check |
| Codergen handler | PASS | `$goal` expansion, backend delegation, artifact logging |
| Wait.human handler | PASS | Interviewer pattern with edge-derived choices |
| Conditional handler | PASS | Pass-through; engine evaluates edges |
| Parallel handler | PARTIAL | Basic parallel; not all join/error policies |
| Fan-in handler | PARTIAL | Basic consolidation; no LLM-based evaluation |
| Tool handler | PASS | Shell command execution |
| Custom handler registration | PASS | Registry supports custom types |

### 11.7 State and Context — PASS

Context store, handler context_updates, checkpoint save/resume — all implemented.

### 11.8 Human-in-the-Loop — PASS

Interviewer interface, question types, AutoApprove, Console, Callback, Queue implementations — all implemented.

### 11.9 Condition Expressions — PASS

`=`, `!=`, `&&` operators, outcome/preferred_label/context.* variable resolution — all implemented.

### 11.10 Model Stylesheet — PASS

Stylesheet parsing, selector matching (universal, class, ID), specificity order, property application — all implemented.

### 11.11 Transforms and Extensibility — PARTIAL

| DoD Item | Status | Notes |
|---|---|---|
| AST transforms modify Graph | PASS | Transform pipeline implemented |
| Variable expansion ($goal) | PASS | Built-in transform |
| Custom transforms registerable | PASS | Extension point exists |
| HTTP server mode | FAIL | Not implemented (optional per spec) |

### Remaining Pipeline Gaps

| Gap | Severity | Where It Goes | Effort |
|---|---|---|---|
| Parallel handler: k_of_n, quorum join policies | Low | loop-pipeline module | M |
| Parallel handler: fail_fast error policy | Low | loop-pipeline module | S |
| Fan-in handler: LLM-based candidate evaluation | Low | loop-pipeline module | M |
| HTTP server mode for web-based management | Low | New module or CLI feature | L |
| Manager loop handler: full steer/observe cycle | Medium | loop-pipeline module | M |
| Context fidelity: all modes fully wired | PARTIAL | loop-pipeline fidelity module | S |

---

## Part 6: Remediation Plan

### Phase 1: Bundle Architecture Fix (Immediate, YAML-Only)

These changes require no code — only new or modified YAML bundle files.

| Item | Description | Where | Effort | Priority |
|---|---|---|---|---|
| 1.1 Multi-provider pipeline bundle | Create `profiles/attractor-pipeline-multi.yaml` that wires all three providers as available agents with `loop-pipeline` orchestrator | Attractor bundle | S | **P0** |
| 1.2 Multi-provider agent bundle | Create a single-agent bundle that includes all three provider modules (for environments where any provider may be used) | Attractor bundle | S | P1 |
| 1.3 Update bundle.md | Expose the pipeline as the primary persona; keep per-provider agents as secondary options | Attractor bundle | S | **P0** |
| 1.4 Document profile architecture | Add a brief architecture doc explaining when to use pipeline vs. single-provider profiles | Attractor docs | S | P1 |

### Phase 2: Attractor Module Fixes (Small Code Changes)

These are changes within the Attractor codebase — either in `loop-agent`, `loop-pipeline`, or tool configuration.

| Item | Description | Where | Effort | Priority |
|---|---|---|---|---|
| 2.1 ToolRegistry latest-wins | Change tool registration to override existing tools with same name instead of rejecting duplicates. Spec Section 3.7: "Name collisions are resolved by latest-wins." | loop-agent module | S | **P0** |
| 2.2 Per-tool truncation defaults | Configure tool-specific character limits matching the spec table (read_file: 50k, shell: 30k, grep: 20k, glob: 20k, edit_file: 10k, write_file: 1k) instead of uniform 50k | loop-agent config or tool modules | S | **P0** |
| 2.3 System prompt Layer 5 wiring | Add a `user_instructions` config field that gets appended as the final (highest-priority) layer in the system prompt construction | loop-agent module | S | P1 |
| 2.4 TOOL_CALL_OUTPUT_DELTA event | Emit incremental tool output events for long-running tools (especially shell). The spec's EventKind includes this for streaming tool output to UIs | loop-agent module | S | P2 |
| 2.5 Environment context block | Generate and include the structured `<environment>` block (working dir, git info, platform, date, model) in system prompts per spec Section 6.3 | loop-agent module | S | P1 |
| 2.6 Project doc provider filtering | Filter project instruction files by active provider (Anthropic loads CLAUDE.md, not GEMINI.md; all load AGENTS.md) | loop-agent module | S | P2 |
| 2.7 Parallel handler join policies | Implement k_of_n and first_success join policies for the parallel handler | loop-pipeline module | M | P2 |
| 2.8 Manager loop handler steer cycle | Wire the full observe/guard/steer cycle for supervisor-managed child pipelines | loop-pipeline module | M | P2 |

### Phase 3: Upstream Ecosystem Enhancements (Benefits All of Amplifier)

These changes belong in amplifier-core or shared modules. They are not Attractor-specific — every Amplifier application would benefit.

| Item | Description | Where | Effort | Priority |
|---|---|---|---|---|
| 3.1 Retry/backoff utility | Create a shared retry utility with exponential backoff + jitter that can wrap any async call. Spec Section 6.6 defines the full `RetryPolicy`. Could be a standalone module or part of provider utilities. | amplifier-core or new shared module | M | **P0** |
| 3.2 `stream()` on Provider protocol | Add `stream(ChatRequest) → AsyncIterator[StreamEvent]` to the Provider protocol. This is the single largest gap in the Unified LLM spec coverage. | amplifier-core (Provider protocol) | L | **P0** |
| 3.3 Expanded error hierarchy | Create typed error classes: `ProviderError`, `RateLimitError`, `AuthenticationError`, `ContextLengthError`, etc. with `retryable` flag. Spec Section 6.1. | amplifier-core | M | P1 |
| 3.4 `provider_options` on ChatRequest | Add an escape-hatch `provider_options: dict` field to ChatRequest for passing provider-specific parameters (Anthropic beta headers, Gemini safety settings, etc.) | amplifier-core (ChatRequest model) | S | P1 |
| 3.5 Usage model expansion | Add `cache_read_tokens`, `cache_write_tokens`, `reasoning_tokens` to the Usage model. Update provider modules to populate them. | amplifier-core + provider modules | M | P1 |
| 3.6 StreamEvent taxonomy | Define the unified `StreamEvent` types (TEXT_DELTA, TOOL_CALL_START, REASONING_DELTA, FINISH, etc.) per spec Section 3.13-3.14. Required foundation for streaming. | amplifier-core | M | P1 |
| 3.7 Anthropic auto-caching | Implement automatic `cache_control` breakpoint injection in the Anthropic provider module for system prompt and conversation prefix. Spec: "single highest-ROI optimization." | provider-anthropic module | M | P1 |
| 3.8 Responses API for OpenAI | Ensure the OpenAI provider uses the Responses API (`/v1/responses`) for reasoning models to get reasoning token breakdowns. Spec Section 2.7 marks this as critical. | provider-openai module | M | P2 |

### Phase 4: Longer-Term (Significant Design Work)

These items require substantial design decisions and potentially architectural changes.

| Item | Description | Where | Effort | Priority |
|---|---|---|---|---|
| 4.1 ExecutionEnvironment abstraction | Define an `ExecutionEnvironment` interface per spec Section 4.1 that tool modules can target. Implementations: Local (default), Docker, K8s, SSH. This would allow the same coding agent to run tools in a container. | Upstream architectural change spanning tool modules | L | P2 |
| 4.2 `generate_object()` / structured output | Add a structured output API that uses provider-native mechanisms (OpenAI json_schema, Gemini responseSchema, Anthropic tool-extraction fallback). Spec Section 4.5. | amplifier-core or new module | M | P2 |
| 4.3 Unified StreamEvent taxonomy implementation | Implement the full start/delta/end streaming pattern across all three providers. Depends on 3.2 and 3.6 being complete. | Provider modules | L | P2 |
| 4.4 Middleware/interceptor chain | Add middleware support to the Provider protocol for cross-cutting concerns (logging, caching, cost tracking, rate limiting). Spec Section 2.3. | amplifier-core | L | P3 |
| 4.5 Per-request provider routing within a session | Allow a single session to route individual requests to different providers via a `provider` field on ChatRequest. Currently, sessions are bound to one provider. | amplifier-core architectural change | L | P3 |
| 4.6 Model catalog | Ship a data file of known models with capabilities, context windows, and costs. Provides `get_model_info()`, `list_models()`, `get_latest_model()` per spec Section 2.9. | New shared module | M | P3 |
| 4.7 HTTP server mode for pipelines | Expose pipeline engine as HTTP service with SSE event streaming, human gate web controls, and checkpoint inspection. Spec Section 9.5. | New module or CLI extension | L | P3 |
| 4.8 Abort signal / cancellation | Implement cooperative cancellation with `AbortController`/`AbortSignal` pattern for both generation and streaming. Spec Section 4.7. | amplifier-core | M | P3 |

---

## Summary

### Coverage by Spec Layer

| Spec | Estimated DoD Coverage | Key Strength | Key Gap |
|---|---|---|---|
| Unified LLM Client | ~41% | Provider protocol, basic complete() | No streaming, no retry, no error hierarchy |
| Coding Agent Loop | ~85% | Full agentic loop, provider profiles, tools | No ExecutionEnvironment abstraction, per-tool truncation |
| Attractor Pipeline | ~90% | DOT parsing, execution engine, handlers | Parallel join policies, HTTP server mode |

### Highest-Impact Items (Recommended Next Actions)

1. **Phase 1.1 + 1.3** (Bundle fix): Create multi-provider pipeline bundle and update bundle.md. YAML-only, immediate impact.
2. **Phase 2.1 + 2.2** (Tool registry + truncation): Small code fixes that directly address spec compliance.
3. **Phase 3.1** (Retry/backoff): The single most impactful upstream contribution. Every provider call benefits.
4. **Phase 3.2** (Provider streaming): The largest gap in the Unified LLM spec. Enables real-time UIs and efficient long-running tasks.

### Philosophy Note

Many "gaps" in the Unified LLM spec are actually **Amplifier ecosystem concerns**, not Attractor concerns. The spec envisions a monolithic client library; Amplifier achieves the same through composable modules. Where the spec says "the client should do X," Amplifier often says "a module can do X." This is a feature, not a bug — but it means some spec features need to be built as shared infrastructure rather than Attractor-specific code.

The key insight: **Attractor's pipeline layer (Layer 3) is nearly complete. The coding agent layer (Layer 2) is well-covered. The gaps concentrate in the foundation layer (Layer 1) where streaming, retry, and error handling belong.** Investing in upstream infrastructure pays dividends across the entire ecosystem.
