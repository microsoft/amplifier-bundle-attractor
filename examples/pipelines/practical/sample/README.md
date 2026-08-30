# Sample target for the practical pipelines

A tiny, self-contained Python package that the **bug-fix**, **refactor**, and
**test-gen** examples run against, so those pipelines work walk-up with no
"bring your own repo" setup.

It ships with three planted problems -- one for each pipeline:

| Pipeline | Planted problem | Where |
|----------|-----------------|-------|
| `bug-fix` | `get_display_name()` raises `TypeError` when a user's `avatar` is `None` | `user_service.py` |
| `refactor` | `validate_user()` is a long, deeply-nested method with duplicated blocks | `user_service.py` |
| `test-gen` | Only the happy path is tested; the None-avatar, missing-user, and validation paths are uncovered | `test_user_service.py` |

The shipped suite is **green** (`pytest` passes) -- the bug is latent because no
existing test exercises the None-avatar path.

## Run the tests directly

```bash
cd examples/pipelines/practical/sample
pytest -v
```

## Don't mutate the committed fixture

The example commands copy this directory to a temp location before running a
pipeline, so the committed sample stays pristine and each run starts clean:

```bash
cp -r examples/pipelines/practical/sample /tmp/attractor-demo
dot-runner run examples/pipelines/practical/bug-fix.dot \
    --worker coding-agent \
    --param goal="Fix the TypeError in get_display_name when avatar is None" \
    --cwd /tmp/attractor-demo
```

Run the command from the **attractor repo root** so the `.dot` path resolves
(it's relative to your current directory) while `--cwd` points the pipeline at
the temp copy (that's where it reads and writes). See
`modules/pipeline-runner/KNOWN_ISSUES.md` for the box-node cwd caveat.
