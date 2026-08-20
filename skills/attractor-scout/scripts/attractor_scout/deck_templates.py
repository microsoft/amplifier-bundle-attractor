"""Every deterministic string DECK MODE emits. NO LLM in here.

Deck mode is the optional third artifact: after the deterministic map (step 7)
and the machine-gated demonstrations (steps 8-9), the skill can produce ONE
deck-grade, personalized, educational HTML page from the same verified data.

The demonstration layer proved a shape: *deterministic brief -> fresh-context
`reasoning` delegation -> machine gates -> publish-or-refuse*. Deck mode reuses
it verbatim, at a larger scale, and this module is the brief half. It carries
three things a fresh-context author cannot see for itself:

**(a) The house style/technique contract.** What the deck's section arc IS, and
what the diagram grammar IS --- learned from an exemplar and written down here
as TEXT so the delegate builds FRESH content in that idiom rather than copying
an exemplar's sentences.

**(b) The hard constraints.** One file, inline everything, zero external
requests, works from `file://`. These are the same self-containment rules the
deterministic renderer honours; a hand-authored page has to be TOLD them.

**(c) The mandates that make the gates passable.** This is the load-bearing
part. `deck verify` is deterministic and unforgiving: an undeclared number
fails the whole deck. A delegate that does not know the whitelist rule will
fail it by accident, so the brief states the rule, lists the numbers that are
already legal, and specifies the exact escape hatch (the `derived-values` JSON
block) for arithmetic the deck computes for itself.

Nothing here fetches anything. The two links the deck footer carries are
anchors a reader may follow, not resources the page loads --- the same
distinction `render.py` documents.
"""

from __future__ import annotations

from . import demo_templates as T

# --------------------------------------------------------------------------
# The one machine-readable seam between the authored deck and the gate.
# --------------------------------------------------------------------------

#: The id of the JSON block a deck carries to declare arithmetic it did itself.
#: `deck verify` reads exactly this id; the brief tells the author exactly this
#: id; one constant, so they can never disagree.
DERIVED_BLOCK_ID = "derived-values"

#: The attribute pair that makes diagram fidelity machine-checkable. Every
#: node group and every edge element in a demonstration diagram carries one.
NODE_ATTR = "data-node"
EDGE_ATTR = "data-edge"

#: The attribute that names WHICH demonstration a diagram is of. Optional:
#: when absent the gate falls back to matching on the node set, which is how
#: the recovered exemplar verifies. When present the mapping is explicit.
DIAGRAM_ATTR = "data-diagram"

# --------------------------------------------------------------------------
# The modal STRUCTURE contract --- the second machine-readable seam.
#
# The style contract below tells the author to build every modal out of these
# named parts; `deck.gate_modal_depth` counts exactly these names. One set of
# constants, so the brief and the gate can never drift apart --- the same
# single-source discipline `DERIVED_BLOCK_ID` uses for the numbers gate.
#
# These are STRUCTURE mandates, not length quotas. A modal cannot satisfy them
# by being long; it satisfies them by having a heading, real sub-sections, a
# block that quotes the reader's own verified data, a why-this-matters, and a
# way in. Padding does not help, which is the entire point.
# --------------------------------------------------------------------------

#: The tiny uppercase label above a modal's title. Part of the heading pattern.
MODAL_KICKER_CLASS = "m-kick"

#: A modal sub-section head. Plain `<h4>` inside the modal body --- no class
#: needed, because the tag alone is unambiguous at this depth.
MODAL_SUBSECTION_TAG = "h4"

#: The inset that quotes THIS reader's own verified data back to them ---
#: their counts, medians, trend, or a machine verdict relayed verbatim.
MODAL_EVIDENCE_CLASS = "evidence"

#: The block that says why the evidence matters *for this reader*.
MODAL_WHY_CLASS = "why"

#: The closing line: the smallest next step, or (for an honest NO) what would
#: change the answer. Every modal ends somewhere the reader can go.
MODAL_ENTRY_CLASS = "entry"

#: How many `<h4>` sub-sections a conforming modal carries, at minimum.
MODAL_MIN_SUBSECTIONS = 2

#: How many evidence insets a conforming modal carries, at minimum.
MODAL_MIN_EVIDENCE = 1

#: The two links the footer carries, and the only two the gate permits.
FOOTER_LINKS: tuple[tuple[str, str], ...] = (
    ("the published explainer", T.EXPLAINER_URL),
    ("the bundle repository", "https://github.com/microsoft/amplifier-bundle-attractor"),
)

#: Exactly how many `href="https://..."` attributes a verified deck may carry.
ALLOWED_HTTPS_HREFS = len(FOOTER_LINKS)


