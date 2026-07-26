# Historical implementation plans — NOT current reference

Every file in this directory is a **point-in-time plan or design record**. Plans were often
superseded, partially implemented, or implemented differently than written. They are kept for
provenance, not guidance.

**Do not treat anything here as a description of how the system currently works.**

Several of these documents describe interfaces and vocabulary that no longer exist — for
example, shape tables listing shapes the engine does not support. Reading them as current
reference is a known way to generate invalid pipelines.

Current, authoritative sources:

| For | Read |
|---|---|
| Node shapes and handlers | `SHAPE_TO_HANDLER` in `modules/loop-pipeline/amplifier_module_loop_pipeline/validation.py` (pinned to upstream by test) |
| Agent-facing reference card | `context/dot-reference.md` (pinned to `SHAPE_TO_HANDLER` by test) |
| Authoring guidance | `docs/DOT-AUTHORING-GUIDE.md`, `docs/DOT-SYNTAX.md` |
| Engine semantics and deltas | `context/engine-semantics.md`, `specs/EXTENSIONS.md` |
| Upstream contract | `specs/canonical/` |
