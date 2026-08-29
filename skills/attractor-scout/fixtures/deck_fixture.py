"""Canned DECK-MODE artifacts — ground truth BY CONSTRUCTION.

CI cannot author a deck (that is one fresh-context `reasoning` delegation), so
what CI proves is *everything around it*: brief assembly and, above all, the
five deterministic gates. All of that needs a candidate deck to act on, and
this module is that candidate — a minimal, clean, self-contained page that
clears every gate, plus one mutation per gate class that breaks exactly one
property.

A gate that has never been shown failing is not a gate. So:

* `broken_nesting()`        — an unclosed container: gate (a) goes red.
* `with_external_resource()`— an `<img>`: gate (b) goes red.
* `with_orphan_dialog()`    — a modal no trigger opens: gate (c) goes red.
* `with_fabricated_number()`— a figure the run never produced: gate (d) red.
* `without_derived_block()` — a real derivation, undeclared: gate (d) red.
* `with_unprovenanced_derivation()` — declared with no `from`: gate (d) red.
* `with_edge_dropped()`     — one back-edge undrawn: gate (e) red.

**ZERO real data.** Every name here is synthetic and marked as such; the unit
name carries the `SYNTHETIC-` marker discipline the corpus fixtures use, and
`tests/test_no_real_data_leak.py` re-checks that claim on every run.
"""

from __future__ import annotations

DECK_UNIT_NAME = "SYNTHETIC-UNIT-K repair the generated report until the check is green"
DECK_UNIT_ID = "k1"
DECK_SLUG = "synthetic-unit-k-k1"

#: 6 nodes, 7 edges — the convergence-factory shape in miniature. The edge
#: multiset is what gate (e) compares, so this is deliberately a shape with
#: two edges INTO the budget wall: dropping either one must be caught.
DECK_DOT = """\
// SYNTHETIC deck-mode demonstration pipeline (test fixture). Not mined from anyone.
digraph SyntheticDeckDemo {
    start [shape=Mdiamond]
    exit  [shape=Msquare]

    worker [shape=box,
        prompt="Advance the repair. Read the last check output, fix exactly what it reports, and change nothing it does not force you to change."]

    verify_gate [shape=parallelogram, max_retries=0,
        tool_command="pytest -q"]

    budget_wall [shape=parallelogram, max_retries=0,
        tool_command="n=$(cat .k/iter 2>/dev/null || echo 0); n=$((n+1)); echo $n > .k/iter; max_attempts=3; if [ $n -ge $max_attempts ]; then echo budget_exhausted; else echo under_budget; fi"]

    loud_fail [shape=parallelogram, max_retries=0,
        tool_command="echo 'NOT CONVERGED: the check never went green' >&2; exit 1"]

    start -> worker
    worker -> verify_gate [condition="outcome=success"]
    worker -> budget_wall [condition="outcome=fail"]
    verify_gate -> exit [label="pass", condition="outcome=success"]
    verify_gate -> budget_wall [label="fail", condition="outcome=fail"]
    budget_wall -> worker [label="under_budget", condition="context.tool.last_line=under_budget && outcome=success"]
    budget_wall -> loud_fail [label="budget_exhausted", condition="context.tool.last_line=budget_exhausted && outcome=success"]
}
"""

#: Every node id in DECK_DOT, in draw order.
DECK_NODES = ("start", "worker", "verify_gate", "exit", "budget_wall", "loud_fail")

#: Every edge in DECK_DOT, as the deck's `data-edge` attributes spell them.
DECK_EDGES = (
    "start->worker",
    "worker->verify_gate",
    "worker->budget_wall",
    "verify_gate->exit",
    "verify_gate->budget_wall",
    "budget_wall->worker",
    "budget_wall->loud_fail",
)


