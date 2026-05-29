# Recurring Bug Classes in loop-pipeline

> **TL;DR**: This codebase has historically been bitten by five recurring
> bug classes. Each is named "Species N" below. The diagnosis is short:
> *invariants live in our verbs (actions repeated at N sites) when they
> should live in our nouns (data structures the type system enforces)*.
> Plus a compounding factor: omissions degrade silently and surface
> displaced from the cause.

## How to use this doc

- **At design time**: Before writing a new handler, parser path, shared
  resource, or symmetric-handling function, read the matching species
  below. If your design has the recognition signature, apply the
  noun-fix.
- **At review time**: See `CODE-REVIEW-CHECKLIST.md` (the Socratic twin).
  It maps the five questions a reviewer should ask back to these species.
- **As a contributor onboarding**: This is the most concentrated
  explanation of "why this code looks like this." Read it once.

## The one-line diagnosis

The system has historically encoded its invariants in *verbs* — "remember
to wire X at every construction site," "remember to thread Y at every
boundary," "remember to strip Z at every parse point." Every recurring
incident has the same shape: one site forgot the verb. The fix that
sticks is to relocate the invariant into a *noun* — a typed structure,
a required parameter, a sole-caller helper, an identity object — so the
language refuses to construct the invalid state in the first place. The
compounding factor is silent degradation: a missed verb does not raise;
it produces wrong-but-plausible output that surfaces somewhere else.

Five species capture roughly 90% of fix commits across the resolve
ecosystem over the past year, and 100% of fix commits in
`amplifier-bundle-attractor` itself.

## The five species

### S1 — Incomplete assembly

- **Invariant violated**: a structure can be constructed in a state where
  required dependencies are absent; failures emerge at runtime, far from
  construction.
- **Recognition signature** (what to look for):
  - constructor uses `**kwargs` or many optional `None` defaults
  - "remember to wire X" pattern across N construction sites
  - a new dep requires touching every existing call site to add a kwarg
  - the type system says nothing about completeness; only runtime does
- **Canonical incident**: #249 (`subgraph_runner` missing at one of four
  `HandlerRegistry` construction sites). Across 12 months, the
  `HandlerRegistry` propagation pattern re-opened the same wound five
  times for three different parameters (`backend`, then `interviewer`,
  then `subgraph_runner`). Surface-level fixed in #36 (eliminated the
  kwarg + closure dance) and further closed by **T2.1 HandlerContext**
  (Wave 1 — `HandlerRegistry` now requires a typed context object).
- **Noun-fix**: required fields on a frozen dataclass. Pyright at
  write-time and `__init__` at runtime cannot let you construct a
  half-wired thing. Every dep is a *required field*, not a kwarg.
- **Anti-fix**: adding the new dep as another optional kwarg →
  re-opens the wound at every site, and the next contributor adding a
  sixth dep will rediscover the same lesson.
- **What this design CLOSES** (per critic, stated honestly): the noun
  makes the species **compile-time loud, not eliminated**. Adding a
  sixth required field still requires a synchronized N-site edit — but
  pyright catches every absent dep at write time, before tests run.
  Recurrence is **relocated to compile-time, not removed**.

### S2 — Lossy reconstruction

- **Invariant violated**: a value that exists upstream is rebuilt from
  scratch at a boundary; part of the value is silently dropped on the
  way through.
- **Recognition signature**:
  - boundary site constructs a `new Outcome(...)` / `new Result(...)`
    instead of threading the existing one
  - the inner `failure_reason` / metadata is "implicit" in the new
    construction — i.e., not passed
  - assertions that pass at the source-site fail at the boundary-site
    with a generic message
  - the surfaced message is structurally correct but semantically empty
    ("No matching edge from node 'X'" instead of the real cause)
- **Canonical incident**: #251 (`failure_reason` masked by "No matching
  edge from node 'X'" — the handler's real cause was thrown away when
  routing built its own outcome). Fixed in #34, then further closed by
  **T2.3 `terminate_pipeline()`** (Wave 1 — sole-caller helper threads
  `failure_reason` automatically).
