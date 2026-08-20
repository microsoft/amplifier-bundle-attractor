# Design: attractor-scout deck mode

**Status:** SHIPPED. Built and live-proved in the PR that also lands this document.
**Date:** 2026-08-19
**Repo:** `amplifier-bundle-attractor`, `main` @ `c8e71dd`
**Scope:** an OPT-IN step 10 on `/attractor-scout` that turns the same verified mining data into
one authored, deck-grade, educational HTML artifact — with deterministic quality/honesty gates.
**Decision-matrix tier:** **additive extension.** Deck mode adds a new optional artifact and two
new CLI subcommands; it changes no observable contract of the mining spine, the demonstration
layer, or the deterministic renderer. Steps 1–9 are byte-untouched in behavior. No
`specs/EXTENSIONS.md` entry is owed, for the same reason the demonstration layer owed none:
onboarding/teaching output extends no pipeline contract.

**Read against:** `skills/attractor-scout/SKILL.md` (steps 8–9 — the demo-brief pattern this
copies), `scripts/attractor_scout/demo.py` + `demo_templates.py` (the pattern's reference
implementation), `docs/designs/2026-08-19-attractor-scout-demonstration-layer.md` (the design
register this matches), `docs/QUALITY_PROTOCOL.md` (§2 guidance toll, §7 leak defense).

---

## 0. The gap, and what ships

The skill's standard artifact is a **deterministic modal report**: `render.py` places every number
from `ranked.json`, which `rank --strict` already re-verified against the raw records. Its trust
story is structural — *no language model is ever in a position to state a number*.

That guarantee is also its ceiling. A deterministic renderer produces a report, not a deck. It
cannot open a section on a claim, cannot draw the reader's own pipeline in the house diagram
grammar, cannot vary its argument to fit what this particular run actually found.

What ships (one sentence): **after step 9, on one explicit yes, the skill assembles a
deterministic deck brief, hands it to ONE fresh-context `reasoning` delegation, runs five
deterministic gates over the result, and publishes `attractor-scout-deck.html` beside the report —
or refuses, with the gate report.**

The trade is stated plainly, because it is the whole design: **what the renderer guaranteed by
construction, deck mode guarantees by inspection.**

---

## 1. Recipe provenance

The method is not new. It was **recovered from a session-transcript replay** of a manual build:
reading back what actually happened when a deck-grade artifact was produced by hand from a real
run, and extracting the repeatable procedure from it. (Described generically on purpose — no
session identifiers, no run identifiers, no corpus content appears in this repo, per §7.)

The recovered recipe, in its own terms:

> **Learn the house TECHNIQUES from an exemplar; build FRESH content from REAL inlined data;
> verify with scripted gates.**

Three properties of that recipe are load-bearing and all three survive into the shipped version:

1. **Fresh-context delegation + inline brief.** The author sees the brief and nothing else. It
   cannot lean on the orchestrator's context, so everything it needs — the style contract, the
   constraints, the data, the gate rules — must be *in the brief*. This is the same seam
   `demo brief` established, and the same reason it exists: an author whose output the
   orchestrator machine-checks lets the session relay verdicts about work it did not itself write.
2. **Techniques, not text.** The exemplar teaches *how* (section arc, diagram grammar,
   progressive disclosure), never *what*. `deck_templates.STYLE_CONTRACT` is the exemplar's method
   written down as prose; no sentence of the exemplar's own content ships.
3. **Scripted gates, not care.** In the manual build, 101 displayed numbers were re-verified **by
   hand**, and diagram fidelity was compared **by hand**. That is exactly the discipline that does
   not survive repetition. `deck verify` is that hand-verification turned into code.

**Evidence that the gate reproduces the manual discipline.** Run against the manual prototype, the
shipped `deck verify` passes gates (a), (b), (c) and (e) — including *diagram node/edge-multiset
fidelity exact on both demonstrations* — and flags exactly **five** numeric tokens under gate (d).
All five are the values the manual build justified by hand arithmetic (a session total and its two
addends, a covered-session subtotal, and an hours total). They are not wrong; they are
*underivable by machine from the run data alone*. That is precisely the case the `derived-values`
mechanism exists for, and it is why the mechanism exists rather than a looser whitelist.

