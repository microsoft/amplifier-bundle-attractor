# Upstream Ecosystem Fixes

**Date:** 2026-02-10
**Context:** Discovered during Attractor implementation and adversarial review
**Status:** Cataloged, not yet started

These are bugs and gaps in existing Amplifier core, foundation, and module repos — not in the Attractor bundle itself. They affect the broader ecosystem and should be fixed upstream, benefiting all Amplifier users.

---

## Fix 1: Hook `modify` Action Not Read by Any Orchestrator

**Severity:** CRITICAL
**Repos:** `amplifier-module-loop-basic`, `amplifier-module-loop-streaming`, `amplifier-module-loop-events`
**Discovered by:** Amplifier-expert investigation of truncation hook wiring

**The bug:** When a hook returns `HookResult(action="modify", data=modified_data)` on a `tool:post` event, every orchestrator ignores the modified data and uses the original `result.get_serialized_output()` instead. The hook protocol's `modify` action works correctly in the hook layer — the orchestrators just never read the result.

**Evidence:**
- `loop-basic:466-468` — Uses `result.get_serialized_output()` after `hooks.emit("tool:post", ...)`, never checks `post_result.data`
- `loop-streaming:990-992` — Same pattern
- `loop-events` — Same pattern
- The string `post_result.data` does not appear in any orchestrator source

**Impact:** The entire hook modification protocol for tool results is broken ecosystem-wide. Any hook that tries to modify tool output (truncation, sanitization, transformation) is silently ignored. The `hooks-redaction` module works around this by mutating the data dict in-place (relying on Python reference semantics), which is fragile.

**The fix:** After `hooks.emit("tool:post", ...)`, check if the result has `action="modify"`:
```python
post_result = await hooks.emit("tool:post", {"tool_name": name, "result": result_data, ...})
if post_result and post_result.action == "modify" and post_result.data is not None:
    result_content = post_result.data.get("result", result_content)
```

**Scope:** 3 PRs (one per orchestrator module), ~5-10 lines each
**Blocks:** Attractor truncation wiring (C-4), any future tool-output-modifying hooks

---

## Fix 2: Provider-OpenAI Sends `reasoning.encrypted_content` for Non-Reasoning Models

**Severity:** HIGH
**Repo:** `amplifier-module-provider-openai`
**Discovered by:** E2E Test 6 (OpenAI pipeline test)

**The bug:** When `store=false` (the default for many configurations), the provider unconditionally sends `include: ["reasoning.encrypted_content"]` even for models that don't support reasoning (gpt-4.1-mini, gpt-4o, etc.). The API rejects with "Encrypted content is not supported with this model."

**Evidence:** `amplifier_module_provider_openai/__init__.py:737`:
```python
if not store_enabled:
    params["include"] = kwargs.get("include", ["reasoning.encrypted_content"])
```

**The fix:** Guard on reasoning being active:
```python
if not store_enabled and "reasoning" in params:
    params["include"] = kwargs.get("include", ["reasoning.encrypted_content"])
```

**Status:** Already fixed on branch `fix/reasoning-include-guard` (commit `9d91e3a`), NOT yet merged.
**Scope:** 1 PR, 1-line change
**Blocks:** Any OpenAI usage with non-reasoning models when store=false

---

## Fix 3: Enrich `PreparedBundle.spawn()` to Capture `orchestrator:complete` Metadata

**Severity:** HIGH
**Repo:** `amplifier-foundation` (`amplifier_foundation/bundle.py`)
**Discovered by:** Core-expert and amplifier-expert analysis of `execute() → str` limitation

**The problem:** `PreparedBundle.spawn()` returns `{"output": str, "session_id": str}` where `output` is the raw string from `execute()`. Callers that need structured metadata (status, routing labels, context updates) must parse JSON out of the string — a fragile heuristic.

The `orchestrator:complete` event already carries `status` and `turn_count`, and orchestrators can emit additional metadata. But `spawn()` doesn't capture this event from the child session.

**The fix:** Before calling `child_session.execute()`, register a temporary hook on the child session to capture `orchestrator:complete`:

```python
async def spawn(self, child_bundle, instruction, ...) -> dict[str, Any]:
    completion_data = {}
    
    async def capture_completion(event, data):
        completion_data.update(data)
        return HookResult()
    
    # Register collector on child session's hooks
    child_session.coordinator.hooks.register("orchestrator:complete", capture_completion)
    
    response = await child_session.execute(instruction)
    
    return {
        "output": response,
        "session_id": child_session.session_id,
        "status": completion_data.get("status", "success"),
        "turn_count": completion_data.get("turn_count"),
        "metadata": completion_data.get("metadata", {}),
    }
```

