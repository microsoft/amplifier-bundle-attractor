"""DECK MODE: brief assembly, and the deterministic gates a deck must clear.

The map (step 7) and the demonstrations (steps 8-9) are produced by a
deterministic renderer over re-verified data, so their trust story is
structural: no language model ever places a number. Deck mode gives that up on
purpose --- a deck-grade page is *authored*, by a fresh-context delegate, in one
file, with its own layout and its own prose --- and buys the trust back with
gates instead.

That is the whole design: **what the renderer guaranteed by construction, the
gates here guarantee by inspection.** Six of them, all deterministic, all
stdlib, run over the candidate deck plus the run's own data:

  a. **It parses.** Container elements nest and close.
  b. **It is self-contained.** No `<img>`/`<link>`/`<script src>`/`@import`/
     `<iframe>`/`srcset`; `url(` only ever as `url(#...)`; exactly two https
     hrefs; zero `file://`. A page that fetches is a page that can break, leak
     a referrer, or render differently tomorrow.
  c. **Every modal has a trigger, every trigger has a modal.** An orphan
     dialog is dead weight the reader can never reach; a trigger pointing at
     nothing is a button that lies.
  d. **Every displayed number resolves.** ★ The load-bearing gate. Each numeric
     token in the deck's visible text must resolve against a whitelist built
     from the run data, the generated pipelines' own numerics, and the machine
     gate reports --- or be declared, with arithmetic provenance, in the deck's
     own `derived-values` JSON block. An unresolvable token fails the deck and
     names itself.
  e. **Every pipeline diagram is the real pipeline.** Node set and edge
     MULTISET, read off `data-node`/`data-edge` attributes, must equal the
     `.dot`'s. A diagram that quietly drops the awkward back-edge is a diagram
     that teaches the wrong shape.
  f. **Every modal carries the mandated structure.** A deck's depth lives in
     its modals, so a hollow modal is a hollow deck. Each `<dialog>` must
     carry a title and kicker, at least two `<h4>` sub-sections, an
     `evidence` inset quoting the reader's own verified data, a `why`, and an
     `entry` point --- names the style contract mandates and this gate counts.
     It is a STRUCTURE check, never a length check: padding buys nothing, and
     a genuinely short modal that has the parts passes.

`verify_deck` returns a report; the CLI exits 0 only when every gate passed.
**A gate-failed deck is never published** --- the same posture as
`demo assemble`'s red verdict, for the same reason.

Gate (d) scans DOM text nodes, the `<meta name="description">` content, AND
prose-bearing string literals in every executable inline `<script>` body (so a
number injected by JS, e.g. `document.title = "8731 units examined"`, cannot
slip past a scan of the static DOM). Three named limits, documented rather than
closed, each with an expected-pass regression test in
``tests/test_scenario9_deck_gates.py`` so a future "I fixed it" is forced to
update this docstring honestly:

* **Spelled-out numbers.** "twenty-five noes" is not a numeric token and
  passes. Detecting written numerals is NLP guesswork, and a fail-loud guard
  that guessed wrong would block honest prose --- the worse failure for a trust
  surface whose whole value is not crying wolf. (The brief tells the author
  this in as many words, and tells them not to use it as a loophole.)
* **Decomposition.** A whitelisted ``397.5`` also whitelists the bare runs
  ``397`` and ``5``, because a rendered stat's own sub-runs are how legitimate
  prose refers to it ("397 hours and change"). Closing it would ban the
  honest reference; ``398`` is still rejected, so the leak is bounded to runs a
  supplied number literally contains. On real data this is not a rare edge:
  0--9 are all free (structural), roughly 94% of 10--99 and only ~14% of
  100--999 resolve from a typical run's supplied set, so a fabricated
  two-digit figure is far likelier to ride a decomposition than a three-digit
  one --- the design doc (§3.2) states the measured figures.
* **Computed / concatenated script strings.** The script scan reads string
  LITERALS only. A number assembled at runtime (`"" + n + " units"`), or
  written as a bare numeric literal in code rather than inside a string, is not
  a literal and is not seen. And only PROSE-bearing literals (a 3+ letter word)
  are checked, so a CSS/geometry string (``"-45% 0px -50% 0px"``) is not
  treated as a displayed claim --- which is exactly the false positive that
  scanning every literal would produce, and the reason the recovered exemplar's
  own IntersectionObserver `rootMargin` does not trip the gate.

A scope boundary, stated so it cannot quietly widen: numbers inside non-text
attribute values (`aria-label`, `id`, `viewBox`, path geometry) are NOT
scanned --- SVG coordinates are numbers by the thousand and whitelisting them
would gut the gate, while flagging them would make it useless. The deck's
*visible* claims are what the reader trusts, and those are what is checked.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from . import deck_templates as DT
from .errors import AttractorScoutError
from .naming import DECK_FILENAME

# --------------------------------------------------------------------------
# Constants.
# --------------------------------------------------------------------------

#: One authoring delegation, plus at most one gate-informed retry. Never more.
DECK_MAX_ATTEMPTS = 2

#: Small integers the deck may always state: Q1/Q2/Q3, the 4a/4b/4c sub-tests,
#: and ordinary structural counts of the test itself. They name structure, not
#: measurements --- the same allowance `demo.validate_narrative` makes.
STRUCTURAL_DIGITS = frozenset({"0", "1", "2", "3", "4"})

#: String-valued fields whose digit runs are legitimately quotable: machine
#: output the deck relays verbatim, identifiers the run itself assigned, and
#: the pipeline text. Numbers inside these are supplied data, not invention.
NUMERIC_STRING_FIELDS = frozenset(
    {
        "author_cmd",
        "companion_relpath",
        "doctrine_report",
        "dot_relpath",
        "dot_text",
        "generated_at",
        "install_cmd",
        "level",
        "lint_not_run_reason",
        "lint_verdict",
        "run_cmd",
        "slug",
        "unit_id",
    }
)

#: Container elements whose nesting gate (a) enforces. Deliberately excludes
#: the implicit-close family (`p`, `li`, `td`, ...) and SVG leaf shapes, which
#: are legally written unclosed or self-closed and would produce noise, not
#: signal.
BALANCED_TAGS = frozenset(
    {
        "html",
        "head",
        "body",
        "style",
        "script",
        "main",
        "section",
        "article",
        "aside",
        "nav",
        "header",
        "footer",
        "dialog",
        "div",
        "svg",
        "g",
        "defs",
        "marker",
        "table",
        "ul",
        "ol",
        "figure",
        "button",
        "pre",
        "blockquote",
        "details",
        "form",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)

VOID_TAGS = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
)

#: A numeric token: an optionally thousands-grouped integer with an optional
#: decimal tail. Grouping must be exact 3-digit runs, so "sections 1,2 and 3"
#: reads as three tokens rather than one bogus 12.
_NUMBER_TOKEN_RE = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")
_DIGIT_RUN_RE = re.compile(r"\d+")

_HTTPS_HREF_RE = re.compile(r"""\shref\s*=\s*["'](https://[^"']*)["']""", re.IGNORECASE)
_URL_FUNC_RE = re.compile(r"url\(\s*([^)]*)\)", re.IGNORECASE)

#: Any start tag carrying a `src=` attribute. A self-contained one-file deck
#: loads nothing, so ANY `src=` is a violation --- this catches script/img/
#: iframe/source/embed/track/video/audio/frame in one sweep.
_SRC_ATTR_RE = re.compile(r"<[a-zA-Z][^>]*\bsrc\s*=", re.IGNORECASE)

#: `xlink:href` (the legacy SVG resource attribute) --- the plain `\shref`
#: count below cannot see it, because the colon breaks the whitespace boundary.
_XLINK_HREF_RE = re.compile(r"""xlink:href\s*=\s*["']([^"']*)["']""", re.IGNORECASE)

#: External `href` on the SVG resource elements `<use>` / `<image>` (a local
#: `#fragment` is legal; anything else pulls in an external document).
_USE_HREF_RE = re.compile(r"""<(?:use|image)\b[^>]*?\shref\s*=\s*["']([^"']*)["']""", re.IGNORECASE)

#: A JS string literal --- double, single, or backtick quoted.
_JS_STRING_RE = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`', re.DOTALL)

#: A run of 3+ ASCII letters --- the mark of PROSE inside a string literal.
#: Only prose-bearing script strings are number-checked, so a bare CSS/geometry
#: string (``"-45% 0px -50% 0px"``, ``"0 0 900 340"``) is not treated as a
#: displayed claim; a displayed claim (``"8731 units examined"``) always is.
_PROSE_WORD_RE = re.compile(r"[A-Za-z]{3,}")


class DeckGateRed(AttractorScoutError):
    """A deterministic deck gate returned a red verdict --- do not publish.

    Distinct from every other fail-loud condition here: the inputs were fine
    and the tooling worked. The authored deck is what failed, and the honest
    response is to re-delegate once with the gate report attached, then stop.
    """


# --------------------------------------------------------------------------
# Deck document parsing.
# --------------------------------------------------------------------------


@dataclass
class DeckSvg:
    """One inline SVG, with the fidelity attributes it declared."""

    diagram: str | None = None
    nodes: list[str] = field(default_factory=list)
    edges: list[str] = field(default_factory=list)


@dataclass
class DeckDialog:
    """One `<dialog>`, counted against the modal-structure contract.

    Every field is a COUNT of a structural part the style contract mandates by
    name (see `deck_templates`'s Mandate 5). Counting, rather than measuring
    length, is the whole design: a modal cannot pad its way past this.
    """

    dialog_id: str
    titles: int = 0  # <h3> --- the modal title
    kickers: int = 0  # class="m-kick" --- the label above it
    subsections: int = 0  # <h4> --- the sub-section spine
    evidence: int = 0  # class="evidence" --- the reader's own data, quoted
    why: int = 0  # class="why" --- why it matters for them
    entry: int = 0  # class="entry" --- where they can go next

    def missing(self) -> list[str]:
        """The mandated parts this dialog does not have. Empty == conforming."""
        gaps: list[str] = []
        if self.titles < 1:
            gaps.append("no <h3> title")
        if self.kickers < 1:
            gaps.append(f"no class={DT.MODAL_KICKER_CLASS!r} kicker")
        if self.subsections < DT.MODAL_MIN_SUBSECTIONS:
            gaps.append(
                f"{self.subsections} <{DT.MODAL_SUBSECTION_TAG}> sub-section(s); {DT.MODAL_MIN_SUBSECTIONS} required"
            )
        if self.evidence < DT.MODAL_MIN_EVIDENCE:
            gaps.append(f"{self.evidence} class={DT.MODAL_EVIDENCE_CLASS!r} block(s); {DT.MODAL_MIN_EVIDENCE} required")
        if self.why < 1:
            gaps.append(f"no class={DT.MODAL_WHY_CLASS!r} why-it-matters")
        if self.entry < 1:
            gaps.append(f"no class={DT.MODAL_ENTRY_CLASS!r} entry point")
        return gaps


class _DeckParser(HTMLParser):
    """Structural read of a candidate deck. Never raises on content."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, int]] = []
        self.nesting_errors: list[str] = []
        self.text_parts: list[str] = []
        self.dialog_ids: list[str] = []
        self.trigger_targets: list[str] = []
        self.dialogs: list[DeckDialog] = []
        self.svgs: list[DeckSvg] = []
        self.json_blocks: dict[str, list[str]] = {}
        self.script_bodies: list[str] = []
        self.meta_description: str | None = None
        self._svg_stack: list[DeckSvg] = []
        self._dialog_stack: list[DeckDialog] = []
        self._suppress_text = 0
        self._json_block_id: str | None = None
        self._capturing_js = False
        self._js_buf: list[str] = []

    # -- helpers ---------------------------------------------------------
    def _start(self, tag: str, attrs: list[tuple[str, str | None]], *, self_closing: bool) -> None:
        adict = {k.lower(): (v if v is not None else "") for k, v in attrs}
        if tag == "svg":
            svg = DeckSvg(diagram=adict.get(DT.DIAGRAM_ATTR) or None)
            self.svgs.append(svg)
            self._svg_stack.append(svg)
        if self._svg_stack:
            current = self._svg_stack[-1]
            if DT.NODE_ATTR in adict:
                current.nodes.append(adict[DT.NODE_ATTR])
            if DT.EDGE_ATTR in adict:
                current.edges.append(adict[DT.EDGE_ATTR])
        if tag == "dialog":
            if adict.get("id"):
                self.dialog_ids.append(adict["id"])
            record = DeckDialog(dialog_id=adict.get("id") or "(unnamed dialog)")
            self.dialogs.append(record)
            self._dialog_stack.append(record)
        elif self._dialog_stack:
            # Structural parts of the modal-depth contract, counted by NAME
            # against the constants the brief also quotes. A class token may
            # sit alongside others, so the class attribute is split, never
            # substring-matched --- `class="whyever"` must not count as `why`.
            current = self._dialog_stack[-1]
            if tag == "h3":
                current.titles += 1
            elif tag == DT.MODAL_SUBSECTION_TAG:
                current.subsections += 1
            tokens = set(adict.get("class", "").split())
            if DT.MODAL_KICKER_CLASS in tokens:
                current.kickers += 1
            if DT.MODAL_EVIDENCE_CLASS in tokens:
                current.evidence += 1
            if DT.MODAL_WHY_CLASS in tokens:
                current.why += 1
            if DT.MODAL_ENTRY_CLASS in tokens:
                current.entry += 1
        if adict.get("data-modal"):
            self.trigger_targets.append(adict["data-modal"])
        if tag == "meta" and adict.get("name", "").lower() == "description":
            self.meta_description = adict.get("content", "")
        if tag in ("style", "script"):
            self._suppress_text += 1
            if tag == "script":
                if adict.get("id"):
                    self._json_block_id = adict["id"]
                # Capture EXECUTABLE (non-JSON, non-external) script bodies so
                # their string literals can be number-checked (gate d). The
                # derived-values block is `type="application/json"`, so it is
                # NOT captured here --- its numbers are read as declarations.
                stype = adict.get("type", "").strip().lower()
                if not adict.get("src") and "json" not in stype:
                    self._capturing_js = True
        if self_closing or tag in VOID_TAGS:
            if tag == "svg" and self._svg_stack:
                self._svg_stack.pop()
            if tag in ("style", "script"):
                self._suppress_text = max(0, self._suppress_text - 1)
                self._json_block_id = None
                self._capturing_js = False
            return
        if tag in BALANCED_TAGS:
            self.stack.append((tag, self.getpos()[0]))

    # -- HTMLParser hooks ------------------------------------------------
    def handle_starttag(self, tag, attrs):
        self._start(tag.lower(), attrs, self_closing=False)

    def handle_startendtag(self, tag, attrs):
        self._start(tag.lower(), attrs, self_closing=True)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "svg" and self._svg_stack:
            self._svg_stack.pop()
        if tag == "dialog" and self._dialog_stack:
            self._dialog_stack.pop()
        if tag in ("style", "script"):
            self._suppress_text = max(0, self._suppress_text - 1)
            self._json_block_id = None
            if tag == "script" and self._capturing_js:
                self.script_bodies.append("".join(self._js_buf))
                self._js_buf = []
                self._capturing_js = False
        if tag not in BALANCED_TAGS:
            return
        if not self.stack:
            self.nesting_errors.append(f"line {self.getpos()[0]}: </{tag}> with nothing open")
            return
        if self.stack[-1][0] == tag:
            self.stack.pop()
            return
        for depth in range(len(self.stack) - 1, -1, -1):
            if self.stack[depth][0] == tag:
                unclosed = [t for t, _ in self.stack[depth + 1 :]]
                self.nesting_errors.append(
                    f"line {self.getpos()[0]}: </{tag}> closed while {', '.join(f'<{u}>' for u in unclosed)} still open"
                )
                del self.stack[depth:]
                return
        self.nesting_errors.append(f"line {self.getpos()[0]}: </{tag}> has no matching opening tag")

    def handle_data(self, data):
        if self._json_block_id:
            self.json_blocks.setdefault(self._json_block_id, []).append(data)
        if self._capturing_js:
            self._js_buf.append(data)
        if self._suppress_text:
            return
        self.text_parts.append(data)

    def close(self):
        super().close()
        for tag, line in self.stack:
            self.nesting_errors.append(f"line {line}: <{tag}> was never closed")
        self.stack = []


@dataclass
class DeckDocument:
    raw: str
    parser: _DeckParser

    @property
    def visible_text(self) -> str:
        parts = list(self.parser.text_parts)
        if self.parser.meta_description:
            parts.append(self.parser.meta_description)
        return "\n".join(parts)


def parse_deck(raw: str) -> DeckDocument:
    parser = _DeckParser()
    parser.feed(raw)
    parser.close()
    return DeckDocument(raw=raw, parser=parser)


# --------------------------------------------------------------------------
# The number whitelist.
# --------------------------------------------------------------------------


def _number_forms(value: float) -> set[str]:
    """Every string form a supplied number may legitimately be rendered as."""
    forms: set[str] = set()
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        return forms
    if math.isnan(as_float) or math.isinf(as_float):
        return forms
    if as_float < 0:
        as_float = -as_float
    forms.add(f"{as_float:g}")
    if as_float == int(as_float):
        forms.add(str(int(as_float)))
    for places in (1, 2):
        forms.add(f"{round(as_float, places):g}")
    forms.add(str(as_float))
    return forms


def _walk_numbers(obj, out: set[str]) -> None:
    """Collect every numeric --- and every quotable numeric string --- in a doc."""
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out |= _number_forms(obj)
        return
    if isinstance(obj, dict):
        for key, val in obj.items():
            if isinstance(val, str) and str(key) in NUMERIC_STRING_FIELDS:
                out |= set(_DIGIT_RUN_RE.findall(val))
            else:
                _walk_numbers(val, out)
        return
    if isinstance(obj, (list, tuple)):
        for item in obj:
            _walk_numbers(item, out)


def dot_graph_counts(dot_text: str) -> tuple[set[str], Counter]:
    """`(node ids, edge multiset)` from a `.dot`, via the vendored parser."""
    from .authoring_contract import DotParseError, parse_dot_min

    try:
        graph = parse_dot_min(dot_text)
    except DotParseError as exc:
        raise AttractorScoutError(f"a demonstration pipeline does not parse: {exc}") from exc
    edges: Counter = Counter((e.src, e.dst) for e in graph.edges)
    return set(graph.nodes), edges


def build_number_whitelist(ranked: dict, demos: dict | None) -> set[str]:
    """Every number the deck may state without declaring it.

    Sources, in order of authority: the re-verified ranking; the demonstration
    bundle (verified stats, convergence arithmetic, verbatim machine reports);
    the generated `.dot` files' own numerics and their node/edge counts; and
    the structural digits 0-4.
    """
    allowed: set[str] = set(STRUCTURAL_DIGITS)
    _walk_numbers(ranked, allowed)
    if demos:
        _walk_numbers(demos, allowed)
        for demo in demos.get("demos") or []:
            dot_text = str(demo.get("dot_text") or "")
            if not dot_text.strip():
                continue
            nodes, edges = dot_graph_counts(dot_text)
            allowed |= _number_forms(len(nodes))
            allowed |= _number_forms(sum(edges.values()))
    # Collection sizes the deck will inevitably state.
    for key in ("opportunities", "honest_no", "waste_findings", "below_frequency_floor"):
        allowed |= _number_forms(len(ranked.get(key) or []))
    if demos:
        allowed |= _number_forms(len(demos.get("demos") or []))
    # Decomposition (named limit): a supplied number's own sub-runs ride along.
    for form in list(allowed):
        allowed |= set(_DIGIT_RUN_RE.findall(form))
    return {a for a in allowed if a}


def _token_candidates(token: str) -> set[str]:
    raw = token.replace(",", "")
    out = {raw}
    try:
        val = float(raw)
    except ValueError:
        return out
    out |= _number_forms(val)
    return out


def read_derived_declarations(doc: DeckDocument) -> tuple[list[dict], list[str]]:
    """`(entries, problems)` from the deck's own `derived-values` JSON block."""
    chunks = doc.parser.json_blocks.get(DT.DERIVED_BLOCK_ID)
    if not chunks:
        return [], []
    text = "".join(chunks).strip()
    if not text:
        return [], [f'the <script id="{DT.DERIVED_BLOCK_ID}"> block is empty']
    try:
        payload = json.loads(text)
    except ValueError as exc:
        return [], [f'the <script id="{DT.DERIVED_BLOCK_ID}"> block is not valid JSON: {exc}']
    if isinstance(payload, dict):
        payload = payload.get("derived") or payload.get("values") or []
    if not isinstance(payload, list):
        return [], [
            (
                f'the <script id="{DT.DERIVED_BLOCK_ID}"> block must hold a JSON array of '
                f"{{value, from}} objects (or an object with a 'derived' array)"
            )
        ]
    entries: list[dict] = []
    problems: list[str] = []
    for i, item in enumerate(payload):
        if not isinstance(item, dict):
            problems.append(f"derived-values[{i}] is not an object")
            continue
        value = item.get("value")
        if value is None or str(value).strip() == "":
            problems.append(f"derived-values[{i}] has no 'value'")
            continue
        value_s = str(value).strip()
        provenance = str(item.get("from") or "").strip()
        if not provenance:
            problems.append(
                f"derived-values[{i}] declares {value_s!r} with no 'from' provenance. An "
                f"undocumented derivation is indistinguishable from an invented number."
            )
            continue
        # `inputs` is MANDATORY and non-empty: a derivation with no declared
        # inputs is a bare assertion the gate cannot check against the run
        # data. This is what closes the self-dealing hole --- a fabricated
        # value with a junk one-word `from` and no inputs used to pass.
        inputs_raw = item.get("inputs")
        if not isinstance(inputs_raw, list) or not inputs_raw:
            problems.append(
                f"derived-values[{i}] declares {value_s!r} with no 'inputs'. Every derived value "
                f"MUST list the supplied numbers it was computed from (a non-empty array), so the "
                f"derivation can be checked against the run data. An input-less declaration is a bare "
                f"assertion, indistinguishable from an invented number."
            )
            continue
        inputs = [str(x).strip() for x in inputs_raw if str(x).strip()]
        if not inputs:
            problems.append(f"derived-values[{i}] declares {value_s!r} with an empty 'inputs' list")
            continue
        # `from` must be NON-TRIVIAL: it must actually reference every input it
        # claims to combine, so "qqq" cannot stand in for a real derivation.
        # Comma-insensitive substring match against the provenance text.
        from_norm = provenance.replace(",", "")
        unreferenced = [inp for inp in inputs if inp.replace(",", "") not in from_norm]
        if unreferenced:
            problems.append(
                f"derived-values[{i}] declares {value_s!r} but its 'from' text does not reference "
                f"input(s) {unreferenced}. The provenance must name the numbers it combines, so a "
                f"trivial or junk 'from' cannot launder a fabricated value."
            )
            continue
        entries.append({"value": value_s, "from": provenance, "inputs": inputs})
    return entries, problems


# --------------------------------------------------------------------------
# The gates.
# --------------------------------------------------------------------------


@dataclass
class GateResult:
    letter: str
    name: str
    passed: bool
    detail: str
    findings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "letter": self.letter,
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "findings": self.findings,
        }


