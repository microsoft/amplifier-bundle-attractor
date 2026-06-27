# DOT Syntax Cheat Sheet

One-page reference for writing Attractor pipelines.

## Minimal Valid Pipeline

```dot
digraph {
    start [shape=Mdiamond]
    task  [prompt="Do the thing described in $goal"]
    done  [shape=Msquare]
    start -> task -> done
}
```

## Shape -> Handler Mapping

| Shape | Handler | Description | LLM? |
|-------|---------|-------------|------|
| `Mdiamond` | start | Pipeline entry point | No |
| `Msquare` | exit | Pipeline exit, triggers goal gates | No |
| `box` | codergen | LLM agent with full tool access | Yes |
| `component` | parallel | Runs all outgoing edges concurrently | No |
| `tripleoctagon` | parallel.fan_in | Collects results from parallel branches | No |
| `parallelogram` | tool | Direct tool invocation (no LLM) | No |
| `hexagon` | wait.human | Pauses for human approval before proceeding | No |
| `house` | stack.manager_loop | Nested sub-pipeline (supervisor cycle) (experimental) | Indirect |
| `folder` | pipeline | Sub-pipeline from external DOT file | No |

## Node Attributes Quick Reference

| Attribute | Type | Default | Description |
|-----------|------|---------|-------------|
| `prompt` | string | -- | Instructions for the LLM. `$goal` and `$param` tokens are expanded. |
| `goal_gate` | bool | false | Must succeed for pipeline to complete |
| `max_retries` | int | graph default | Retry count on failure |
| `retry_target` | string | -- | Node to jump to on gate failure |
| `fidelity` | string | "compact" | Context carryover mode |
| `llm_provider` | string | -- | Override provider (anthropic/openai/gemini) |
| `llm_model` | string | -- | Override model name |
| `reasoning_effort` | string | -- | low/medium/high (provider-dependent) |
| `auto_status` | bool | false | Force SUCCESS regardless of outcome |
| `timeout` | string | -- | Per-node timeout (e.g. "30s", "2m") |
| `class` | string | -- | CSS class for model stylesheet matching |
| `dot_file` | string | -- | Path to child DOT file (folder nodes only) |
| `outputs` | string | -- | Comma-separated child context keys to merge back (folder nodes only) |
| `context.*` | string | -- | Inject named values into child context (folder nodes only) |

## Edge Attributes Quick Reference

| Attribute | Type | Description |
|-----------|------|-------------|
| `condition` | string | Key=value match: `outcome=success` (NOT Python) |
| `label` | string | Matched against node's `preferred_label` |
| `weight` | int | Higher = preferred when multiple edges match |
| `fidelity` | string | Override fidelity for this transition |
| `thread_id` | string | Share message history across edges with same ID |

## Graph Attributes Quick Reference

| Attribute | Type | Description |
|-----------|------|-------------|
| `goal` | string | Pipeline objective. Expands `$goal` in prompts. |
| `default_fidelity` | string | Default fidelity for all nodes |
| `default_max_retry` | int | Default retry count for all nodes |
| `retry_target` | string | Retry target when exit has unsatisfied goal gates (spec §3.4); not consulted on per-node failure |
| `max_pipeline_duration` | string | Timeout for entire pipeline (e.g. "10m") |
| `model_stylesheet` | string | CSS-like rules for model/provider config |
| `params` | string | Comma-separated list of `$param` names (documentation) |

## Model Stylesheet

```dot
graph [model_stylesheet="
    box { llm_provider: anthropic; llm_model: claude-sonnet-4-6 }
    .fast { llm_model: claude-haiku-3-5-20241022 }
"]
```

Selectors match node shapes or classes (`.classname`).

## Variable Expansion

| Token | Source | Description |
|-------|--------|-------------|
| `$goal` | `graph.goal` or tool `goal` parameter | The pipeline objective |
| `$param_name` | `params` dict from tool input | Custom key-value parameters |

Example: `params: {"language": "Python"}` expands `$language` in prompts.

## Copy-Paste Patterns

### Linear (3 stages)
```dot
digraph {
    start [shape=Mdiamond]
    plan [prompt="Create a plan for: $goal"]
    implement [prompt="Implement the plan", goal_gate=true]
    test [prompt="Write and run tests"]
    done [shape=Msquare]
    start -> plan -> implement -> test -> done
}
```