def deck_ranked_fixture() -> dict:
    """A minimal `ranked.json`: one opportunity, one honest-NO, one waste find."""
    return {
        "opportunities": [
            {
                "unit_id": DECK_UNIT_ID,
                "name": DECK_UNIT_NAME,
                "n_sessions": 7,
                "leverage": 32.5,
                "fit": 1,
                "score": 22.75,
                "author": "human",
                "verdict": "OPPORTUNITY",
                "recovery": "PASS",
                "confidence": "high",
                "provisional": False,
                "trajectory": "escalating",
                "gist": "SYNTHETIC scenario: run the check, fix what it reports, run it again.",
                "leverage_detail": {
                    "n_sessions": 7,
                    "med_tool_calls": 12.0,
                    "med_llm_cycles": 4.0,
                    "med_span_capped_s": 930.0,
                    "errors_per_session": 0.33,
                },
                "fit_detail": {"cycle": True, "gate": True},
            }
        ],
        "honest_no": [
            {
                "unit_id": "k2",
                "name": "SYNTHETIC-UNIT-L draft the weekly summary",
                "n_sessions": 5,
                "leverage": 9.0,
                "verdict": "HONEST-NO",
                "no_class": "recipe",
                "failed_subtest": "4a",
                "remediation": "This runs straight through. A script, or just doing it, beats a pipeline here.",
                "gist": "SYNTHETIC scenario: assemble the same summary from the same places each week.",
            }
        ],
        "waste_findings": [
            {
                "unit_id": "k3",
                "name": "SYNTHETIC-UNIT-M trivial liveness probes",
                "author": "harness",
                "n_sessions": 11,
                "leverage": 1.25,
                "reclaimable_hours": 1.5,
                "note": "harness ceremony - a waste finding to eliminate, not an opportunity to act on",
            }
        ],
        "below_frequency_floor": [],
        "summary": {
            "n_units_in": 3,
            "n_admitted": 2,
            "n_waste": 1,
            "n_opportunities": 1,
            "n_honest_no": 1,
            "n_below_floor": 0,
            "honest_no_rate": 0.5,
        },
    }


def deck_demos_fixture() -> dict:
    """A minimal `demos.json` carrying the fixture pipeline verbatim."""
    return {
        "primer": True,
        "explainer_url": "https://microsoft.github.io/amplifier-bundle-attractor/attractor-explained.html",
        "demos": [
            {
                "unit_id": DECK_UNIT_ID,
                "name": DECK_UNIT_NAME,
                "slug": DECK_SLUG,
                "dot_relpath": f"attractor-scout-demos/{DECK_SLUG}.dot",
                "companion_relpath": f"attractor-scout-demos/{DECK_SLUG}.md",
                "dot_text": DECK_DOT,
                "stats": {
                    "n_sessions": 7,
                    "med_tool_calls": 12.0,
                    "med_llm_cycles": 4.0,
                    "med_span_s": 930.0,
                    "err_rate": 0.33,
                    "provisional": False,
                },
                "fit": {"cycle": True, "gate": True, "recovery": "PASS", "verdict": "OPPORTUNITY"},
                "narrative": {
                    "scenario_gist": "You run the check, fix what it reports, and run it again.",
                    "q1_cycle_note": "Yes: attempted, checked, re-attempted.",
                    "q2_gate_note": "Yes: the check is red before and green after.",
                    "q3_recovery_note": "A bad attempt is caught by the gate and paid for by the budget.",
                    "pipeline_walk": [
                        {"node": "worker", "note": "the worker: reads the last failure and fixes exactly that."},
                        {"node": "verify_gate", "note": "the evidence gate: runs the real check."},
                    ],
                    "payoff_note": "The loop stops on evidence you already trust.",
                },
                "convergence_math": {
                    "chain_len": 4,
                    "p_step": 0.9,
                    "once_through": 0.6561,
                    "gated_loop": 0.8817,
                    "budget": 2,
                    "label": "illustrative arithmetic - not a measurement of your sessions",
                },
                "verification": {
                    "level": "doctrine-only",
                    "lint_verdict": None,
                    "lint_not_run_reason": "dot-runner lint: NOT RUN - the CLI is not installed here.",
                    "doctrine_verdict": "doctrine_ok",
                    "doctrine_report": "AUTHORED-PIPELINE DOCTRINE REPORT\nverdict:   doctrine_ok\n",
                },
                "invocation": {
                    "run_cmd": f"dot-runner run attractor-scout-demos/{DECK_SLUG}.dot --cwd .",
                    "author_cmd": "dot-runner run examples/authoring/pipeline-author.dot --cwd .",
                    "install_cmd": "uv tool install git+https://github.com/microsoft/amplifier-bundle-dot-runner@main",
                },
                "generated_at": "2026-08-19T00:00:00+00:00",
            }
        ],
    }