@dataclass
class DeckReport:
    deck_path: str
    gates: list[GateResult]

    @property
    def ok(self) -> bool:
        return all(g.passed for g in self.gates)

    def as_dict(self) -> dict:
        return {"deck": self.deck_path, "ok": self.ok, "gates": [g.as_dict() for g in self.gates]}

    def render(self) -> str:
        lines = [f"DECK VERIFY REPORT\ndeck: {self.deck_path}\nverdict: {'PASS' if self.ok else 'FAIL'}", ""]
        for gate in self.gates:
            lines.append(f"[{'PASS' if gate.passed else 'FAIL'}] {gate.letter} {gate.name}")
            lines.append(f"       {gate.detail}")
            for finding in gate.findings[:40]:
                lines.append(f"       - {finding}")
            if len(gate.findings) > 40:
                lines.append(f"       ... and {len(gate.findings) - 40} more")
        return "\n".join(lines)


def gate_parses(doc: DeckDocument) -> GateResult:
    findings = list(doc.parser.nesting_errors)
    if "<!doctype" not in doc.raw[:200].lower():
        findings.append("the document does not open with <!doctype html>")
    if "<html" not in doc.raw.lower():
        findings.append("there is no <html> element")
    return GateResult(
        letter="a",
        name="the HTML parses",
        passed=not findings,
        detail=(
            f"{len(BALANCED_TAGS)} container element types checked for balanced nesting; "
            f"{len(findings)} structural problem(s)"
        ),
        findings=findings,
    )


