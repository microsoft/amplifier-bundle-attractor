"""Tests for the inert-vocabulary lint rule — VOCAB-001.

VOCAB-001: an LLM (codergen) node that carries NO ``prompt=`` but does carry an
invented attribute spelling the parser keeps and no handler ever reads
(``instruction=``, ``agent=``, ``goal=`` at node level, …).  The node runs with
no prompt at all while reading as fully configured — the failure issue #261
measured in two graded sessions.

Test pattern mirrors test_command_content_lint.py / test_topological_lint.py:
construct Graph/Node/Edge objects directly (no DOT parsing) for speed and
isolation, except where the point of the test is the parse path itself.

False-positive discipline is the primary focus.  The rule reads *intent* from
an attribute the engine is entitled to ignore, so every silent case below
documents WHY the node is legitimately not a defect.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.graph import Edge, Graph, Node
from amplifier_module_loop_pipeline.validation import (
    Diagnostic,
    lint,
    validate,
)

# ---------------------------------------------------------------------------
# Helpers — mirrors test_command_content_lint.py
# ---------------------------------------------------------------------------


def _mdiamond(node_id: str = "start") -> Node:
    return Node(id=node_id, shape="Mdiamond", label="Start")


def _msquare(node_id: str = "done") -> Node:
    return Node(id=node_id, shape="Msquare", label="Done")


def _graph(nodes: dict[str, Node], edges: list[Edge] | None = None) -> Graph:
    return Graph(name="test", nodes=nodes, edges=edges or [])


def _wrap(work: Node) -> Graph:
    """A minimal valid graph: start -> work -> done."""
    return _graph(
        nodes={"start": _mdiamond(), work.id: work, "done": _msquare()},
        edges=[Edge("start", work.id), Edge(work.id, "done")],
    )


def _vocab(diags: list[Diagnostic]) -> list[Diagnostic]:
    return [d for d in diags if d.rule == "VOCAB-001"]


def _rule(diags: list[Diagnostic], rule: str) -> list[Diagnostic]:
    return [d for d in diags if d.rule == rule]


# ---------------------------------------------------------------------------
# (a) Positive cases — the rule MUST fire
# ---------------------------------------------------------------------------


class TestVocab001Fires:
    def test_instruction_without_prompt_warns(self) -> None:
        """The exact issue #261 shape: shape=box + instruction=, no prompt."""
        node = Node(
            id="fetch_pr",
            shape="box",
            label="Fetch PR Branch",
            attrs={"instruction": "Fetch the PR branch using git."},
        )
        found = _vocab(lint(_wrap(node)))
        assert len(found) == 1
        d = found[0]
        assert d.severity == "WARNING"
        assert d.node_id == "fetch_pr"
        assert "will run with no prompt" in d.message
        assert "`instruction=`" in d.message
        assert "`prompt=`" in d.message
        assert "prompt=" in d.fix

    def test_message_names_node_invented_attr_and_real_attr(self) -> None:
        """The message must carry all three facts an author needs to act."""
        node = Node(id="review", shape="box", attrs={"instruction": "review it"})
        (d,) = _vocab(lint(_wrap(node)))
        assert "'review'" in d.message  # the node
        assert "`instruction=`" in d.message  # the invented attribute
        assert "`prompt=`" in d.message  # the one the engine reads

    def test_agent_without_prompt_warns(self) -> None:
        """`agent=` is inert too — the engine picks the handler from shape=."""
        node = Node(id="fetch", shape="box", attrs={"agent": "foundation:git-ops"})
        (d,) = _vocab(lint(_wrap(node)))
        assert "`agent=`" in d.message
        assert "shape=box" in d.fix

    def test_agent_and_instruction_reported_together_once(self) -> None:
        """A node carrying both gets ONE diagnostic naming both."""
        node = Node(
            id="fetch_branch",
            shape="box",
            label="Fetch PR Branch",
            attrs={"agent": "foundation:git-ops", "instruction": "check it out"},
        )
        found = _vocab(lint(_wrap(node)))
        assert len(found) == 1, "one diagnostic per node, not one per attribute"
        assert "`instruction=`" in found[0].message
        assert "`agent=`" in found[0].message

    def test_node_level_goal_without_prompt_warns(self) -> None:
        """A *node*-level goal= is inert; graph-level goal= is real (untouched)."""
        node = Node(id="work", shape="box", attrs={"goal": "make it converge"})
        assert len(_vocab(lint(_wrap(node)))) == 1

    def test_attractor_goal_without_prompt_warns(self) -> None:
        node = Node(id="work", shape="box", attrs={"attractor_goal": "converge"})
        assert len(_vocab(lint(_wrap(node)))) == 1

    def test_missing_shape_defaults_to_llm_and_warns(self) -> None:
        """Node.shape defaults to 'box' — the second evidence file's shape."""
        node = Node(id="run_tests", label="Run Tests", attrs={"instruction": "go"})
        assert node.shape == "box"
        assert len(_vocab(lint(_wrap(node)))) == 1

    def test_explicit_type_codergen_warns(self) -> None:
        """type=codergen dispatches to the LLM handler regardless of shape."""
        node = Node(
            id="work",
            shape="ellipse",
            type="codergen",
            attrs={"instruction": "do the thing"},
        )
        assert len(_vocab(lint(_wrap(node)))) == 1

    def test_explicit_label_does_not_suppress(self) -> None:
        """The near-miss that let #261 through: prompt_on_llm_nodes needs BOTH
        no-prompt AND no-explicit-label.  Every evidence node had a label."""
        node = Node(
            id="fetch_pr",
            shape="box",
            label="Fetch PR Branch",
            attrs={"instruction": "fetch it"},
        )
        diags = lint(_wrap(node))
        assert _rule(diags, "prompt_on_llm_nodes") == [], (
            "the labelled node is exactly what the old rule cannot see"
        )
        assert len(_vocab(diags)) == 1


