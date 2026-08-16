#!/usr/bin/env python3
"""Structural gate on an AUTHORED pipeline (the authoring contract).

``pipeline-author.dot`` lets an LLM node WRITE a new reusable attractor pipeline
from a design brief. That is only worth doing if the result is checked by
something outside the author's context before anyone is asked to trust it. Three
gates do that, in order:

1. ``attractor lint`` -- executability and the topology bug classes the engine
   itself knows about (dead conditional edges, stale-label collisions,
   fail-routed-to-exit, pipe-masked gate exit codes). ERRORs block.
2. this script -- the *doctrine* checks lint deliberately does not own, or owns
   only as advice: is the authored graph actually an attractor, is its exit
   really unreachable without machine evidence, does it carry a budget wall,
   and does every worker have somewhere to fail to?
3. an independent critique, in a fresh context, which reads what these two
   printed and judges what neither can.

**The load-bearing check is A4.** A1-A3 can all pass on a graph whose exit is
reachable by a path that never touches a gate: a corrective loop can sit off to
one side while ``done`` hangs directly off a worker. A4 deletes every
evidence-bearing gate from the graph and asks whether the exit is still
reachable from ``start``. If it is, the exit was never gated on evidence --
which is the one property the whole doctrine rests on
(``docs/PIPELINE_DESIGN_PRINCIPLES.md`` section 0).

**A10 asks the question A4 leaves over.** A4 proves the exit is reached
*through* a gate; it says nothing about whether the gate's answer mattered. A
graph that routes both of a gate's tokens into the exit passes A3 (the command
is real), A4 (the exit is gate-protected) and A8 (no *failure outcome* goes near
the exit) while ending green whether the tests passed or failed. That shape is
the cheapest way to comply with the letter of "do not weaken a gate" while
defeating it: the gate is not weakened, deleted or relaxed -- it is left fully
intact and simply unwired. A10 is pure topology, like A4, and its boundaries are
stated on ``inert_gate_routes`` rather than left to be discovered.

**What this script deliberately does NOT do** is judge whether the authored
pipeline is any *good* for the brief it came from. Structure is checkable;
fitness for purpose is not. That is the critique node's job, and it is why the
authoring pipeline still pays for an LLM after these gates have already run.

Token contract (Idiom A -- always exit 0 so ``tool.last_line`` is always fresh):

  doctrine_ok    every check passed; the authored pipeline may go to critique
  doctrine_bad   at least one check failed; route back to the author

A nonzero exit means this script could not run at all (unwritable report). That
routes through ``outcome=fail`` to the postmortem path -- deliberately distinct
from ``doctrine_bad``, which is a judgement about the authored graph. A missing
or unparseable pipeline file is a *judgement* (``doctrine_bad``), not a crash:
the author node is the one that was supposed to write it.

DOT parsing is deliberately stdlib-only and self-contained, for the same reason
``examples/objective/check_child_contract.py`` is: this gate must run under
whatever ``python3`` is on PATH in the target workspace, which is not the
``attractor`` CLI's own virtualenv. It is a *second* opinion, and it runs only
after the engine's own parser has already accepted the file via ``attractor
lint`` -- so a disagreement fails closed (``doctrine_bad``) rather than silently
admitting a graph neither tool understood.

Usage:
    check_authored_pipeline.py --pipeline out/my-pipeline.dot \
                               --companion out/my-pipeline.md \
                               --report .authoring/doctrine-report.txt
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# A small DOT reader -- only what the structural checks need
#
# Adapted from examples/objective/check_child_contract.py, which is pinned
# against the engine's own parser by a shipped test. This copy carries its own
# parser-agreement test for the same reason: a second opinion is only worth
# anything if it reads the same graph the engine reads.
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
    (?P<string>"(?:[^"\\]|\\.)*")        # quoted string (backslash escapes)
  | (?P<arrow>->|--)                      # edge operators
  | (?P<punct>[\[\]{};,=])                # structural punctuation
  | (?P<ident>[A-Za-z_\u0080-\uffff][A-Za-z_0-9.\u0080-\uffff]*|-?\.?[0-9][0-9.]*)
    """,
    re.VERBOSE,
)

_KEYWORDS = {"digraph", "graph", "strict", "subgraph", "node", "edge"}