def gate_self_contained(doc: DeckDocument) -> GateResult:
    raw = doc.raw
    findings: list[str] = []
    lowered = raw.lower()
    external = 0  # count of external-resource elements/attributes found

    # (1) Elements that exist ONLY to load or embed another resource. Presence
    #     alone is a violation, regardless of attributes.
    for pattern, why in (
        (r"<img\b", "<img> (every graphic must be inline SVG)"),
        (r"<link\b", "<link>"),
        (r"<iframe\b", "<iframe>"),
        (r"<object\b", "<object> (its data= attribute loads an external resource)"),
        (r"<embed\b", "<embed>"),
        (r"<source\b", "<source> (a media/picture src)"),
        (r"<track\b", "<track> (a subtitle src)"),
    ):
        n = len(re.findall(pattern, raw, re.IGNORECASE))
        if n:
            external += n
            findings.append(f"{n} occurrence(s) of a banned {why} element")

    # (2) A `src` attribute on ANY element. A self-contained one-file deck
    #     loads nothing, so a src attribute anywhere (script/video/audio/frame/
    #     and the banned elements above) is a violation.
    src_attrs = _SRC_ATTR_RE.findall(raw)
    if src_attrs:
        external += len(src_attrs)
        findings.append(f"{len(src_attrs)} element(s) carrying a src attribute (no resource may be loaded)")

    # (3) `xlink:href` / `<use>`/`<image> href` pointing anywhere but a local
    #     #fragment. The anchor-href count below uses `\shref`, which CANNOT
    #     see `xlink:href` (the colon breaks the whitespace boundary) --- so
    #     these external references are checked explicitly here.
    ext_refs = [
        v.strip()
        for v in (_XLINK_HREF_RE.findall(raw) + _USE_HREF_RE.findall(raw))
        if v.strip() and not v.strip().startswith("#")
    ]
    if ext_refs:
        external += len(ext_refs)
        findings.append(f"{len(ext_refs)} external xlink:href / <use> href reference(s): {ext_refs[:5]}")

    # (4) CSS @import and responsive srcset.
    for needle, why in (("@import", "a CSS @import"), ("srcset", "a srcset attribute")):
        n = lowered.count(needle)
        if n:
            external += n
            findings.append(f"{n} occurrence(s) of {why}")

    bad_urls = [m.group(1).strip() for m in _URL_FUNC_RE.finditer(raw) if not m.group(1).strip().startswith("#")]
    if bad_urls:
        findings.append(f"{len(bad_urls)} url(...) reference(s) that are not url(#local-id): {bad_urls[:5]}")
    file_urls = lowered.count("file://")
    if file_urls:
        findings.append(f"{file_urls} file:// URL(s)")
    https_hrefs = _HTTPS_HREF_RE.findall(raw)
    if len(https_hrefs) != DT.ALLOWED_HTTPS_HREFS:
        findings.append(
            f"{len(https_hrefs)} https href(s); exactly {DT.ALLOWED_HTTPS_HREFS} are permitted "
            f"(the published explainer and the bundle repository): {https_hrefs[:6]}"
        )
    return GateResult(
        letter="b",
        name="the page is self-contained (zero external requests)",
        passed=not findings,
        detail=(
            f"{len(https_hrefs)} https href(s), {len(bad_urls)} non-local url(), {file_urls} file:// URL(s), "
            f"{external} external-resource element(s)/attribute(s)"
        ),
        findings=findings,
    )


