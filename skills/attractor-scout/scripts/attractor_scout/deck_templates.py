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

STYLE_CONTRACT = """\
THE HOUSE DECK IDIOM (learn the TECHNIQUES; write FRESH content)

This is a DECK, not a report. A report enumerates; a deck argues. Every section
opens on a claim a reader can disagree with, then pays it off with the run's own
verified evidence. Write for one specific reader --- the person whose work this
run mined --- in second person, in their vocabulary, with no internal jargon and
no meta-commentary about being a language model.

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
     vouch for the pipeline yourself.

  5. HONEST NOES AND WASTE --- the units that recur and cost real effort and are
     still NOT worth automating, each with the sub-test it failed and what would
     change the answer; plus the waste channel (ceremony that cost time but is
     not an opportunity to act on). Give EVERY honest-NO in the data its own
     modal too, the same as the opportunities --- the credibility of this
     section is that it withholds nothing; depth scales with the findings, to no
     fixed quota. This section is the deck's credibility: a deck that only sells
     is an advertisement.

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
THE FOUR MANDATES (machine-enforced by `deck verify`; a violation means the
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
that every displayed number resolves (Mandate 1/2); and that every pipeline
diagram matches its real `.dot` node-for-node and edge-for-edge (Mandate 3).
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