# --------------------------------------------------------------------------
# The clean deck.
# --------------------------------------------------------------------------

_NODE_ROWS = "\n".join(
    f'      <g {"data-node"}="{node}"><rect class="n" x="{20 + 130 * i}" y="40" width="110" height="46" rx="6"/>'
    f'<text class="nl" x="{75 + 130 * i}" y="68" text-anchor="middle">{node}</text></g>'
    for i, node in enumerate(DECK_NODES)
)

_EDGE_ROWS = "\n".join(
    f'      <line class="e" data-edge="{edge.replace("->", "-&gt;")}" '
    f'x1="{20 + 20 * i}" y1="110" x2="{60 + 20 * i}" y2="110" marker-end="url(#ar)"/>'
    for i, edge in enumerate(DECK_EDGES)
)

CLEAN_DECK = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Your work, mapped &mdash; a synthetic deck</title>
<meta name="description" content="A synthetic deck-mode fixture: one opportunity, one honest NO, one waste finding.">
<style>
  :root {{ --ink:#eef2f7; --bg:#0b0e13; --accent:#ffa62b; --pass:#45d483; }}
  body {{ background:var(--bg); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif; }}
  .n {{ fill:#151b23; stroke:#3a4855; }}
  .e {{ stroke:#3a4855; }}
  .hot:focus-visible {{ outline:2px solid var(--accent); }}
  @media (prefers-reduced-motion: reduce) {{ * {{ animation:none !important; transition:none !important; }} }}
</style>
</head>
<body>
<svg aria-hidden="true" focusable="false" style="position:absolute;width:0;height:0;overflow:hidden">
  <defs>
    <marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6"
      orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="#3a4855"/></marker>
  </defs>
</svg>

<main>
<section id="map">
  <h1>Your work, mapped.</h1>
  <p>Something on this machine read your own session history and grouped it into recurring units of
  work. Nothing left the machine.</p>
  <p>One unit came back loop-shaped. It shows up in seven distinct sessions, and each one costs a
  median of twelve tool calls before it lands.</p>
  <p>Across the opportunity and the waste ledger together, eighteen sessions are accounted for.</p>
</section>

<section id="shape">
  <h2>An attractor is a basin, not a checklist.</h2>
  <p>Work that is attempted, checked against evidence produced outside the worker, and re-attempted
  until it lands &mdash; under a budget that routes exhaustion somewhere honest.</p>
  <p>The three-question test: is there a cycle? is the exit gated on machine-checkable evidence?
  would it survive one node having a bad day?</p>
  <p>Illustrative arithmetic, not a measurement of your sessions: a four-step chain that has to get
  every step right once has worse odds than the same chain inside a gated loop with a budget.</p>
</section>

<section id="opportunities">
  <h2>One piece of your work is already loop-shaped.</h2>
  <p class="hot" role="button" tabindex="0" data-modal="m-unit">The repair loop &mdash; seven
  sessions, twelve median tool calls. Open detail.</p>
  <p>This run recorded no project attribution for that unit, so this page does not name one.</p>
</section>

<section id="demos">
  <h2>One of them was built, not just recommended.</h2>
  <svg class="dg" viewBox="0 0 900 200" role="img" aria-labelledby="d1T d1D" data-diagram="{DECK_SLUG}">
      <title id="d1T">The generated repair pipeline: six nodes and seven edges</title>
      <desc id="d1D">Start leads to the worker. The worker leads to the verify gate on success and
      to the budget wall on failure. The gate passes to the exit or fails into the budget wall. The
      budget wall either routes back to the worker or out to a loud failure that is not an exit
      node.</desc>
{_NODE_ROWS}
{_EDGE_ROWS}
  </svg>
  <p>Six nodes, seven edges, one door. The authoring contract came back clean; the engine's own
  linter did not run here, and this page does not pretend it did.</p>
</section>

<section id="honest">
  <h2>One recurring habit that is still not worth automating.</h2>
  <p>The weekly summary runs straight through. That is a recipe, not an attractor, and the honest
  answer is to keep doing it by hand or write a script.</p>
  <p>Separately, the waste ledger holds harness ceremony worth about an hour and a half.</p>
</section>

<section id="forward">
  <h2>Four doors, smallest commitment first.</h2>
  <p>Read the explainer. Try the conversational designer on one piece of work. Copy the canonical
  skeleton. Run the authoring pipeline under executed gates.</p>
</section>
</main>

<footer>
  <p>The full explainer:
  <a href="https://microsoft.github.io/amplifier-bundle-attractor/attractor-explained.html">attractor, explained</a></p>
  <p>The bundle:
  <a href="https://github.com/microsoft/amplifier-bundle-attractor">the bundle repository</a></p>
</footer>

<dialog id="m-unit" aria-labelledby="h-unit">
  <div class="mbox" tabindex="-1">
    <div class="mhead">
      <div class="mh-b">
        <p class="m-kick">The strongest fit</p>
        <h3 id="h-unit">The repair loop</h3>
      </div>
      <button class="m-close" type="button" aria-label="Close">Close</button>
    </div>
    <div class="mbody">
      <p>The one unit in this run that cleared all three questions.</p>
      <h4>What it is</h4>
      <p>A generated report comes back wrong, you repair it, and you run the check again until the
      check stops complaining.</p>
      <div class="evidence">
        <span class="lbl">Your own numbers</span>
        <p>Seven distinct sessions. A median of twelve tool calls and four LLM cycles per session,
        over a median span of nine hundred and thirty seconds, with about a third of an error per
        session.</p>
      </div>
      <h4>Why it recurs</h4>
      <p class="why">Every one of those sessions ended when a check you already run agreed with
      you. That agreement is the evidence gate; nothing about it needs a person.</p>
      <p class="entry">Smallest next step: name the command you already run at the end, and let a
      loop run it instead.</p>
    </div>
  </div>
</dialog>

<script type="application/json" id="derived-values">
[
  {{"value": "18",
   "from": "7 sessions inside the one opportunity + 11 in the waste ledger",
   "inputs": ["7", "11"]}}
]
</script>

<script>
(function(){{
  "use strict";
  var triggers = document.querySelectorAll("[data-modal]");
  Array.prototype.forEach.call(triggers, function(el){{
    el.addEventListener("click", function(){{
      var d = document.getElementById(el.getAttribute("data-modal"));
      if (d && typeof d.showModal === "function") {{ d.showModal(); }}
    }});
  }});
}})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# The mutations — one broken property each.
# --------------------------------------------------------------------------


def clean_deck() -> str:
    return CLEAN_DECK


def broken_nesting() -> str:
    """Gate (a): a container element that is never closed."""
    return CLEAN_DECK.replace('</section>\n\n<section id="shape">', '\n\n<section id="shape">', 1)


def with_external_resource() -> str:
    """Gate (b): a resource the page would have to fetch."""
    return CLEAN_DECK.replace(
        "<h1>Your work, mapped.</h1>",
        '<h1>Your work, mapped.</h1>\n  <img src="chart.png" alt="a chart">',
        1,
    )


def with_orphan_dialog() -> str:
    """Gate (c): a modal no trigger can open."""
    return CLEAN_DECK.replace(
        '<script type="application/json" id="derived-values">',
        '<dialog id="m-orphan"><div class="mbox"><h3>Unreachable</h3></div></dialog>\n\n'
        '<script type="application/json" id="derived-values">',
        1,
    )


#: The conforming modal body from CLEAN_DECK, sliced out once so the hollow
#: mutation below can replace exactly it and nothing else.
_CONFORMING_MBODY_START = '    <div class="mbody">'
_CONFORMING_MBODY_END = "    </div>\n  </div>\n</dialog>"

#: A modal that says a true thing in two flat paragraphs and carries not one
#: of the five mandated structural parts. This is the shape gate (f) exists to
#: catch: readable, honest, and hollow.
_HOLLOW_MBODY = """\
    <div class="mbody">
      <p>The repair loop shows up in seven distinct sessions.</p>
      <p>It is worth automating.</p>
"""


def with_hollow_modal() -> str:
    """Gate (f): a modal with a title but none of the mandated structure.

    Everything else about the deck is unchanged --- it still parses, is still
    self-contained, its numbers still resolve. Only the modal's INSIDE is
    gutted, which is precisely the failure a length check would miss and a
    structure check catches.
    """
    start = CLEAN_DECK.index(_CONFORMING_MBODY_START)
    end = CLEAN_DECK.index(_CONFORMING_MBODY_END, start)
    return CLEAN_DECK[:start] + _HOLLOW_MBODY + CLEAN_DECK[end:]


def with_modal_missing_evidence() -> str:
    """Gate (f): every part present EXCEPT the reader's own data, quoted.

    The narrow case: a modal that has a heading, sub-sections, a why and an
    entry point, and still never shows the reader a number of their own. The
    gate must name the missing evidence block specifically, not just fail.
    """
    return CLEAN_DECK.replace('<div class="evidence">', '<div class="aside">', 1)


def with_modal_one_subsection() -> str:
    """Gate (f): one sub-section where the contract mandates two."""
    return CLEAN_DECK.replace("<h4>Why it recurs</h4>", "<p><strong>Why it recurs</strong></p>", 1)


def with_fabricated_number() -> str:
    """Gate (d): a figure this run never produced, stated as fact."""
    return CLEAN_DECK.replace(
        "One unit came back loop-shaped.",
        "One unit came back loop-shaped, out of 4812 units examined.",
        1,
    )


def without_derived_block() -> str:
    """Gate (d): the deck does real arithmetic and never declares it."""
    start = CLEAN_DECK.index('<script type="application/json" id="derived-values">')
    end = CLEAN_DECK.index("</script>", start) + len("</script>")
    stripped = CLEAN_DECK[:start] + CLEAN_DECK[end:]
    # State the derived total in digits, so the scan has something to catch.
    return stripped.replace("eighteen sessions are accounted for", "18 sessions are accounted for", 1)


def with_unprovenanced_derivation() -> str:
    """Gate (d): declared, but with no arithmetic provenance."""
    return CLEAN_DECK.replace(
        '"from": "7 sessions inside the one opportunity + 11 in the waste ledger",',
        '"from": "",',
        1,
    )


def with_edge_dropped() -> str:
    """Gate (e): the corrective back-edge is simply not drawn."""
    dropped = DECK_EDGES[-2].replace("->", "-&gt;")
    lines = [line for line in CLEAN_DECK.splitlines(keepends=True) if f'data-edge="{dropped}"' not in line]
    return "".join(lines)


def with_node_added() -> str:
    """Gate (e): a node drawn for visual balance that the pipeline never had."""
    return CLEAN_DECK.replace(
        '<g data-node="start">',
        '<g data-node="tidy_up"><rect class="n" x="20" y="150" width="80" height="30" rx="6"/></g>\n'
        '      <g data-node="start">',
        1,
    )


# ---- FIX 1: derived-values self-dealing (inputs now mandatory) -------------


def with_inputless_derivation() -> str:
    """Gate (d): the reviewer's exact probe --- a fabricated value with a junk
    one-word `from` and NO `inputs`. Used to PASS; must now be RED."""
    body = CLEAN_DECK.replace(
        "One unit came back loop-shaped.",
        "One unit came back loop-shaped, out of 8731 examined.",
        1,
    )
    return body.replace(
        '[\n  {"value": "18",\n   "from": "7 sessions inside the one opportunity + 11 in the waste ledger",\n   "inputs": ["7", "11"]}\n]',
        '[\n  {"value": "8731", "from": "qqq"}\n]',
        1,
    )


def with_empty_inputs_derivation() -> str:
    """Gate (d): inputs present but empty --- still a bare assertion, RED."""
    return CLEAN_DECK.replace('"inputs": ["7", "11"]', '"inputs": []', 1)


def with_from_not_referencing_inputs() -> str:
    """Gate (d): real inputs, but a `from` that references neither --- RED."""
    return CLEAN_DECK.replace(
        '"from": "7 sessions inside the one opportunity + 11 in the waste ledger",',
        '"from": "a total of some sessions",',
        1,
    )


def with_wrong_total_but_real_inputs() -> str:
    """Gate (d) NAMED LIMIT (expected PASS): real inputs, `from` references them,
    but the arithmetic is wrong (7 + 11 != 900). The gate does not recompute, so
    this passes --- the declarative-provenance residual, kept on purpose."""
    body = CLEAN_DECK.replace(
        "eighteen sessions are accounted for",
        "900 sessions are accounted for",
        1,
    )
    return body.replace(
        '{"value": "18",\n   "from": "7 sessions inside the one opportunity + 11 in the waste ledger",\n   "inputs": ["7", "11"]}',
        '{"value": "900",\n   "from": "7 sessions plus 11 in the waste ledger",\n   "inputs": ["7", "11"]}',
        1,
    )


# ---- FIX 2: gate (b) blocklist holes --------------------------------------


def _inject_after_h1(snippet: str) -> str:
    return CLEAN_DECK.replace("<h1>Your work, mapped.</h1>", "<h1>Your work, mapped.</h1>\n  " + snippet, 1)


def with_object_data() -> str:
    """Gate (b): <object data="https://..."> loads an external resource."""
    return _inject_after_h1('<object data="https://example.invalid/x.pdf" type="application/pdf"></object>')


def with_xlink_href() -> str:
    """Gate (b): SVG xlink:href to an external image (the `\\shref` count is blind to it)."""
    return _inject_after_h1('<svg><image xlink:href="https://example.invalid/x.png" width="10" height="10"/></svg>')


def with_media_source() -> str:
    """Gate (b): <video><source src="https://..."> --- both the element and the src."""
    return _inject_after_h1('<video controls><source src="https://example.invalid/x.mp4" type="video/mp4"></video>')


def with_embed() -> str:
    """Gate (b): <embed src="https://...">."""
    return _inject_after_h1('<embed src="https://example.invalid/x.swf" type="application/x-shockwave-flash">')


def with_track() -> str:
    """Gate (b): <track src="https://...">."""
    return _inject_after_h1('<track src="https://example.invalid/subs.vtt" kind="subtitles">')


def with_use_href() -> str:
    """Gate (b): SVG <use href="https://..."> pointing at an external document."""
    return _inject_after_h1('<svg><use href="https://example.invalid/icons.svg#gear"/></svg>')


def with_local_use_href() -> str:
    """Gate (b) expected PASS: <use href="#local"> is a legal same-document reference."""
    return _inject_after_h1('<svg><use href="#ar"/></svg>')


# ---- FIX 3: gate (d) blind to script-injected text ------------------------


def with_script_injected_number() -> str:
    """Gate (d): a fabricated number injected by JS into a displayed string.
    Never appears as a DOM text node; must be caught in the script literal."""
    return CLEAN_DECK.replace(
        '  "use strict";',
        '  "use strict";\n  document.title = "8731 units examined";',
        1,
    )


def with_script_geometry_string() -> str:
    """Gate (d) NAMED LIMIT (expected PASS): a CSS/geometry string literal with
    digits but no prose word (like the exemplar's IntersectionObserver rootMargin)
    is not treated as a displayed claim."""
    return CLEAN_DECK.replace(
        '  "use strict";',
        '  "use strict";\n  var rootMargin = "-45% 0px -50% 0px";',
        1,
    )


def with_script_concatenated_number() -> str:
    """Gate (d) NAMED LIMIT (expected PASS): a number built by concatenation from
    a bare numeric literal (not inside a string) is not a literal and is not seen."""
    return CLEAN_DECK.replace(
        '  "use strict";',
        '  "use strict";\n  var label = "" + 8731 + " units examined";',
        1,
    )


__all__ = [
    "CLEAN_DECK",
    "DECK_DOT",
    "DECK_EDGES",
    "DECK_NODES",
    "DECK_SLUG",
    "DECK_UNIT_ID",
    "DECK_UNIT_NAME",
    "broken_nesting",
    "clean_deck",
    "deck_demos_fixture",
    "deck_ranked_fixture",
    "with_edge_dropped",
    "with_embed",
    "with_empty_inputs_derivation",
    "with_external_resource",
    "with_fabricated_number",
    "with_from_not_referencing_inputs",
    "with_hollow_modal",
    "with_inputless_derivation",
    "with_local_use_href",
    "with_media_source",
    "with_modal_missing_evidence",
    "with_modal_one_subsection",
    "with_node_added",
    "with_object_data",
    "with_orphan_dialog",
    "with_script_concatenated_number",
    "with_script_geometry_string",
    "with_script_injected_number",
    "with_track",
    "with_unprovenanced_derivation",
    "with_use_href",
    "with_wrong_total_but_real_inputs",
    "with_xlink_href",
    "without_derived_block",
]