def gate_dialogs(doc: DeckDocument) -> GateResult:
    dialogs = doc.parser.dialog_ids
    triggers = doc.parser.trigger_targets
    dialog_set = set(dialogs)
    trigger_set = set(triggers)
    findings: list[str] = []
    dupes = [d for d, n in Counter(dialogs).items() if n > 1]
    if dupes:
        findings.append(f"duplicate dialog id(s): {sorted(dupes)}")
    dangling = sorted(trigger_set - dialog_set)
    if dangling:
        findings.append(f"{len(dangling)} trigger(s) point at no dialog: {dangling[:10]}")
    orphans = sorted(dialog_set - trigger_set)
    if orphans:
        findings.append(f"{len(orphans)} dialog(s) no trigger can open: {orphans[:10]}")
    return GateResult(
        letter="c",
        name="every modal has a trigger and every trigger has a modal",
        passed=not findings,
        detail=f"{len(dialogs)} dialog(s), {len(triggers)} trigger(s), {len(dialog_set & trigger_set)} paired",
        findings=findings,
    )


def gate_modal_depth(doc: DeckDocument) -> GateResult:
    """Gate (f): every modal carries the five mandated structural parts.

    Cheap and structural on purpose. It counts named parts --- a title, a
    kicker, `<h4>` sub-sections, an evidence inset, a why-it-matters, an entry
    point --- and never looks at length. A hollow modal cannot pass by being
    padded, and an honest short modal that HAS the parts is never punished for
    being short. The names come from `deck_templates`, which is also what the
    brief quotes, so the author and the gate read one contract.
    """
    dialogs = doc.parser.dialogs
    findings: list[str] = []
    conforming = 0
    for dialog in dialogs:
        gaps = dialog.missing()
        if gaps:
            findings.append(f"{dialog.dialog_id}: {'; '.join(gaps)}")
        else:
            conforming += 1
    total_sub = sum(d.subsections for d in dialogs)
    total_ev = sum(d.evidence for d in dialogs)
    return GateResult(
        letter="f",
        name="every modal carries the mandated structure",
        passed=not findings,
        detail=(
            f"{conforming}/{len(dialogs)} dialog(s) conforming; "
            f"{total_sub} sub-section(s) and {total_ev} evidence block(s) across the deck"
        ),
        findings=findings,
    )


