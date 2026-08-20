## Summary
<!-- One-paragraph description of what this PR changes and why. -->

## Verification checklist

- [ ] Unit tests pass (`pytest modules/loop-pipeline/`)
- [ ] Live pipeline run exercising changed code path — required when touching `engine.py` or any handler; paste `events.jsonl` analysis or test-run output in the section below
- [ ] AGENTS.md reviewed; repo-specific gates met
- [ ] Backward-compat path unchanged (if applicable)
- [ ] If this PR changes an **observable contract** — dispatch semantics, event contracts, admission/validation behavior, or anything else a pipeline author or downstream consumer could observe — it includes a `specs/EXTENSIONS.md` entry (new or updated), **or** this box is checked with an explicit one-line reason the change doesn't need one (e.g. "internal refactor, no observable change"). This applies identically to maintainer and external contributions.
- [ ] If this PR adds or changes a **doc claim about code behavior** (a number, default, vocabulary, or contract), it ships a guard test pinning that claim to its source of truth in code (see `docs/QUALITY_PROTOCOL.md`), **or** this box is checked with a one-line reason none is needed.
- [ ] **Pre-publication leak review** — the deterministic leak guards are green **and**, for any **new public content class** this PR introduces (a new top-level directory, a new artifact type reaching users, docs carrying real-run evidence, a new fixture corpus), a fresh-context reviewer read the diff under the outsider brief in `docs/QUALITY_PROTOCOL.md` and reported what it identifies, **or** this box is checked with a one-line reason it is N/A (e.g. "no new public content class in this diff").
- [ ] PR body includes verification evidence, not just "tests pass"
- [ ] **CI is green before merge — on every path** (auto-merge, manual/UI merge, or agentic/CLI merge). Confirm with `gh pr checks <n>` and look for `CI Gate (all checks passed)` reporting `pass`. `--admin` may bypass the code-owner review requirement only (see `AGENTS.md`) — it must never bypass a red or pending `CI Gate`.

## Verification evidence
<!-- Paste the relevant slice of events.jsonl, test-run output, or other proof here. For engine/handler changes, include enough of the event stream to demonstrate the changed path actually fired. -->

## Notes for reviewers
<!-- Anything reviewers should know — caveats, follow-ups, breaking changes, spec implications, etc. -->

---
See: [Per-Repo Conventions](https://github.com/microsoft/amplifier-foundation/blob/main/docs/PER_REPO_CONVENTIONS.md) and this repo's `AGENTS.md` for the verification discipline this checklist enforces.
