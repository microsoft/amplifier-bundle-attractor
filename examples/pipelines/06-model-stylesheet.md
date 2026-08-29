# 06 - Model Stylesheet Pipeline

> **Engine-feature demo** — this guide teaches a single mechanism: CSS-style
> `model_stylesheet` rules for per-node model selection. For the canonical
> attractor shape used in real work, start with
> [Tutorial 00: The Convergence Loop](00-convergence-loop.md).

## Run it

Self-contained -- the goal is baked into the `.dot`, so no `--param` is needed.
From the **attractor repo root**:

```bash
DOT="$PWD/examples/pipelines/06-model-stylesheet.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
dot-runner run "$DOT" --cwd .
```

See [README.md](README.md) in this folder for the run pattern and why the `$DOT` capture + `cd` + `--cwd .` are needed (box-node process-cwd alignment + dot-path resolution).

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
| `analyze` | `planning` | `*`(0), `.planning`(2) | `.planning` | `gpt-[5-9]*` (openai) |
| `refactor` | `code` | `*`(0), `.code`(2) | `.code` | `claude-sonnet-*` (anthropic) |
| `lint_check` | `fast` | `*`(0), `.fast`(2) | `.fast` | `gemini-*-flash` (gemini, low) |
| `critical_review` | `code` | `*`(0), `.code`(2), `#critical_review`(3) | `#critical_review` | `claude-opus-*` (anthropic, high) |

> **How these globs resolve — and how evergreen they are.** Each glob is copied into
> `node.attrs["llm_model"]` verbatim, then the engine resolves it against the
> provider's LIVE model list at run time (newest stable match wins). Evergreen-ness
> depends on whether the provider keeps a stable TIER NAME across generations:
> - Anthropic (`claude-sonnet-*`, `claude-opus-*`) and Gemini (`gemini-*-flash`) —
>   the tier persists, so the glob tracks new generations indefinitely.
> - OpenAI has no persistent tier (the generation *is* the name), so `.planning`
>   uses a generation RANGE `gpt-[5-9]*` — tracks the newest through gpt-9, and needs
>   a one-char bump at gpt-10.
>
> A bare concrete id (e.g. `claude-sonnet-4-6`) is NOT resolved — it's passed to the
> provider as-is and 404s once retired. Pin a concrete id only for locked evals.
| `quick_fix` | `code` | `*`(0), `.code`(2) | `.code` BUT node has explicit `llm_model` + `llm_provider` | `gemini-*-flash` on `gemini` (explicit override) |

> **Overriding a model glob on a node?** Override the **provider too**. A glob is
> resolved against the node's provider, so `llm_model="gemini-*-flash"` needs
> `llm_provider="gemini"` alongside it — otherwise it inherits `anthropic` from the
> `.code` class and matches nothing. (Concrete ids bypass resolution and are
> provider-inferred; globs are not.)

## Expected Behavior

1. Stylesheet is parsed during the INITIALIZE phase (before execution)
2. `apply_stylesheet()` walks all nodes:
   - For each node, finds all matching rules
   - For each property, keeps the highest-specificity match
   - Only sets properties the node doesn't already have explicitly
3. During execution, each node's `llm_model`, `llm_provider`, and `reasoning_effort` are available in `node.attrs` for the backend to use
4. The codergen handler passes these to the backend via the `node` parameter

## Or run from a bundle / recipe

```yaml
steps:
  - agent: attractor:pipeline-runner
    instruction: "Run the model stylesheet pipeline"
    context:
      pipeline_path: "examples/pipelines/06-model-stylesheet.dot"
```

## What to Look For

- After stylesheet application, inspect node attrs:
  - `analyze.attrs["llm_model"]` == `"gpt-[5-9]*"`
  - `critical_review.attrs["llm_model"]` == `"claude-opus-*"` (ID selector wins over .code class; resolved to a concrete opus id at run time)
  - `quick_fix.attrs["llm_model"]` == `"gemini-*-flash"` (explicit attribute wins)
- Validation passes (stylesheet syntax is valid)
- Each node's prompt.md is written with the correct model context
- No stylesheet properties are applied to start/exit nodes (they have no LLM interaction)