# --------------------------------------------------------------------------
# (a) The house style / technique contract --- the section arc, as TEXT.
# --------------------------------------------------------------------------

STYLE_CONTRACT = f"""\
THE HOUSE DECK IDIOM (learn the TECHNIQUES; write FRESH content)

This is a DECK, not a report. A report enumerates; a deck argues. Every section
opens on a claim a reader can disagree with, then pays it off with the run's own
verified evidence. Write for one specific reader --- the person whose work this
run mined --- in second person, in their vocabulary, with no internal jargon and
no meta-commentary about being a language model.

What follows is a TECHNIQUE SHEET, not a template: concrete tokens, a concrete
nav spec, and a concrete modal-structure contract, extracted from the house
decks this one has to sit beside. Reproduce the TECHNIQUES exactly. Write every
sentence yourself, from the data below.

=========================================================================
PART 1 --- THE DESIGN TOKENS (declare these verbatim in :root)
=========================================================================

  --bg:#0d1117;      --bg2:#11171e;     --surface:#171e26; --surface2:#1e262f;
  --track:#232c36;   --line:#2a3540;    --line2:#3a4753;
  --ink:#eef2f6;     --ink2:#c3ccd6;    --muted:#93a1af;   --faint:#8593a1;
  --accent:#ff7a45;  --accent2:#ffa27a; --accent-tint:#2b1a12;
  --pass:#4ade80;    --pass2:#86efac;   --pass-tint:#102a1c;
  --focus:#7cc4ff;
  --wide:1040px;     --col:760px;

THE RULES THOSE TOKENS ENCODE --- follow them, do not just paste them:

  * EVERY SEMANTIC COLOUR IS A TRIAD: a saturated hue for strokes/borders
    (`--accent`, `--pass`), a LIGHTER sibling for text on dark (`--accent2`,
    `--pass2`), and a very dark TINT for filled shapes (`--accent-tint`,
    `--pass-tint`). Never set accent-hue text directly on the page background;
    always use the lighter sibling. Accent = attention, a gate, a control node.
    Pass/green = the passing edge and the single exit door. Nothing else gets
    a colour.
  * FOCUS IS ITS OWN HUE, outside both families, so a keyboard ring can never
    be mistaken for an accent: `:focus-visible {{ outline:2px solid var(--focus);
    outline-offset:3px }}` declared once, globally, for links, buttons and
    anything with a tabindex.
  * SURFACES GO LIGHTER, INSETS GO DARKER. A card/panel sits ABOVE the page
    (`--surface`, hover `--surface2`); a quoted/evidence inset sits BELOW it
    (`--bg2`). That inversion is what makes quoted material read as quoted.
  * FLAT, NOT GLOSSY. No gradients anywhere. Exactly ONE shadow in the whole
    document, on the dialog: `0 26px 70px rgba(0,0,0,.62)`. Everything else is
    a 1px `--line` border.

TYPOGRAPHY --- three stacks, and the split between them is the signature:

  --sans:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,
         "Helvetica Neue",sans-serif;
  --serif:"Iowan Old Style","Palatino Linotype",Palatino,"Book Antiqua",
          Georgia,"Times New Roman",serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,
         "Liberation Mono",monospace;

  * SERIF carries every h1/h2/h3, the hero sub-line, every pull quote, every
    doctrine strip, every card title, and every big stat value. SANS carries
    body copy and the tiny uppercase labels. MONO carries counters, diagram
    edge labels, code, and stat figures inside modals. A deck that sets its
    headings in the body sans is the single most obvious way to miss this
    house style.
  * WEIGHT AND TRACKING: headings are `font-weight:600` with NEGATIVE tracking
    (h1 about -.035em, h2/h3 about -.02em) and tight leading (h1 near .98).
    Heavy 800-weight headings are wrong here; the serif does the work.
  * SCALE: body 17px/1.6. h1 `clamp(44px,7.4vw,82px)`.
    h2 `clamp(32px,4.8vw,50px)`. h3 about 21px. A serif deck-line under the
    hero headline at `clamp(20px,2.6vw,27px)`. A sans standfirst at 19px/1.45.
    A muted lede at 16px.
  * THE MICRO-LABEL RHYTHM (this recurs in ~8 places and is the second
    signature): 10.5-11px, `font-weight:700`, `letter-spacing:.16em-.2em`,
    `text-transform:uppercase`, coloured `--accent` when it labels a section
    or a modal, `--faint` when it labels a figure or an inset. Use it for the
    hero kicker, the section eyebrow, the figure title, the modal kicker, and
    every inset's label.

LAYOUT RHYTHM:

  * TWO WIDTHS, deliberately different: `--col:760px` for PROSE, `--wide:1040px`
    for FIGURES and the top bar. Prose narrower than diagrams is what makes the
    page feel airy instead of like a report.
  * `main > section {{ padding:76px 0 34px; scroll-margin-top:70px;
    border-top:1px solid var(--line) }}`, and the hero section drops the top
    border. Sections are separated by a hairline, never by a colour change.
  * RADII BY ROLE: 5px insets/notes, 6px cards and diagram nodes, 10px dialog,
    999px pill buttons, 50% circular counters and the modal close button.
  * THE 3px LEFT STRIPE is the house emphasis device. A doctrine strip, a
    pull-out note, an evidence inset, and a card all carry
    `border-left:3px solid <a semantic hue>` --- accent for attention, pass for
    "what changes", faint for "what we cannot see".

=========================================================================
PART 2 --- THE NAV SPEC (build all four pieces; they are not optional)
=========================================================================

  1. SCROLL PROGRESS. A fixed 3px bar across the top of the viewport
     (`position:fixed;top:0;left:0;right:0;z-index:80`, `aria-hidden="true"`)
     whose inner element's width is set from the scroll fraction by the inline
     script, on a passive scroll listener plus resize.
  2. STICKY TOP BAR. `position:sticky;top:0;z-index:60`, background the page
     colour at ~92% alpha with `backdrop-filter:saturate(150%) blur(10px)`, a
     1px bottom border, about 54px tall, its contents laid out at `--wide`.
     It carries a BRAND word-mark (12.5px, 700, .16em tracking, uppercase, one
     word inside it in `--accent`) and, optionally, one bordered mono pill
     tagging what this document is.
  3. NUMBERED SECTION NAV. Inside the bar, `<nav aria-label="Sections">` with
     one anchor per non-hero section, each labelled with a zero-padded ordinal
     and a two-or-three-word name (`01 The shape`, `02 What is already
     loop-shaped`, ...). 12px, `--muted`, transparent bottom border; on hover
     and when current, an `--accent` bottom border. The bar scrolls
     horizontally on narrow screens with the scrollbar hidden.
  4. CURRENT-SECTION HIGHLIGHT. An `IntersectionObserver` over the section
     elements with a `rootMargin` that narrows the trigger band to the middle
     of the viewport (about `-45% 0px -50% 0px`), setting `aria-current="true"`
     on the matching anchor and removing it from the others. Guard the whole
     block with a feature check so a browser without it simply gets no
     highlight. Pair it with `html {{ scroll-behavior:smooth }}` and the
     `scroll-margin-top` above, so a jumped-to heading is never hidden under
     the sticky bar.

=========================================================================
PART 3 --- THE SECTION FURNITURE (what a section is made of, in order)
=========================================================================

  HERO: a `--col` block holding a kicker micro-label, a serif h1 that is ONE
  declarative sentence ending in a full stop, a serif deck-line, and a muted
  lede that says how to read the page (that cards, nodes and numbers open
  detail). Then, immediately, a full-`--wide` `<figure>` carrying the hero
  diagram. No top border.

  EVERY OTHER SECTION, in this order:
    a. `--col` block: eyebrow micro-label with the ordinal in its own <span>,
       then a claim-shaped serif h2, then a sans standfirst.
    b. full-`--wide` `<figure>`: a figure-title micro-label, the inline SVG,
       and a `<figcaption>` (14px, `--muted`, top-bordered, capped near 660px)
       that opens with a bold lead-in clause and then says what the diagram
       MEANS --- never what it depicts.
    c. `--col` block again: a serif PULL QUOTE (`clamp(20px,2.4vw,25px)`, with
       one clause in accent italics), then the section's content grid (cards,
       stat tiles, numbered rows, or chips), then a cluster of PILL OPENER
       buttons that open modals, then optionally one bordered NOTE block.
    d. a full-width DOCTRINE STRIP closing the section: a single serif line at
       `clamp(21px,2.8vw,28px)` in `--accent2`, with a 3px accent left rule and
       about 22px of left padding. One per section. It is the deck's
       punctuation --- the last thing a skimmer reads before the next hairline.

  FOOTER: `--bg2` background, its heading deliberately set in SANS at 14px
  uppercase `--faint` (breaking the serif rule to de-emphasise), a flex row of
  mono links, and one closing paragraph.

  TRIGGER SPECIES --- use several, not one. The house decks open modals from
  FIVE different kinds of element, and that variety is most of why the page
  feels alive: (i) grid CARDS with a mono index line, a serif title, a muted
  description, a mono stat line and an uppercase go-line; (ii) big STAT TILES
  whose value is serif at ~54px in `--accent2`; (iii) NUMBERED ROWS with a
  circular mono counter; (iv) PILL OPENERS prefixed with a `+` in accent;
  (v) SVG HOTSPOTS --- a `<g>` with `role="button" tabindex="0"` and a `+`
  glyph, carrying its own focus ring rect that appears only on
  `:focus-visible`. Every one of them carries `data-modal`.

  DENSITY: one idea per screen, one diagram per section, prose capped at
  `--col`, grids on `repeat(auto-fit,minmax(230-300px,1fr))`. Hover states lift
  a card by 2px and change its border; all of it opts out under
  `prefers-reduced-motion`.

=========================================================================
PART 4 --- THE MODAL MACHINERY AND ITS DEPTH CONTRACT
=========================================================================

MACHINERY (build exactly this):

  * Native `<dialog id="m-slug" aria-labelledby="h-slug">`, opened with
    `showModal()` from a delegated handler bound to every `[data-modal]`
    element --- buttons AND SVG groups. Non-button triggers additionally
    handle Enter and Space. Keep a `setAttribute("open")` fallback for engines
    without `showModal`.
  * Inside the dialog: ONE scroll box (`max-height:86vh; overflow-y:auto`,
    `tabindex="-1"`, its own outline suppressed) containing a STICKY HEADER and
    a BODY. On open, reset the box's `scrollTop` to 0 and focus it.
  * The sticky header carries the modal kicker micro-label and the serif h3
    (`clamp(23px,3.4vw,30px)`), plus a circular close button that inverts to
    accent on hover. It stays put while the body scrolls.
  * Backdrop: `dialog::backdrop {{ background:rgba(5,8,12,.76) }}`. Close on a
    click OUTSIDE the dialog's own bounding rect --- compute it from
    `getBoundingClientRect()`, and skip the check when a click reports
    coordinates of exactly 0,0 (that is a keyboard-synthesised click, and
    treating it as a backdrop hit closes the dialog the instant it opens).
  * On the dialog's `close` event, return focus to the trigger that opened it.
    Escape is native; the `close` event fires for every path, which is why the
    focus return belongs there and nowhere else.
  * Motion: `dialog[open]` gets a ~.18s ease-out fade-and-rise, and the
    `prefers-reduced-motion` block turns that animation off along with every
    hover transform.

DEPTH --- WHAT A MODAL CONTAINS (this is Mandate 5; the gate counts it):

  A modal is a DEEP-DIVE, not a tooltip. Every dialog in this deck is built
  from these named parts, and `deck verify` checks for them by name:

    * HEADING PATTERN --- the sticky header's `<p class="{MODAL_KICKER_CLASS}">`
      kicker plus an `<h3>` title (the one `aria-labelledby` points at).
    * AT LEAST {MODAL_MIN_SUBSECTIONS} SUB-SECTIONS --- plain `<{MODAL_SUBSECTION_TAG}>`
      heads inside the modal body, each naming a distinct move: what it is,
      why it recurs, what changes, what is still unknown, what it is NOT.
      Set them in the micro-label rhythm (uppercase, tracked, `--faint`).
    * AT LEAST {MODAL_MIN_EVIDENCE} EVIDENCE BLOCK --- an inset carrying
      `class="{MODAL_EVIDENCE_CLASS}"` that quotes THIS reader's own verified
      data back to them: their session count, their medians, their trend, the
      machine verdict relayed verbatim. Give it the darker inset background,
      a 3px left stripe, and an uppercase label of its own. A grid of stat
      tiles (a mono figure over a small caption) is the densest form and is
      encouraged wherever several of the reader's numbers belong together.
      Numbers inside it are subject to Mandate 1 exactly like any other text.
    * A WHY-IT-MATTERS --- one element carrying `class="{MODAL_WHY_CLASS}"`
      that says what the evidence means FOR THIS READER, in their vocabulary.
      Not what an attractor is in general; what this specific piece of their
      week costs them, or buys them.
    * AN ENTRY POINT --- one element carrying `class="{MODAL_ENTRY_CLASS}"`
      that closes the modal on somewhere to go: the smallest next step for an
      opportunity, what would change the answer for an honest NO, what to stop
      doing for a waste finding, how to run it for a demonstration.

  A conforming modal lands at roughly five to fourteen block elements. That is
  a consequence of having the parts, NOT a quota --- padding a thin modal with
  filler paragraphs satisfies nothing and is worse than a short honest one.
  Optionally close on a serif accent pull-quote with a left rule, the same
  device as the section doctrine strips.

  A KNOWN LIMIT, stated so you do not go looking for it: the house decks do
  NOT put diagrams inside modals. Diagrams live in section figures; modals
  carry stat tiles and quoted verdicts instead. Follow that.

=========================================================================
PART 5 --- THE SECTION ARC
=========================================================================

THE SECTION ARC (six sections, in this order; each is one <section> element):

  1. HERO --- "your work, mapped". One headline claim. The scope of the run
     stated plainly: what was read, that it was read locally, that nothing left
     the machine. A short stat strip of the run's shape. This section sets the
     honesty tone for everything after it: name what the run did NOT see.

  2. TEACH THE BASIN --- what an attractor actually is, before any
     recommendation is made. An attractor is a BASIN, not a checklist: work
     that is attempted, checked against evidence produced OUTSIDE the worker,
     and re-attempted until it lands, under a budget that routes exhaustion
     somewhere honest. Carry the THREE-QUESTION TEST here (cycle? evidence
     gate? survives one node having a bad day?), and the CONVERGENCE MATH ---
     once-through odds versus a gated loop's odds within its budget --- LABELLED
     as illustrative arithmetic, never as a measurement of the reader's
     sessions.

  3. RANKED OPPORTUNITIES --- the units that cleared all three questions,
     strongest first, as cards. Each card is a claim plus the reader's own
     verified numbers. Depth lives in a MODAL per card, never inline: the card
     is skimmable, the modal is the deep-dive. Give EVERY ranked opportunity in
     the data its own card and its own modal --- depth scales with the number of
     findings, not to a fixed quota; do not stop at a "top few" and drop the
     rest. Say what makes each unit loop-shaped in ITS terms, not in the test's
     terms.

  4. DEMONSTRATIONS --- the pipelines that were actually generated and machine-
     gated for this run, drawn as INLINE SVG in the house diagram grammar (see
     below). Each diagram is the real graph, node for node and edge for edge.
     Quote the machine verdicts verbatim. Say which checks did NOT run. Never
     vouch for the pipeline yourself. EVERY demonstration gets its own modal
     too, built to the same structure contract --- its evidence block is the
     machine verdict relayed verbatim, and its entry point is how to run it.

  5. HONEST NOES AND WASTE --- the units that recur and cost real effort and are
     still NOT worth automating, each with the sub-test it failed and what would
     change the answer; plus the waste channel (ceremony that cost time but is
     not an opportunity to act on). Give EVERY honest-NO AND EVERY waste finding
     its own modal too, the same depth as the opportunities --- the credibility
     of this section is that it withholds nothing, and a finding demoted to a
     table row is a finding the deck is quietly embarrassed by. Depth scales
     with the findings, to no fixed quota. An honest-NO modal's entry point is
     what would change the answer; a waste finding's is what to stop doing.
     This section is the deck's credibility: a deck that only sells is an
     advertisement.

  6. GOING FORWARD --- the entry points, ordered by how much the reader has to
     commit. Smallest first. End on the smallest possible next step.

  FOOTER --- exactly two outbound links and no others: the published explainer,
  and the bundle repository.

TECHNIQUES THAT MAKE IT READ AS A DECK:

  * A claim-shaped heading per section, not a label. "Nine pieces of your work
    are already loop-shaped" beats "Opportunities".
  * Progressive disclosure. Surface = skimmable; depth = modals. A reader who
    reads only the headings still gets the whole argument.
  * One idea per screen. Long prose blocks are the failure mode.
  * Typographic hierarchy carries the structure --- a kicker, a headline, a
    deck line, then body. System fonts only.
  * Diagrams do work no paragraph can: use one wherever a relationship, a flow,
    or a distribution is the point.
  * Honesty is a design element. Absent data gets a visible, styled note ---
    never a blank, never an invented value.

THE DIAGRAM GRAMMAR (every inline SVG uses exactly this vocabulary):

  * NEUTRAL fill = an LLM worker node. ACCENT fill = an evidence gate or a
    control node that runs a real command. GREEN = the passing edge and the
    single exit door.
  * SOLID line = normal flow. DASHED line = a corrective back-edge (the loop
    closing) or a failure route.
  * Arrowheads come from ONE shared <defs> block declared once at the top of
    the document and referenced with url(#...) --- never re-declared per
    diagram, never fetched.
  * Every diagram is accessible: role="img" plus a <title> and a <desc> that
    a screen reader can follow end to end, referenced by aria-labelledby.
  * A diagram of a REAL generated pipeline additionally carries the fidelity
    attributes (see the mandates below) --- that is what makes it checkable.
"""