# ---------------------------------------------------------------------------
# (b) Invalid fidelity — already shipped as `fidelity_valid`; pinned here so
#     the second half of issue #261's ask cannot silently regress.
# ---------------------------------------------------------------------------


class TestFidelityValidCoversIssue261:
    @pytest.mark.parametrize("bad", ["stateless", "fresh"])
    def test_invented_fidelity_value_warns(self, bad: str) -> None:
        node = Node(id="work", shape="box", prompt="do it", attrs={"fidelity": bad})
        found = _rule(lint(_wrap(node)), "fidelity_valid")
        assert len(found) == 1
        assert found[0].severity == "WARNING"
        assert bad in found[0].message
        assert "compact" in found[0].message  # the valid set is named

    @pytest.mark.parametrize(
        "good",
        [
            "full",
            "truncate",
            "compact",
            "summary:low",
            "summary:medium",
            "summary:high",
        ],
    )
    def test_valid_fidelity_is_silent(self, good: str) -> None:
        node = Node(id="work", shape="box", prompt="do it", attrs={"fidelity": good})
        assert _rule(lint(_wrap(node)), "fidelity_valid") == []


# ---------------------------------------------------------------------------
# (c) False-positive pins — the rule MUST stay silent
# ---------------------------------------------------------------------------


class TestVocab001FalsePositives:
    def test_prompt_plus_other_attr_is_silent(self) -> None:
        """A node can legitimately carry prompt= AND another attribute."""
        node = Node(
            id="work",
            shape="box",
            prompt="Do the real work",
            attrs={"instruction": "leftover from an edit", "fidelity": "full"},
        )
        assert _vocab(lint(_wrap(node))) == []

    def test_prompt_plus_agent_is_silent(self) -> None:
        node = Node(
            id="work",
            shape="box",
            prompt="Do the real work",
            attrs={"agent": "foundation:git-ops"},
        )
        assert _vocab(lint(_wrap(node))) == []

    def test_tool_node_without_prompt_is_silent(self) -> None:
        """A tool node never takes a prompt — its config is tool_command=."""
        node = Node(
            id="gate",
            shape="parallelogram",
            attrs={"tool_command": "pytest -q", "instruction": "run the suite"},
        )
        assert _vocab(lint(_wrap(node))) == []

    def test_human_gate_without_prompt_is_silent(self) -> None:
        node = Node(id="approve", shape="hexagon", label="Human Approval")
        assert _vocab(lint(_wrap(node))) == []

    def test_human_gate_with_instruction_is_silent(self) -> None:
        """Even carrying an inert spelling — a hexagon is not an LLM node."""
        node = Node(
            id="approve",
            shape="hexagon",
            label="Human Approval",
            attrs={"instruction": "approve the release"},
        )
        assert _vocab(lint(_wrap(node))) == []

    def test_conditional_node_is_silent(self) -> None:
        node = Node(id="router", shape="diamond", label="Route")
        assert _vocab(lint(_wrap(node))) == []

    def test_folder_node_with_goal_is_silent(self) -> None:
        """A sub-pipeline node passing goal= to its child is not an LLM node."""
        node = Node(
            id="child",
            shape="folder",
            attrs={"dot_file": "child.dot", "goal": "converge the child"},
        )
        assert _vocab(lint(_wrap(node))) == []

    def test_start_and_exit_nodes_are_silent(self) -> None:
        start = Node(id="start", shape="Mdiamond", attrs={"instruction": "begin"})
        done = Node(id="done", shape="Msquare", attrs={"instruction": "end"})
        graph = _graph(
            nodes={"start": start, "done": done}, edges=[Edge("start", "done")]
        )
        assert _vocab(lint(graph)) == []

    def test_llm_node_with_no_prompt_and_no_inert_attr_is_silent(self) -> None:
        """Bare no-prompt LLM node is prompt_on_llm_nodes' territory, not ours."""
        node = Node(id="work", shape="box", label="Do Work")
        assert _vocab(lint(_wrap(node))) == []

    def test_typoed_shape_is_left_to_shape_resolvable(self) -> None:
        """No double-diagnosis: dispatch hard-fails on an unknown shape
        (specs/EXTENSIONS.md §38) and shape_resolvable already ERRORs."""
        node = Node(id="work", shape="parallelgram", attrs={"instruction": "run it"})
        diags = lint(_wrap(node))
        assert _vocab(diags) == []
        assert len(_rule(diags, "shape_resolvable")) == 1

    def test_graph_level_goal_is_untouched(self) -> None:
        """Graph-level goal= is a real attribute backing $goal substitution."""
        graph = _wrap(Node(id="work", shape="box", prompt="do it"))
        graph.graph_attrs["goal"] = "converge the repo"
        graph.goal = "converge the repo"
        assert _vocab(lint(graph)) == []