### Retry Loop
```dot
digraph {
    graph [default_max_retry=3]
    start [shape=Mdiamond]; done [shape=Msquare]
    do_work [prompt="$goal", goal_gate=true, retry_target="do_work"]
    verify [prompt="Verify the work"]
    start -> do_work -> verify
    verify -> done [condition="outcome=success"]
    verify -> do_work [condition="outcome!=success"]
}
```

### Parallel Fan-Out/In
```dot
digraph {
    start [shape=Mdiamond]; done [shape=Msquare]
    fork [shape=component, join_policy="wait_all"]
    join [shape=tripleoctagon]
    a [prompt="Task A"]; b [prompt="Task B"]; c [prompt="Task C"]
    start -> fork
    fork -> a; fork -> b; fork -> c
    a -> join; b -> join; c -> join
    join -> done
}
```

### Human Approval Gate
```dot
digraph {
    start [shape=Mdiamond]; done [shape=Msquare]
    work [prompt="$goal"]
    review [shape=hexagon, label="Human Review"]
    start -> work -> review -> done
}
```

### Sub-Pipeline (Folder)
```dot
digraph {
    start [shape=Mdiamond]; done [shape=Msquare]
    prepare [prompt="Prepare inputs for sub-pipeline"]
    sub [shape=folder,
         dot_file="pipelines/child.dot",
         "context.goal"="$goal",
         outputs="result"]
    finish [prompt="Use $result from sub-pipeline"]
    start -> prepare -> sub -> finish -> done
}
```

### Model Stylesheet Example
```dot
digraph {
    graph [
        goal="$goal",
        model_stylesheet="box { llm_provider: anthropic; llm_model: claude-sonnet-4-6 }
                          .reasoning { llm_provider: openai; llm_model: o3-mini; reasoning_effort: high }"
    ]
    start [shape=Mdiamond]; done [shape=Msquare]
    plan [class="reasoning", prompt="Create a plan for: $goal"]
    implement [prompt="Execute the plan"]
    start -> plan -> implement -> done
}
```

## Quoting and Escaping

Attribute values are delimited by **plain double-quotes only**.  Backslash-escapes
are valid **inside** a quoted value, never as the outer delimiter.

```dot
# CORRECT — plain double-quotes as delimiters
verify -> execute [condition="context.tool.last_line=continue"]
node [tool_command="echo \"hello world\""]   # \" inside the value = literal "

# WRONG — backslash-quote as delimiter (parse error)
verify -> execute [condition=\"context.tool.last_line=continue\"]
```

**Rule of thumb**: if you're writing `attr=\"...\"`, stop — the outer `\"` is
wrong.  Only the inner content of a string may use `\"` as an escape.

The tricky case is `tool_command` or `prompt` attributes that contain shell
quoting.  The outer delimiters are still plain `"`:

```dot
node [tool_command="if [ ! -d \"$dir\" ]; then echo missing; fi"]
#                   ^^ interior escape for literal "    ^^ still plain outer
```

**What happens if you get it wrong (pre-fix):** the tokenizer silently
mis-parses `\"key=value\"` as the bare key `key=value`, which resolves
truthy — so a conditional edge appears to match even when its condition
is false.  **After the fix**, a loud `ValueError` points at the position
and names the required form.

## Common Mistakes

| Mistake | Fix |
|---------|-----|
| Forgetting `shape=Mdiamond` on start | Every pipeline needs exactly one Mdiamond |
| Forgetting `shape=Msquare` on exit | Every pipeline needs at least one Msquare |
| Using `goal_gate=true` without `retry_target` | Gate failure with no retry = pipeline fails |
| Condition on wrong context key | Use `outcome` not `status` |
| Missing edge from node | Every non-exit node needs at least one outgoing edge |
| `$goal` not expanding | Ensure graph has `goal="..."` attribute or pass `goal` to the tool |
| Fan-in without fan-out | `tripleoctagon` expects `parallel.results` in context |
| `$param` not expanding | Pass `params` dict to `run_pipeline` tool call |
| Using `\"...\"` as attribute delimiter | Use plain `"..."` — see **Quoting and Escaping** above |