- **Noun-fix**: thread through, don't reconstruct. A single helper that
  centralizes the boundary construction is the noun; the helper takes
  the upstream value and the boundary metadata, never reinvents.
- **Anti-fix**: copy the threading logic to each of N boundary sites by
  hand. The next contributor adding a new boundary forgets again.
- **What this design CLOSES**: **mechanism centralized, enforced by
  sole-caller convention**. An AST guard catches reintroduction at
  review time, not at write time. Closer to a noun than a verb, but
  still relies on the AST guard staying in place. Not unconstructable.

### S3 — Unscoped shared state

- **Invariant violated**: a resource is shared by location, not by
  identity. Two different runs / graphs / contexts collide silently in
  the same location.
- **Recognition signature**:
  - filesystem path or in-memory dict keyed only by location
    (`logs_root`, archive path, in-memory pool)
  - tests that "work in isolation but not together"
  - "this file doesn't belong to this run" surprises
  - reads that succeed against stale data from a prior run
- **Canonical incident**: #252 (`logs_root` checkpoint pollution across
  pipelines — one pipeline's checkpoint silently restored into a
  different pipeline). Spot-fixed in #35 (graph-fingerprint check at
  read time); further closed by **T2.4 RunIdentity** (Wave 2 — typed
  identity required at read time, with hard-fail on mismatch).
- **Noun-fix**: identity is required to access the resource. The
  `RunIdentity` object is the key; without it, there is no read API.
  Mismatch on restore is a hard failure (per refinement C1), never a
  silent restart — side-effecting nodes cannot tolerate a silent retry.
- **Anti-fix**: add the identity check after-the-fact at each consumer.
  The next consumer added forgets the check.
- **What this design CLOSES**: **fully — `RunIdentity`-as-access-key is
  unconstructable**. You cannot form a read without the right key.
  Adding a new `logs_root`-keyed resource later is the only re-entry,
  and the S3 questions in the review checklist catch it at PR time.

### S4 — Partial-coverage symmetry

- **Invariant violated**: a normalization step applies to one branch of
  a structural symmetry but not its sibling. The uncovered branch fails
  silently with wrong-but-valid output (the data still parses; it just
  means something different).
- **Recognition signature**:
  - parser / translator handles `X` but not the structurally-equivalent
    `Y` (keys vs values, host vs DTU, Docker vs Incus, quoted vs
    unquoted)
  - bugs that take the form "also handle Z"
  - a normalizer at one site, and a fresh-eyes contributor adds a
    second normalizer at the next site instead of moving the existing
    one upstream
- **Canonical incident**: #253 (DOT parser strips quotes from attribute
  *values* but not from attribute *keys* — same lexical event, two code
  paths, only one normalizes). Fixed in #33. The dev-mode `/workspace`
  path translation gap (host paths leaking into DTU contexts) is also
  S4. Will be partially closed by **T3.1 HostPath/DTUPath** (Wave 3 —
  typed paths force translation at the host/DTU boundary, with a
  runtime check in `to_host()` per refinement C3).
- **Noun-fix**: centralize normalization at the single entry point.
  Once data enters the system, it is already normalized in every
  branch of the symmetry.
- **Anti-fix**: add a second strip / translate / normalize site for
  each new case. Each new symmetric branch grows the obligation linearly.
- **What this design CLOSES**: **partial — typed paths is advisory,
  not enforced**. `NewType` is runtime-erased; constructors can be
  bypassed by anyone who writes `HostPath(some_string)`. The runtime
  check in `to_host()` adds defense at the boundary. A third path
  domain added later without a corresponding type (e.g., a Gitea
  sidecar path domain) re-opens the wound. **Not a security boundary**;
  do not present it as one.

### S5 — Aspirational contract

- **Invariant violated**: code is written against an interface that
  does not exist. The branch never fires; nobody notices.
