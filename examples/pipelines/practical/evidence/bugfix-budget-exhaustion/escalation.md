# Escalation Brief

**Source postmortem:** `.ai/postmortem/report.md`
**Repo:** `/tmp/attractor-bugfix-budget-v2`
**Date:** 2026-07-30

---

## Recommended Owner

**Pipeline / DevOps owner** — the person or team responsible for configuring and maintaining the Attractor pipeline (budget parameters, graph topology, convergence criteria). This is not a code-owner issue; the application fix is already correct and complete.

---

## Decision Needed

**Change approach: increase the pipeline budget from 1 to at least 3.**

The non-convergence is a pipeline configuration defect, not a code defect. The current budget of 1 makes it structurally impossible for any fix-then-verify loop to declare convergence, regardless of whether the fix is correct. The owner must choose one of the following:

| Option | Action | When to choose |
|--------|--------|----------------|
| **A (recommended)** | Raise `budget` to ≥ 3 and re-run the pipeline | Fastest path; fix is already in place, next run should converge on iteration 1 |
| **B** | Accept current state as done; close manually after human review | Appropriate if a re-run is not worth the overhead for this specific task |
| **C** | Split the task into three sequential sub-tasks (reproduce → fix → verify), each with `budget = 1` | Appropriate if the pipeline must keep a per-node budget cap of 1 |

Human escalation (Option D in the postmortem) is **not warranted** — the code is correct and no ambiguity remains.

---

## Evidence to Act On

### 1. The fix is already correct and all tests are green

```
PASSED  test_user_service.py::test_add_and_get_user
PASSED  test_user_service.py::test_display_name_with_avatar
PASSED  test_user_service.py::test_display_name_without_avatar

3 passed in 0.00s
```

Verify independently:
```bash
cd /tmp/attractor-bugfix-budget-v2 && pytest -v
```

### 2. The applied fix (user_service.py)

```python
# Before (raises TypeError when avatar is None)
return f"{user.username} [avatar: {user.avatar[:4]}]"

# After (correct)
avatar_tag = user.avatar[:4] if user.avatar is not None else "none"
return f"{user.username} [avatar: {avatar_tag}]"
```

A regression test (`test_display_name_without_avatar`) was added to `test_user_service.py` covering the previously-untested `avatar=None` path.

### 3. Budget exhaustion is the sole cause of non-convergence

| Artifact | State | Meaning |
|----------|-------|---------|
| `.ai/budget` | `0` | Budget fully consumed |
| `.ai/iter` | `1` | Only one iteration ran |
| `.ai/feedback/` | Does not exist | Evaluation/critique step never wrote output |
| `.ai/critique.md` | Does not exist | Same |
| `.ai/test.log` | Does not exist | Same |
| `.pytest_cache/v/cache/lastfailed` | `{}` (empty) | No test has ever failed in the most recent run |

The pipeline requires at minimum **two passes** to converge: one to apply the fix and one to confirm the green state. A budget of 1 allows only the first pass, so the loop always terminates with "budget exhausted" before the confirmation pass can run.

### 4. Recommended re-run command (Option A)

```bash
attractor run bug-fix.dot \
  --param goal="Fix TypeError in get_display_name() when avatar is None" \
  --param budget=3 \
  --cwd /tmp/attractor-bugfix-budget-v2
```

Because the fix is already applied, the pipeline should evaluate the existing green state on iteration 1 and declare convergence without needing to modify any code.

---

## No Further Code Changes Required

The application code is correct. This escalation is purely a pipeline configuration matter.
