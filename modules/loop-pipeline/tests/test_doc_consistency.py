"""Tests for doc/spec internal consistency — D-135, D-137, D-240..D-243.

These are regression guards against documentation contradictions.

D-240..D-242 were added closing the 2026-08-15 Layer-3 drift review (issue
#240). Each pins a claim that had already drifted once, against the thing the
claim is *about* rather than against itself:

  D-240  ``README.md``'s ``suggested_next_ids`` note
         <- ``edge_selection._coerce_suggested_id``'s real behavior.
         The README taught the pre-fix bug as current for the life of
         ``specs/EXTENSIONS.md`` §34 (DR-CORE-001). Two-sided: revert the
         coercion in code and this fails, naming the README paragraph that
         would need its caveat back.
  D-241  ``README.md``'s section count for ``docs/PIPELINE_DESIGN_PRINCIPLES.md``
         <- that file's actual numbered ``## N.`` headings.
         The row said "Six" while the file carried eight sections; §0 and §7
         shipped and the summary was never revisited (issue #236).
  D-242  ``SPEC_CONFORMANCE.md``'s Summary row for the attractor spec
         <- the §3 attractor table it summarizes.
         The summary claimed 3 resolved / 3 open while four more rows had been
         decided (DR-LEDGER-002). Recomputes the arithmetic from the table.

Honest limit shared by all three: extracting a claim from prose is
regex-over-prose. Rewording the sentence a claim lives in fails the guard with
"claim not found" — the intended failure, not a false alarm. A reworded claim
needs a re-anchored guard.
"""

import re
from pathlib import Path

# Root of the bundle repo relative to this test file
BUNDLE_ROOT = Path(__file__).parent.parent.parent.parent


def _read(rel: str) -> str:
    return (BUNDLE_ROOT / rel).read_text()


# ---------------------------------------------------------------------------
# D-135: default_max_retry must be documented as 0 everywhere
# ---------------------------------------------------------------------------


def test_spec_default_max_retry_table_is_zero():
    """Canonical spec table rows for the retry ceiling must show default 0 (D-135).

    Reads ``specs/canonical/attractor-spec-canonical.md`` -- the byte-identical
    upstream snapshot @ ``fb57a55``, which is the normative text. The former
    ``specs/attractor-spec.md`` working copy this used to read was retired to a
    pointer stub (2026-08-14): it had drifted into contradicting the canonical
    snapshot (five-phase lifecycle, ``k_of_n``/``quorum``, ``preferred_next_label``),
    so asserting against it proved nothing about the spec we actually implement.

    Attribute-name note: canonical names the graph attribute
    ``default_max_retries`` (plural), keeping the singular ``default_max_retry``
    only as a legacy alias (canonical ``:139``, ``:1993``; see
    ``specs/EXTENSIONS.md`` section 2). The pattern below accepts BOTH spellings so
    the check stays anchored on the documented default value rather than on which
    of the two names a given table row happens to use.
    """
    content = _read("specs/canonical/attractor-spec-canonical.md")
    # The table row pattern: | `default_max_retries` | Integer | <default> | ...
    matches = re.findall(
        r"\|\s*`default_max_retr(?:y|ies)`\s*\|\s*Integer\s*\|\s*`(\d+)`", content
    )
    assert matches, (
        "default_max_retries table row not found in "
        "specs/canonical/attractor-spec-canonical.md"
    )
    for val in matches:
        assert val == "0", (
            f"attractor-spec-canonical.md: default_max_retries table default is "
            f"'{val}', expected '0' (D-135)"
        )


