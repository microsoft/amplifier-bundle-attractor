# Multi-Provider Multi-Lens Code Review

A "review panel" pipeline: the **same code** is reviewed in parallel by **three
different providers**, each wearing a **different review lens**, then one node
synthesizes their findings into a ranked verdict.

The idea: model providers have different strengths, so instead of asking one
model to review everything, you convene a panel where each seat is a different
model **and** a different perspective — and the cross-provider agreement (or
disagreement) is itself signal.

## Usage

```bash
dot-runner run examples/pipelines/practical/multi-lens-review.dot --worker loop-agent --cwd .
```

Self-contained, so no `--param goal=...` is needed. Run from the repo root so box-node agents root their writes at `--cwd .` (see `modules/pipeline-runner/KNOWN_ISSUES.md`). Needs all three provider keys set (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`) — check with `dot-runner doctor`.

Or via the interactive agent:
> "Run the multi-lens review pipeline"

The pipeline is self-contained — it reviews a small flawed snippet embedded in
each branch, so it runs with no repo or file dependencies. To review real code,
replace the embedded snippet with a `prepare` step that loads a diff (see
`pr-review.dot`) and have each lens review the shared context.

## What It Does

1. **Fan out** (`review_fanout`, `shape=component`, `wait_all` / `continue`) the
   same code to three branches:
   - **Security lens** on **anthropic** — injection, secrets, auth, data exposure
   - **Architecture lens** on **openai** — separation of concerns, coupling, SRP
   - **Performance/correctness lens** on **gemini** — N+1, O(n²), logic bugs
2. **Fan in** (`reviews_join`, `shape=tripleoctagon`) — collect the three reports
3. **Synthesize** (`synthesize`, `goal_gate=true`) — one ranked verdict
   (MUST-FIX → SHOULD-FIX → CONSIDER), explicitly noting where the providers
   **agree** vs **conflict**

## Per-Node Provider Routing

Each lens branch pins its own `llm_provider`, so each lens genuinely runs on a
different model. This relies on loop-agent honoring a node's `llm_provider` for
**both** the completion model **and** the matching provider base prompt — so a
node pinned to `openai` runs the OpenAI model with the OpenAI base prompt, even
when the bundle mounts several providers.

The provider↔lens mapping is **illustrative and meant to be tuned**. Swap which
provider wears which lens to match your own read of each model's strengths.

## Expected Behavior

- Wall-clock: roughly one review's time (the three lenses run in parallel)
- Output: a ranked, deduplicated verdict that attributes findings to lenses and
  flags cross-provider agreement/conflict
- `goal_gate` on `synthesize` ensures the pipeline won't exit without a verdict