---

## 2. The gate design

`deck verify <deck.html> --ranked <ranked.json> [--demos <demos.json>]` → exit 0 iff all five pass;
exit 3 on a red gate (distinct from the skill's existing exit 2 fail-loud, so a red gate is
never mistaken for a broken input). Failures name the file and the reason.

| Gate | Checks | Why it is a gate and not a lint |
|---|---|---|
| **a — it parses** | Balanced nesting across 33 container element types; `<!doctype html>`; an `<html>` element. Deliberately excludes the implicit-close family (`p`, `li`, `td`) and SVG leaf shapes, which are legally written unclosed and would produce noise, not signal. | An unclosed `<dialog>` silently swallows the rest of the page. |
| **b — self-contained** | No `<img>` / `<link>` / `<script src>` / `@import` / `<iframe>` / `srcset`; `url(` only ever as `url(#…)`; **exactly two** `https` hrefs; zero `file://`. | The artifact's promise is "own data, one machine, no network". A page that fetches breaks that promise silently, and breaks entirely offline. |
| **c — modals** | Every trigger resolves to a dialog; every dialog is reachable from ≥1 trigger; no duplicate dialog ids. | An orphan modal is content the reader can never reach; a dangling trigger is a button that lies. |
| **d — every number resolves** ★ | Every numeric token in **visible text** must resolve against a whitelist built from the run data, or be declared in the deck's own `derived-values` block. | This is the deterministic renderer's guarantee, re-established by inspection. It is the reason deck mode is allowed to exist at all. |
| **e — diagram fidelity** ★ | For each demonstration: node **set** and edge **MULTISET**, read off `data-node`/`data-edge`, must equal the real `.dot`'s. | A diagram that drops the awkward corrective back-edge teaches the wrong shape — and is the single most plausible way an authored diagram goes wrong. |

### Gate (d) — the whitelist, and the derived-values mechanism

**Whitelist sources**, in order of authority: every numeric in `ranked.json`; every numeric in
`demos.json` (verified stats, convergence arithmetic); every digit run inside a **named** set of
string fields that legitimately carry numerics (the generated `.dot` text, the verbatim machine
gate reports, identifiers, timestamps, invocation commands); each demonstration's node and edge
**counts**, computed from its `.dot` by the vendored parser; collection sizes; and the structural
digits 0–4 (Q1/Q2/Q3, the 4a/4b/4c sub-tests — the same allowance `demo.validate_narrative` makes).

**The derived-values mechanism, in one sentence:** a deck declares any number it computed itself —
a total, a percentage, a unit conversion — in a `<script type="application/json"
id="derived-values">` block as `{value, from, inputs}`, where `from` is the arithmetic provenance
in words and `inputs` is a **mandatory, non-empty** array of the supplied numbers it was computed
from; the gate whitelists declared values only when every input is itself a supplied number **and**
the `from` text references each input it lists — so a bare `{"value":"8731","from":"qqq"}` with no
inputs, or a junk `from` that names nothing, is rejected.

**Why `inputs` is mandatory (the self-dealing fix).** An earlier cut made `inputs` optional; a
fabricated value with a one-word `from` and no inputs then passed, because the only check was that
`from` was non-empty. That is self-dealing — the author declaring their own fiction legitimate.
Requiring a non-empty `inputs`, each a supplied number, and a `from` that references them, means a
derivation cannot be conjured from nothing: every declared number is anchored to numbers the run
actually produced.

**Why declarative and not recomputed (a named limit).** The gate checks that a derivation exists,
is attributed, and cites only supplied inputs referenced by its provenance; it does **not** evaluate
`from` as an expression. Evaluating natural language would be guesswork, and a formal expression
language would force every legitimate derivation through a parser the author cannot see. The one gap
this leaves open, on purpose: a declaration with **real inputs but a wrong total**
(`{"value":"9999","from":"120 + 180","inputs":["120","180"]}`) passes, because the gate trusts the
stated arithmetic. Closing it would mean recomputing, which the design rejects for the reasons
above; it is named limit #4 in §3.

