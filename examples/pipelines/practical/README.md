# Practical Pipelines

Task-oriented pipelines for everyday development work -- bug fixing, refactoring,
test generation, PR review, feature building, and multi-lens code review. Each
one is a real, runnable `attractor run` workflow, not a toy.

Each pipeline is a pair: a `.dot` (the graph) and a `.md` (the **walk-up guide** --
start there). The links below point at the guides.

| Pipeline | Guide | What it does | Target |
|----------|-------|--------------|--------|
| Bug Fix | [bug-fix.md](bug-fix.md) | Reproduce → diagnose → fix → regression test → verify | **ships a sample** |
| Refactoring | [refactor.md](refactor.md) | Analyze smells → plan → snapshot tests → refactor → verify no regressions | **ships a sample** |
| Test Generation | [test-gen.md](test-gen.md) | Analyze module → find coverage gaps → write tests → self-healing retry loop | **ships a sample** |
| PR Review | [pr-review.md](pr-review.md) | Analyze diff → parallel bug/security/perf/style review → prioritize → comment | bring-your-own branch |
| Feature Build | [feature-build.md](feature-build.md) | Parse spec → parallel implement → integration test → human review gate | bring-your-own repo |
| Multi-Lens Review | [multi-lens-review.md](multi-lens-review.md) | Same code reviewed by 3 providers wearing 3 lenses → synthesized verdict | self-contained |

## Run one walk-up

`bug-fix`, `refactor`, and `test-gen` ship a runnable target in [`sample/`](sample/)
-- a tiny `user_service.py` package with a planted bug, a code smell, and thin
tests -- so they work with no setup. From the **repo root**:

```bash
DOT="$PWD/examples/pipelines/practical/bug-fix.dot"
cp -r examples/pipelines/practical/sample /tmp/attractor-demo
cd /tmp/attractor-demo
attractor run "$DOT" \
    --param goal="Fix the TypeError in get_display_name when a user's avatar is None" \
    --cwd .
```

We copy the sample to a temp dir first so the committed fixture stays pristine.
`$DOT` is captured as an absolute path before `cd`, because the `.dot` path
resolves from your current directory while `--cwd` is where the pipeline reads
and writes -- and for box-node (agent) pipelines the two must match (see
[`../../../modules/pipeline-runner/KNOWN_ISSUES.md`](../../../modules/pipeline-runner/KNOWN_ISSUES.md)).

To point any of these at your own code instead, `cd` into your repo, keep `$DOT`
absolute, keep `--cwd .`, and swap in your own `goal`. Each guide has the details.

## Targets at a glance

- **Ships a sample** (`bug-fix`, `refactor`, `test-gen`) -- runs walk-up against [`sample/`](sample/).
- **Bring-your-own** (`pr-review`, `feature-build`) -- point at a real repo/branch; the guide states the requirements.
- **Self-contained** (`multi-lens-review`) -- reviews an embedded snippet; needs all three provider keys set (`attractor doctor`).

## Models

Every pipeline here is **model-agnostic** except `multi-lens-review`, which pins
one provider per lens on purpose. To route specific steps to specific models, add
a `model_stylesheet` (see [`../06-model-stylesheet.dot`](../06-model-stylesheet.dot)).
