## Summary
<!-- One-paragraph description of what this PR changes and why. -->

## Verification checklist

- [ ] Unit tests pass (`pytest modules/loop-pipeline/`)
- [ ] Live pipeline run exercising changed code path — required when touching `engine.py` or any handler; paste `events.jsonl` analysis or test-run output in the section below
- [ ] AGENTS.md reviewed; repo-specific gates met
- [ ] Backward-compat path unchanged (if applicable)
- [ ] PR body includes verification evidence, not just "tests pass"

## Verification evidence
<!-- Paste the relevant slice of events.jsonl, test-run output, or other proof here. For engine/handler changes, include enough of the event stream to demonstrate the changed path actually fired. -->

## Notes for reviewers
<!-- Anything reviewers should know — caveats, follow-ups, breaking changes, spec implications, etc. -->

---
See: [Per-Repo Conventions](https://github.com/microsoft/amplifier-foundation/blob/main/docs/PER_REPO_CONVENTIONS.md) and this repo's `AGENTS.md` for the verification discipline this checklist enforces.