### Gate (e) — how a diagram is matched to a pipeline

Primary: the `<svg>` carries `data-diagram="<slug>"`. Fallback: a unique node-set match. The
fallback exists so a deck authored before the attribute convention — the recovered exemplar —
still verifies; the mandate asks for the explicit attribute because inference is not a contract.

---

## 3. Named limits (documented, not closed)

Each has an expected-pass regression test in `tests/test_scenario9_deck_gates.py`, so a future
"I fixed it" is forced to update the docs honestly. The first two are inherited verbatim from
`demo.validate_narrative`'s whitelist and are named the same way.

1. **Spelled-out numbers.** "twenty-five noes" is not a numeric token and passes unchecked.
   Detecting written numerals is NLP guesswork, and a fail-loud guard that guessed wrong would
   block honest prose — the worse failure for a trust surface whose whole value is not crying
   wolf. The brief tells the author this in as many words, *and tells them not to use it as a
   loophole*.
2. **Decomposition.** A whitelisted `397.5` also whitelists the bare runs `397` and `5`, because a
   rendered stat's own sub-runs are how legitimate prose refers to it. `398` is still rejected, so
   the leak is bounded to runs a supplied number literally contains.

   **§3.2 — how large this leak actually is (measured, not hand-waved).** "Decomposition" undersells
   it, so here are the figures from the live-proof run's own whitelist (946 supplied values): of the
   ten single digits **0–9, 100% are always free** (0–4 are structural; 5–9 are supplied or
   decomposed sub-runs); of the ninety two-digit values **10–99, ~94% resolve**; of the
   three-digit values **100–999, only ~14% resolve**. The shape is the point: a fabricated *two*-digit
   figure will almost always ride an existing decomposition and pass, so the gate's real teeth are on
   larger numbers, where the supplied set is sparse. This is why the brief pushes authors to spell
   out or declare, and why gate (d) is a strong check on the big aggregates a deck is tempted to
   invent, not on small incidental counts.
