# Bug-Fix Loop Postmortem

**Repo:** `/tmp/attractor-bugfix-budget-v2`
**Goal:** Fix the `TypeError` raised by `get_display_name()` when a user's `avatar` is `None`
**Budget:** 1 iteration
**Iterations completed:** 1
**Final test status:** 3/3 passing (all green)
**Converged?** No — budget exhausted after exactly one iteration, so the pipeline declared non-convergence even though the code is correct

---

## What the loop was trying to fix

`user_service.py` originally contained:

```python
return f"{user.username} [avatar: {user.avatar[:4]}]"
```

When `avatar=None`, `None[:4]` raises `TypeError: 'NoneType' object is not subscriptable`.
The shipped test suite only exercised the happy path (avatar present), so the suite was green
while the bug remained latent.

---

## What was attempted each iteration

### Iteration 1 (the only iteration)

**Action taken:** Applied the None-guard fix to `user_service.py`:

```python
# Before
return f"{user.username} [avatar: {user.avatar[:4]}]"

# After
avatar_tag = user.avatar[:4] if user.avatar is not None else "none"
return f"{user.username} [avatar: {avatar_tag}]"
```

A regression test (`test_display_name_without_avatar`) was also added to
`test_user_service.py` asserting that `get_display_name("carol")` returns
`"carol [avatar: none]"` when the user has no avatar.

**Test result after iteration 1:**

```
PASSED  test_user_service.py::test_add_and_get_user
PASSED  test_user_service.py::test_display_name_with_avatar
PASSED  test_user_service.py::test_display_name_without_avatar

3 passed in 0.00s
```

`reproduce_bug.py` also runs cleanly — no TypeError, output is
`carol [avatar: none]`.

---

## Convergence analysis: descending or oscillating?

**Neither — the loop was descending correctly and had already converged after
iteration 1.** There is no oscillation, no regression, and no remaining
failure. The non-convergence signal is a pipeline accounting artifact, not a
technical failure:

| Metric | Value |
|--------|-------|
| `.ai/budget` | `0` (exhausted) |
| `.ai/iter` | `1` |
| `.ai/feedback/` | Does not exist |
| `.ai/critique.md` | Does not exist |
| `.ai/test.log` | Does not exist |
| Failing tests | 0 |
| Passing tests | 3 |

The pipeline was configured with a budget of 1. It consumed that budget on
the first (and only) iteration. Because the budget counter hit zero before
a second evaluation pass could confirm the green state, the pipeline
terminated with "budget exhausted" rather than "converged."

---

## Best hypothesis for why the loop did not converge

**Root cause: budget = 1 is insufficient for a fix-then-verify loop.**

A typical fix loop needs at minimum:

1. **Iteration N:** apply fix, run tests → observe result
2. **Iteration N+1:** evaluate result, confirm green → declare convergence

With `budget = 1`, the pipeline can apply a fix but cannot perform the
confirmation pass. The convergence check requires at least one iteration
*after* the fix is applied and tests are green. A budget of 1 means the
loop always exhausts before that confirmation can happen, regardless of
whether the fix is correct.

Secondary observations:
- No feedback artifacts (`feedback/`, `critique.md`, `test.log`) were
  written, which suggests the pipeline's evaluation/critique step either
  did not run or ran but could not write its output before the budget was
  consumed.
- The `.pytest_cache/v/cache/lastfailed` file contains `{}` (empty object),
  confirming no test has failed in the most recent run.

---

## Current state of the code

The code is **correct and complete**. No further changes are needed.

`user_service.py` line 49 (the fix):
```python
avatar_tag = user.avatar[:4] if user.avatar is not None else "none"
```

`test_user_service.py` includes the regression test that covers the
previously-untested None-avatar path.

---

## Options

### Option 1: Re-run with a higher budget (recommended)

Increase the pipeline budget to at least 3 (apply → verify → confirm).
Because the fix is already in place and all tests are green, the very next
run should pass the evaluation step immediately and declare convergence on
iteration 1.

```bash
attractor run bug-fix.dot --param goal="..." --param budget=3 --cwd /tmp/attractor-bugfix-budget-v2
```

### Option 2: Accept the current state as done

The fix is technically correct. A human reviewer can inspect the diff,
run `pytest -v`, and close the task manually. The postmortem you are
reading now serves as the audit trail.

### Option 3: Split the task

If the pipeline is expected to handle more complex bugs where a single
iteration is genuinely insufficient, split the work:

- **Sub-task A:** reproduce and characterise the bug (write `reproduce_bug.py`,
  confirm the TypeError)
- **Sub-task B:** apply the fix and add the regression test
- **Sub-task C:** run the full suite and confirm green

Each sub-task fits within a budget of 1, and the pipeline graph wires them
in sequence.

### Option 4: Escalate to a human

Appropriate only if the fix were ambiguous or the tests were still failing.
That is not the case here. Human escalation is not warranted for this
specific bug.

---

## Recommendation

**Option 1 or Option 2.** The fix is correct. The loop failed to converge
solely because the budget was set to 1, which is structurally too small for
any fix-then-verify loop. No code changes are needed; only the pipeline
configuration needs adjustment.