# --------------------------------------------------------------------------
# (b) The hard constraints --- verbatim, non-negotiable.
# --------------------------------------------------------------------------

HARD_CONSTRAINTS = """\
HARD CONSTRAINTS (verbatim --- a violation fails the deck):

  * ONE FILE. A single .html document. No companion assets of any kind.
  * INLINE CSS AND JS ONLY. One <style> block, one <script> block. No
    <link>, no <script src>, no @import, no <iframe>, no srcset.
  * ZERO EXTERNAL REQUESTS. Nothing the page loads may come from the network.
    `url(` is legal ONLY as `url(#local-id)` --- never `url(http...)`, never
    `url(data:...)`, never a font or image URL. NO resource-loading element or
    attribute of ANY kind: no <object>, <embed>, <source>, <track>, <video>,
    <audio>; no `src=` attribute anywhere; and no `xlink:href` or `<use>`/
    `<image>` href that points at anything but a local `#fragment`. The gate
    checks for every one of these by name.
  * NO <img>. Every graphic is INLINE SVG, drawn in markup.
  * SYSTEM FONTS ONLY. Declare font stacks from fonts that already exist on
    the reader's machine. No webfont of any kind.
  * WORKS FROM file://. Opened by double-clicking the file, with no server and
    no network, the page must be complete and fully functional.
  * EXACTLY TWO OUTBOUND LINKS, both https, both in the footer: the published
    explainer and the bundle repository. Zero `file://` URLs anywhere.
  * ACCESSIBLE. Every diagram carries role="img" with a <title> and a <desc>
    referenced by aria-labelledby. Every modal is reachable and dismissable by
    keyboard. Honour `prefers-reduced-motion` --- animation is opt-out by the
    reader's own setting, in the stylesheet.
  * NO INTERNAL JARGON. No repo-internal codenames, no issue numbers, no
    build-process vocabulary, no "the harness", no tool names the reader never
    typed. Write about their work, in their words.
  * NO IDENTITY. No hostnames, usernames, home directories, absolute local
    paths, or e-mail addresses --- not in text, not in comments.
"""


