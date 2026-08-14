"""Tests for doc/spec internal consistency — D-135, D-137.

These are regression guards against documentation contradictions.
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
    """DOT-AUTHORING-GUIDE.md table row for default_max_retry must show default 0 (D-135)."""
    content = _read("docs/DOT-AUTHORING-GUIDE.md")
    matches = re.findall(
        r"\|\s*`default_max_retry`\s*\|\s*Integer\s*\|\s*`(\d+)`", content
    )
    assert matches, "default_max_retry table row not found in DOT-AUTHORING-GUIDE.md"
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
