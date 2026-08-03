"""Evidence tests: 04-retry-with-fallback.dot gate commands are truthful.

This file is the reproducible fixture behind the retry-with-fallback
tutorial's enforcement claims:

  1. `check_renegotiation` enforces a REAL disclosure record -- not a
     forgeable shape.  A renegotiation.md must contain the five required
     headings as distinct lines, in prescribed order, outside code fences,
     each with non-empty content beneath it.
  2. `validate_gate` is truthful on negative evidence (a failing regex
     yields gate_fail, never gate_pass) and enforces the budget wall
     (entry count past budget yields budget_exhausted).

Every test extracts the tool_command through the repository DOT parser
(`parse_dot`), so what is exercised is byte-identical to what the engine
executes -- a hand-copied command could drift; this cannot.

The negative cases document real forgery shapes that defeated earlier,
weaker versions of the gate:

  - A bare-heading file (five headings, no content) passed a
    substring+presence check.
  - A one-line "heading salad" (all five heading strings on one line plus
    one unrelated sentence) passed a substring+section-slicing check,
    because every alleged section claimed the same unrelated sentence as
    its content.

The current gate parses actual heading LINES and slices sections by line
boundaries, so both shapes -- and their neighbors (out-of-order headings,
duplicated headings, headings hidden in code fences, empty sections) --
are rejected.  "Heading presence is not section presence."
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from amplifier_module_loop_pipeline.dot_parser import parse_dot

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
_DOT_FILE = _REPO_ROOT / "examples" / "pipelines" / "04-retry-with-fallback.dot"


# ---------------------------------------------------------------------------
# Command extraction (engine-parsed, never hand-copied)
# ---------------------------------------------------------------------------


def _tool_command(node_id: str) -> str:
    graph = parse_dot(_DOT_FILE.read_text(encoding="utf-8"))
    command = graph.nodes[node_id].attrs.get("tool_command")
    assert command, f"{node_id} must carry a tool_command"
    return command


def _run_gate(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command, shell=True, cwd=cwd, text=True, capture_output=True, timeout=60
    )


# ---------------------------------------------------------------------------
# Disclosure-record fixtures
# ---------------------------------------------------------------------------

COMPLETE_RECORD = """\
## ORIGINAL GOAL
Validate email addresses per RFC 5322.
## RELAXED CRITERIA
Common email formats only (user@domain.com style).
## REASON
Budget exhaustion: validate_gate ran 4 times without the hard case set passing.
## WHAT THIS RUN WILL ACHIEVE
A regex that handles common formats.
## WHAT THIS RUN WILL NOT ACHIEVE
Quoted strings, domain literals, comments, and other RFC 5322 edge cases.
"""

PLAIN_COLON_RECORD = """\
ORIGINAL GOAL:
Validate email addresses per RFC 5322.
RELAXED CRITERIA:
Common email formats only.
REASON:
Budget exhaustion.
WHAT THIS RUN WILL ACHIEVE:
A common-format regex.
WHAT THIS RUN WILL NOT ACHIEVE:
RFC 5322 edge cases.
"""

NUMBERED_BOLD_RECORD = """\
1. **ORIGINAL GOAL:**
Validate email addresses per RFC 5322.
2. **RELAXED CRITERIA:**
Common email formats only.
3. **REASON:**
Budget exhaustion.
4. **WHAT THIS RUN WILL ACHIEVE:**
A common-format regex.
5. **WHAT THIS RUN WILL NOT ACHIEVE:**
RFC 5322 edge cases.
"""

# The one-line "heading salad" forgery: all five heading strings on a single
# line plus one unrelated sentence.  A substring-based checker saw every
# heading "present" and accepted the unrelated sentence as every section's
# content.  The line-based parser sees no heading LINE at all.
FORGED_HEADING_SALAD = (
    "ORIGINAL GOAL RELAXED CRITERIA REASON WHAT THIS RUN WILL ACHIEVE"
    " WHAT THIS RUN WILL NOT ACHIEVE\n"
    "This unrelated sentence is the only alleged section content.\n"
)

# Five bare headings, zero content: the "empty template" forgery.
BARE_HEADINGS = """\
ORIGINAL GOAL
RELAXED CRITERIA
REASON
WHAT THIS RUN WILL ACHIEVE
WHAT THIS RUN WILL NOT ACHIEVE
"""

OUT_OF_ORDER = """\
REASON
Budget exhaustion.
ORIGINAL GOAL
Validate email addresses per RFC 5322.
RELAXED CRITERIA
Common formats only.
WHAT THIS RUN WILL ACHIEVE
A common-format regex.
WHAT THIS RUN WILL NOT ACHIEVE
RFC 5322 edge cases.
"""

FENCED_HEADINGS = "```\n" + COMPLETE_RECORD + "```\n"

DUPLICATE_HEADING = COMPLETE_RECORD + "REASON\nA second, contradictory reason.\n"

ONE_EMPTY_SECTION = COMPLETE_RECORD.replace(
    "Budget exhaustion: validate_gate ran 4 times without the hard case set passing.\n",
    "",
)


# ---------------------------------------------------------------------------
# check_renegotiation: rejects forged / malformed records
# ---------------------------------------------------------------------------


class TestCheckRenegotiationRejectsForgedRecords:
    """The disclosure gate must reject every known forgery shape."""

    @pytest.mark.parametrize(
        ("name", "content", "expected_diag"),
        [
            ("heading_salad", FORGED_HEADING_SALAD, "missing heading line"),
            ("bare_headings", BARE_HEADINGS, "no content under"),
            ("out_of_order", OUT_OF_ORDER, "out of order"),
            ("fenced_headings", FENCED_HEADINGS, "missing heading line"),
            ("duplicate_heading", DUPLICATE_HEADING, "duplicate heading line"),
            ("one_empty_section", ONE_EMPTY_SECTION, "no content under"),
        ],
    )
    def test_malformed_record_rejected(
        self, tmp_path: Path, name: str, content: str, expected_diag: str
    ) -> None:
        (tmp_path / "renegotiation.md").write_text(content, encoding="utf-8")
        result = _run_gate(_tool_command("check_renegotiation"), tmp_path)

        assert result.returncode == 0, (
            f"[{name}] gate command must always exit 0 (printf sentinel), "
            f"got {result.returncode}: {result.stderr!r}"
        )
        assert result.stdout.strip() == "record_missing", (
            f"[{name}] forged/malformed record must yield record_missing, "
            f"got {result.stdout!r}"
        )
        diag = (tmp_path / "check_renegotiation_output.txt").read_text(encoding="utf-8")
        assert expected_diag in diag, (
            f"[{name}] diagnostic must name the defect ({expected_diag!r}) so the "
            f"retry loop can fix the record.  Got: {diag!r}"
        )

    def test_missing_file_rejected(self, tmp_path: Path) -> None:
        result = _run_gate(_tool_command("check_renegotiation"), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == "record_missing"
        diag = (tmp_path / "check_renegotiation_output.txt").read_text(encoding="utf-8")
        assert "MISSING" in diag


class TestCheckRenegotiationAcceptsRealRecords:
    """A genuine disclosure in any reasonable authoring style passes."""

    @pytest.mark.parametrize(
        ("name", "content"),
        [
            ("markdown_headings", COMPLETE_RECORD),
            ("plain_with_colons", PLAIN_COLON_RECORD),
            ("numbered_bold", NUMBERED_BOLD_RECORD),
        ],
    )
    def test_complete_record_accepted(
        self, tmp_path: Path, name: str, content: str
    ) -> None:
        (tmp_path / "renegotiation.md").write_text(content, encoding="utf-8")
        result = _run_gate(_tool_command("check_renegotiation"), tmp_path)

        assert result.returncode == 0
        assert result.stdout.strip() == "record_ok", (
            f"[{name}] complete record must yield record_ok, got {result.stdout!r}; "
            f"diag: {(tmp_path / 'check_renegotiation_output.txt').read_text()!r}"
        )


# ---------------------------------------------------------------------------
# validate_gate: truthful labels + budget wall
# ---------------------------------------------------------------------------

# Passes all seven cases in validate_gate's RFC 5322 case set (including the
# quoted-string and domain-literal cases the relaxed gate does not test).
RFC_CAPABLE_REGEX = (
    '("[^"]+"|[A-Za-z0-9.+_-]+)@(\\[[0-9.]+\\]|[A-Za-z0-9.-]+\\.[A-Za-z]{2,})'
)


class TestValidateGateTruthfulness:
    def test_failing_regex_yields_gate_fail(self, tmp_path: Path) -> None:
        """A regex that fails the case set must emit gate_fail -- never gate_pass."""
        (tmp_path / "email_regex.txt").write_text("[a-z]+\n", encoding="utf-8")
        result = _run_gate(_tool_command("validate_gate"), tmp_path)

        assert result.returncode == 0
        assert result.stdout.strip() == "gate_fail", (
            f"known-failing regex must yield gate_fail, got {result.stdout!r}"
        )
        report = (tmp_path / "validate_output.txt").read_text(encoding="utf-8")
        assert "FAIL" in report, "per-case results must land in validate_output.txt"

    def test_passing_regex_yields_gate_pass(self, tmp_path: Path) -> None:
        (tmp_path / "email_regex.txt").write_text(
            RFC_CAPABLE_REGEX + "\n", encoding="utf-8"
        )
        result = _run_gate(_tool_command("validate_gate"), tmp_path)

        assert result.returncode == 0
        assert result.stdout.strip() == "gate_pass", (
            f"case-set-passing regex must yield gate_pass, got {result.stdout!r}; "
            f"report: {(tmp_path / 'validate_output.txt').read_text()!r}"
        )

    def test_missing_regex_yields_gate_fail(self, tmp_path: Path) -> None:
        result = _run_gate(_tool_command("validate_gate"), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == "gate_fail"

    def test_budget_wall_fires_past_budget(self, tmp_path: Path) -> None:
        """Entry count past the budget (3) must emit budget_exhausted.

        Budget exhaustion is a decision point, not a fuse: the sentinel routes
        the drawn edge validate_gate -> renegotiate.
        """
        (tmp_path / "validate_count.txt").write_text("3\n", encoding="utf-8")
        # Even a PASSING regex must not be consulted past budget.
        (tmp_path / "email_regex.txt").write_text(
            RFC_CAPABLE_REGEX + "\n", encoding="utf-8"
        )
        result = _run_gate(_tool_command("validate_gate"), tmp_path)

        assert result.returncode == 0
        assert result.stdout.strip() == "budget_exhausted", (
            f"4th entry (count 3 -> 4 > budget 3) must yield budget_exhausted, "
            f"got {result.stdout!r}"
        )
        assert (tmp_path / "validate_count.txt").read_text().strip() == "4"

    def test_entry_count_increments(self, tmp_path: Path) -> None:
        (tmp_path / "email_regex.txt").write_text("[a-z]+\n", encoding="utf-8")
        _run_gate(_tool_command("validate_gate"), tmp_path)
        assert (tmp_path / "validate_count.txt").read_text().strip() == "1"
        _run_gate(_tool_command("validate_gate"), tmp_path)
        assert (tmp_path / "validate_count.txt").read_text().strip() == "2"


class TestValidateRelaxedTruthfulness:
    def test_failing_regex_yields_relaxed_fail(self, tmp_path: Path) -> None:
        (tmp_path / "email_regex.txt").write_text("[a-z]+\n", encoding="utf-8")
        result = _run_gate(_tool_command("validate_relaxed"), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == "relaxed_fail"

    def test_common_format_regex_yields_relaxed_pass(self, tmp_path: Path) -> None:
        """A common-format regex passes the RELAXED set (it would fail the hard set)."""
        (tmp_path / "email_regex.txt").write_text(
            "[A-Za-z0-9.+_-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\n", encoding="utf-8"
        )
        result = _run_gate(_tool_command("validate_relaxed"), tmp_path)
        assert result.returncode == 0
        assert result.stdout.strip() == "relaxed_pass", (
            f"common-format regex must pass the relaxed set, got {result.stdout!r}; "
            f"report: {(tmp_path / 'validate_relaxed_output.txt').read_text()!r}"
        )

    def test_relaxed_gate_differs_from_hard_gate(self, tmp_path: Path) -> None:
        """The SAME common-format regex fails the hard gate -- the visible
        difference between the two case sets IS the renegotiation."""
        (tmp_path / "email_regex.txt").write_text(
            "[A-Za-z0-9.+_-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{2,}\n", encoding="utf-8"
        )
        hard = _run_gate(_tool_command("validate_gate"), tmp_path)
        assert hard.stdout.strip() == "gate_fail", (
            "the common-format regex must FAIL the hard (RFC 5322) gate -- "
            f"got {hard.stdout!r}"
        )


# ---------------------------------------------------------------------------
# Topology: the renegotiation path is drawn, not invisible ink
# ---------------------------------------------------------------------------


class TestRenegotiationTopology:
    def test_budget_exhaustion_edge_is_drawn(self) -> None:
        graph = parse_dot(_DOT_FILE.read_text(encoding="utf-8"))
        edges = [
            e
            for e in graph.edges
            if e.from_node == "validate_gate" and e.to_node == "renegotiate"
        ]
        assert edges, "validate_gate -> renegotiate edge must be drawn"
        assert "budget_exhausted" in (edges[0].condition or ""), (
            "the budget-exhaustion edge must route on the budget_exhausted sentinel"
        )

    def test_relaxed_path_blocked_behind_disclosure_gate(self) -> None:
        """simple_implement's only drawn entry is check_renegotiation on record_ok."""
        graph = parse_dot(_DOT_FILE.read_text(encoding="utf-8"))
        incoming = [e for e in graph.edges if e.to_node == "simple_implement"]
        sources = {e.from_node for e in incoming}
        assert sources == {"check_renegotiation", "validate_relaxed"}, (
            f"relaxed implementation must be entered only via the disclosure gate "
            f"(or its own retry loop), got sources: {sources}"
        )
        gate_edges = [e for e in incoming if e.from_node == "check_renegotiation"]
        assert "record_ok" in (gate_edges[0].condition or "")

    def test_no_edge_orphan_work_nodes(self) -> None:
        graph = parse_dot(_DOT_FILE.read_text(encoding="utf-8"))
        targets = {e.to_node for e in graph.edges}
        entries = {n.id for n in graph.nodes.values() if n.shape == "Mdiamond"}
        orphans = [n for n in graph.nodes if n not in targets and n not in entries]
        assert not orphans, (
            f"every work node must have a drawn incoming edge, orphans: {orphans}"
        )