# --------------------------------------------------------------------------
# (c) The MANDATES --- the four rules that make the machine gates passable.
# --------------------------------------------------------------------------

MANDATES = f"""\
THE FIVE MANDATES (machine-enforced by `deck verify`; a violation means the
deck is NOT published --- these are not style advice, they are the gate):

  ** MANDATE 1 --- EVERY DISPLAYED NUMBER RESOLVES. **
  Every number that appears in the deck's visible text must EITHER be one of the
  numbers supplied in the DATA section below (the run's re-verified stats, the
  generated pipelines' own numerics, the machine gate reports), OR be declared
  as derived arithmetic in the block described in Mandate 2. There is no third
  option. An undeclared number fails the whole deck, and the failure names the
  token. This applies to text a SCRIPT writes as well: a number placed by JS
  (e.g. `element.textContent = "8731 units examined"`) is scanned in the
  script's string literals exactly as if it were in the DOM, so injecting a
  figure through JavaScript does not evade the gate. Prose like "about a dozen"
  or "twenty-five" (spelled out) is always legal --- the scan reads digits, not
  words. When in doubt, spell it out or declare it.

  ** MANDATE 2 --- DECLARE EVERY DERIVED VALUE. **
  A deck legitimately does arithmetic the run data does not contain: a total, a
  percentage, a sum of two supplied numbers, a unit conversion. Every such
  number must be declared, ONCE, in a JSON block the deck carries. (The numbers
  below are ILLUSTRATIVE placeholders, not your run's data --- use your own.)

      <script type="application/json" id="{DERIVED_BLOCK_ID}">
      [
        {{"value": "300",
         "from": "120 sessions inside named units + 180 in the waste ledger",
         "inputs": ["120", "180"]}},
        {{"value": "40",
         "from": "8 opportunities out of 20 named units, as a percentage",
         "inputs": ["8", "20"]}}
      ]
      </script>

  `value` is the number exactly as the deck displays it, minus any thousands
  separator or unit. `from` is the arithmetic provenance in plain words --- what
  was combined, and how. `inputs` is MANDATORY and must be a NON-EMPTY array;
  every entry must itself be a number the DATA section supplied, AND the `from`
  text must reference each input it lists. These are not optional niceties ---
  they are what makes a derivation checkable instead of a bare assertion. Any of
  these fails the whole deck: an empty or missing `from`; a missing, empty, or
  non-array `inputs`; an input that is not a supplied number; or a `from` that
  does not mention an input it claims to combine. (What the gate does NOT do is
  re-evaluate the arithmetic: it trusts that `120 + 180 = 300` because you said
  so and named real inputs. Declaring real inputs with a wrong total is the one
  gap left open, on purpose --- see the design doc's named limits.)

  ** MANDATE 3 --- DIAGRAM FIDELITY IS CHECKED, NODE BY NODE AND EDGE BY EDGE. **
  Every diagram of a REAL generated pipeline must carry, on its SVG elements:

    * `{NODE_ATTR}="<node id>"` on the group that draws each node, exactly once
      per node, spelled exactly as the `.dot` spells it.
    * `{EDGE_ATTR}="<src>-&gt;<dst>"` on the element that draws each edge, once
      per edge in the `.dot` --- INCLUDING duplicates. Two edges between the
      same pair of nodes means two elements carrying that attribute. The gate
      compares a MULTISET, so an edge drawn once but declared twice in the
      `.dot` (or the reverse) is a failure.
    * `{DIAGRAM_ATTR}="<slug>"` on the `<svg>` element itself, naming which
      demonstration it draws. (Optional but strongly preferred: without it the
      gate has to infer the mapping from the node set.)

  Draw the pipeline that was actually generated. Do not simplify it, do not
  add a node for visual balance, do not drop an edge that clutters the layout.
  The diagram IS the graph.

  ** MANDATE 4 --- ABSENT DATA GETS AN HONEST NOTE; TESTIMONY IS LABELLED. **
  Where the run data has no value for something the deck's shape wants, SAY SO
  in the deck, visibly --- "this run did not record that" --- and never fill the
  hole with a plausible number, a placeholder, or a rounded guess. And where the
  deck tells a story about something for which no artifact exists in the run
  data --- what a gate report would have said, what a failed draft looked like,
  what happened before the artifact was written --- label it as TESTIMONY
  ("reported, not recovered from a file"), distinctly from anything quoted from
  a real artifact. A reader must always be able to tell which is which.

  ** MANDATE 5 --- EVERY MODAL IS STRUCTURED, NOT A PARAGRAPH DUMP. **
  A modal is where this deck earns the word "deck-grade". Every single
  `<dialog>` in the document --- an opportunity, an honest NO, a waste finding,
  a demonstration, a methodology aside, all of them --- must carry ALL FIVE of
  the following, and `deck verify` counts them:

    1. An `<h3>` title (the one `aria-labelledby` points at), preceded by a
       kicker element carrying `class="{MODAL_KICKER_CLASS}"`.
    2. At least {MODAL_MIN_SUBSECTIONS} `<{MODAL_SUBSECTION_TAG}>` sub-section
       heads inside the modal body. Each names a distinct move --- what it is,
       why it recurs, what changes, what is still unknown --- not "Details" and
       "More details".
    3. At least {MODAL_MIN_EVIDENCE} element carrying
       `class="{MODAL_EVIDENCE_CLASS}"`: the inset that quotes THIS reader's
       own verified data back to them (their counts, their medians, their
       trend, a machine verdict relayed verbatim). Numbers inside it obey
       Mandate 1 like everything else.
    4. One element carrying `class="{MODAL_WHY_CLASS}"` --- why that evidence
       matters for this reader specifically.
    5. One element carrying `class="{MODAL_ENTRY_CLASS}"` --- where they can go
       next: the smallest next step, or what would change the answer, or what
       to stop doing, or how to run it.

  The class name may sit alongside others (`class="inset {MODAL_EVIDENCE_CLASS}"`
  is fine); the gate looks for the token. A dialog missing any of the five
  fails, and the failure names the dialog's id and exactly which parts are
  absent.

  This is a STRUCTURE mandate and deliberately not a length one. There is no
  word count to hit and no reward for padding: a modal passes by having a
  heading, real sub-sections, the reader's own evidence, a reason it matters,
  and a way in. If a finding genuinely does not support five parts, that is
  worth saying inside the modal --- but say it in the parts, not by omitting
  them.
"""


