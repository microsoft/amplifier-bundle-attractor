# 13 — Local Provider

Run pipeline nodes on a model you host yourself: Ollama, vLLM, llama.cpp,
LM Studio, Docker Model Runner — anything exposing an OpenAI-compatible
`/v1/chat/completions` endpoint.

Two reasons to want this:

- **Cost.** Most nodes in a pipeline do not need a frontier model.
- **Data control.** Context for a node running on `local` never leaves the
  machine serving it. You choose, per node, what is allowed to reach a
  third-party provider.

## The one rule

**A `.dot` names a provider. It never says where that provider lives.**

```dot
summarise [ llm_provider="local", llm_model="qwen2.5-coder:7b" ]
```

`llm_provider="local"` is a **role**, like a DNS name. The endpoint URL is
**deployment** and is configured outside the graph. There is deliberately no
`base_url` node attribute — the recognised attributes are exactly `llm_model`,
`llm_provider`, and `reasoning_effort`.

Put a URL in a `.dot` and the graph stops working on the next machine, and your
infrastructure ends up committed to git.

## Wiring the endpoint

Pick the row matching how you run attractor.

### Programmatic / embedded — no bundle

`DirectProviderBackend` builds a `unified_llm.Client` from the environment:

```bash
export OPENAI_COMPAT_BASE_URL=http://localhost:11434/v1
export OPENAI_COMPAT_PROVIDER_NAME=local   # optional, default "local"
export OPENAI_COMPAT_API_KEY=...           # optional; local servers ignore it
```

The name you set here is what `llm_provider=` must say in the `.dot`. Nodes on
this path get **LLM calls only, no tools** — good for analysis, summarisation
and reasoning. See `examples/programmatic_usage.py`, Option C.

### Amplifier session — nodes get full tools

Mount the provider and map it to an agent:

```yaml
providers:
  - module: provider-chat-completions
    id: local                       # `id`, NOT config.name — see Gotchas
    config:
      base_url: http://localhost:11434/v1
      api_key: not-needed
      default_model: qwen2.5-coder:7b

session:
  orchestrator:
    module: loop-pipeline
    config:
      profiles:
        local: attractor-agent-local

agents:
  attractor-agent-local:
    description: Local coding agent.
    session:
      orchestrator:
        module: loop-agent        # REQUIRED inline — see Gotchas
```

No per-provider agent bundle is needed. The shipped `anthropic` / `openai` /
`gemini` agents differ only in **tool dialect** (openai gets native
`apply_patch`, gemini gets `tool-web`); a local model needs no dialect, so it
gets the provider-neutral base prompt automatically.

## Mixing local and cloud

Per-node routing is the point. Map both providers, then choose per node:

```dot
summarise [ llm_provider="local",     llm_model="qwen2.5-coder:7b" ]      // private
architect [ llm_provider="anthropic", llm_model="claude-sonnet-4-6" ]      // hard reasoning
```

Or set them by class with a stylesheet:

```dot
graph [model_stylesheet="
    *          { llm_provider: local;     llm_model: qwen2.5-coder:7b }
    .reasoning { llm_provider: anthropic; llm_model: claude-sonnet-4-6 }
"]
```

## Gotchas

**Use a concrete model id.** `qwen2.5-coder:7b`, not `qwen*`. Globs and the
family tokens (`opus`/`sonnet`/`haiku`) resolve against cloud catalogues and
will not match a local model. A concrete id skips resolution entirely.

**In a bundle, use `id:`, not `config.name`.** `id` is bridged to the kernel's
`instance_id` *and* indexed for provider-preference resolution. `config.name`
suppresses the kernel's mount remap and the module warns about it.

**The child agent needs an inline `loop-agent` orchestrator.** Omit it and the
child inherits `loop-pipeline` and recurses.

**A node that declares no `llm_provider`** falls back to `default_provider` in
the orchestrator config, or — when exactly one provider is mapped — to that one,
emitting a `pipeline:provider_defaulted` event. An implicit choice is never
silent; grep your `events.jsonl` for it.

**Pick a model that can call tools.** On the spawn path, routing depends on the
model calling `report_outcome`. Verify before committing to a model:

```bash
curl -s http://localhost:11434/v1/chat/completions -H 'Content-Type: application/json' -d '{
  "model": "<your-model>",
  "messages": [{"role":"user","content":"Call report_outcome with status success."}],
  "tools": [{"type":"function","function":{"name":"report_outcome",
    "parameters":{"type":"object","properties":{"status":{"type":"string"}},"required":["status"]}}}]
}' | jq '.choices[0].finish_reason'
```

`"tool_calls"` means it works. Anything else — pick a different model rather
than reshaping your pipeline around it.

**Context window.** Amplifier's system prompt plus tool definitions run to
roughly 53k tokens. A model served with a smaller window fails before it
generates anything. With Docker Model Runner:
`docker model configure --context-size 200000 <model>`.