3. **Computed / concatenated script strings.** Gate (d) now scans prose-bearing string *literals*
   in executable inline `<script>` bodies (closing the `document.title = "8731 units examined"`
   bypass), but a number *assembled at runtime* (`"" + n + " units"`), or written as a bare numeric
   literal in code rather than inside a string, is not a literal and is not seen. And only literals
   carrying a 3+ letter word are checked, so a CSS/geometry string (`"-45% 0px -50% 0px"`, which the
   recovered exemplar's IntersectionObserver actually uses) is not treated as a displayed claim —
   the alternative, scanning every literal, would false-positive on exactly that pattern.
4. **Provenance is declarative.** Gate (d) does not recompute `from`: a declaration with **real,
   supplied inputs but a wrong total** passes (`{"value":"9999","from":"120 + 180",
   "inputs":["120","180"]}`). What the hardening *did* close is the self-dealing case — `inputs` is
   now mandatory and non-empty, every input must be a supplied number, and `from` must reference
   each input — so a value can no longer be conjured with a junk `from` and no inputs. Recomputing
   the arithmetic is the residual, rejected for the reasons in §2.
5. **Scope: text and prose-script only.** Numbers in non-text attribute values (`aria-label`,
   `viewBox`, path geometry, `id` references) are **not** scanned: SVG coordinates are numbers by
   the thousand, so whitelisting them would gut the gate and flagging them would make it useless.
   The deck's *visible claims* are what a reader trusts, and those are what is checked.
6. **Not a strict bijection.** Gate (c) permits **several triggers for one dialog** — the exemplar
   does exactly this, with two entry points into one deep-dive. What is forbidden is an orphan in
   either direction. Stated because "bijection" was the word in the ask and this is deliberately
   weaker than it.

---

## 4. What resisted templating

Two things could not be reduced to a deterministic template, and both are handled by *mandating an
honest label* rather than by generating a value.

**Absent workspace/project fields.** The ranking records which sessions a unit covers, not which
project or repository they belong to. A deck's natural shape wants to say "this happens mostly in
X". There is no honest way to synthesize that, and a plausible-looking guess is the worst possible
output for a trust artifact. So `deck brief` **computes the absences** (workspace attribution,
first/last-seen dates, gists, per-step reliability) and hands the author an explicit
"WHAT THIS RUN DOES NOT HAVE" list, under Mandate 4: *say so in the deck, visibly; never fill the
hole.* The absence is content.

**Testimony vs. artifact.** The most interesting stories a deck can tell are about things for
which no file exists: what a rejected first draft looked like, what a gate report said before it
was superseded. Those are real and worth telling — and they are **testimony**, not recovered
artifacts. No gate can tell the difference, because there is nothing on disk to compare against.
Mandate 4 therefore requires the deck to *label* them ("reported, not recovered from a file"),
distinctly from anything quoted verbatim. This is the one honesty property in deck mode enforced
by instruction rather than by code, and it is named here so that limit is on the record.

---

## 5. Cost, consent, and refusal

- **Opt-in, one explicit yes**, asked once, after step 9, with the LLM cost named in the question.
  Unlike demo #1 — which is the skill's advertised second half — a deck is a bonus. The report and
  the demonstrations are already complete artifacts.
- **Budget: 2 attempts.** One delegation, plus at most one re-delegation with the verify-red report
  fed back **verbatim** — the same corrective loop the demonstrations teach, applied to the deck
  that teaches it.
- **Refusal is a normal outcome.** Still red after the retry ⇒ do not publish, name the failing
  gates, move on. A gate-failed deck is discarded, never shipped with a caveat. (This differs from
  the demo layer's *unavailability* case, where an unverified-but-labelled artifact still
  publishes: there, a rung could not run; here, a check ran and said no.)
- **The vision rung is a ladder, not a requirement.** After — and only after — `deck verify`
  passes: if a vision-analysis tool is available in the session, render/inspect for layout defects
  and take at most one fix round (re-verifying afterwards, because a visual fix must not break a
  gate). If no such tool is available, the deck carries `<!-- vision QA: NOT RUN -->`. Same
  philosophy as the demo lint ladder: **a rung that could not run is labelled, never silently
  treated as a pass.**

---

## 6. Files

**New:**

| File | Contents |
|---|---|
| `scripts/attractor_scout/deck.py` | Brief assembly; the five gates; whitelist construction; the `derived-values` reader; publish-after-gates. |
| `scripts/attractor_scout/deck_templates.py` | The style/technique contract, the hard constraints, the four mandates, the brief template. No LLM anywhere. |
| `fixtures/deck_fixture.py` | A clean synthetic deck that clears every gate, plus one mutation per gate class. |
| `tests/test_scenario9_deck_gates.py` | Green baseline + red proof per gate class + the named-limit regression tests. |
| `tests/test_scenario10_deck_brief.py` | Brief carries the mandates, the real `.dot` text, the gate verdicts, the absences; CLI exit-code contract (0 / 3). |
| `docs/designs/2026-08-19-attractor-scout-deck-mode.md` | This document. |

**Changed:** `scripts/attractor_scout_cli.py` (new `deck brief|verify` subcommand; exit code 3
documented), `scripts/attractor_scout/naming.py` (`DECK_FILENAME`, single naming source preserved),
`SKILL.md` (step 10, three hard-rule bullets, Output line — no new numeric claims).

**Unchanged:** every module of the mining spine, `demo.py`, `demo_templates.py`, `render.py`, the
byte-pinned `authoring_contract.py`, and every pre-existing test.

---

## 7. Out of scope

- **Rendering the deck deterministically.** That artifact already exists; it is the report.
- **Auto-running the vision rung, or making it a requirement.** A missing tool is labelled.
- **Recomputing derived arithmetic** (see §3.4).
- **Decks for honest-NOs or waste findings as standalone artifacts.** They are *sections* of the
  one deck; a deck per finding is spend without teaching.
- **Any edit to the deterministic renderer's output.** `render.py` is untouched; a deck is a
  sibling file, never a modification of the map.
- **Shipping the exemplar.** No sentence of it is committed. Only the method is.

---

# Addendum — 2026-08-20: the style uplift and the structural depth gate (gate f)

**Status:** SHIPPED. Built and live-proved on the branch that lands this addendum.
**Repo:** `amplifier-bundle-attractor`, branched from `main` @ `4e1ba02`.
**Decision-matrix tier:** still an **additive extension**. One new gate, one new mandate, a
rewritten style contract. No CLI surface changed; steps 1–9 remain byte-untouched in behaviour.

## A.0 What was wrong

The shipped deck mode produced *correct* pages that did not read like the house decks they were
meant to sit beside. Reviewing a real generated deck against the two reference decks turned up a
consistent set of misses, and every one of them was an **absence in the brief**, not an author
failure — the style contract described a section *arc* and a diagram *grammar* and left everything
between them to taste:

| Axis | Reference decks do | The shipped contract said | Result |
|---|---|---|---|
| Type | three stacks; a serif carries every heading, pull quote, card title and stat value | "system fonts only" | sans-only, report-flavoured |
| Colour | every semantic hue is a **triad** (stroke / lighter text sibling / dark tint fill), plus a separate focus hue | "typographic hierarchy carries the structure" | a flat palette, no tints, no focus token |
| Width | **two** measures — narrow for prose, wide for figures | (unstated) | one width for everything; dense |
| Nav | fixed scroll-progress bar, sticky top bar, numbered section nav, `IntersectionObserver` current-section highlight | (unstated) | **no navigation of any kind** |
| Section furniture | eyebrow → claim heading → standfirst → figure with title+caption → pull quote → openers → doctrine strip | "a kicker, a headline, a deck line, then body" | no captions, no pull quotes, no closing strip |
| Modals | sticky header with kicker + serif title over a scrolling body; `<h4>` sub-sections; inset evidence blocks; focus return; reduced-motion | "depth lives in a MODAL per card" | a floated close button and three flat paragraphs |
| Modal count | ~3.5–4.5 per section, across every finding class | "every opportunity + every honest-NO" | modals on opportunities only; demos and waste had none |

## A.1 The approach: a technique sheet, not a template

The original design's §4 records why the exemplar itself is not shipped ("only the method is").
That still holds — and it is exactly why the fix is **not** "give the author a better exemplar."
The fix is to make the method *concrete enough to reproduce*.

So `STYLE_CONTRACT` was rewritten as a five-part **technique sheet**: the design tokens with the
rules they encode, the nav spec, the section furniture in order, the modal machinery, and only then
the section arc. It carries literal token values (a hex ramp is a design token, not prose) and
literal structural instructions, and it carries **no sentence of any reference deck**. The leak
guard's Layer-1/2 scan covers the shipped file as it covers every other; the discipline the
addendum adds on top is editorial — *techniques, not sentences* — and it is checked by reading, the
same way §4's "no exemplar sentence ships" is.

The bet: an author who is told "the serif carries every heading, `font-weight:600`, negative
tracking near -.035em on the h1" will produce the house look from scratch, where an author told
"typographic hierarchy carries the structure" will produce something reasonable and generic. That
is a claim about briefs, and the live proof in §A.4 is what tests it.

## A.2 The new mandate, and why it is structural

**Mandate 5 — every modal is structured, not a paragraph dump.** Dissecting ten representative
modals (five per reference deck) showed one invariant shape, not a length: an unheaded lede, two or
more sub-section heads, at least one *inset* that quotes evidence, a reading of what it means, and
somewhere to go. Length varied 5–14 block elements; **structure did not vary at all**.

So the contract mandates the parts, by name, with marker classes the gate can count:

| Part | Marker | Minimum |
|---|---|---|
| title | `<h3>` | 1 |
| kicker | `class="m-kick"` | 1 |
| sub-sections | `<h4>` | **2** |
| evidence (the reader's own verified data, quoted) | `class="evidence"` | **1** |
| why-it-matters | `class="why"` | 1 |
| entry point | `class="entry"` | 1 |

These live as constants in `deck_templates.py` (`MODAL_*`), quoted by the brief and read by the
gate — the same single-source discipline `DERIVED_BLOCK_ID` already uses for the numbers gate, and
for the same reason: a contract stated in two places drifts.

**Why structure and not a length quota.** A byte or word minimum is trivially satisfiable by
padding, and padding is the failure mode a deck-grade page most needs protection from. A structure
check inverts that: a hollow modal cannot pass by growing, and a genuinely short modal that has the
parts passes untouched. `test_gate_f_is_structural_not_a_length_check` asserts both halves
directly — a padded hollow modal fails, a trimmed conforming one passes.

## A.3 Gate (f), and its named limits

`gate_modal_depth` counts the parts above per `<dialog>` and fails naming the dialog id and exactly
which parts are absent. It is stdlib, deterministic, and O(document) — it rides the existing
`HTMLParser` pass, adding a dialog stack and six counters. Fixtures: `with_hollow_modal` (the RED
proof — a modal gutted to two flat paragraphs while every other gate stays green),
`with_modal_missing_evidence`, `with_modal_one_subsection`; the clean fixture's modal was rebuilt
to the contract and is the GREEN baseline.

Named limits, documented rather than closed, each with a test:

1. **It counts presence, not quality.** Two `<h4>`s reading "Details" and "More details" pass.
   Judging whether a sub-section *earns* its heading is exactly the taste question a deterministic
   gate must not pretend to answer; the brief tells the author what the sub-sections are for, and
   the gate only ensures the slots exist. Same posture as gate (d)'s refusal to recompute
   arithmetic (§3.4).
2. **`class="evidence"` is a claim, not a proof.** The gate checks that an evidence inset exists;
   it does not verify that what is inside it came from the run. It does not need to — every number
   in it is already subject to gate (d), which is the check that actually binds. The residual is an
   evidence block with no numbers in it at all.
3. **Class tokens are matched exactly, never as substrings.** `class="whyever"` is not a `why`
   block (`test_gate_f_class_token_is_matched_exactly`). A token may sit beside others.
4. **Nesting is stack-based but flat in practice.** Dialogs do not nest in any real deck; the
   parser keeps a stack anyway so a malformed document degrades to a named failure rather than a
   miscount.
5. **The gate cannot see a modal that is never opened.** It counts structure inside `<dialog>`
   elements; gate (c) is what guarantees each of them is reachable. The two are complementary and
   neither subsumes the other.

## A.4 What the technique sheet does NOT close

- **Nav is mandated in the brief, not gated.** A deck could ship without the sticky bar and still
  pass every gate. Gating "has a top bar" would be checkable, but it is a *taste* mandate wearing
  machine clothes, and the deck's trust story is about honesty, not chrome. Named, not closed.
- **Diagrams still do not appear inside modals.** The reference decks do not do it; the contract
  says so explicitly so an author does not go looking for the pattern and invent one.
- **Token adoption is unenforced.** The contract states the hex ramp; nothing checks that the
  author used it. A colour gate would fight legitimate variation for no honesty gain.

## A.5 Files

**Changed:** `scripts/attractor_scout/deck_templates.py` (the technique sheet; `MODAL_*` constants;
Mandate 5), `scripts/attractor_scout/deck.py` (`DeckDialog`, dialog-structure parsing,
`gate_modal_depth`, docstring), `fixtures/deck_fixture.py` (conforming clean modal + three
mutations), `tests/test_scenario9_deck_gates.py` (+8 gate-f tests),
`tests/test_scenario10_deck_brief.py` (six gates, five mandates),
`tests/test_skill_doc_claims.py` (gate (f) pinned to `gate_modal_depth`), `SKILL.md` (step 10 names
gate (f)).

**Unchanged:** every module of the mining spine, `demo.py`, `demo_templates.py`, `render.py`, the
byte-pinned `authoring_contract.py`, and the CLI's surface and exit codes.