def gate_numbers(doc: DeckDocument, whitelist: set[str]) -> GateResult:
    declared, problems = read_derived_declarations(doc)
    findings = list(problems)
    allowed = set(whitelist)
    for entry in declared:
        allowed |= _token_candidates(entry["value"])
        allowed.add(entry["value"])
    for entry in declared:
        for supplied in entry["inputs"] or []:
            if not (_token_candidates(str(supplied)) & whitelist):
                findings.append(
                    f"derived-values entry {entry['value']!r} claims input {str(supplied)!r}, which is not "
                    f"one of this run's supplied numbers"
                )

    text = doc.visible_text
    unknown: list[str] = []
    seen: set[str] = set()
    for match in _NUMBER_TOKEN_RE.finditer(text):
        token = match.group(0)
        if _token_candidates(token) & allowed:
            continue
        if token in seen:
            continue
        seen.add(token)
        start = max(0, match.start() - 45)
        context = " ".join(text[start : match.end() + 45].split())
        unknown.append(f"{token!r} is not a supplied number and is not declared derived --- ...{context}...")

    # Script-injected text: a claim written by JS (`document.title = "8731
    # units examined"`) never appears as a DOM text node, so the scan above
    # cannot see it. Scan PROSE-bearing string literals in every executable
    # inline <script> body against the same whitelist. Only literals carrying a
    # 3+ letter word are checked, so a bare CSS/geometry string is not treated
    # as a displayed claim (see _PROSE_WORD_RE). Named residual below.
    script_unknown: list[str] = []
    for body in doc.parser.script_bodies:
        for lit in _JS_STRING_RE.findall(body):
            inner = lit[1:-1]
            if not _PROSE_WORD_RE.search(inner):
                continue
            for match in _NUMBER_TOKEN_RE.finditer(inner):
                token = match.group(0)
                if _token_candidates(token) & allowed or token in seen:
                    continue
                seen.add(token)
                snippet = " ".join(inner.split())[:90]
                script_unknown.append(
                    f"{token!r} appears in an inline <script> string literal and is not a supplied "
                    f'number or declared derivation --- "{snippet}"'
                )
    findings.extend(unknown)
    findings.extend(script_unknown)
    scanned = len(_NUMBER_TOKEN_RE.findall(text))
    return GateResult(
        letter="d",
        name="every displayed number resolves against the run data or a declared derivation",
        passed=not findings,
        detail=(
            f"{scanned} numeric token(s) in visible text + {len(doc.parser.script_bodies)} inline script "
            f"body(ies) re-verified against {len(whitelist)} supplied value(s) + {len(declared)} declared "
            f"derivation(s); {len(unknown) + len(script_unknown)} unresolved"
        ),
        findings=findings,
    )