def test_authoring_guide_default_max_retry_is_zero():
    """DOT-AUTHORING-GUIDE.md table row for the retry ceiling must show default 0 (D-135).

    Accepts BOTH spellings, for the same reason the canonical-spec check above
    does: canonical names the attribute ``default_max_retries`` and keeps the
    singular only as a legacy alias, so this guard is anchored on the documented
    default *value*, not on which of the two names the row happens to use.
    """
    content = _read("docs/DOT-AUTHORING-GUIDE.md")
    matches = re.findall(
        r"\|\s*`default_max_retr(?:y|ies)`\s*\|\s*Integer\s*\|\s*`(\d+)`", content
    )
    assert matches, "default_max_retries table row not found in DOT-AUTHORING-GUIDE.md"
    for val in matches:
        assert val == "0", (
            f"DOT-AUTHORING-GUIDE.md: default_max_retry table default is '{val}', expected '0' (D-135)"
        )


# ---------------------------------------------------------------------------
# D-137: house shape LLM classification must be consistent across both docs
# ---------------------------------------------------------------------------


def _extract_llm_value_from_house_row(content: str, filename: str) -> str:
    """Find the house table row and return the value in the LLM column.

    Handles different column orderings by first reading the header row in
    the same table, then finding the LLM column index.
    """
    lines = content.splitlines()
    # Find the table containing the house row
    house_line_idx = None
    for i, line in enumerate(lines):
        if re.search(r"\|\s*`?house`?\s*\|", line):
            house_line_idx = i
            break

    if house_line_idx is None:
        raise AssertionError(f"Could not find house shape row in {filename}")

    # Walk backwards to find the header row (first row before the separator ---)
    header_idx = None
    for i in range(house_line_idx - 1, -1, -1):
        row = lines[i].strip()
        if re.match(r"\|[-\s|]+\|", row):
            # This is the separator line; header is one above
            if i > 0:
                header_idx = i - 1
            break

    if header_idx is None:
        raise AssertionError(f"Could not find header row for house table in {filename}")

    # Parse header columns
    header_cols = [c.strip() for c in lines[header_idx].split("|") if c.strip()]
    # Find which column contains "LLM"
    llm_col_idx = None
    for idx, col in enumerate(header_cols):
        if "LLM" in col.upper():
            llm_col_idx = idx
            break

    if llm_col_idx is None:
        raise AssertionError(f"Could not find LLM column in header of {filename}")

    # Parse the house row
    house_cols = [c.strip() for c in lines[house_line_idx].split("|") if c.strip()]
    if llm_col_idx >= len(house_cols):
        raise AssertionError(
            f"LLM column index {llm_col_idx} out of range for house row in {filename}"
        )

    return house_cols[llm_col_idx]


def test_house_llm_classification_consistent_across_docs():
    """DOT-AUTHORING-GUIDE.md and DOT-SYNTAX.md must agree on house LLM classification (D-137)."""
    guide_content = _read("docs/DOT-AUTHORING-GUIDE.md")
    syntax_content = _read("docs/DOT-SYNTAX.md")

    guide_val = _extract_llm_value_from_house_row(
        guide_content, "DOT-AUTHORING-GUIDE.md"
    )
    syntax_val = _extract_llm_value_from_house_row(syntax_content, "DOT-SYNTAX.md")

    assert guide_val == syntax_val, (
        f"house LLM column mismatch: "
        f"DOT-AUTHORING-GUIDE.md='{guide_val}' vs DOT-SYNTAX.md='{syntax_val}' (D-137)"
    )


def test_house_llm_classification_is_indirect():
    """Both docs must describe house LLM classification as 'Indirect' (D-137)."""
    guide_content = _read("docs/DOT-AUTHORING-GUIDE.md")
    syntax_content = _read("docs/DOT-SYNTAX.md")

    guide_val = _extract_llm_value_from_house_row(
        guide_content, "DOT-AUTHORING-GUIDE.md"
    )
    syntax_val = _extract_llm_value_from_house_row(syntax_content, "DOT-SYNTAX.md")

    assert "Indirect" in guide_val, (
        f"DOT-AUTHORING-GUIDE.md house LLM field should contain 'Indirect', got: '{guide_val}' (D-137)"
    )
    assert "Indirect" in syntax_val, (
        f"DOT-SYNTAX.md house LLM field should contain 'Indirect', got: '{syntax_val}' (D-137)"
    )