def _strip_comments(text: str) -> str:
    """Remove //, #, and /* */ comments without touching quoted strings."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            out.append(ch)
            i += 1
            while i < n:
                out.append(text[i])
                if text[i] == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
                if text[i] == '"':
                    i += 1
                    break
                i += 1
            continue
        if text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            continue
        if ch == "#" and (not out or out[-1] == "\n"):
            while i < n and text[i] != "\n":
                i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _unquote(token: str) -> str:
    """Strip the quotes and process escapes exactly as the engine's parser does.

    ORDER MATTERS, and it is the engine's order (``dot_parser._parse_value``):
    ``\\\\`` collapses to ``\\`` FIRST, so a source ``\\\\n`` -- the escape
    pattern the shipped graphs use between shell statements -- becomes a real
    newline rather than a backslash followed by one.  Reproducing the order
    rather than approximating it is what lets the parser-agreement test compare
    attribute *values*, not just node ids: a reader that agreed on the shape of
    the graph while disagreeing about what a gate's command says would gate on
    a string the engine never runs.
    """
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        body = token[1:-1]
        body = body.replace("\\\\", "\\")
        body = body.replace('\\"', '"')
        body = body.replace("\\n", "\n")
        body = body.replace("\\t", "\t")
        return body
    return token


def _tokenize(text: str) -> list[str]:
    return [m.group(0) for m in _TOKEN_RE.finditer(_strip_comments(text))]


@dataclass
class DotNode:
    node_id: str
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class DotEdge:
    src: str
    dst: str
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass
class DotGraph:
    nodes: dict[str, DotNode] = field(default_factory=dict)
    edges: list[DotEdge] = field(default_factory=list)
    graph_attrs: dict[str, str] = field(default_factory=dict)
    node_defaults: dict[str, str] = field(default_factory=dict)

    def node(self, node_id: str) -> DotNode:
        node = self.nodes.get(node_id)
        if node is None:
            node = DotNode(node_id)
            self.nodes[node_id] = node
        return node

    def attr(self, node_id: str, key: str, default: str = "") -> str:
        node = self.nodes.get(node_id)
        if node is not None and key in node.attrs:
            return node.attrs[key]
        return self.node_defaults.get(key, default)

    def outgoing(self, node_id: str) -> list[DotEdge]:
        return [e for e in self.edges if e.src == node_id]


class DotParseError(Exception):
    """The authored .dot could not be read as a graph."""


def parse_dot_min(text: str) -> DotGraph:
    """Parse the subset of DOT the shipped pipelines actually use."""
    tokens = _tokenize(text)
    if not tokens or tokens[0] not in ("strict", "digraph", "graph"):
        raise DotParseError(
            "does not start with a graph header (strict/digraph/graph) -- "
            "this is not a DOT graph"
        )
    graph = DotGraph()
    i = 0
    total = len(tokens)

    def read_attr_list(idx: int) -> tuple[dict[str, str], int]:
        attrs: dict[str, str] = {}
        assert tokens[idx] == "["
        idx += 1
        while idx < total and tokens[idx] != "]":
            if tokens[idx] in (",", ";"):
                idx += 1
                continue
            key = _unquote(tokens[idx])
            idx += 1
            if idx < total and tokens[idx] == "=":
                idx += 1
                if idx >= total:
                    raise DotParseError("attribute list ended after '='")
                attrs[key] = _unquote(tokens[idx])
                idx += 1
            else:
                attrs[key] = "true"
        if idx >= total:
            raise DotParseError("unterminated attribute list")
        return attrs, idx + 1

    while i < total:
        tok = tokens[i]

        if tok in ("{", "}", ";", ","):
            i += 1
            continue

        # `graph|node|edge [ ... ]` -- attribute defaults. Checked BEFORE the
        # header-keyword branch, because `graph` is both a keyword and the name
        # of the graph-attribute statement.
        if tok in ("graph", "node", "edge") and i + 1 < total and tokens[i + 1] == "[":
            defaults, i = read_attr_list(i + 1)
            if tok == "node":
                graph.node_defaults.update(defaults)
            elif tok == "graph":
                graph.graph_attrs.update(defaults)
            continue

        if tok in ("digraph", "graph", "strict", "subgraph"):
            # Skip the header keyword and any graph name; the body is flat for
            # our purposes (cluster subgraphs are flattened by the engine too).
            i += 1
            while i < total and tokens[i] not in ("{", ";"):
                i += 1
            continue

        # A bare `key = value` at statement level is a graph attribute.
        if i + 2 < total and tokens[i + 1] == "=" and tok not in _KEYWORDS:
            graph.graph_attrs[_unquote(tok)] = _unquote(tokens[i + 2])
            i += 3
            continue

        # Node statement or edge chain.
        chain = [_unquote(tok)]
        i += 1
        while i < total and tokens[i] in ("->", "--"):
            i += 1
            if i >= total:
                raise DotParseError("edge chain ended after '->'")
            chain.append(_unquote(tokens[i]))
            i += 1

        stmt_attrs: dict[str, str] = {}
        if i < total and tokens[i] == "[":
            stmt_attrs, i = read_attr_list(i)

        if len(chain) == 1:
            graph.node(chain[0]).attrs.update(stmt_attrs)
        else:
            for src, dst in zip(chain, chain[1:], strict=False):
                graph.node(src)
                graph.node(dst)
                graph.edges.append(DotEdge(src, dst, dict(stmt_attrs)))

    if not graph.nodes:
        raise DotParseError("no nodes found")
    return graph


# ---------------------------------------------------------------------------
# Shape helpers -- mirror the engine's own resolution order
# ---------------------------------------------------------------------------


def _is_exit(graph: DotGraph, node_id: str) -> bool:
    return (
        graph.attr(node_id, "shape") == "Msquare"
        or graph.attr(node_id, "type") == "exit"
        or node_id.lower() in ("exit", "end")
    )


def _is_start(graph: DotGraph, node_id: str) -> bool:
    return (
        graph.attr(node_id, "shape") == "Mdiamond"
        or graph.attr(node_id, "type") == "start"
        or node_id.lower() == "start"
    )


def _is_tool(graph: DotGraph, node_id: str) -> bool:
    return graph.attr(node_id, "shape") == "parallelogram" or bool(
        graph.attr(node_id, "tool_command")
    )


def _is_worker(graph: DotGraph, node_id: str) -> bool:
    """A codergen (LLM) node: shape=box, or no shape at all (box is the default)."""
    if _is_start(graph, node_id) or _is_exit(graph, node_id):
        return False
    if graph.attr(node_id, "type"):
        return False
    return graph.attr(node_id, "shape") in ("", "box")


def _starts(graph: DotGraph) -> list[str]:
    return sorted(n for n in graph.nodes if _is_start(graph, n))


def _reachable(graph: DotGraph, sources: list[str], blocked: set[str] | None = None) -> set[str]:
    """Nodes reachable from *sources*, never entering any node in *blocked*."""
    blocked = blocked or set()
    seen: set[str] = set()
    stack = [s for s in sources if s not in blocked]
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        for edge in graph.outgoing(node_id):
            if edge.dst not in blocked:
                stack.append(edge.dst)
    return seen


def _has_cycle(graph: DotGraph) -> bool:
    adjacency: dict[str, list[str]] = {n: [] for n in graph.nodes}
    for edge in graph.edges:
        adjacency.setdefault(edge.src, []).append(edge.dst)
    state: dict[str, int] = {}

    def visit(node_id: str) -> bool:
        state[node_id] = 1
        for nxt in adjacency.get(node_id, []):
            mark = state.get(nxt, 0)
            if mark == 1:
                return True
            if mark == 0 and visit(nxt):
                return True
        state[node_id] = 2
        return False

    sys.setrecursionlimit(10000)
    return any(state.get(n, 0) == 0 and visit(n) for n in graph.nodes)


_IS_FAIL_RE = re.compile(r"outcome\s*=\s*fail\b")
_NOT_SUCCESS_RE = re.compile(r"outcome\s*!=\s*success\b")
_SUCCESS_CONJUNCTION_RE = re.compile(r"outcome\s*=\s*success\b")
_LAST_LINE_RE = re.compile(r"context\.tool\.last_line\s*=")

#: The token an edge routes ON, as opposed to one it routes AWAY from.
#: ``context.tool.last_line=green && outcome=success`` yields ``green``. An
#: inequality cannot match -- ``last_line!=green`` puts a ``!`` where this
#: pattern requires ``=`` -- and that exclusion is deliberate: A10 reasons about
#: which *answer* sends the run where, and "anything but green" is not an answer.
_LAST_LINE_TOKEN_RE = re.compile(r"context\.tool\.last_line\s*=\s*([^\s&|]+)")

#: Shapes that route without doing anything. Entering one of these and leaving
#: by its single unconditional edge is indistinguishable, in every observable
#: respect, from having taken that edge directly -- ``diamond`` is documented as
#: a no-op node whose "outgoing edges do the deciding". A10 sees through exactly
#: these and nothing else, so "both answers landed in the same place" can never
#: quietly mean "the two branches ran different work and then converged".
_TRANSPARENT_SHAPES = frozenset({"diamond", "point"})


def _routed_token(condition: str) -> str | None:
    """The ``tool.last_line`` value this edge condition selects on, if any."""
    match = _LAST_LINE_TOKEN_RE.search(condition)
    if match is None:
        return None
    return match.group(1).strip().strip('"').strip("'")


def _transparent_relay(graph: DotGraph, node_id: str) -> DotEdge | None:
    """The single edge out of a pure routing no-op, or ``None`` if not one."""
    if _is_exit(graph, node_id) or _is_start(graph, node_id):
        return None
    if graph.attr(node_id, "shape") not in _TRANSPARENT_SHAPES:
        return None
    out = graph.outgoing(node_id)
    if len(out) != 1 or out[0].attrs.get("condition"):
        return None
    return out[0]


def _token_landing(graph: DotGraph, edge: DotEdge) -> str:
    """Where a token edge actually puts the run, seeing through relay no-ops."""
    node_id = edge.dst
    seen = {edge.src}
    while node_id not in seen:
        seen.add(node_id)
        relay = _transparent_relay(graph, node_id)
        if relay is None:
            break
        node_id = relay.dst
    return node_id


def _is_failure_condition(condition: str) -> bool:
    """Does this edge condition select the FAILURE branch of its source node?"""
    return bool(_IS_FAIL_RE.search(condition) or _NOT_SUCCESS_RE.search(condition))


#: The last statement of a command that ends the process nonzero, deliberately.
#: Anchored at the end because what matters is the EXIT STATUS THE NODE LEAVES
#: WITH -- an `exit 1` on some interior branch says nothing about the others.
_TERMINAL_NONZERO_EXIT_RE = re.compile(r"(?:^|;|&&|\|\||\n)\s*exit\s+[1-9][0-9]*\s*;?\s*$")


def _is_loud_terminal(graph: DotGraph, node_id: str) -> bool:
    """Is this node a DESIGNED LOUD TERMINAL, whose own FAIL becomes the status?

    A8's hazard is a failure being CONVERTED into a successful run -- classically
    by putting one succeeding bookkeeping step (a recorder, a notifier, a
    cleanup) between the failed gate and the exit, so the run's final status
    comes from that step and not from the failure.  There is exactly one shape
    where routing a failure into the exit does NOT do that, and the engine
    settles it: at the exit node, with every goal gate satisfied,
    ``_check_goal_gates()`` returns ``node_outcomes[completed_nodes[-1]]`` -- the
    LAST COMPLETED node's outcome.  So if the node that routes into the exit is
    itself the last thing that runs and it exits nonzero, the run's status IS
    that failure: ``status=fail``, CLI exit 1.  That is the convergence-factory
    idiom -- "a budget-exhaustion exit that deliberately ends at the single exit
    node with a genuine FAIL outcome" (docs/DOT-AUTHORING-GUIDE.md, TOPO-006),
    shipped in ``examples/patterns/convergence-factory.dot`` and adopted by
    ``examples/objective/objective-runner.dot`` in #248 -- and it is the ONLY
    way a deliberately loud terminal can exist on this engine at all, because
    the main loop has no designed-terminus concept: a terminal that dead-ends
    is reported as PIPELINE_ERROR ``error_type=no_matching_edge`` (issue #252).

    Blocking it was therefore not strictness, it was a miscalibration: A8
    rejected the repo's own merged flagship.  A gate that rejects
    ``objective-runner.dot`` is a wrong gate, not a strict one -- the same rule
    this contract's calibration suite applies to ``task-runner.dot``.

    The exemption is deliberately narrow, so it cannot be worn as a costume by
    the shape A8 exists to catch.  ALL FOUR must hold:

    1. It is a TOOL node.  A worker's "failure" is a provider verdict, not a
       process exit status; only a shell command can guarantee the exit code.
    2. Its command's LAST statement exits NONZERO -- so it fails on EVERY path,
       not just some branch.  A node that can exit 0 could hand the exit a
       SUCCESS and turn the escalation green: precisely the A8 hazard.
    3. ``max_retries=0``.  The failure is the point, not a flake to retry.
    4. The edge into the exit is its ONLY outgoing edge.  A node with somewhere
       else to go is a step on a path, and a step on a path is the bookkeeping
       intermediary A8 is looking for.

    A recorder that exits 0, a notifier with a second route, or an LLM node all
    still block -- see the mutation tests in ``test_authoring_layer_gates.py``.
    """
    if not _is_tool(graph, node_id):
        return False
    command = graph.attr(graph_node_id := node_id, "tool_command")
    if not command or not _TERMINAL_NONZERO_EXIT_RE.search(command.strip()):
        return False
    if graph.attr(graph_node_id, "max_retries").strip().strip('"') != "0":
        return False
    outgoing = graph.outgoing(node_id)
    return len(outgoing) == 1 and _is_exit(graph, outgoing[0].dst)


def _is_truthy(value: str) -> bool:
    return value.strip().strip('"').lower() in ("true", "1", "yes", "on")


def _has_fail_route(graph: DotGraph, node_id: str) -> bool:
    if graph.attr(node_id, "retry_target") or graph.graph_attrs.get("retry_target"):
        return True
    if graph.attr(node_id, "fallback_retry_target"):
        return True
    if _is_truthy(graph.attr(node_id, "continue_on_fail")):
        return True
    return any(
        _is_failure_condition(edge.attrs.get("condition", "")) for edge in graph.outgoing(node_id)
    )


# ---------------------------------------------------------------------------
# "Is this command checking something, or just typing?"
#
# A gate whose command only ever emits a constant is not a gate -- it is an
# assertion wearing a parallelogram. `printf gate_pass` cannot fail, so nothing
# behind it is gated on anything. This is the code-tier form of the doctrine's
# own self-test: "is the model here for judgment, or just to type?"
# ---------------------------------------------------------------------------

#: Head words that only ever emit or arrange -- they never *check* anything.
_CONSTANT_EMITTERS = frozenset(
    {
        "printf", "echo", "exit", "return", "true", "false", ":", "cd", "mkdir",
        "touch", "sleep", "shift", "set", "umask", "export", "unset", "break",
        "continue", "rm", "cp", "mv", "chmod",
    }
)

#: Shell keywords and grouping tokens -- skip past them to the real head word.
_SHELL_KEYWORDS = frozenset(
    {
        "if", "then", "else", "elif", "fi", "while", "until", "do", "done",
        "case", "esac", "for", "select", "function", "time", "!", "in",
    }
)

_ASSIGNMENT_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
_REDIRECT_RE = re.compile(r"^\d*[<>]")


def _split_segments(command: str) -> list[str]:
    """Split a shell command into command positions, respecting quotes.

    Deliberately approximate -- a lint-grade reader in the same spirit as the
    engine's own CMD-001 rule, not a shell. It errs toward *more* segments,
    which can only make the substantive-command search more generous, never
    less; the fail-closed direction is preserved because what gets rejected is
    a command with no substantive head word anywhere in it.
    """
    segments: list[str] = []
    current: list[str] = []
    quote: str | None = None
    i = 0
    n = len(command)
    while i < n:
        ch = command[i]
        if quote:
            if ch == "\\" and i + 1 < n:
                current.append(ch)
                current.append(command[i + 1])
                i += 2
                continue
            current.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            i += 1
            continue
        if command.startswith("&&", i) or command.startswith("||", i):
            segments.append("".join(current))
            current = []
            i += 2
            continue
        if ch in (";", "|", "&", "\n", "(", ")", "{", "}", "`"):
            segments.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    segments.append("".join(current))
    return segments


def _head_word(segment: str) -> str:
    """The command word a segment actually runs, or '' if it runs nothing."""
    for word in segment.split():
        if word in _SHELL_KEYWORDS:
            continue
        if _ASSIGNMENT_RE.match(word) or _REDIRECT_RE.match(word):
            continue
        return word.strip('"').strip("'")
    return ""


def substantive_commands(command: str) -> list[str]:
    """Head words in *command* that check or compute something real.

    ``[``, ``test``, ``grep``, ``pytest``, ``python3``, ``bash``, ``git``,
    ``attractor`` -- anything that is not purely an emitter or a file
    arrangement.
    """
    found: list[str] = []
    for segment in _split_segments(command):
        head = _head_word(segment)
        if not head or head in _CONSTANT_EMITTERS:
            continue
        found.append(head)
    return found


#: Vocabulary A5 accepts as evidence that a node is counting something. Stated
#: here and in README.md so the author is told the contract, not made to guess.
_BUDGET_TOKENS = ("max_iteration", "max_round", "max_attempt", "max_retries", "budget", "iter")

#: Vocabulary A5 accepts on an outgoing edge as the exhaustion route.
_EXHAUSTION_TOKENS = ("exhaust", "budget", "stall", "over_budget", "give_up", "spent")


# ---------------------------------------------------------------------------
# The contract checks
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    check_id: str
    title: str
    passed: bool
    detail: str


def evidence_gates(graph: DotGraph) -> list[str]:
    """Reachable tool nodes that run a real command AND route on the answer.

    Three conditions, each load-bearing:

    * **a tool node** -- ``shape=parallelogram`` or a ``tool_command``. An LLM
      node's opinion of its own work is not evidence, however confidently it is
      phrased (``docs/VISION.md``, "Gates outside workers").
    * **a substantive command** -- something that can come back with an answer
      the author did not already write. ``printf gate_pass`` is not a gate.
    * **it routes** -- at least two outgoing edges, at least one conditional. A
      node whose result changes nothing is not deciding anything.
    """
    reachable = _reachable(graph, _starts(graph))
    gates: list[str] = []
    for node_id in sorted(graph.nodes):
        if node_id not in reachable or not _is_tool(graph, node_id):
            continue
        command = graph.attr(node_id, "tool_command")
        if not command or not substantive_commands(command):
            continue
        out = graph.outgoing(node_id)
        if len(out) < 2 or not any(e.attrs.get("condition") for e in out):
            continue
        gates.append(node_id)
    return gates


def inert_gate_routes(graph: DotGraph) -> list[str]:
    """A10 -- evidence gates that answer into the exit no matter what they find.

    ``evidence_gates`` above already refuses a gate that cannot fail
    (``printf gate_pass``) and a gate that does not route (fewer than two
    outgoing edges). Neither notices the shape that *satisfies both* and still
    decides nothing::

        gate -> done [condition="context.tool.last_line=green"]
        gate -> done [condition="context.tool.last_line=red"]

    The command is real, the exit is reached only through the gate, and no
    *failure outcome* is routed anywhere near the exit -- so A3, A4 and A8 are
    all green while the run ends successfully whether the tests passed or not.
    A4 asks whether the exit is reached THROUGH a gate; A10 asks the question
    left over, which is whether the gate's answer changed where the run went.
    Both are pure topology: a set comparison over outgoing edges, no different
    in kind from A4's reachability computation.

    Deliberately narrow, in two directions, because a doctrine check that
    over-fires teaches authors to route around it:

    * **Only the exit.** Two distinct tokens landing on the same *ordinary*
      node is inert for routing too, but it is frequently deliberate -- three
      of this repo's own shipped graphs do it on purpose, sending several
      distinct diagnoses to one node that writes them up
      (``criteria_gate`` -> ``write_unspecced_finding`` on four separate
      malformed-criteria tokens). There the token is recorded rather than
      routed on, which is legitimate. Two tokens into the *exit* has no such
      reading: the run ends green either way, and that is the single property
      the whole doctrine rests on.
    * **Only through relay no-ops.** ``_token_landing`` sees through a
      ``diamond`` that merely forwards, because that is pure laundering of the
      same defect. It stops at any node that *does* something -- if the two
      answers ran different workers before converging, the gate's answer
      demonstrably changed what happened, and whether that path should still
      end green is a judgement A10 does not have. That is left to the critique
      tier, honestly rather than by guessing.

    Returns one message per offending gate, in the style of A7/A8: the gate,
    the tokens, the shared target, and the edges as written.
    """
    exits = {n for n in graph.nodes if _is_exit(graph, n)}
    messages: list[str] = []
    for gate in evidence_gates(graph):
        landed: dict[str, dict[str, str]] = {}
        for edge in graph.outgoing(gate):
            condition = edge.attrs.get("condition", "")
            token = _routed_token(condition)
            if token is None:
                continue
            landing = _token_landing(graph, edge)
            if landing not in exits:
                continue
            landed.setdefault(landing, {})[token] = f"{gate} -> {edge.dst} [condition={condition!r}]"
        for landing, edges in sorted(landed.items()):
            if len(edges) < 2:
                continue
            tokens = sorted(edges)
            messages.append(
                f"{gate}: tokens {tokens} all end at the exit '{landing}' -- "
                + "; ".join(edges[t] for t in tokens)
            )
    return messages


def run_checks(graph: DotGraph, companion: Path | None) -> list[CheckResult]:
    results: list[CheckResult] = []
    starts = _starts(graph)
    reachable = _reachable(graph, starts)
    exits = sorted(n for n in graph.nodes if _is_exit(graph, n))
    gates = evidence_gates(graph)

    # --- A1: exactly one exit node -------------------------------------------
    results.append(
        CheckResult(
            "A1",
            "exactly one exit node",
            len(exits) == 1,
            f"exit nodes found: {exits or 'none'}"
            + (
                ""
                if len(exits) == 1
                else " -- the engine refuses to run a graph without exactly one. Express a "
                "second honest terminal as a LOUD nonzero tool node (the escalation idiom), "
                "never as a second Msquare"
            ),
        )
    )

    # --- A2: at least one corrective cycle ------------------------------------
    has_cycle = _has_cycle(graph)
    results.append(
        CheckResult(
            "A2",
            "at least one corrective cycle",
            has_cycle,
            "cycle present"
            if has_cycle
            else "graph is acyclic -- there is nothing to converge to. "
            "'If your pipeline graph has no cycle, it should probably have been a recipe.'",
        )
    )

    # --- A3: an evidence-bearing gate exists ----------------------------------
    results.append(
        CheckResult(
            "A3",
            "at least one evidence-bearing gate runs a real command and routes on it",
            bool(gates),
            f"evidence gates: {gates or 'none'}"
            + (
                ""
                if gates
                else " -- an evidence gate is a tool node (shape=parallelogram) whose command "
                "actually checks something (a test, a build, a linter, a grep, a file "
                "predicate) and whose outgoing edges route on the answer. A node that only "
                "`printf`s a constant cannot fail, so nothing behind it is gated"
            ),
        )
    )

    # --- A4: the exit is unreachable without passing an evidence gate ---------
    # THE load-bearing check. Delete every evidence gate; if the exit is still
    # reachable from start, some path to it never touched machine evidence --
    # and that path is the one a bad day will find.
    if len(exits) == 1 and starts:
        exit_id = exits[0]
        # Designed loud terminals are blocked alongside the gates. A4 asks
        # whether the run can FINISH WITHOUT EVIDENCE; a loud terminal cannot
        # finish at all in the sense A4 means -- it is the last node to
        # complete, it exits nonzero, and the exit therefore returns its FAIL
        # (status=fail, CLI exit 1). Counting that as a "bypass" would mean
        # the only shape in which a deliberately red terminal can exist on this
        # engine (see _is_loud_terminal) is also the shape A4 forbids, which
        # would leave an author with no legal way to fail loudly at all.
        # The green door is unchanged: every path that ends SUCCESSFULLY still
        # has to pass a gate, which is the whole of what A4 was protecting.
        loud = {n for n in graph.nodes if _is_loud_terminal(graph, n)}
        bypass = exit_id in _reachable(graph, starts, blocked=set(gates) | loud)
        results.append(
            CheckResult(
                "A4",
                "the exit is structurally unreachable without passing an evidence gate",
                not bypass,
                f"with gate(s) {gates or 'none'} removed, '{exit_id}' is "
                + ("STILL reachable from start" if bypass else "unreachable from start")
                + (
                    ""
                    if not bypass
                    else " -- there is a path from start to the exit that never passes a gate, "
                    "so the run can finish without any machine evidence at all. Route that "
                    "path through a deterministic gate (a file predicate is enough) before it "
                    "reaches the exit"
                ),
            )
        )
    else:
        results.append(
            CheckResult(
                "A4",
                "the exit is structurally unreachable without passing an evidence gate",
                False,
                "not evaluable: "
                + ("no start node" if not starts else f"expected exactly one exit, found {exits}"),
            )
        )

    # --- A5: a budget wall that routes exhaustion -----------------------------
    budget_nodes: list[str] = []
    for node_id in sorted(graph.nodes):
        if node_id not in reachable or not _is_tool(graph, node_id):
            continue
        command = graph.attr(node_id, "tool_command")
        if not any(token in command for token in _BUDGET_TOKENS):
            continue
        for edge in graph.outgoing(node_id):
            haystack = (edge.attrs.get("condition", "") + " " + edge.attrs.get("label", "")).lower()
            if any(token in haystack for token in _EXHAUSTION_TOKENS):
                budget_nodes.append(node_id)
                break
    results.append(
        CheckResult(
            "A5",
            "a tool node walls an iteration budget and routes exhaustion somewhere",
            bool(budget_nodes),
            f"budget-walling gates: {budget_nodes or 'none'}"
            + (
                ""
                if budget_nodes
                else " -- a corrective loop with no wall spends until the engine's step cap "
                "kills it with a bare FAIL, bypassing every salvage path. The wall is a tool "
                f"node whose command names one of {list(_BUDGET_TOKENS)} and which has an "
                f"outgoing edge whose condition or label names one of {list(_EXHAUSTION_TOKENS)}"
            ),
        )
    )

    # --- A6: every reachable LLM worker has a failure route -------------------
    unrouted = sorted(
        n
        for n in graph.nodes
        if _is_worker(graph, n) and n in reachable and not _has_fail_route(graph, n)
    )
    results.append(
        CheckResult(
            "A6",
            "every reachable LLM worker has an outcome=fail route or a retry_target",
            not unrouted,
            f"workers with no failure route: {unrouted or 'none'}"
            + (
                ""
                if not unrouted
                else " -- a FAIL does not traverse plain edges. One transient provider error at "
                "an unrouted node ends the whole run, however much work already landed"
            ),
        )
    )

    # --- A7: stale-label conjunctions where labels route ----------------------
    # A tool node that exits nonzero does NOT refresh tool.last_line
    # (docs/ROUTING-REFERENCE.md section 3). So on a node that can both fail and
    # route on a label, a STALE label can match at the same time as the failure
    # edge, and the engine's deterministic pick may not be the one you meant.
    stale_violations: list[str] = []
    for node_id in sorted(graph.nodes):
        out = graph.outgoing(node_id)
        label_edges = [e for e in out if _LAST_LINE_RE.search(e.attrs.get("condition", ""))]
        fail_edges = [e for e in out if _is_failure_condition(e.attrs.get("condition", ""))]
        if not label_edges or not fail_edges:
            continue
        for edge in label_edges:
            condition = edge.attrs.get("condition", "")
            if not _SUCCESS_CONJUNCTION_RE.search(condition):
                stale_violations.append(f"{node_id} -> {edge.dst} [condition={condition!r}]")
    results.append(
        CheckResult(
            "A7",
            "label-routing edges conjoin && outcome=success where the source can also fail",
            not stale_violations,
            f"unconjoined label edges: {stale_violations or 'none'}"
            + (
                ""
                if not stale_violations
                else " -- a failing tool node does not refresh context.tool.last_line, so a "
                "STALE label can match alongside the failure edge on a later visit. Add "
                "'&& outcome=success' to each label edge listed above"
            ),
        )
    )

    # --- A8: no failure outcome routed into the exit --------------------------
    fail_to_exit = sorted(
        f"{e.src} -> {e.dst} [condition={e.attrs.get('condition', '')!r}]"
        for e in graph.edges
        if _is_exit(graph, e.dst)
        and _is_failure_condition(e.attrs.get("condition", ""))
        and not _is_loud_terminal(graph, e.src)
    )
    results.append(
        CheckResult(
            "A8",
            "no failure outcome is routed into the terminal success node",
            not fail_to_exit,
            f"failure edges into the exit: {fail_to_exit or 'none'}"
            + (
                ""
                if not fail_to_exit
                else " -- this converts a failure into a successful run. A failure belongs on a "
                "salvage path (postmortem, escalation) that ends LOUD, not through the success "
                "door. The engine's own lint calls this TOPO-006 and warns; here it blocks. "
                "The ONE exemption is a designed loud terminal: a tool node with max_retries=0 "
                "whose command's last statement exits nonzero and whose only outgoing edge is "
                "this one. It is then the last node to complete, so the exit returns ITS fail "
                "and the run ends status=fail / exit 1 -- the convergence-factory idiom, and "
                "the only way a loud terminal can exist on an engine whose main loop reports a "
                "dead end as no_matching_edge"
            ),
        )
    )

    # --- A9: the companion document covers every worker -----------------------
    workers = sorted(n for n in graph.nodes if _is_worker(graph, n) and n in reachable)
    results.append(_check_companion(companion, workers))

    # --- A10: an evidence gate's answer must be able to change the path -------
    inert = inert_gate_routes(graph)
    results.append(
        CheckResult(
            "A10",
            "no evidence gate routes two different answers into the exit",
            not inert,
            f"gates whose answer cannot change the outcome: {inert or 'none'}"
            + (
                ""
                if not inert
                else " -- every one of those answers ends the run green, so the gate is "
                "decorative: it runs, it prints a verdict, and the graph goes to the exit "
                "either way. A4 only asks whether the exit is reached THROUGH a gate; this "
                "asks whether the gate's answer decided anything. Route the failing token to "
                "a repair loop, a postmortem, or a LOUD escalation -- somewhere that is not "
                "the success door"
            ),
        )
    )

    return results


def _check_companion(companion: Path | None, workers: list[str]) -> CheckResult:
    """A9 -- the authored pipeline ships as a pair: the .dot and its .md.

    Every exemplar in this repo has a paired guide, because a graph shows what
    it does and not why it is shaped that way. The machine-checkable core of the
    node-contract doctrine is coverage: each LLM worker in the graph has to be
    named in the prose that explains it.
    """
    title = "a companion document names every LLM worker node"
    if companion is None:
        return CheckResult(
            "A9", title, False, "no companion path resolved -- pass --companion or name the .md"
        )
    try:
        prose = companion.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return CheckResult(
            "A9",
            title,
            False,
            f"{companion}: not found -- the authored pipeline ships as a pair, the .dot and "
            "its .md companion",
        )
    except OSError as exc:
        return CheckResult("A9", title, False, f"{companion}: {exc}")

    if not prose.strip():
        return CheckResult(
            "A9",
            title,
            False,
            f"{companion}: empty -- state each LLM node's contract: objective, constraints, "
            "available capabilities, required evidence "
            "(docs/DOT-AUTHORING-GUIDE.md, 'The node contract')",
        )
    missing = [w for w in workers if w not in prose]
    return CheckResult(
        "A9",
        title,
        not missing,
        f"{companion}: {len(prose.split())} words; "
        + (
            "every worker documented"
            if not missing
            else f"undocumented workers: {missing} -- state each one's contract: objective, "
            "constraints, available capabilities, required evidence "
            "(docs/DOT-AUTHORING-GUIDE.md, 'The node contract')"
        ),
    )


def render_report(pipeline: str, companion: str, results: list[CheckResult], verdict: str) -> str:
    lines = [
        "AUTHORED-PIPELINE DOCTRINE REPORT",
        f"pipeline:  {pipeline}",
        f"companion: {companion}",
        f"verdict:   {verdict}",
        "",
    ]
    for result in results:
        lines.append(f"[{'PASS' if result.passed else 'FAIL'}] {result.check_id} {result.title}")
        lines.append(f"       {result.detail}")
    if any(not r.passed for r in results):
        lines += [
            "",
            "Fix every FAIL above and rewrite the pipeline. See examples/authoring/README.md and",
            "docs/PIPELINE_DESIGN_PRINCIPLES.md. Do not weaken a gate to get past this one.",
        ]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Structural doctrine gate on an authored attractor pipeline."
    )
    parser.add_argument("--pipeline", required=True, help="path to the authored .dot")
    parser.add_argument(
        "--companion",
        default=None,
        help="path to the authored .md companion (default: the .dot path with a .md suffix)",
    )
    parser.add_argument(
        "--report",
        default=".authoring/doctrine-report.txt",
        help="where to write the human-readable report",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report_path = Path(args.report)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - environment failure
        print(f"check_authored_pipeline: cannot create report dir: {exc}", file=sys.stderr)
        return 2

    pipeline_path = Path(args.pipeline)
    companion_path = Path(args.companion) if args.companion else pipeline_path.with_suffix(".md")

    results: list[CheckResult] = []
    graph: DotGraph | None = None
    try:
        graph = parse_dot_min(pipeline_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        results.append(
            CheckResult(
                "A0",
                "the authored pipeline exists",
                False,
                f"{pipeline_path}: not found -- the author node was asked to write exactly this path",
            )
        )
    except OSError as exc:
        results.append(
            CheckResult("A0", "the authored pipeline is readable", False, f"{pipeline_path}: {exc}")
        )
    except DotParseError as exc:
        results.append(
            CheckResult("A0", "the authored pipeline parses", False, f"{pipeline_path}: {exc}")
        )

    if graph is not None:
        results.extend(run_checks(graph, companion_path))

    verdict = "doctrine_ok" if results and all(r.passed for r in results) else "doctrine_bad"

    try:
        report_path.write_text(
            render_report(str(pipeline_path), str(companion_path), results, verdict),
            encoding="utf-8",
        )
    except OSError as exc:  # pragma: no cover - environment failure
        print(f"check_authored_pipeline: cannot write report: {exc}", file=sys.stderr)
        return 2

    print(verdict, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())
