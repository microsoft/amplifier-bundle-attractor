"""Drift guard for context/engine-semantics.md -- D-200, D-201 (text-anchored half).

# --- Split from modules/loop-pipeline/tests/test_engine_semantics_doc_guard.py as
# part of the repo split's Track A (root guard harness, DESIGN-repo-split.md
# §1.4/§5#2). This half guards the OPINIONATED layer (context/engine-semantics.md
# doc text) via pure regex-over-prose, with no engine import, so it runs from the
# repo-root `tests/` suite. The behavior-anchored half (D-202a, which executes a
# real PipelineEngine) and the source-inspection half (D-202b, which reads engine
# .py source as text) stayed behind in modules/loop-pipeline/tests/ -- both need
# the engine's own code/source tree, which rides with the runner at extraction. ---

Guards against doc/engine drift in ``context/engine-semantics.md``, the
bundle's declared source of truth for shipped-engine behavior.  Two claim
classes are checked:

  (a) **Text-anchored** (shape b): regex the doc for the claim and assert
      the corrected language is present.  These verify that the *words* are
      there, not that the *behavior* is correct.  They catch edits that
      silently revert a correction.

  (b) **Behavior-anchored** (shape a): execute a minimal graph or inspect
      source and assert the doc's claim holds in the running code.  These
      verify actual engine/handler behavior.

Honest limits:
  - Text-anchored checks (D-200, D-201) can be fooled by wording changes
    that preserve the wrong meaning.  They are a last-resort gate, not a
    substitute for behavioral tests.
  - The no-matching-edge behavioral check (D-202a) exercises the main loop
    via a full engine run.  The companion ``test_edge_selection_no_silent_
    fallthrough.py`` covers ``select_edge()`` at the unit level.
  - The stale-label rule (D-201) has no dedicated behavioral test here: the
    bug only manifests on the *second visit* to a gate in a corrective loop,
    which requires a full looping graph — deferred to a future behavioral
    test (or lint).  The text-anchored check is the right tradeoff for now.
  - Source-inspection checks (D-202b) verify that the cited line numbers in
    the doc are in the right ballpark; they are NOT cite-freshness checks
    (which would break on every harmless refactor) but sanity checks that
    the referenced behavior exists at a nearby line.

Reference: ``context/engine-semantics.md`` §3 Routing contract.
Paired behavioral anchors: ``test_edge_selection_no_silent_fallthrough.py``,
``test_engine.py::test_no_matching_edge_returns_fail``.
"""

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUNDLE_ROOT = (
    Path(__file__).resolve().parent.parent
)  # <repo_root>/tests/ -> parent.parent
DOC_PATH = BUNDLE_ROOT / "context" / "engine-semantics.md"


def _doc() -> str:
    return DOC_PATH.read_text()


# ---------------------------------------------------------------------------
# D-200: No-matching-edge claim — text-anchored (shape b)
#
# The doc must NOT contain the stale "does NOT hard-fail" assertion.
# The doc MUST document the main-loop hard-fail AND distinguish the subgraph
# path.  Both halves are required; over-correcting to "always hard-fails"
# would be wrong in the other direction.
# ---------------------------------------------------------------------------


def test_d200_stale_no_matching_edge_claim_absent():
    """D-200a (text-anchored): the stale 'does NOT hard-fail' assertion must
    be gone from engine-semantics.md.

    The main loop DOES hard-fail on no-matching-edge (engine.py:773-788,
    PIPELINE_ERROR error_type=no_matching_edge; shipped behavior since the
    initial engine commit).  Presence of the old claim means the doc has
    regressed.
    """
    doc = _doc()
    assert not re.search(r"does NOT hard-?fail", doc, re.IGNORECASE), (
        "engine-semantics.md still contains the stale 'does NOT hard-fail "
        "no_matching_edge' claim. The main loop DOES hard-fail "
        "(engine.py:773-788). Remove or correct this claim. (D-200a)"
    )


def test_d200_no_matching_edge_main_loop_documented():
    """D-200b (text-anchored): the doc must mention no_matching_edge behavior."""
    doc = _doc()
    assert re.search(r"no.matching.edge|no_matching_edge", doc, re.IGNORECASE), (
        "engine-semantics.md does not mention no-matching-edge behavior at all. "
        "The main loop hard-fails with PIPELINE_ERROR error_type=no_matching_edge "
        "(engine.py:773-788). Add the corrected claim. (D-200b)"
    )


def test_d200_subgraph_path_distinguished():
    """D-200c (text-anchored): the doc must distinguish the subgraph path.

    run_subgraph now distinguishes two dead-end cases:
    - Conditional-mismatch (outgoing edges exist but none matched): returns FAIL
      with a non-empty failure_reason (issue-172 fix; EXTENSIONS.md §33 update).
    - No outgoing edges at all (designed terminus): returns last outcome unchanged.
    Both halves must be stated to avoid authors over-trusting the hard-fail
    inside parallel/manager layers.
    """
    doc = _doc()
    assert re.search(r"subgraph|run_subgraph|parallel branch", doc, re.IGNORECASE), (
        "engine-semantics.md documents no-matching-edge behavior but never "
        "distinguishes the subgraph path. run_subgraph distinguishes conditional-"
        "mismatch dead ends (FAIL with failure_reason) from designed termini "
        "(last outcome unchanged). Both halves must be stated. (D-200c)"
    )


# ---------------------------------------------------------------------------
# D-201: Stale-label rule — text-anchored (shape b)
#
# The doc must document that a failing tool node does NOT refresh
# tool.last_line.  The key retains the value from the last successful run.
# On the second visit to a gate, a stale label can match a
# context.tool.last_line=X edge even when the current run failed, causing
# an unintended parallel fan-out.
# ---------------------------------------------------------------------------


def test_d201_stale_label_rule_documented():
    """D-201 (text-anchored): the stale-label rule must be documented near
    tool.last_line in engine-semantics.md.

    Failing tool nodes do NOT refresh tool.last_line (early FAIL return at
    tool.py:158-176 precedes the context.set at tool.py:220).  Without this
    rule documented, authors write 'context.tool.last_line=X' edges beside
    'outcome=fail' edges and get a parallel fan-out on the second visit to
    the gate.  (D-201)
    """
    doc = _doc()
    # Find the context around 'last_line' and check for staleness language
    last_line_sections = []
    for m in re.finditer(r"last_line", doc, re.IGNORECASE):
        start = max(0, m.start() - 300)
        end = min(len(doc), m.end() + 300)
        last_line_sections.append(doc[start:end])

    assert last_line_sections, (
        "engine-semantics.md does not mention tool.last_line at all. "
        "The token channel section is missing. (D-201)"
    )

    staleness_pattern = re.compile(
        r"stale|not refresh|does not refresh|not updated|retain|preserv|previous",
        re.IGNORECASE,
    )
    found = any(staleness_pattern.search(section) for section in last_line_sections)
    assert found, (
        "engine-semantics.md mentions tool.last_line but does not document the "
        "stale-label rule: a failing tool node does NOT refresh tool.last_line "
        "(early FAIL return at tool.py:158-176 precedes context.set at tool.py:220). "
        "Add the staleness caveat and the '&& outcome=success' discipline. (D-201)"
    )