def _match_svg(svg_list: list[DeckSvg], slug: str, nodes: set[str]) -> DeckSvg | None:
    for svg in svg_list:
        if svg.diagram and svg.diagram == slug:
            return svg
    candidates = [svg for svg in svg_list if svg.nodes and set(svg.nodes) == nodes]
    if len(candidates) == 1:
        return candidates[0]
    return None


def gate_diagram_fidelity(doc: DeckDocument, demos: dict | None) -> GateResult:
    svgs_with_data = [s for s in doc.parser.svgs if s.nodes or s.edges]
    demo_list = list((demos or {}).get("demos") or [])
    findings: list[str] = []
    if not demo_list:
        if svgs_with_data:
            findings.append(
                f"the deck carries {len(svgs_with_data)} pipeline diagram(s) but no demonstration bundle was "
                f"supplied to check them against"
            )
        return GateResult(
            letter="e",
            name="every pipeline diagram matches its real .dot node-for-node and edge-for-edge",
            passed=not findings,
            detail="0 demonstration(s) in this run; 0 diagram(s) checked",
            findings=findings,
        )

    checked = 0
    for demo in demo_list:
        slug = str(demo.get("slug") or demo.get("unit_id") or "?")
        dot_text = str(demo.get("dot_text") or "")
        if not dot_text.strip():
            findings.append(f"demonstration {slug!r} carries no .dot text to check a diagram against")
            continue
        real_nodes, real_edges = dot_graph_counts(dot_text)
        svg = _match_svg(svgs_with_data, slug, real_nodes)
        if svg is None:
            findings.append(
                f"no diagram in the deck could be matched to demonstration {slug!r}. Give its <svg> a "
                f'{DT.DIAGRAM_ATTR}="{slug}" attribute, or make its {DT.NODE_ATTR} set equal the pipeline\'s '
                f"{len(real_nodes)} node(s): {sorted(real_nodes)}"
            )
            continue
        checked += 1
        drawn_nodes = set(svg.nodes)
        node_dupes = [n for n, c in Counter(svg.nodes).items() if c > 1]
        if node_dupes:
            findings.append(f"{slug}: node(s) drawn more than once: {sorted(node_dupes)}")
        if drawn_nodes != real_nodes:
            missing = sorted(real_nodes - drawn_nodes)
            extra = sorted(drawn_nodes - real_nodes)
            findings.append(
                f"{slug}: node set differs from the pipeline --- missing {missing or '(none)'}, "
                f"not in the pipeline {extra or '(none)'}"
            )
        drawn_edges: Counter = Counter()
        for raw_edge in svg.edges:
            if "->" not in raw_edge:
                findings.append(f"{slug}: {DT.EDGE_ATTR}={raw_edge!r} is not in `src->dst` form")
                continue
            src, dst = raw_edge.split("->", 1)
            drawn_edges[(src.strip(), dst.strip())] += 1
        if drawn_edges != real_edges:
            missing = sorted((real_edges - drawn_edges).elements())
            extra = sorted((drawn_edges - real_edges).elements())
            findings.append(
                f"{slug}: edge multiset differs from the pipeline --- "
                f"undrawn {[f'{s}->{d}' for s, d in missing] or '(none)'}, "
                f"not in the pipeline {[f'{s}->{d}' for s, d in extra] or '(none)'} "
                f"({sum(drawn_edges.values())} drawn vs {sum(real_edges.values())} declared)"
            )
    return GateResult(
        letter="e",
        name="every pipeline diagram matches its real .dot node-for-node and edge-for-edge",
        passed=not findings,
        detail=f"{checked} of {len(demo_list)} demonstration diagram(s) matched and compared",
        findings=findings,
    )