# ---------------------------------------------------------------------------
# (d) Entry-point + exit-code contract
# ---------------------------------------------------------------------------


class TestVocab001Contract:
    def test_never_emits_error_severity(self) -> None:
        """Advisory only — VOCAB-001 must never block a run (rc stays 0)."""
        node = Node(id="work", shape="box", attrs={"instruction": "go", "agent": "x"})
        diags = lint(_wrap(node))
        assert _vocab(diags), "sanity: the rule fired"
        assert [d for d in diags if d.severity == "ERROR"] == [], (
            "a VOCAB-001-only graph must produce zero ERRORs so lint exits 0"
        )
        assert all(d.severity == "WARNING" for d in _vocab(diags))

    def test_lint_only_not_in_validate(self) -> None:
        """validate()/validate_or_raise() — the admission gate — is untouched."""
        graph = _wrap(Node(id="work", shape="box", attrs={"instruction": "go"}))
        assert _vocab(validate(graph)) == []
        assert _vocab(lint(graph)) != []


# ---------------------------------------------------------------------------
# (e) Regression: the measured evidence shape from issue #261, via the parser
# ---------------------------------------------------------------------------


_EVIDENCE_DOT = """
digraph pr_automation {
    start [shape=Mdiamond, label="Start"];
    exit  [shape=Msquare,  label="Exit"];

    fetch_pr [
        shape=box,
        label="Fetch PR Branch",
        instruction="Fetch the PR branch using git.",
        fidelity="stateless"
    ];

    run_linter [
        shape=box,
        label="Run Linter",
        instruction="Run the linter on the fetched PR branch.",
        fidelity="stateless"
    ];

    start -> fetch_pr;
    fetch_pr -> run_linter;
    run_linter -> exit;
}
"""


class TestIssue261EvidenceShape:
    def test_evidence_dot_is_caught_end_to_end(self) -> None:
        """Parse -> lint: both LLM nodes flagged, both fidelities flagged, no ERROR."""
        graph = parse_dot(_EVIDENCE_DOT)
        diags = lint(graph)

        vocab_nodes = {d.node_id for d in _vocab(diags)}
        assert vocab_nodes == {"fetch_pr", "run_linter"}

        fidelity_nodes = {d.node_id for d in _rule(diags, "fidelity_valid")}
        assert fidelity_nodes == {"fetch_pr", "run_linter"}

        assert [d for d in diags if d.severity == "ERROR"] == [], (
            "the whole finding is advisory — rc must stay 0"
        )

    def test_renaming_instruction_to_prompt_clears_the_warning(self) -> None:
        """The fix the message prescribes actually resolves the finding."""
        fixed = _EVIDENCE_DOT.replace("instruction=", "prompt=").replace(
            'fidelity="stateless"', 'fidelity="compact"'
        )
        diags = lint(parse_dot(fixed))
        assert _vocab(diags) == []
        assert _rule(diags, "fidelity_valid") == []


# ---------------------------------------------------------------------------
# (f) Calibration: zero fires across the shipped examples corpus
#     (mirrors TestOutcomeLabelShadowingCalibration in test_topological_lint.py)
# ---------------------------------------------------------------------------


_REPO_ROOT = Path(__file__).resolve().parents[3]
_EXAMPLES_DIR = _REPO_ROOT / "examples"
_EXAMPLE_DOTS = sorted(_EXAMPLES_DIR.rglob("*.dot")) if _EXAMPLES_DIR.is_dir() else []


@pytest.mark.skipif(
    not _EXAMPLE_DOTS,
    reason="examples/ directory not present (installed-package run)",
)
def test_vocab_001_fires_on_zero_shipped_examples() -> None:
    """Calibration pin: every shipped example uses prompt= correctly.

    If this goes red, the example is wrong — fix the example, do not
    loosen the rule.
    """
    offenders: list[str] = []
    for dot_path in _EXAMPLE_DOTS:
        graph = parse_dot(dot_path.read_text(encoding="utf-8"))
        for d in _vocab(lint(graph)):
            offenders.append(f"{dot_path.relative_to(_REPO_ROOT)}: {d.message}")
    assert not offenders, "VOCAB-001 fired on shipped examples:\n" + "\n".join(
        offenders
    )
