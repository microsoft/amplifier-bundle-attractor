# 06 - Model Stylesheet Pipeline

## What This Exercises

- **Stylesheet parsing**: The `model_stylesheet` graph attribute is parsed into `StyleRule` objects with selectors, specificity, and properties
- **CSS selectors**:
  - `*` (universal, specificity=0): Applies to all nodes as a baseline
  - `.class` (class selector, specificity=2): Targets nodes with matching `class` attribute
  - `#id` (ID selector, specificity=3): Targets a specific node by its ID
- **Specificity resolution**: Higher specificity wins. `#critical_review` (3) > `.code` (2) > `*` (0)
- **Explicit node attribute override**: `quick_fix` has `llm_model="gemini-2.5-flash-preview-05-20"` directly on the node, which beats any stylesheet rule (highest precedence)
- **Recognized properties**: `llm_model`, `llm_provider`, `reasoning_effort`

## Pipeline Structure

```
start -> analyze -> refactor -> lint_check -> critical_review -> quick_fix -> done
         (.planning) (.code)    (.fast)       (#id + .code)     (.code + explicit)
```

## Resolved Model Assignments

| Node | Class | Matching Rules | Winner (by specificity) | Final Model |
|------|-------|----------------|------------------------|-------------|
| `analyze` | `planning` | `*`(0), `.planning`(2) | `.planning` | `o3` (openai) |
| `refactor` | `code` | `*`(0), `.code`(2) | `.code` | `claude-sonnet-4-*` (anthropic — resolves to latest) |
| `lint_check` | `fast` | `*`(0), `.fast`(2) | `.fast` | `gemini-2.5-flash-preview-05-20` (gemini, low) |
| `critical_review` | `code` | `*`(0), `.code`(2), `#critical_review`(3) | `#critical_review` | `claude-opus-4-*` (anthropic, high — resolves to latest) |

> **Glob model ids.** The anthropic selectors use glob ids (`claude-sonnet-4-*`,
> `claude-opus-4-*`). These are copied into `node.attrs["llm_model"]` verbatim by
> the stylesheet, then resolved by the engine at run time to the newest stable
> served model in that line — so they self-heal and never rot. Pin a concrete id
> when you need a locked/reproducible evaluation.
| `quick_fix` | `code` | `*`(0), `.code`(2) | `.code` BUT node has explicit `llm_model` | `gemini-2.5-flash-preview-05-20` (explicit override) |

## Expected Behavior

1. Stylesheet is parsed during the INITIALIZE phase (before execution)
2. `apply_stylesheet()` walks all nodes:
   - For each node, finds all matching rules
   - For each property, keeps the highest-specificity match
   - Only sets properties the node doesn't already have explicitly
3. During execution, each node's `llm_model`, `llm_provider`, and `reasoning_effort` are available in `node.attrs` for the backend to use
4. The codergen handler passes these to the backend via the `node` parameter

## How to Run

```yaml
steps:
  - agent: attractor:pipeline-runner
    instruction: "Run the model stylesheet pipeline"
    context:
      pipeline_path: "examples/pipelines/06-model-stylesheet.dot"
```

## What to Look For

- After stylesheet application, inspect node attrs:
  - `analyze.attrs["llm_model"]` == `"o3"`
  - `critical_review.attrs["llm_model"]` == `"claude-opus-4-*"` (ID selector wins over .code class; resolved to a concrete opus id at run time)
  - `quick_fix.attrs["llm_model"]` == `"gemini-2.5-flash-preview-05-20"` (explicit attribute wins)
- Validation passes (stylesheet syntax is valid)
- Each node's prompt.md is written with the correct model context
- No stylesheet properties are applied to start/exit nodes (they have no LLM interaction)
