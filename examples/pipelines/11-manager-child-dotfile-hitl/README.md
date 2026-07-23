# 11 - Manager + Child Dotfile with a Human Gate

A **multi-file** example -- and a **regression fixture**: a parent pipeline whose
`manager` node (a `house`-shaped supervisor) runs a **separate child pipeline**
that contains a human-in-the-loop (HITL) approval gate. Its purpose is to prove the
interviewer driving a gate is wired all the way through -- from the top-level run,
through the manager, into the child pipeline's gate.

> **Known issue -- does not run walk-up today.** Driven via standalone
> `attractor run ... --on-human-gate auto-approve`, this pipeline **fails**: the
> child pipeline's `HumanGateHandler` does not receive an interviewer through the
> manager, so the child fails immediately (`Manager exhausted 1 cycle(s)`, last
> child status: fail). That is the exact failure mode this fixture exists to catch.
> It is normally driven by a test harness that wires the interviewer end-to-end.
> Root-causing the standalone-CLI path (real regression vs CLI-only gap) is tracked
> as a follow-up -- unlike the other numbered examples, this one is **not** a
> proven walk-up demo.

Unlike the single-file numbered tutorials, this one is a directory of two `.dot`s:

| File | Role |
|------|------|
| [`parent.dot`](parent.dot) | `start -> manager (shape=house, stack.child_dotfile="child-with-gate.dot") -> done` |
| [`child-with-gate.dot`](child-with-gate.dot) | `start -> hitl_gate (shape=hexagon) -> approved / rejected -> done` |

The manager references the child by filename; `stack.child_dotfile` resolves
relative to `parent.dot`'s own directory, so the pair travels together.

## What This Exercises

- **`shape=house` manager node** supervising a child pipeline via
  `stack.child_dotfile` (rather than inlining every stage in one graph).
- **`manager.max_cycles=1` / `manager.actions=observe`** -- one deterministic pass,
  no steering, which is enough to prove the wiring.
- **HITL gate in the child** (`shape=hexagon`, `type="wait.human"`): edge labels
  become the choices -- `[A] Approve` -> `approved`, `[R] Reject` -> `rejected`.
- **Interviewer threading**: the interviewer must reach the child gate
  (HandlerRegistry -> ManagerLoopHandler -> child HandlerContext -> HumanGateHandler).
  This example is the regression fixture for that path.

## Running it (currently fails -- see Known issue above)

The intended command, once the standalone path is fixed, is -- from the **attractor
repo root**:

```bash
DOT="$PWD/examples/pipelines/11-manager-child-dotfile-hitl/parent.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
attractor run "$DOT" --cwd . --on-human-gate auto-approve
```

**Today this exits `status=fail`** -- the manager's child pipeline fails instantly
because the gate never receives the interviewer (see Known issue above). The child
resolves relative to `parent.dot` automatically; `--on-human-gate auto-approve`
would take the gate's first option (`[A] Approve`). See [../README.md](../README.md)
for why the `$DOT` capture + `cd` + `--cwd .` are needed.

## Or run from a bundle / recipe

```yaml
steps:
  - agent: attractor:pipeline-runner
    instruction: "Run the manager + child-dotfile HITL example"
    context:
      pipeline_path: "examples/pipelines/11-manager-child-dotfile-hitl/parent.dot"
```

## DOT parser note

Attribute keys containing dots (`manager.max_cycles`, `stack.child_dotfile`) are
written **without** surrounding double-quotes -- the attractor DOT parser stores a
quoted key with its quote characters, which breaks the bare-string lookups the
handlers use. Correct: `manager.max_cycles=1`. Wrong: `"manager.max_cycles"="1"`.