# ---------------------------------------------------------------------------
# D-240: README's suggested_next_ids note vs the shipped coercion (DR-CORE-001)
# ---------------------------------------------------------------------------

# The exact phrases the README used while it still taught the pre-§34 bug as
# current. Kept verbatim so the guard fails loudly if that paragraph is ever
# restored without the code regressing to match it.
_RETIRED_SUGGESTED_ID_CAVEAT_PHRASES = (
    "This is a known issue being addressed",
    "Non-string or mismatched entries currently fail to match",
)


def test_readme_suggested_next_ids_note_matches_the_shipped_coercion():
    """README's `suggested_next_ids` note must describe the code as it is (D-240).

    Source of truth: ``edge_selection._coerce_suggested_id``. The README carried
    a "Known caveat" teaching the pre-fix behavior (non-string entries silently
    fail to match, "known issue being addressed") for the whole life of the
    shipped fix -- ``specs/EXTENSIONS.md`` §34, drift finding DR-CORE-001.

    This asserts the *code's* behavior first, so reverting the coercion fails
    here and names the README paragraph that would then need its caveat back.
    """
    from amplifier_module_loop_pipeline import edge_selection

    coerce = getattr(edge_selection, "_coerce_suggested_id", None)
    assert coerce is not None, (
        "edge_selection._coerce_suggested_id is gone. README.md's "
        "'`suggested_next_ids` typing' paragraph (Stability & Compatibility) "
        "documents that int entries are coerced and malformed shapes are "
        "skipped, and specs/EXTENSIONS.md §34 records that as shipped. If the "
        "coercion was deliberately removed, restore the README's caveat and "
        "re-anchor this guard in the same PR."
    )

    # The contract §34 records, and the README now describes.
    assert coerce("review") == "review", "str entries must pass through unchanged"
    assert coerce(3) == "3", (
        'int entries must coerce to their string form (§34: `[3]` -> `["3"]`). '
        "README.md now tells readers the type slip is handled; if this stops "
        "being true the README is lying again (DR-CORE-001)."
    )
    for malformed in (True, 3.0, {"a": 1}, ["x"], None):
        assert coerce(malformed) is None, (
            f"{malformed!r} must be rejected, not coerced -- README.md and "
            "specs/EXTENSIONS.md §34 both say only int is coerced and every "
            "other shape is skipped."
        )

    readme = _read("README.md")
    for phrase in _RETIRED_SUGGESTED_ID_CAVEAT_PHRASES:
        assert phrase not in readme, (
            f"README.md still carries the retired pre-§34 caveat phrase "
            f"{phrase!r}, but the coercion above is shipped and passing. That "
            "combination teaches readers to work around a closed bug (DR-CORE-001)."
        )


# ---------------------------------------------------------------------------
# D-241: README's principle count vs PIPELINE_DESIGN_PRINCIPLES.md (issue #236)
# ---------------------------------------------------------------------------

_PRINCIPLES_REL = "docs/PIPELINE_DESIGN_PRINCIPLES.md"
_NUMBER_WORDS = {
    "One": 1,
    "Two": 2,
    "Three": 3,
    "Four": 4,
    "Five": 5,
    "Six": 6,
    "Seven": 7,
    "Eight": 8,
    "Nine": 9,
    "Ten": 10,
    "Eleven": 11,
    "Twelve": 12,
}


