#!/usr/bin/env python3
"""Structural gate on a GENERATED child pipeline (the composed-child contract).

``objective-runner.dot``'s compose path lets an LLM node WRITE a purpose-built
child ``.dot`` and its definition-of-done script, then executes it. That is only
safe if the generated graph is checked by something outside the composer's
context before it is allowed to run. Two gates do that, in order:

1. ``dot-runner lint`` -- executability and the topology bug classes the engine
   itself knows about (dead conditional edges, stale-label collisions,
   fail-routed-to-exit, pipe-masked gate exit codes). ERRORs block.
2. this script -- the *design* checks lint deliberately does not own: does the
   generated graph actually have the shape of an attractor, is its gate really
   outside its workers, and (C9) does the definition of done actually FAIL
   before the work exists?

C9 is the one check that is not structural. It EXECUTES ``dod.sh`` once, here,
at admission time. Everything else about a vacuous ``exit 0`` definition of done
looks perfect: the graph is shaped correctly, the gate runs the script, the
child converges on the first attempt, and the parent's evidence gate re-runs the
same script and agrees. Only running it *before the work exists* distinguishes
"this check can go green" from "this check was always green". The rule used to
live in the composer's prompt; as a prompt instruction it was a suggestion.

On admission this script also PINS the bytes it approved (``--pin``, default
``.objective/dod.sha256``). The parent's evidence gate re-hashes ``dod.sh``
against that pin before re-running it, so a child that rewrites the definition
of done *after* admission is caught rather than believed. See
``compose-contract.md`` -- "The pin, and what it is not" -- for the honest
boundary: the pin is anti-accident, not anti-adversary.

See ``compose-contract.md`` for the contract in the composer's own words, and
``docs/PIPELINE_DESIGN_PRINCIPLES.md`` section 0 for why the gate must live
outside the context that produced the artifact.

Token contract (Idiom A -- always exit 0 so ``tool.last_line`` is always fresh):

  contract_ok    every check passed; the child may run
  contract_bad   at least one check failed; route back to the composer

A nonzero exit means this script could not run at all (unreadable child file,
unwritable report). That routes through ``outcome=fail`` to the postmortem path
-- deliberately distinct from ``contract_bad``, which is a judgement about the
generated graph.

DOT parsing is deliberately stdlib-only and self-contained: this gate must run
under whatever ``python3`` is on PATH in the target workspace, which is not the
``attractor`` CLI's own virtualenv. It is a *second* opinion, and it runs only
after the engine's own parser has already accepted the file via ``attractor
lint`` -- so a disagreement fails closed (``contract_bad``) rather than silently
admitting a graph neither tool understood.

Usage:
    check_child_contract.py --child .objective/gen/child.dot \\
                            --dod .objective/gen/dod.sh \\
                            --report .objective/contract-report.txt
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

#: How long C9's admission-time DoD probe may run. The contract tells the
#: composer to keep ``dod.sh`` fast -- it is executed at least three times per
#: iteration (here at admission, by the child's own gate, and again by the
#: parent's evidence gate).
DOD_PROBE_TIMEOUT_S = 120

# ---------------------------------------------------------------------------
# A small DOT reader -- only what the structural checks need
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
    if len(token) >= 2 and token[0] == '"' and token[-1] == '"':
        body = token[1:-1]
        return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), body)
    return token


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(_strip_comments(text)):
        tokens.append(match.group(0))
    return tokens


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
    """The child .dot could not be read as a graph."""


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


def _is_truthy(value: str) -> bool:
    return value.strip().strip('"').lower() in ("true", "1", "yes", "on")


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


def _reachable_from_start(graph: DotGraph) -> set[str]:
    starts = [n for n in graph.nodes if _is_start(graph, n)]
    seen: set[str] = set()
    stack = list(starts)
    while stack:
        node_id = stack.pop()
        if node_id in seen:
            continue
        seen.add(node_id)
        stack.extend(e.dst for e in graph.outgoing(node_id))
    return seen


_FAIL_COND_RE = re.compile(r"outcome\s*=\s*fail\b")


def _has_fail_route(graph: DotGraph, node_id: str) -> bool:
    if graph.attr(node_id, "retry_target") or graph.graph_attrs.get("retry_target"):
        return True
    for edge in graph.outgoing(node_id):
        if _FAIL_COND_RE.search(edge.attrs.get("condition", "")):
            return True
    return False


# ---------------------------------------------------------------------------
# The contract checks
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    check_id: str
    title: str
    passed: bool
    detail: str


def run_checks(graph: DotGraph, dod_path: str) -> list[CheckResult]:
    results: list[CheckResult] = []
    dod_basename = os.path.basename(dod_path)
    reachable = _reachable_from_start(graph)

    # --- C1: exactly one exit node (also an engine admission rule) ------------
    exits = sorted(n for n in graph.nodes if _is_exit(graph, n))
    results.append(
        CheckResult(
            "C1",
            "exactly one exit node",
            len(exits) == 1,
            f"exit nodes found: {exits or 'none'}"
            + ("" if len(exits) == 1 else " -- the engine refuses to run a graph without exactly one"),
        )
    )

    # --- C2: an evidence gate OUTSIDE the workers, running the provided DoD ---
    gate_nodes = [
        n
        for n in graph.nodes
        if _is_tool(graph, n) and dod_basename and dod_basename in graph.attr(n, "tool_command")
    ]
    results.append(
        CheckResult(
            "C2",
            f"a tool node runs the provided definition of done ({dod_basename})",
            bool(gate_nodes),
            f"tool nodes invoking {dod_basename}: {sorted(gate_nodes) or 'none'}"
            + (
                ""
                if gate_nodes
                else " -- the child's exit must be gated on the DoD the parent will re-run, "
                "not on an LLM's opinion of its own work"
            ),
        )
    )

    # --- C3: no goal gate colocated inside a worker ---------------------------
    colocated = sorted(
        n for n in graph.nodes if _is_worker(graph, n) and _is_truthy(graph.attr(n, "goal_gate"))
    )
    results.append(
        CheckResult(
            "C3",
            "no goal_gate on an LLM worker node",
            not colocated,
            f"workers carrying goal_gate=true: {colocated or 'none'}"
            + (
                ""
                if not colocated
                else " -- verification inside the context that produced the evidence "
                "is not verification"
            ),
        )
    )

    # --- C4: at least one corrective cycle ------------------------------------
    has_cycle = _has_cycle(graph)
    results.append(
        CheckResult(
            "C4",
            "at least one corrective cycle",
            has_cycle,
            "cycle present" if has_cycle else "graph is acyclic -- this should have been a recipe",
        )
    )

    # --- C5: a budget wall inside the gate, with an exhaustion route ----------
    budget_nodes: list[str] = []
    for node_id in graph.nodes:
        if not _is_tool(graph, node_id):
            continue
        command = graph.attr(node_id, "tool_command")
        if "max_iterations" not in command and "budget" not in command:
            continue
        if any(
            "exhausted" in edge.attrs.get("condition", "") for edge in graph.outgoing(node_id)
        ):
            budget_nodes.append(node_id)
    results.append(
        CheckResult(
            "C5",
            "a tool node walls an iteration budget and routes exhaustion",
            bool(budget_nodes),
            f"budget-walling gates: {sorted(budget_nodes) or 'none'}"
            + (
                ""
                if budget_nodes
                else " -- a corrective loop with no wall spends until the engine step cap "
                "kills it with a bare FAIL"
            ),
        )
    )

    # --- C6: a fail-loud escalation terminal ----------------------------------
    escalators = sorted(
        n
        for n in graph.nodes
        if _is_tool(graph, n)
        and re.search(r"\bexit\s+[1-9]", graph.attr(n, "tool_command"))
        and n in reachable
    )
    results.append(
        CheckResult(
            "C6",
            "a reachable node fails the run loudly when it cannot converge",
            bool(escalators),
            f"fail-loud terminals: {escalators or 'none'}"
            + (
                ""
                if escalators
                else " -- a non-converging run must exit nonzero, not leave through the "
                "success door"
            ),
        )
    )

    # --- C7: every worker has a real failure route ---------------------------
    unrouted = sorted(
        n
        for n in graph.nodes
        if _is_worker(graph, n) and n in reachable and not _has_fail_route(graph, n)
    )
    results.append(
        CheckResult(
            "C7",
            "every LLM worker has an outcome=fail route or a retry_target",
            not unrouted,
            f"workers with no failure route: {unrouted or 'none'}"
            + (
                ""
                if not unrouted
                else " -- a FAIL does not traverse plain edges; an unrouted worker failure "
                "runs the pipeline off the rim"
            ),
        )
    )

    # --- C8: the child actually consumes the objective ------------------------
    haystack = " ".join(
        [
            " ".join(graph.graph_attrs.values()),
            *(
                " ".join(node.attrs.values())
                for node in graph.nodes.values()
            ),
        ]
    )
    consumes_goal = "$goal" in haystack or "${goal}" in haystack or "${graph.goal}" in haystack
    results.append(
        CheckResult(
            "C8",
            "the child consumes the objective ($goal / ${graph.goal})",
            consumes_goal,
            "objective referenced"
            if consumes_goal
            else "no $goal / ${graph.goal} reference -- the child would work on nothing in particular",
        )
    )

    return results


def check_dod_is_red(dod_path: Path, timeout_s: int = DOD_PROBE_TIMEOUT_S) -> CheckResult:
    """C9 -- EXECUTE the definition of done once, here, before the child runs.

    The contract has always told the composer that ``dod.sh`` must be red before
    the work exists. As a prompt instruction that is a *suggestion*: a composer
    that writes ``exit 0`` satisfies every structural check, its child converges
    instantly, and the parent's evidence gate then re-runs the same vacuous
    script and agrees. ``rc=0 && delta=changed`` is a false green -- the exact
    shape of the incident this whole exemplar exists to prevent.

    So the gate runs it, once, at admission time -- outside the composer's
    context, before any work exists -- and reads the exit status:

      rc == 1        PASS. The check is genuinely red, so it can go green later.
      rc == 0        FAIL. Already satisfied before the work exists; whatever it
                     asserts, it is not this objective.
      rc >= 2, <0    FAIL, named separately: a script that cannot run (syntax
                     error, missing interpreter, missing tool) is BROKEN, not
                     red. Conflating the two would teach the composer to ship a
                     crash as a definition of done.
      timeout        FAIL, named separately: the DoD is re-run at least three
                     times per iteration, so one that never finishes is unusable.

    This is a probe of a script the composer wrote and the child is about to run
    anyway; running it one extra time here buys the only trustworthy answer to
    "can this check ever fail?"
    """
    title = "the definition of done is red before the work exists"
    try:
        # Executing the DoD is the entire point of C9: it is the only way to
        # distinguish "this check can go red" from "this check is a constant".
        completed = subprocess.run(
            ["bash", str(dod_path)],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            "C9",
            title,
            False,
            f"{dod_path}: did not finish within {timeout_s}s -- a definition of done is "
            "re-run by the child's gate and again by the parent; it must terminate",
        )
    except OSError as exc:
        return CheckResult(
            "C9", title, False, f"{dod_path}: could not be executed: {exc}"
        )

    rc = completed.returncode
    tail = (completed.stdout + completed.stderr).strip().splitlines()
    excerpt = f" | last output: {tail[-1][:160]}" if tail else ""

    if rc == 0:
        return CheckResult(
            "C9",
            title,
            False,
            f"{dod_path}: exited 0 BEFORE any work was done -- this check is already "
            "green, so it proves nothing and can never fail. Assert something that is "
            "false right now and becomes true only when the objective is satisfied"
            + excerpt,
        )
    if rc == 1:
        return CheckResult(
            "C9", title, True, f"{dod_path}: exited 1 before the work exists -- red, as required"
        )
    return CheckResult(
        "C9",
        title,
        False,
        f"{dod_path}: exited {rc} -- that is a BROKEN script, not a red check. A "
        "definition of done signals 'not satisfied' with exit 1; 2 or more (or a "
        "signal) means it could not run at all" + excerpt,
    )


def sha256_file(path: Path) -> str:
    """Hex digest of a file's bytes -- the same number ``sha256sum`` prints."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_report(child: str, dod: str, results: list[CheckResult], verdict: str) -> str:
    lines = [
        "COMPOSED-CHILD CONTRACT REPORT",
        f"child:   {child}",
        f"dod:     {dod}",
        f"verdict: {verdict}",
        "",
    ]
    for result in results:
        lines.append(f"[{'PASS' if result.passed else 'FAIL'}] {result.check_id} {result.title}")
        lines.append(f"       {result.detail}")
    failed = [r for r in results if not r.passed]
    if failed:
        lines += [
            "",
            "Fix every FAIL above and rewrite the child graph. See compose-contract.md.",
        ]
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Structural gate on a generated child pipeline.")
    parser.add_argument("--child", required=True, help="path to the generated child .dot")
    parser.add_argument("--dod", required=True, help="path to the generated definition-of-done script")
    parser.add_argument(
        "--report",
        default=".objective/contract-report.txt",
        help="where to write the human-readable report",
    )
    parser.add_argument(
        "--pin",
        default=".objective/dod.sha256",
        help=(
            "where to record the sha256 of the ADMITTED dod.sh (default: "
            ".objective/dod.sha256). The parent's evidence gate re-hashes the "
            "file against this pin before re-running it, so a child that "
            "rewrites the definition of done after admission is caught."
        ),
    )
    parser.add_argument(
        "--dod-timeout",
        type=int,
        default=DOD_PROBE_TIMEOUT_S,
        help=f"seconds C9's admission-time DoD probe may run (default: {DOD_PROBE_TIMEOUT_S})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    report_path = Path(args.report)
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:  # pragma: no cover - environment failure
        print(f"check_child_contract: cannot create report dir: {exc}", file=sys.stderr)
        return 2

    child_path = Path(args.child)
    dod_path = Path(args.dod)

    problems: list[CheckResult] = []
    graph: DotGraph | None = None
    try:
        graph = parse_dot_min(child_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        problems.append(CheckResult("C0", "child graph exists", False, f"{child_path}: not found"))
    except OSError as exc:
        problems.append(CheckResult("C0", "child graph readable", False, f"{child_path}: {exc}"))
    except DotParseError as exc:
        problems.append(
            CheckResult("C0", "child graph parses", False, f"{child_path}: {exc}")
        )

    if not dod_path.is_file():
        problems.append(
            CheckResult(
                "C0b",
                "definition-of-done script exists",
                False,
                f"{dod_path}: not found -- the parent evidence gate re-runs this file",
            )
        )
    elif dod_path.stat().st_size == 0:
        problems.append(
            CheckResult("C0b", "definition-of-done script is non-empty", False, f"{dod_path}: empty")
        )

    results = list(problems)
    if graph is not None:
        results.extend(run_checks(graph, str(dod_path)))

    # C9 runs LAST and only when there is a non-empty script to run: a missing or
    # empty dod.sh is already C0b's judgement, and executing nothing would say
    # nothing. It is a *behavioural* check, so it deliberately does not depend on
    # the child graph parsing -- a vacuous DoD is disqualifying on its own.
    dod_runnable = not any(r.check_id == "C0b" for r in problems)
    if dod_runnable:
        results.append(check_dod_is_red(dod_path, args.dod_timeout))

    verdict = "contract_ok" if all(r.passed for r in results) and results else "contract_bad"

    # The sha-pin: record the bytes we just admitted, so `evidence_gate` can tell
    # "the DoD I approved" from "a DoD rewritten after I approved it". Written
    # ONLY on admission; a rejected child leaves no pin behind to be matched
    # against later. See compose-contract.md, "The pin, and what it is not".
    pin_path = Path(args.pin)
    try:
        if verdict == "contract_ok" and dod_path.is_file():
            pin_path.parent.mkdir(parents=True, exist_ok=True)
            pin_path.write_text(sha256_file(dod_path) + "\n", encoding="utf-8")
        else:
            pin_path.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - environment failure
        print(f"check_child_contract: cannot write pin {pin_path}: {exc}", file=sys.stderr)
        return 2

    report_path.write_text(render_report(args.child, args.dod, results, verdict), encoding="utf-8")
    print(verdict, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())