# --------------------------------------------------------------------------
# The brief itself.
# --------------------------------------------------------------------------

_OUTPUT_CONTRACT = """\
## What to write

Write exactly ONE file: `deck.html`, into this same directory.

It is the whole deliverable. There is no companion, no asset folder, no second
file. Write it in full --- do not emit a fragment, a patch, or a description of
what you would write.

ONE FILE, BUILT IN PASSES. A deck of this size is tens of thousands of tokens
of markup, and trying to emit all of it in a single model response reliably hits
a provider request timeout. So build the file up across SEVERAL tool calls:
write the document head, the stylesheet, the shared `<defs>` and the first
section; then append each remaining section, then the dialogs, then the closing
script. Finish every pass with valid, closeable markup so a partial file is
never mistaken for a finished one, and make the LAST pass the one that writes
the closing `</body></html>`. The result is still exactly one file.

When you are done, the orchestrating session runs `deck verify` over it. That
gate is deterministic and it is the only thing that decides whether the deck is
published. It checks, in order: that the HTML parses; that the page is fully
self-contained; that every modal has a trigger and every trigger has a modal;
that every displayed number resolves (Mandate 1/2); that every pipeline diagram
matches its real `.dot` node-for-node and edge-for-edge (Mandate 3); and that
every modal carries the five structural parts (Mandate 5).

Write the modals to the structure contract on the FIRST pass. Retro-fitting
five structural parts into a page of finished paragraphs is far more work than
writing them that way to begin with, and the structural gate is the one most
likely to catch a draft that read fine to its author.
"""