- **Recognition signature**:
  - `if [ -d /opt/X ]` / `if hasattr(obj, "method")` guarding what
    "should" exist somewhere upstream
  - fallback paths that "ought to never trigger" — and do, because the
    primary path was never wired
  - documented in code as "for future use" with no consumer
  - dead branches whose absence of test coverage is justified as
    "defensive"
- **Canonical incident**: dot-graph manifest's
  `if [ -d /opt/amplifier-bundle-attractor ]` — no infrastructure
  creates that directory, so the if-branch never fires; the "fallback"
  path is in fact the only path, and the guard is a lie. No fix yet;
  deferred and will be filed separately.
- **Noun-fix**: contract becomes a checked dependency. Build, install,
  or startup fails loud if the assumed interface is absent. The
  if-branch disappears because the assumption is now enforced upstream.
- **Anti-fix**: paper over the false branch with a TODO. The next
  contributor reading the code believes the upstream contract exists.
- **What this design CLOSES**: **not addressed in the current Wave 1–3
  refactor**. Filed as future work. The S5 question in the review
  checklist is the immediate guard against new instances landing.

## The "true nouns vs located nouns" distinction (this matters)

Of the five designs shipping or staged in Wave 1–3, **only two make
violation unconstructable**:

| Tier | Item | Mechanism |
|---|---|---|
| **TRUE NOUN** | HandlerContext (T2.1) | Compile-time + runtime: required fields. Cannot construct a half-wired registry. |
| **TRUE NOUN** | RunIdentity (T2.4) | Required key at read time. Cannot read a foreign run's state. |
| **LOCATED NOUN** | terminate_pipeline (T2.3) | Sole-caller AST guard + totality test. Verb relocated earlier in the lifecycle. |
| **ASSISTED PATTERN** | HostPath / DTUPath (T3.1) | Pyright at write time + `to_host()` runtime validation. Advisory; constructor bypassable. |
| **SPOT FIX** | DOT parser key-stripping (#33) | Centralized at one site for now. A future tokenizer change could reintroduce S4. |

**Lesson**: earlier detection is cheaper than later detection, so the
located nouns and assisted patterns are still net-positive — but they
should not be presented as having retired their species. They have made
the species *cheaper to catch*, not *impossible to introduce*. When
deciding which problem to attack next, prefer turning a located noun
into a true noun, or an assisted pattern into a located noun, over
adding a sixth species.

## When a verb-invariant is acceptable (the honest exception)

A verb-invariant is acceptable when:

- The obligation is genuinely **single-site**. Nobody else can hit it;
  there is no sibling. The noun would invent symmetry that isn't there.
- The behavior is **genuinely optional** — not "required but skipped";
  rather "skip is a real configuration choice." A noun would force
  callers to express the absence, which is needless ceremony.
- The cost of the noun **exceeds** the cost of the recurrence. A noun
  that adds five concepts to retire a one-line forget is a bad trade.

This exception is real. It is also the most over-claimed exemption in
review. The direction-of-travel question (`CODE-REVIEW-CHECKLIST.md`)
exists to challenge it: if the PR adds a new "remember to call X at
every site" obligation, the burden of proof is on the verb, not on the
noun.

## Cross-links

- Code review checklist (Socratic): `CODE-REVIEW-CHECKLIST.md`
- Workspace migration guide: `../../RESOLVE-MIGRATION-GUIDE.md`
- Kernel philosophy: `foundation:docs/KERNEL_PHILOSOPHY.md` —
  "mechanism, not policy" is the parent principle. The five species
  are all variations on the same theme: a *mechanism* (the noun) that
  callers cannot bypass is durable; a *policy* (the verb) that callers
  must remember is not.
- Language philosophy: `foundation:docs/LANGUAGE_PHILOSOPHY.md` —
  "make failure impossible, not success easy" is the implementation
  lens. The true nouns satisfy this; the located nouns and assisted
  patterns approximate it.
