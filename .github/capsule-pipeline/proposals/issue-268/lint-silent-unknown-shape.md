---
id: lint-silent-unknown-shape
title: "Lint is silent on unknown node shapes"
red_signal: lint() produced no ERROR for node with unknown shape and no explicit type
base_sha: da8ffd1faa87128573bd9872e12aa4f4f7747f0b
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

`lint()` in `modules/loop-pipeline/amplifier_module_loop_pipeline/validation.py`
must produce an ERROR-severity diagnostic when a node has no explicit `type` attribute and its
`shape` is not in `SHAPE_TO_HANDLER`. The diagnostic must be associated with the offending node.
This applies regardless of whether the node carries a `tool_command` attribute.

At the base SHA, a node with a typo'd shape (e.g. `shape=parallelgram` instead of
`shape=parallelogram`) and no explicit `type` passes `lint()` with zero ERROR diagnostics.
Instead, the silent fallback in `_check_prompt_on_llm_nodes` promotes the node to the `codergen`
handler class and emits a misleading WARNING telling the author to add a `prompt` to a node they
intended as a shell-command node. The runtime dispatch layer (`HandlerRegistry.get()` in
`handlers/__init__.py`) raises a `ValueError` for the same node -- loud-late rather than
loud-early.

## Why this matters

The dispatch raise exists to enforce the "fail loud; never fall back silently" principle. Holding
that line only at the dispatch layer means a pipeline author can write a graph with a typo'd
shape, pass `attractor lint`, and only discover the error mid-run when the engine refuses to
execute the node. `lint()` is documented as the gate an author can put in front of a run; it
must surface shape errors before any node executes.

## Definition of done

The following conditions must all hold. `DEFINITION.verify.sh` checks them mechanically.

1. **`lint()` produces at least one ERROR-severity diagnostic for an unknown-shape node that
   carries `tool_command`.** A graph node whose `shape` is not in `SHAPE_TO_HANDLER`, whose
   `type` attribute is empty, and whose `attrs` include `tool_command` must produce an
   ERROR associated with that node (via `node_id` field or message text).

2. **`lint()` produces at least one ERROR-severity diagnostic for an unknown-shape node that
   has NO `tool_command` and NO prompt.** A graph node whose `shape` is not in `SHAPE_TO_HANDLER`
   and whose `type` attribute is empty must produce an ERROR even when no `tool_command` attribute
   is present. This rules out a fix that only diagnoses unknown-shape nodes that carry
   `tool_command` -- the reported behavior is general: any node with an unrecognized shape and
   no explicit `type` must produce an ERROR.

3. **`lint()` produces at least one ERROR-severity diagnostic for an unknown-shape node that
   has a non-empty prompt and NO `tool_command`.** A graph node whose `shape` is not in
   `SHAPE_TO_HANDLER`, whose `type` attribute is empty, and which carries a non-empty `prompt`
   value must produce an ERROR. This rules out a fix conditioned on the absence of a prompt or
   label -- the issue's stated condition is `unknown shape + no explicit type => ERROR`,
   unconditionally, regardless of whether the node has a prompt attribute.

4. **The rule fires per-node, not per-graph.** A graph containing both unknown-shape nodes
   and known-shape nodes must produce the ERROR only for the unknown-shape nodes. Known-shape
   nodes in the same graph must not be flagged for having an unknown shape.

5. **Nodes with an explicit `type=` attribute are not flagged by this rule.** The existing
   type-checking rule already handles unknown `type` values. The new rule targets only the
   shape-based resolution path: `node.type` is empty AND `node.shape` is not in
   `SHAPE_TO_HANDLER`.

6. **All existing tests continue to pass** (`uv run pytest tests/test_validation.py -q` from
   `modules/loop-pipeline/`). A correct fix includes at least one real regression test in the
   repo's own test suite exercising the reported behavior through the public `lint()` or
   `validate()` API.

## Non-goals

- Requiring a specific lint rule name or message format. Any correct fix that emits an
  ERROR-severity diagnostic associated with the unknown-shape node satisfies this definition.
- Requiring `validate_or_raise()` to raise for unknown-shape graphs. The reported surface is
  `lint()` (the `attractor lint` command); a lint-only rule appended to `lint()` repairs
  exactly the reported author workflow. Whether `validate_or_raise()` also rejects such graphs
  is an implementation choice beyond the reported requirement.
- Verifying that the misleading `prompt_on_llm_nodes` WARNING disappears after the fix. That is
  a downstream symptom; the root cause is the missing ERROR.
- Changing the runtime dispatch raise in `handlers/__init__.py`. That raise is correct and must
  remain.
- Changing `graph.py`, `dot_parser.py`, or any other module outside `validation.py`.
- Introducing new dependencies.
- Handling nodes that have both a bad `shape` and a bad `type`. The existing type-checking rule
  already warns on unknown `type` values; the new rule must not double-diagnose a node that has
  an explicit (even unknown) `type`.