def test_readme_principle_count_matches_the_principles_file():
    """README's doc-table row must count the sections the file actually has (D-241).

    The row said "Six framework-agnostic design principles" while the file
    carried eight numbered sections: §0 (the control-plane vs recipe-plane line,
    the most vision-load-bearing section in the repo) and §7 shipped later and
    the summary was never revisited -- issue #236.

    Source of truth: the ``## N.`` headings in the principles file itself. Add a
    ``## 8.`` and this fails, which is the recurrence this guard exists to stop.
    """
    principles = _read(_PRINCIPLES_REL)
    sections = re.findall(r"^##\s+(\d+)\.", principles, re.MULTILINE)
    assert sections, (
        f"{_PRINCIPLES_REL}: no numbered `## N.` section headings found. Either "
        "the file's heading style changed -- re-anchor this pattern -- or the "
        "numbered sections are gone, which would make README.md's count "
        "meaningless rather than merely wrong."
    )
    actual = len(sections)

    readme = _read("README.md")
    match = re.search(
        r"\|\s*\[Pipeline Design Principles\]\([^)]*\)\s*\|\s*(\w+)\s+framework-agnostic",
        readme,
    )
    assert match is not None, (
        "README.md: could not find the Pipeline Design Principles row's "
        "'<count> framework-agnostic design principles' claim. If the row was "
        "reworded, re-anchor this guard in the same PR (issue #236)."
    )
    word = match.group(1)
    claimed = _NUMBER_WORDS.get(word)
    assert claimed is not None, (
        f"README.md claims '{word} framework-agnostic design principles', which "
        f"is not a number word this guard knows. Known: {sorted(_NUMBER_WORDS)}."
    )
    assert claimed == actual, (
        f"README.md's documentation table claims {word} ({claimed}) principles in "
        f"{_PRINCIPLES_REL}, but that file carries {actual} numbered sections "
        f"(§{', §'.join(sections)}). A section shipped and the summary was not "
        "revisited -- exactly the drift issue #236 recorded. Update the README "
        "row (and name the new section in it) in the PR that adds the section."
    )


# ---------------------------------------------------------------------------
# D-242: SPEC_CONFORMANCE's Summary row vs the table it summarizes (DR-LEDGER-002)
# ---------------------------------------------------------------------------

_LEDGER_REL = "SPEC_CONFORMANCE.md"