def verify_deck(
    *,
    deck_path: str | Path,
    ranked_path: str | Path,
    demos_path: str | Path | None = None,
) -> DeckReport:
    """Run every gate over a candidate deck. Deterministic; never publishes."""
    deck_file = Path(deck_path)
    try:
        raw = deck_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise AttractorScoutError(f"could not read the candidate deck at {deck_file}: {exc}") from exc
    ranked = _load_json(ranked_path, "ranking")
    demos = _load_json(demos_path, "demonstration bundle") if demos_path else None

    doc = parse_deck(raw)
    whitelist = build_number_whitelist(ranked, demos)
    gates = [
        gate_parses(doc),
        gate_self_contained(doc),
        gate_dialogs(doc),
        gate_numbers(doc, whitelist),
        gate_diagram_fidelity(doc, demos),
        gate_modal_depth(doc),
    ]
    return DeckReport(deck_path=str(deck_file), gates=gates)


# --------------------------------------------------------------------------
# Brief assembly.
# --------------------------------------------------------------------------


def _load_json(path: str | Path | None, what: str) -> dict:
    if path is None:
        raise AttractorScoutError(f"no {what} was supplied")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AttractorScoutError(f"could not read the {what} at {path}: {exc}") from exc


def _fmt(value) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        forms = _number_forms(value)
        as_float = float(value)
        if as_float == int(as_float):
            return str(int(as_float))
        return f"{round(as_float, 2):g}" if forms else str(value)
    return str(value)


def _summary_lines(ranked: dict, demos: dict | None) -> list[str]:
    summary = ranked.get("summary") or {}
    lines = [f"{key}: {_fmt(val)}" for key, val in summary.items()]
    lines.append(f"opportunities listed: {len(ranked.get('opportunities') or [])}")
    lines.append(f"honest-NOes listed: {len(ranked.get('honest_no') or [])}")
    lines.append(f"waste findings listed: {len(ranked.get('waste_findings') or [])}")
    lines.append(f"demonstrations generated: {len((demos or {}).get('demos') or [])}")
    return lines


def _opportunity_block(index: int, unit: dict) -> str:
    lev = unit.get("leverage_detail") or {}
    fit = unit.get("fit_detail") or {}
    flags = []
    if unit.get("provisional"):
        flags.append("provisional (seen in only 2-3 sessions)")
    if str(unit.get("verdict", "")).endswith("(unproven)"):
        flags.append("unproven recovery --- a caveat, never a failure")
    return "\n".join(
        [
            f"### {index}. {unit.get('name')}  [{unit.get('unit_id')}]",
            f"  verdict:            {unit.get('verdict')}",
            f"  distinct sessions:  {_fmt(unit.get('n_sessions'))}",
            f"  leverage:           {_fmt(unit.get('leverage'))}",
            f"  score:              {_fmt(unit.get('score'))}",
            f"  median tool calls:  {_fmt(lev.get('med_tool_calls'))}",
            f"  median LLM cycles:  {_fmt(lev.get('med_llm_cycles'))}",
            f"  median span (s):    {_fmt(lev.get('med_span_capped_s'))}",
            f"  errors per session: {_fmt(lev.get('errors_per_session'))}",
            f"  trajectory:         {unit.get('trajectory')}",
            f"  confidence:         {unit.get('confidence')}",
            f"  fit  4a cycle:      {_fmt(fit.get('cycle'))}",
            f"  fit  4b gate:       {_fmt(fit.get('gate'))}",
            f"  fit  4c recovery:   {unit.get('recovery')}",
            f"  flags:              {'; '.join(flags) if flags else '(none)'}",
            f"  gist:               {unit.get('gist') or '(no gist recorded for this unit)'}",
            "",
        ]
    )


def _honest_no_block(unit: dict) -> str:
    return "\n".join(
        [
            f"### {unit.get('name')}  [{unit.get('unit_id')}]",
            f"  distinct sessions:  {_fmt(unit.get('n_sessions'))}",
            f"  leverage:           {_fmt(unit.get('leverage'))}",
            f"  class:              {unit.get('no_class')}",
            f"  failed sub-test:    {unit.get('failed_subtest')}",
            f"  remediation:        {unit.get('remediation')}",
            f"  gist:               {unit.get('gist') or '(no gist recorded for this unit)'}",
            "",
        ]
    )


def _waste_block(unit: dict) -> str:
    return "\n".join(
        [
            f"### {unit.get('name')}  [{unit.get('unit_id')}]",
            f"  sessions:           {_fmt(unit.get('n_sessions'))}",
            f"  reclaimable hours:  {_fmt(unit.get('reclaimable_hours'))}",
            f"  note:               {unit.get('note')}",
            "",
        ]
    )


