# 08 - Human Gate Pipeline

> **Engine-feature demo** — this guide teaches a single mechanism: the
> `shape=hexagon` human gate that blocks execution for a human decision.
> For the canonical attractor shape used in real work, start with
> [Tutorial 00: The Convergence Loop](00-convergence-loop.md).

## Run it

Self-contained -- the goal is baked into the `.dot`, so no `--param` is needed.
From the **attractor repo root**:

```bash
DOT="$PWD/examples/pipelines/08-human-gate.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
dot-runner run "$DOT" --cwd . --on-human-gate auto-approve
```

`--on-human-gate auto-approve` always takes the gate's first option so the run completes non-interactively; drop it and run interactively if you want the gate to actually branch.

See [README.md](README.md) in this folder for the run pattern and why the `$DOT` capture + `cd` + `--cwd .` are needed (box-node process-cwd alignment + dot-path resolution).

## What This Exercises

- **Human handler** (`shape=hexagon`): Blocks execution until a human selects an option
- **Choices from edges**: Outgoing edge labels from a human gate become the options presented to the human
- **Accelerator key parsing**: `[A] Approve` extracts key `"A"`, `[Y] Yes, deploy` extracts key `"Y"`
- **`suggested_next_ids` routing**: The human handler returns the selected edge's target node as `suggested_next_ids`, which the edge selection algorithm uses (Step 3)
- **Multiple human gates**: Two separate human decision points in one pipeline
- **AutoApproveInterviewer fallback**: When no interviewer is configured, always selects the first option (CI/CD mode)
- **Context updates**: Human selection is recorded in `human.gate.selected` and `human.gate.label`
- **Looping from human rejection**: "Request Changes" loops back through fix -> test -> review

## Pipeline Structure

```
start -> implement -> test -> review_gate --[A] Approve--> deploy_staging -> prod_gate --[Y] Yes--> deploy_prod -> done
                       ^      |                                              |
                       |      +--[R] Request Changes--> fix_issues           +--[N] No--> rollback -> done
                       |                                |                    |
                       +--------------------------------+                    +--[F] Fix--> fix_issues
```

## Expected Behavior

### Interactive Mode (ConsoleInterviewer)
1. Pipeline executes through `implement` and `test`
2. At `review_gate`, the handler:
   - Reads outgoing edges: `[A] Approve` and `[R] Request Changes`
   - Builds options: `[{key: "A", label: "[A] Approve"}, {key: "R", label: "[R] Request Changes"}]`
   - Emits `pipeline:interview:started` event
   - Presents question: "Code Review: Approve changes?" with options
   - Blocks until human responds
3. If human presses "A":
   - `suggested_next_ids=["deploy_staging"]`
   - `context["human.gate.selected"] = "[A] Approve"`
   - Edge selection uses suggested_next_ids to pick `review_gate -> deploy_staging`
4. At `prod_gate`, similar flow with 3 options (Y/N/F)

### Automated Mode (AutoApproveInterviewer)
1. At `review_gate`, auto-approve selects the first option: `[A] Approve`
2. At `prod_gate`, auto-approve selects the first option: `[Y] Yes, deploy to production`
3. Pipeline flows: implement -> test -> review_gate -> deploy_staging -> prod_gate -> deploy_prod -> done

### Rejection Loop
1. Human selects `[R] Request Changes` at review_gate
2. Pipeline routes to `fix_issues`
3. `fix_issues` loops back to `test`
4. After `test`, reaches `review_gate` again
5. Human can approve this time or request more changes

## Accelerator Key Extraction

| Edge Label | Extracted Key | Pattern |
|-----------|--------------|---------|
| `[A] Approve` | `A` | `[X] Label` bracket pattern |
| `[R] Request Changes` | `R` | `[X] Label` bracket pattern |
| `[Y] Yes, deploy to production` | `Y` | `[X] Label` bracket pattern |
| `[N] No, rollback staging` | `N` | `[X] Label` bracket pattern |
| `[F] Fix issues first` | `F` | `[X] Label` bracket pattern |

## Or run from a bundle / recipe

```yaml
# Interactive mode (will prompt in terminal):
steps:
  - agent: attractor:pipeline-runner
    instruction: "Run the human gate pipeline"
    context:
      pipeline_path: "examples/pipelines/08-human-gate.dot"
      interviewer: "console"

# Automated mode (auto-approves everything):
steps:
  - agent: attractor:pipeline-runner
    instruction: "Run the human gate pipeline in auto-approve mode"
    context:
      pipeline_path: "examples/pipelines/08-human-gate.dot"
      interviewer: "auto"
```

## What to Look For

- `pipeline:interview:started` event with the question text and node_id
- `pipeline:interview:completed` event with the selected answer
- Context after human gate: `human.gate.selected` contains the chosen label
- Edge selection log shows "suggested_next_ids" matching the human's choice
- In auto mode: always picks the first option (Approve, then Yes)
- If SKIPPED: handler returns FAIL per spec (human skipped interaction)
- If TIMEOUT: handler checks `human.default_choice` attribute, returns RETRY if none
