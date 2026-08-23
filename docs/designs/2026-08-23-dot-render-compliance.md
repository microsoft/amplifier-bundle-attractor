# DOT Render Compliance — two validators, one file format

**Date:** 2026-08-23
**Status:** Implemented
**Scope:** `RENDER-001` / `RENDER-002` lint rules, six corpus fixes, CI render gate.

---

## The problem

A `.dot` pipeline file is read by **two** validators with different strictness:

| Validator | Who runs it | Strictness | Consequence of failure |
|---|---|---|---|
| `amplifier_module_loop_pipeline.dot_parser` (our engine) | every `attractor run` / `attractor lint` | **lenient** — a hand-written tokenizer over the strict DOT subset in spec §2 | pipeline will not run |
| `dot -Tsvg` (GraphViz) | any human who wants to *see* the graph | **strict** — the real DOT grammar | no picture |

The two do not agree. Measured on this repository at `696c080`: **6 of 63**
git-tracked `.dot` files parse and lint cleanly but fail `dot -Tsvg` with a
syntax error. They run fine. They just cannot be drawn.

That gap is invisible today. Nothing in the repo ever asked GraphViz whether a
shipped file renders, so the six drifted in silently over months.

### The two divergence classes

Both are static, both are detectable from *our own tokenizer* with no GraphViz
dependency, and both are fixable by quoting with **zero** change in engine
semantics.

**Class A — unescaped inner quote.** A raw `"` inside a `"…"` attribute string
closes the string early. GraphViz then sees a bare identifier where it expects
`,` or `]`.

```dot
// WRONG — the ' before gate closes nothing; the " before gate CLOSES the string
tool_command="n=$(grep -c '"gate": "verify"' .ai/log.jsonl); ..."

// CORRECT — the inner quotes are escaped, so the string ends where intended
tool_command="n=$(grep -c '\"gate\": \"verify\"' .ai/log.jsonl); ..."
```

Our tokenizer survives this because it never re-checks that the token stream
after a string makes grammatical sense; GraphViz does. Repo instance:
`examples/patterns/task-runner.dot:207`.

**Class B — dotted bare attribute key.** GraphViz's `NAME` production is
`[A-Za-z_\200-\377][A-Za-z_\200-\3770-9]*` — **no dot**. A dotted key is only
legal *quoted*. Our tokenizer's ident pattern explicitly allows the qualified
form `ident(.ident)*`, so `manager.max_cycles=5` parses for us and is a syntax
error for GraphViz.

```dot
// WRONG — bare dotted key; GraphViz: "syntax error ... near '.'"
manager [shape=house, manager.max_cycles=5]

// CORRECT — quoting the key is legal DOT and changes nothing for the engine
manager [shape=house, "manager.max_cycles"=5]
```

Repo instances: `examples/patterns/demo-combined.dot:15`,
`demo-convergence-factory.dot:15`, `demo-conversational-gates.dot:15`,
`examples/pipelines/09-manager-supervisor.dot:40`,
`examples/pipelines/11-manager-child-dotfile-hitl/parent.dot:55`.

### Quoting is semantically inert (verified, not assumed)

`dot_parser._unquote_key()` strips surrounding quotes from a key token and does
**not** coerce types — so the key string is identical. `_parse_value()` is not
reached for keys at all. Measured on the real shapes, bare vs. quoted:

```
bare  : {'manager.max_cycles': (5, 'int'), 'manager.actions': ('observe,steer', 'str'),
         'stack.child_dotfile': ('c.dot', 'str'), 'context.gate_topic': ('hi', 'str')}
quoted: {'manager.max_cycles': (5, 'int'), 'manager.actions': ('observe,steer', 'str'),
         'stack.child_dotfile': ('c.dot', 'str'), 'context.gate_topic': ('hi', 'str')}
IDENTICAL: True
```

Note in particular that `manager.max_cycles` stays the **int** `5` in both
forms — the value is unaffected by whether the *key* was quoted. Any lookup
code doing `node.attrs["manager.max_cycles"]` is untouched.

---

## Decision matrix: toward-spec or drift?

The compat doctrine asks one question of any proposed tightening: does it move
toward the spec's own intent, or does it break conforming users?

**Requiring OUR OWN shipped files to render is toward-spec.** The spec chose
DOT *because* it renders — "free visualization, PR-reviewable" is the stated
reason the format was picked over YAML or JSON. A shipped `.dot` that cannot be
drawn has silently stopped paying the rent that justified the format choice.
Holding our own corpus to that is enforcing the spec's own premise.

**Forcing a hard ERROR on everyone would be drift.** The engine's runtime
contract is "this parser accepts this graph." A community graph with a dotted
bare key is *conforming to that contract* today and runs correctly today.
Promoting non-rendering to an ERROR would break a conforming user's working
pipeline for a reason that has nothing to do with whether their pipeline works.
Renderability is desirable, not load-bearing for execution.

