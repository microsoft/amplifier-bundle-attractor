"""Engine-dependent residual of the doc/spec internal-consistency guard (D-240, "reasoning_effort" default).

The majority of this guard (D-135, D-137, D-241..D-243 -- pure prose-vs-prose
checks) moved to the repo-root opinionated-layer harness at
tests/test_doc_consistency.py (Track A of the repo split,
DESIGN-repo-split.md §1.4/§5#2). These two checks stayed here because they
assert the doc's claim against LIVE engine code
(amplifier_module_loop_pipeline.edge_selection / .graph.Node /
.transforms), not just against other doc text.
"""

import re
from pathlib import Path

BUNDLE_ROOT = Path(__file__).parent.parent.parent.parent


def _read(rel: str) -> str:
    return (BUNDLE_ROOT / rel).read_text()


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
        'canonical spec gives as "high" and this engine deliberately does not '
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