def _fmt_allowed_numbers(allowed: list[str], *, cap: int = 400) -> str:
    """The whitelist, as the author must see it: sorted, capped, honest."""
    if not allowed:
        return "  (none --- this run supplied no numerics, which is itself worth saying out loud)"
    shown = allowed[:cap]
    body = "\n".join("  " + ", ".join(shown[i : i + 12]) for i in range(0, len(shown), 12))
    if len(allowed) > cap:
        body += f"\n  ... and {len(allowed) - cap} more (every numeric in the DATA below is legal)"
    return body


def deck_brief_markdown(
    *,
    run_label: str,
    summary_lines: list[str],
    opportunity_blocks: list[str],
    honest_no_blocks: list[str],
    waste_blocks: list[str],
    demo_blocks: list[str],
    absent_fields: list[str],
    allowed_numbers: list[str],
    deck_filename: str,
) -> str:
    """Assemble the deck brief. Deterministic --- no improvised prompting.

    Every block passed in is already rendered from re-verified data by
    `deck.py`; this function only arranges them. Mined text (unit names, gists)
    is expanded into PROSE here and nowhere else --- nothing in this document
    becomes a command the orchestrating session executes.
    """
    absent_block = (
        "\n".join(f"  - {line}" for line in absent_fields)
        if absent_fields
        else "  (nothing the deck's shape wants is missing from this run)"
    )
    return f"""\
# Deck brief --- build one deck-grade page from this run's verified data

You are building a DECK: one self-contained HTML page that teaches a person what
an attractor pipeline is, shows them where their own recurring work is already
loop-shaped, and is honest about everything it does not know. The reader is the
person whose sessions this run mined. They have never built an attractor
pipeline. They are smart and busy and will not read a wall of text.

You are NOT copying an exemplar. You are learning the house TECHNIQUES stated
below and building FRESH content from the REAL data inlined below.

Run: {run_label}

---

{STYLE_CONTRACT}
---

{HARD_CONSTRAINTS}
---

{MANDATES}
---

## NUMBERS THAT ARE ALREADY LEGAL

Every number below came from this run's re-verified data or from the generated
pipelines themselves. You may state any of them freely. Anything else must be
declared per Mandate 2.

{_fmt_allowed_numbers(allowed_numbers)}

Two things the number scan deliberately does NOT catch, so do not rely on them
as loopholes: spelled-out numerals ("twenty-five") pass unchecked, and a
decimal's sub-runs pass with it. Using either to smuggle in a figure the data
does not support breaks the deck's whole promise, which is that every number on
the page traces back to something that was actually measured.

## WHAT THIS RUN DOES NOT HAVE

Per Mandate 4, these get a visible honest note in the deck --- never a value:

{absent_block}

---

# THE DATA

## Run summary (re-verified)

{chr(10).join("  " + line for line in summary_lines)}

## Opportunities --- units that cleared all three questions

{chr(10).join(opportunity_blocks) if opportunity_blocks else "  (none in this run)"}

## Honest NOes --- recurring, costly, and still not worth automating

{chr(10).join(honest_no_blocks) if honest_no_blocks else "  (none in this run)"}

## Waste findings --- time spent, but not an opportunity to act on

{chr(10).join(waste_blocks) if waste_blocks else "  (none in this run)"}

## Demonstrations --- the pipelines that were generated and machine-gated

{chr(10).join(demo_blocks) if demo_blocks else "  (none in this run --- say so, and skip the demonstrations section)"}

---

{_OUTPUT_CONTRACT}
The published file will be named `{deck_filename}`.
"""


__all__ = [
    "ALLOWED_HTTPS_HREFS",
    "DERIVED_BLOCK_ID",
    "DIAGRAM_ATTR",
    "EDGE_ATTR",
    "FOOTER_LINKS",
    "HARD_CONSTRAINTS",
    "MANDATES",
    "NODE_ATTR",
    "STYLE_CONTRACT",
    "deck_brief_markdown",
]