def _demo_block(demo: dict) -> str:
    verification = demo.get("verification") or {}
    narrative = demo.get("narrative") or {}
    maths = demo.get("convergence_math") or {}
    dot_text = str(demo.get("dot_text") or "")
    nodes, edges = (set(), Counter())
    if dot_text.strip():
        nodes, edges = dot_graph_counts(dot_text)
    walk = "\n".join(f"    - {step.get('node')}: {step.get('note')}" for step in (narrative.get("pipeline_walk") or []))
    return "\n".join(
        [
            f"### {demo.get('name')}  [{demo.get('unit_id')}]  slug: {demo.get('slug')}",
            f"  published as:        {demo.get('dot_relpath')} + {demo.get('companion_relpath')}",
            f"  graph shape:         {len(nodes)} node(s), {sum(edges.values())} edge(s)",
            f"  node ids:            {', '.join(sorted(nodes)) or '(none)'}",
            "  edges (multiset):    " + (", ".join(f"{s}->{d}" for s, d in sorted(edges.elements())) or "(none)"),
            f"  verification level:  {verification.get('level')}",
            f"  attractor lint:      {verification.get('lint_verdict') or verification.get('lint_not_run_reason')}",
            f"  doctrine verdict:    {verification.get('doctrine_verdict')}",
            (
                f"  convergence math:    chain {_fmt(maths.get('chain_len'))} steps at "
                f"p={_fmt(maths.get('p_step'))} -> once-through {_fmt(maths.get('once_through'))} vs "
                f"gated loop {_fmt(maths.get('gated_loop'))} within a budget of {_fmt(maths.get('budget'))} "
                f"({maths.get('label')})"
            ),
            "",
            "  narrative slots (already gate-checked; reuse the CONTENT, rewrite the prose):",
            f"    scenario_gist:    {narrative.get('scenario_gist')}",
            f"    q1_cycle_note:    {narrative.get('q1_cycle_note')}",
            f"    q2_gate_note:     {narrative.get('q2_gate_note')}",
            f"    q3_recovery_note: {narrative.get('q3_recovery_note')}",
            f"    payoff_note:      {narrative.get('payoff_note')}",
            "    pipeline_walk:",
            walk or "      (none)",
            "",
            "  THE REAL PIPELINE (verbatim --- your diagram must match this graph exactly):",
            "",
            "```dot",
            dot_text.rstrip(),
            "```",
            "",
            "  THE MACHINE GATE REPORT (verbatim --- quote it, never paraphrase a verdict):",
            "",
            "```",
            str(verification.get("doctrine_report") or "(not run)").rstrip(),
            "```",
            "",
        ]
    )


def _absent_fields(ranked: dict, demos: dict | None) -> list[str]:
    """What the run's shape wants and this run does not have. Honest, computed."""
    absent: list[str] = []
    units = list(ranked.get("opportunities") or []) + list(ranked.get("honest_no") or [])
    if units and not any(u.get("workspace") or u.get("workspaces") for u in units):
        absent.append(
            "workspace / project attribution per unit --- the ranking records which sessions a unit "
            "covers, but not which project or repository they belong to. Do not guess a project name."
        )
    if units and not any(u.get("first_seen") or u.get("last_seen") for u in units):
        absent.append(
            "first-seen / last-seen dates per unit --- this run records how often work recurred, not "
            "when it started or whether it is still happening."
        )
    if not any((u.get("gist") or "").strip() for u in units):
        absent.append("scenario gists --- no unit in this run carries a recorded gist.")
    if not (demos or {}).get("demos"):
        absent.append("generated demonstration pipelines --- none were produced in this run.")
    absent.append(
        "per-step LLM success rate --- nothing in the run measures it. The convergence arithmetic uses a "
        "FIXED illustrative rate, and must be labelled as illustrative wherever it appears."
    )
    absent.append(
        "any artifact for what happened BEFORE a file was written --- a first draft that was rejected, a "
        "gate report that was superseded. If the deck tells that story it is TESTIMONY, and must say so."
    )
    return absent


def build_deck_brief(
    *,
    ranked_path: str | Path,
    demos_path: str | Path | None,
    workdir: str | Path,
    run_label: str | None = None,
) -> Path:
    """Write `<workdir>/deck-brief.md`. Deterministic; returns its path."""
    ranked = _load_json(ranked_path, "ranking")
    demos = _load_json(demos_path, "demonstration bundle") if demos_path else None

    target = Path(workdir)
    target.mkdir(parents=True, exist_ok=True)
    brief_path = target / "deck-brief.md"

    opportunities = list(ranked.get("opportunities") or [])
    honest_nos = list(ranked.get("honest_no") or [])
    waste = list(ranked.get("waste_findings") or [])
    demo_list = list((demos or {}).get("demos") or [])

    whitelist = sorted(build_number_whitelist(ranked, demos), key=lambda s: (len(s), s))

    brief_path.write_text(
        DT.deck_brief_markdown(
            run_label=run_label or "one attractor-scout run over your own session history",
            summary_lines=_summary_lines(ranked, demos),
            opportunity_blocks=[_opportunity_block(i + 1, u) for i, u in enumerate(opportunities)],
            honest_no_blocks=[_honest_no_block(u) for u in honest_nos],
            waste_blocks=[_waste_block(u) for u in waste],
            demo_blocks=[_demo_block(d) for d in demo_list],
            absent_fields=_absent_fields(ranked, demos),
            allowed_numbers=whitelist,
            deck_filename=DECK_FILENAME,
        ),
        encoding="utf-8",
    )
    return brief_path


def publish_deck(candidate: str | Path, output_dir: str | Path) -> Path:
    """Copy a VERIFIED deck beside the report. Never called before the gates."""
    import shutil

    dest = Path(output_dir) / DECK_FILENAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(Path(candidate), dest)
    except OSError as exc:
        raise AttractorScoutError(f"could not publish the deck into {dest.parent}: {exc}") from exc
    if not dest.is_file():
        raise AttractorScoutError(f"publication into {dest.parent} did not produce {DECK_FILENAME}")
    return dest


__all__ = [
    "DECK_MAX_ATTEMPTS",
    "DeckGateRed",
    "DeckReport",
    "GateResult",
    "build_deck_brief",
    "build_number_whitelist",
    "dot_graph_counts",
    "gate_diagram_fidelity",
    "gate_dialogs",
    "gate_modal_depth",
    "gate_numbers",
    "gate_parses",
    "gate_self_contained",
    "parse_deck",
    "publish_deck",
    "read_derived_declarations",
    "verify_deck",
]