def _attractor_table_rows(ledger: str) -> dict[str, str]:
    """Map ``ATX-n`` -> its Status cell, read from the section-3 table."""
    section = re.split(r"^##\s+3\.\s+attractor-spec", ledger, flags=re.MULTILINE)
    assert len(section) == 2, (
        f"{_LEDGER_REL}: the '## 3. attractor-spec ...' heading this guard "
        "anchors on was not found exactly once. Re-anchor if the heading was "
        "reworded (DR-LEDGER-002)."
    )
    body = re.split(r"^##\s+", section[1], flags=re.MULTILINE)[0]

    rows: dict[str, str] = {}
    for line in body.splitlines():
        if not re.match(r"^\|\s*ATX-\d+\s*\|", line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 6, (
            f"{_LEDGER_REL}: attractor table row has {len(cells)} cells, "
            f"expected 6 (ID|Area|Spec|Impl|Status|Disposition):\n    {line}\n"
            "  Re-anchor this guard if the table's shape changed."
        )
        rows[cells[0]] = cells[4]
    assert rows, f"{_LEDGER_REL}: no ATX-* rows found in the section-3 table."
    return rows


def _summary_attractor_cells(ledger: str) -> list[str]:
    matches = [
        line for line in ledger.splitlines() if re.match(r"^\|\s*attractor\s*\|", line)
    ]
    assert len(matches) == 1, (
        f"{_LEDGER_REL}: expected exactly one Summary row starting `| attractor |`, "
        f"found {len(matches)}. Re-anchor this guard if the Summary table changed."
    )
    return [c.strip() for c in matches[0].strip().strip("|").split("|")]


def _ids_in(cell: str) -> set[str]:
    return set(re.findall(r"ATX-\d+", cell))


def _count_in(cell: str) -> int:
    match = re.match(r"^(\d+)", cell)
    assert match is not None, (
        f"{_LEDGER_REL}: Summary cell {cell!r} does not start with a count."
    )
    return int(match.group(1))


def test_ledger_summary_row_matches_the_attractor_table():
    """The Summary row's arithmetic must be re-derivable from the table (D-242).

    The summary read "8 gaps | 3 resolved (ATX-1, ATX-2, ATX-10) | 3 open" while
    ATX-4, ATX-5, ATX-11 and ATX-12 had all been decided in the table below it --
    drift finding DR-LEDGER-002. The summary is a derived view; this recomputes
    it, so deciding a row without updating the summary fails here.

    Classification rule, matching the Status legend: a Status cell containing
    ``OPEN`` is open; everything else (DONE / WONTFIX, decided either way) is
    resolved.
    """
    ledger = _read(_LEDGER_REL)
    rows = _attractor_table_rows(ledger)

    open_ids = {rid for rid, status in rows.items() if "OPEN" in status.upper()}
    resolved_ids = set(rows) - open_ids

    cells = _summary_attractor_cells(ledger)
    assert len(cells) == 5, (
        f"{_LEDGER_REL}: Summary row has {len(cells)} cells, expected 5 "
        f"(Spec|Areas reviewed|Off-spec gaps|Resolved|Open):\n    {cells}"
    )
    _, _, gaps_cell, resolved_cell, open_cell = cells

    def _sorted(ids: set[str]) -> list[str]:
        return sorted(ids, key=lambda i: int(i.split("-")[1]))

    assert _count_in(gaps_cell) == len(rows), (
        f"{_LEDGER_REL} Summary: attractor 'Off-spec gaps' says "
        f"{_count_in(gaps_cell)}, but the section-3 table carries {len(rows)} "
        f"ATX rows ({', '.join(_sorted(set(rows)))}). Adding a row to the table "
        "means updating the summary in the same PR (DR-LEDGER-002)."
    )
    assert _ids_in(resolved_cell) == resolved_ids, (
        f"{_LEDGER_REL} Summary: 'Resolved' names {_sorted(_ids_in(resolved_cell))}, "
        f"but the table's non-OPEN rows are {_sorted(resolved_ids)}. The summary "
        "is a derived view of the table; re-derive it."
    )
    assert _count_in(resolved_cell) == len(resolved_ids), (
        f"{_LEDGER_REL} Summary: 'Resolved' count is {_count_in(resolved_cell)} "
        f"but names/derives {len(resolved_ids)} rows ({_sorted(resolved_ids)})."
    )
    assert _ids_in(open_cell) == open_ids, (
        f"{_LEDGER_REL} Summary: 'Open' names {_sorted(_ids_in(open_cell))}, but "
        f"the table's OPEN rows are {_sorted(open_ids)}."
    )
    assert _count_in(open_cell) == len(open_ids), (
        f"{_LEDGER_REL} Summary: 'Open' count is {_count_in(open_cell)} but "
        f"names/derives {len(open_ids)} rows ({_sorted(open_ids)})."
    )


def test_selfcheck_summary_recount_rejects_the_drifted_row():
    """The D-242 checker must actually fail on the shape DR-LEDGER-002 found."""
    drifted = (
        "| attractor | ~30 | 8 | 3 (ATX-1, ATX-2, ATX-10) | 3 (ATX-3, ATX-6, ATX-7) |"
    )
    cells = [c.strip() for c in drifted.strip().strip("|").split("|")]
    assert _count_in(cells[2]) == 8
    assert _ids_in(cells[3]) == {"ATX-1", "ATX-2", "ATX-10"}
    # The real table resolves seven; the drifted row named three.
    real_resolved = _attractor_table_rows(_read(_LEDGER_REL))
    real_resolved_ids = {
        rid for rid, status in real_resolved.items() if "OPEN" not in status.upper()
    }
    assert _ids_in(cells[3]) != real_resolved_ids, (
        "The drifted summary row would now pass the D-242 check, which means the "
        "check cannot detect the drift it was written for."
    )


# ---------------------------------------------------------------------------
# D-243: DOT-AUTHORING-GUIDE's reasoning_effort default vs the shipped engine
# (SPEC_CONFORMANCE.md ATX-14 / specs/EXTENSIONS.md section 39, issue #234 F4)
# ---------------------------------------------------------------------------

_AUTHORING_GUIDE_REL = "docs/DOT-AUTHORING-GUIDE.md"


def _authoring_guide_reasoning_effort_default_cell() -> str:
    """Extract the Default cell of the guide's `reasoning_effort` table row."""
    guide = _read(_AUTHORING_GUIDE_REL)
    m = re.search(
        r"^\| `reasoning_effort` \| String \| (?P<default>[^|]+) \|",
        guide,
        flags=re.MULTILINE,
    )
    assert m, (
        f"{_AUTHORING_GUIDE_REL}: could not find the node-attribute table row for "
        "`reasoning_effort`. If the row was reworded, re-anchor this guard (D-243) "
        "in the same PR -- the claim it pins is the attribute's DEFAULT, which the "
        "canonical spec gives as \"high\" and this engine deliberately does not "
        "implement (ledger ATX-14, specs/EXTENSIONS.md section 39)."
    )
    return m.group("default").strip()


def test_authoring_guide_reasoning_effort_default_matches_engine():
    """The guide's reasoning_effort Default cell must describe the code (D-243).

    Source of truth: ``graph.Node`` -- ``reasoning_effort`` is ``None`` unless
    the author (node attr), a ``model_stylesheet`` rule, or a profile sets it.
    The guide shipped Appendix A's ``high`` in that cell as though it held on
    this engine; it does not, and the divergence is decided and ledgered
    (SPEC_CONFORMANCE.md ATX-14, specs/EXTENSIONS.md section 39, issue #234 F4).

    Two-sided, D-240 style: asserting the CODE first means introducing an
    engine default fails here naming the ledger entries that must move with
    it; asserting the DOC second means restoring the spec's ``high`` to the
    guide fails here naming the engine truth it would contradict.
    """
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.graph import Node
    from amplifier_module_loop_pipeline.transforms import apply_transforms

    # Code side: no engine-injected default at any resolution layer.
    assert Node(id="n").reasoning_effort is None, (
        "Node.reasoning_effort now has a dataclass default of "
        f"{Node(id='n').reasoning_effort!r}. That is the divergence "
        "SPEC_CONFORMANCE.md ATX-14 / specs/EXTENSIONS.md section 39 decided "
        "AGAINST re-introducing (issue #234 F4). If this is a deliberate "
        "re-decision, move both ledger entries, matrix row ATX-M-F04, and "
        "docs/DOT-AUTHORING-GUIDE.md's reasoning_effort row in the same PR."
    )
    graph = parse_dot(
        """
        digraph D243 {
            start [shape=Mdiamond]
            exit  [shape=Msquare]
            work  [prompt="do work"]
            start -> work -> exit
        }
        """
    )
    transformed = apply_transforms(graph, PipelineContext())
    assert transformed.nodes["work"].reasoning_effort is None, (
        "apply_transforms() resolved reasoning_effort to "
        f"{transformed.nodes['work'].reasoning_effort!r} for a node that "
        "omitted it, with no stylesheet rule. The transform pipeline is the "
        "resolution point EXTENSIONS section 39 says injects NOTHING; see the "
        "code-side message above for the same-PR checklist."
    )

    # Doc side: the guide must not re-adopt the spec's "high" as this engine's
    # default, and must say what actually happens (unset -> provider default).
    default_cell = _authoring_guide_reasoning_effort_default_cell()
    assert default_cell != "`high`", (
        f"{_AUTHORING_GUIDE_REL}: the reasoning_effort Default cell says `high` "
        "again, but the engine injects no default (Node.reasoning_effort is "
        "None -- asserted above). That cell taught the canonical spec's "
        "Appendix A default as though it held here for as long as it shipped; "
        "the divergence is decided and ledgered (ATX-14, EXTENSIONS section 39)."
    )
    assert "unset" in default_cell.lower() and "provider" in default_cell.lower(), (
        f"{_AUTHORING_GUIDE_REL}: the reasoning_effort Default cell "
        f"({default_cell!r}) no longer says what an omitted attribute does "
        "(unset -> the provider's own default). Keep the real behavior in the "
        "cell or re-anchor this guard (D-243) with the reworded claim."
    )