**Why not change `execute() → str`?** Both the core-expert and amplifier-expert independently concluded the kernel protocol should stay `→ str`. The return type is mechanism (stable); the shape of structured metadata is policy (varies per orchestrator). The event system was designed for this exact sideband data flow. The two-implementation rule isn't satisfied at the kernel level (only loop-pipeline needs non-string returns), but IS satisfied at the spawn level (pipeline, delegate, and recipes all need structured spawn results).

**Scope:** 1 PR to amplifier-foundation, ~15 lines in `bundle.py`
**Blocks:** Clean pipeline→agent structured data flow (eliminates json.dumps/json.loads hacks)

---

## Fix 4: Spawn Capability Reference Implementation Is Stale

**Severity:** MEDIUM
**Repo:** `amplifier-foundation` (`examples/07_full_workflow.py`)
**Discovered by:** Amplifier-expert code audit

**The bug:** The reference `spawn_capability` in the example doesn't accept `tool_inheritance`, `hook_inheritance`, `provider_preferences`, or `self_delegation_depth` — kwargs that `tool-delegate` sends on every spawn call. The example and the consumer have diverged.

**Impact:** Anyone building a new app using the example as reference gets broken spawn behavior when tool-delegate sends extra kwargs.

**The fix:** Update the example's `spawn_capability` to accept `**kwargs` and forward relevant params to `PreparedBundle.spawn()`:
```python
async def spawn_capability(
    agent_name, instruction, parent_session, agent_configs,
    sub_session_id=None, orchestrator_config=None, parent_messages=None,
    **kwargs,  # Accept tool_inheritance, hook_inheritance, etc.
):
    ...
```

**Scope:** 1 PR to amplifier-foundation
**Blocks:** Developer guidance accuracy

---

## Fix 5: `tool-delegate` Sends Params That `PreparedBundle.spawn()` Doesn't Accept

**Severity:** MEDIUM
**Repo:** `amplifier-foundation` (`modules/tool-delegate/`)
**Discovered by:** Amplifier-expert investigation of spawn contracts

**The bug:** tool-delegate sends `tool_inheritance`, `hook_inheritance`, `self_delegation_depth` to the spawn function. `PreparedBundle.spawn()` doesn't accept these parameters. They work because the app-cli's actual spawn implementation accepts `**kwargs` and silently drops them — but this is fragile and undocumented.

**Impact:** The contract between tool-delegate and the spawn mechanism is undocumented and relies on silent kwarg dropping. New app implementations that don't use `**kwargs` in their spawn capability will crash.

**The fix:** Either:
- (A) Update `PreparedBundle.spawn()` to accept and use `tool_inheritance`/`hook_inheritance` (proper solution)
- (B) Document that these are app-layer concerns and the spawn capability glue must handle them
- (C) At minimum, update the reference example (Fix 4) to show how to handle these

**Scope:** 1 PR to amplifier-foundation
**Blocks:** Spawn contract clarity; Attractor Track 2 (sessions all the way down)

---

## Fix 6: Misleading `amplifier init` Tip Message

**Severity:** LOW
**Repo:** `amplifier-app-cli` (`commands/init.py:150-152`)
**Discovered by:** Bug-hunter investigation of shadow environment failures

**The bug:** CLI says "Tip: Set ANTHROPIC_API_KEY, OPENAI_API_KEY, etc. to skip this" but setting API keys does NOT skip init. The `check_first_run()` function only checks `~/.amplifier/settings.yaml` — it explicitly ignores environment variables. The message actively misleads users.

**The fix:**
```python
# Before (WRONG):
"Tip: Set ANTHROPIC_API_KEY, OPENAI_API_KEY, etc. to skip this."

# After (CORRECT):
"Tip: Run 'amplifier init --yes' to auto-configure from environment variables."
```

**Scope:** 1 PR to amplifier-app-cli, 1-line text change
**Blocks:** Nothing (workaround: `amplifier init --yes`)

---

## Fix 7: CLI Should Auto-Init in Non-Interactive Contexts

**Severity:** MEDIUM
**Repo:** `amplifier-app-cli` (`commands/run.py`, `commands/init.py`)
**Discovered by:** Bug-hunter investigation of shadow environment failures

**The bug:** When `check_first_run()` returns True and stdin is not a TTY (shadow containers, CI, automation), the CLI shows an interactive prompt that can't be answered, then aborts. It should auto-init from env vars in non-interactive contexts.

**The fix:** In the run command, when first-run is detected and stdin is not a TTY:
```python
if check_first_run():
    if sys.stdin.isatty():
        prompt_first_run_init(console)
    else:
        # Non-interactive: auto-init from env vars
        from .init import init_cmd
        ctx.invoke(init_cmd, non_interactive=True)
```

