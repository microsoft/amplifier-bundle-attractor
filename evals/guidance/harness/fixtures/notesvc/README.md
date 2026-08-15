# notesvc

A deliberately tiny service package, used as the workspace fixture for the guidance eval's
exemplar-robustness scenarios (`evals/guidance/scenarios/exemplar-*.yaml`).

It exists to give the objective layer something real to look at:

- `notesvc/users.py` carries a **real bug**: `user_slug()` strips every non-ASCII character, so a
  username made entirely of emoji collapses to an empty slug and `save_path()` raises.
- `tests/test_users.py` carries **two failing tests naming that bug**, and one passing test that
  pins the behavior a fix must not break.

`pytest` is therefore RED on a fresh checkout, and that redness is the machine evidence scenario
(e) expects the intake to find underneath a sloppily-phrased objective. Scenario (f) uses the same
fixture with an objective that has no machine check available at all — same workspace, opposite
correct answer.

Do not "fix" this fixture. Its red tests are the instrument.
