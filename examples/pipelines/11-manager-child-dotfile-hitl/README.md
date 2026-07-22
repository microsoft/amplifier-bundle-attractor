# 11 - Manager + Child Dotfile with a Human Gate

A **multi-file** example: a parent pipeline whose `manager` node (a `house`-shaped
supervisor) runs a **separate child pipeline** that contains a human-in-the-loop
(HITL) approval gate. It demonstrates that the interviewer driving a gate is wired
all the way through -- from the top-level run, through the manager, into the child
pipeline's gate.

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

## Run it

The child pipeline has a human gate, so a non-interactive run needs
`--on-human-gate auto-approve`. From the **attractor repo root**:

```bash
DOT="$PWD/examples/pipelines/11-manager-child-dotfile-hitl/parent.dot"
mkdir -p /tmp/attractor-demo && cd /tmp/attractor-demo
attractor run "$DOT" --cwd . --on-human-gate auto-approve
```

`auto-approve` always takes the gate's first option (`[A] Approve`), so the run
completes non-interactively; drop the flag and run interactively (type `A` at the
prompt) if you want the gate to actually branch. Point the absolute `$DOT` at
`parent.dot` -- the child resolves relative to it automatically. See
[../README.md](../README.md) for why the `$DOT` capture + `cd` + `--cwd .` are needed.

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