**Scope:** 1 PR to amplifier-app-cli, ~10 lines
**Blocks:** Shadow/CI/automation workflows (workaround: `amplifier init --yes` before run)

---

---

## Fix 8: ConditionalHandler Should NOT Pass Through Upstream Outcomes (T0-1 Upstream Rec)

**Severity:** LOW (the correct fix is at the graph level; this is a spec clarification)
**Repo:** `amplifier-bundle-attractor` (this repo) — `modules/loop-pipeline/amplifier_module_loop_pipeline/handlers/conditional.py`
**Discovered by:** T0-1 dead-edge analysis (2026-07)
**Status:** Resolved at graph level (T0-1). Upstream spec clarification recommended.

**The situation:** `ConditionalHandler.execute()` unconditionally returns `Outcome(status=StageStatus.SUCCESS)`. It does NOT pass through the upstream node's outcome. This means a `shape=diamond` node followed by `condition="outcome!=success"` edges creates a dead edge — the diamond always produces SUCCESS, so the `outcome!=success` branch can never match.

**Why the graph-level fix is correct:** A diamond (`shape=diamond`) is the right tool when routing on `context.*` keys set by *earlier* nodes (e.g., a `preferred_label` from a `report_outcome` call). In that use case, the diamond is a pure routing switch — its own outcome is irrelevant, and unconditional SUCCESS is correct. The bug was in the graph authors using diamonds with `outcome=` conditions, not in ConditionalHandler's behavior.

**The T0-1 fix:** Replace `shape=diamond` + `condition="outcome=..."` with `shape=parallelogram` + `tool_command` that runs the real verifier and prints a routing token, routed via `condition="context.tool.last_line=<token>"`. This is the correct pattern — it routes on observed evidence, not on the handler's synthetic outcome.

**Upstream recommendation (not a code change):** The canonical spec at `github.com/strongdm/attractor` should clarify:
1. `shape=diamond` (ConditionalHandler) is for routing on `context.*` keys set by earlier nodes — NOT for routing on `outcome=` of the node before it.
2. `condition="outcome=..."` on edges from a diamond is always a bug (dead edge).
3. The correct pattern for evidence-based routing is `shape=parallelogram` + `tool_command` + `condition="context.tool.last_line=<token>"`.

**Why NOT change ConditionalHandler to pass through upstream outcomes:** If ConditionalHandler passed through the upstream outcome, it would break the valid use case where a diamond routes on `context.*` keys (e.g., `preferred_label`). The diamond would then need to be SUCCESS for the normal case AND pass through FAIL for the error case — conflating two different routing semantics. The cleaner design is: diamond = context-key routing (always SUCCESS); parallelogram = evidence routing (outcome reflects tool exit code).

**Scope:** Spec documentation PR to `github.com/strongdm/attractor`, no code change.
**Blocks:** Nothing (already fixed at graph level in T0-1).

---

## Summary

| # | Repo | Bug | Severity | Status | Blocks |
|---|---|---|---|---|---|
| **1** | loop-basic, loop-streaming, loop-events | Hook `modify` ignored for tool results | **CRITICAL** | Not started | Truncation, any hook modification |
| **2** | provider-openai | reasoning include for non-reasoning models | **HIGH** | Branch ready | OpenAI non-reasoning models |
| **3** | amplifier-foundation | Enrich spawn() with orchestrator:complete metadata | **HIGH** | Not started | Clean pipeline→agent data flow |
| **4** | amplifier-foundation | Stale spawn capability example | **MEDIUM** | Not started | Developer guidance |
| **5** | amplifier-foundation | tool-delegate spawn kwargs vs spawn() params | **MEDIUM** | Not started | Spawn contract clarity |
| **6** | amplifier-app-cli | Misleading init tip message | **LOW** | Not started | UX |
| **7** | amplifier-app-cli | No auto-init for non-interactive | **MEDIUM** | Not started | Shadow/CI workflows |
| **8** | attractor spec | ConditionalHandler/diamond routing semantics clarification | **LOW** | Resolved at graph level (T0-1) | Future graph authors |

### Recommended Fix Order

1. **Fix 1 (hook modify)** — Unblocks truncation for the entire ecosystem. Fix in all 3 orchestrators + loop-agent simultaneously.
2. **Fix 2 (provider-openai)** — Already written, just needs PR/merge.
3. **Fix 3 (spawn enrichment)** — Eliminates the json.dumps/json.loads hack. Proper structured data flow.
4. **Fixes 4-5 (spawn contract)** — Clarity for the spawn mechanism. Part of Track 2.
5. **Fixes 6-7 (CLI init)** — Quality of life. Workarounds exist.