Those two conclusions point in different directions, so the design splits into
tiers by audience.

---

## The design — three tiers

### Tier 1 — runtime contract: UNCHANGED

The engine parser keeps accepting these graphs exactly as before. **A
non-rendering graph still runs.** No render check is added to the run/parse
path, `validate()` is untouched, and `validate_or_raise()` (the admission gate)
gains no new failure mode. Nobody's working pipeline stops working.

This is the whole reason the rules live in `lint()` and not `validate()`.

### Tier 2 — lint advisory, for EVERYONE

Two new rules in `validation.py`, reached only through `lint()` (the
`attractor lint` entry point):

| Rule | Detects | Severity |
|---|---|---|
| `RENDER-001` | unescaped inner quote — a lexer STRING token abutting a non-separator | **WARNING** |
| `RENDER-002` | dotted bare identifier — matches neither GraphViz `NAME` nor `NUMERAL` | **WARNING** |

**WARNING, never ERROR.** A new WARNING is additive and non-breaking per the
compat doctrine: `attractor lint` still exits 0, CI for community users still
passes, and the finding is advice. This matches the existing
TOPO-002…010 / CMD-001…002 / VOCAB-001 precedent — every advisory rule in the
family is a WARNING, and only TOPO-001 (a *provably dead* edge) is an ERROR.

**Pure-lexer, no GraphViz dependency.** Both rules run over `graph.dot_source`
using `dot_parser._TOKEN_RE` — the engine's own tokenizer regex — so the check
works on a machine with no `dot` binary, adds no import, and cannot drift from
the tokenizer it is reasoning about. Comments are blanked (replaced with
spaces, preserving byte offsets) before scanning, so a dotted key *mentioned in
a comment* is never flagged and reported line numbers stay exact.

Both rules stay silent when `graph.dot_source` is empty — a
programmatically-constructed `Graph` has no source text to check, and inventing
a finding there would be a lie.

Each finding carries a `fix=` hint naming the exact edit.

### Tier 3 — CI render gate, OUR repo only

A blocking `dot -Tsvg` sweep over every git-tracked `*.dot`, in this
repository's CI only. This is where "our files must render" is *enforced*,
because this is the only place where the audience is us.

It never touches community authors: it is a workflow job in this repo, not a
rule in the shipped engine.

Exclusions: `.ai/`, `.amplifier/`, and evaluation snapshots — those are runtime
scratch and captured artifacts, not authored corpus.

The in-repo pytest twin (`test_dot_render_compliance.py::test_shipped_dot_renders`)
**skips** with an explicit reason when `dot` is not on PATH, so a developer
without GraphViz sees "skipped: graphviz `dot` not on PATH" rather than a
false green. CI installs graphviz, so in CI the test always actually runs —
the skip is a local-developer affordance, never the CI behaviour.

---

## Why not just make the engine tokenizer strict?

Rejected. Narrowing the ident pattern to forbid `.` would turn every
`context.*` / `manager.*` / `stack.*` graph in the wild into a hard parse
error — the exact drift the tier split exists to avoid. The qualified-ident
extension is load-bearing for the folder-node context-injection mechanism and
the manager-loop attributes; it is a deliberate divergence from GraphViz's
grammar, not an accident.

The right move is to keep accepting the lenient form and *tell the author* that
quoting it costs nothing and buys a picture.

## Why not shell out to `dot` from the lint rule?

Rejected. It would make `attractor lint` — a sub-second, hermetic, no-subprocess
static check — depend on an external binary that most users do not have. Both
divergence classes are statically decidable from tokens we already produce, so
the dependency buys nothing.

---

## Ledger call

**No `specs/EXTENSIONS.md` entry.** The engine contract is unchanged: no new
attribute, no new parse behaviour, no new runtime semantics, nothing that
changes what a conforming graph does. This follows the precedent set for the
whole advisory-lint family (§ "lint — five topological basin-lint rules + CLI":
lint-only rules that do not change run-time behaviour need no ledger entry).

The doc home is `docs/DOT-AUTHORING-GUIDE.md` § "Static Lint Rules", alongside
TOPO / CMD / VOCAB, with WRONG/CORRECT examples for each rule.

---

## Verification

- All 63 git-tracked `.dot` files render (`dot -Tsvg` exit 0) after the fix; 6
  failed before.
- Engine parse is byte-identical before/after for the changed files —
  asserted in `test_dot_render_compliance.py`, not just spot-checked by hand.
- `RED-proof`: each rule was run against the pre-fix shapes and fired; against
  the corrected shapes it is silent.
