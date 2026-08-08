#!/usr/bin/env python3
"""check-witness-gate.py -- mechanical WITNESS-DODGE tripwire for a work
capsule's already-proven-greening hypothesis patches (capsule.dot's
witness_gate).

THE HOLE THIS CLOSES (see context/NOTES-vacuous-green.md for the full
account -- this docstring gives the load-bearing facts, not a paraphrase):
a capsule gate can go GREEN two ways -- because the defect was fixed, or
because the *occasion* for observing the defect was deleted. GitHub issue
#146 produced two capsules (PR #149, PR #159) that both bind GREEN to a
single witness file (`examples/pipelines/00-convergence-loop.dot`) rather
than to the rule's behavior. PR #159's hypothesis.patch deletes the single
token `goal_gate=true` from that file -- `git apply --numstat` on it gives
`0	1` (zero added, one removed) -- and DEFINITION.verify.sh goes GREEN while
the reported lint blind spot (`_check_goal_gate_has_retry` in validation.py
ignores edge-based retry) stands untouched. Neither `degenerate_gate`
(Python-AST-shaped signals: stub/deleted-function/removed-guard; measured
RC=0 on this exact patch) nor `diff_shape_gate` (asks only "are hack A and
hack B mechanically different", never "is either one a candidate fix") sees
this. This checker does.

THE MECHANISM (NOTES-vacuous-green.md \u00a76.C, "run the test on the hacks you
already have" -- no new LLM call, no new round-trip): for each hypothesis
patch already PROVEN to green the gate (mutate_gate / mutate_gate_b already
confirmed this before witness_gate ever runs), fire only if ALL FOUR hold:

  1. the patch is one of the patches already proven to green the gate --
     guaranteed by PLACEMENT (this checker only ever runs post-round-trip;
     it does not re-verify this itself);
  2. files(patch) \u2229 S = \u2205 -- S is the set of repo files that DEFINE any
     symbol DEFINITION.verify.sh itself names, reusing
     check-existing-tests.py's own subject-symbol resolver verbatim (see
     `resolve_subject_symbols`, imported below, not reimplemented);
  3. the patch is PURELY SUBTRACTIVE in that file -- removes >=1 line, adds
     0 (same raw line-count convention as `git apply --numstat`);
  4. at least one removed line shares a non-trivial token with the
     capsule's declared `red_signal`.

Any condition failing on a given patch -> that patch does not fire. S empty
(no symbol in DEFINITION.verify.sh's own text resolves to a definition in
this repo's own source) -> UNDECIDABLE -> PASS, same "absence must be
DETERMINED, never asserted" discipline check-existing-tests.py already
practices. An internal error -> exit 2, biased to PASS at the wiring level
(see CONTRACT below for why this diverges from check-degenerate-hack.py's
own rc>=2 handling).

CONTRACT (mirrors check-degenerate-hack.py's and check-existing-tests.py's
own: read before wiring):

    check-witness-gate.py --verify <path/to/DEFINITION.verify.sh> \\
        --repo <target repo root> --red-signal <declared red_signal text> \\
        --patch <path/to/hypothesis.patch> \\
        [--patch <path/to/hypothesis_b.patch> ...]
    check-witness-gate.py --self-test

  exit 0 -- PASS. First stdout line is one of:
      VERDICT: witness_clean    -- no patch tripped all four conditions.
      VERDICT: undetermined     -- S is empty; nothing to compare against.
  exit 1 -- FIRE. At least one already-proven-greening patch tripped all
      four conditions. STDOUT names which patch, which file(s), which
      removed line(s), and the shared token(s) with the declared
      red_signal.
  exit 2 -- usage/self-test failure (infra problem, not a finding).

BIAS ON exit 2 -- FALSE NEGATIVE, DELIBERATELY, and DIFFERENT FROM
check-degenerate-hack.py's OWN rc>=2 (which capsule.dot routes to triage):
this checker, like check-existing-tests.py, walks an ARBITRARY third-party
repository tree and resolves symbols in it -- an internal failure here is a
property of scanning somebody else's repo, not of the diff text alone.
check-degenerate-hack.py takes exactly one input (a diff) and is
self-contained; a checker-error there is rare and loud. This checker takes
a whole repo as a second input, exactly like check-existing-tests.py, and
inherits its bias: blocking a capsule because OUR scanner tripped is the
false block the bias exists to prevent (capsule.dot's own wiring, not this
script, decides to treat rc>=2 as PASS-with-a-finding).

REUSE, NOT REIMPLEMENTATION (NOTES-vacuous-green.md \u00a710, "Not extend"):
`resolve_subject_symbols` / `walk_repo` / `STOPWORDS` are imported directly
from check-existing-tests.py (same directory), and diff-hunk parsing is
imported directly from check-degenerate-hack.py. Both are already shipped
and already self-tested; this script adds no independent copy of either.

Self-test (proves the checker catches the known #146/#159 dodge AND does
not flag the two things it must not):
    python3 runner/check-witness-gate.py --self-test
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(mod_name: str, filename: str):
    """Load a sibling script (hyphenated filename, not import-able normally)
    as a module, so its functions can be reused verbatim rather than copied."""
    spec = importlib.util.spec_from_file_location(mod_name, _HERE / filename)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_ET = _load("_witness_reuse_existing_tests", "check-existing-tests.py")
_DH = _load("_witness_reuse_degenerate_hack", "check-degenerate-hack.py")

# Same token shape check-existing-tests.py's candidate_identifiers() uses:
# an identifier-looking run of >=4 characters. Deliberately reused rather
# than invented so "non-trivial token" means the same thing in both places.
TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")

PLUS_PLUS_PLUS_RE = re.compile(r"^\+\+\+ (.+)$", re.MULTILINE)


def tokenize(text: str) -> set[str]:
    """Lowercased, stopword-filtered token set (reuses check-existing-tests.py's
    own STOPWORDS list rather than inventing a second one)."""
    out: set[str] = set()
    for m in TOKEN_RE.finditer(text):
        tok = m.group(0)
        if tok.lower() in _ET.STOPWORDS:
            continue
        out.add(tok.lower())
    return out


def patch_files(text: str) -> set[str]:
    """Repo-relative paths this patch's hunks touch, from '+++ ' lines (same
    signal diff_shape_gate already uses to compare hack A and hack B)."""
    out: set[str] = set()
    for m in PLUS_PLUS_PLUS_RE.finditer(text):
        raw = m.group(1).strip()
        raw = raw.split("\t", 1)[0]
        if raw == "/dev/null":
            continue
        for prefix in ("a/", "b/"):
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
                break
        out.add(raw)
    return out


def evaluate_patch(patch_path: Path, subject_files: set[str], red_signal: str) -> dict:
    text = patch_path.read_text(encoding="utf-8", errors="replace")
    files = patch_files(text)
    hunks = _DH.parse_hunks(text)
    added = sum(len(h["added"]) for h in hunks)
    removed_lines = [line for h in hunks for line in h["removed"]]
    removed = len(removed_lines)

    cond2_outside_subject = not (files & subject_files)
    cond3_purely_subtractive = removed >= 1 and added == 0

    removed_tokens: set[str] = set()
    for line in removed_lines:
        removed_tokens |= tokenize(line)
    sig_tokens = tokenize(red_signal)
    shared = removed_tokens & sig_tokens
    cond4_shared_token = bool(shared)

    fired = cond2_outside_subject and cond3_purely_subtractive and cond4_shared_token
    fire_lines = (
        [line for line in removed_lines if tokenize(line) & shared] if shared else []
    )

    return {
        "patch": str(patch_path),
        "files": sorted(files),
        "added": added,
        "removed": removed,
        "cond2_outside_subject": cond2_outside_subject,
        "cond3_purely_subtractive": cond3_purely_subtractive,
        "cond4_shared_tokens": sorted(shared),
        "fire_lines": fire_lines[:3],
        "fired": fired,
    }


def analyze(
    verify_text: str, repo: Path, red_signal: str, patch_paths: list[Path]
) -> tuple[int, list[str]]:
    try:
        tests, sources = _ET.walk_repo(repo)
    except _ET.ScanLimit as exc:
        return 0, [
            "VERDICT: undetermined",
            f"REASON: repo scan cap hit ({exc}) -- this checker will not fire on an",
            "  incomplete scan. Biased to PASS, same discipline as check-existing-tests.py.",
        ]

    symbols, subject_paths = _ET.resolve_subject_symbols(verify_text, repo, sources)
    subject_files: set[str] = {f for files in symbols.values() for f in files}
    subject_files |= set(subject_paths)

    if not subject_files:
        return 0, [
            "VERDICT: undetermined",
            "REASON: S (the set of repo files that DEFINE a symbol DEFINITION.verify.sh",
            "  itself names) is empty -- undecidable, biased to PASS per",
            "  NOTES-vacuous-green.md \u00a76.C.",
            f"SEARCHED: {len(sources)} non-test source files, {len(tests)} test files.",
            f"SOURCE PATHS THE GATE SCRIPT NAMES: {subject_paths or '(none)'}",
        ]

    results = [evaluate_patch(p, subject_files, red_signal) for p in patch_paths]
    fired = [r for r in results if r["fired"]]

    evidence = [
        f"SUBJECT FILES (S): {sorted(subject_files)}",
        f"SUBJECT SYMBOLS: {sorted(symbols)}",
        f"DECLARED red_signal: {red_signal!r}",
        "",
    ]
    for r in results:
        evidence.append(
            f"  {r['patch']}: touches={r['files']} added={r['added']} removed={r['removed']} "
            f"outside_S={r['cond2_outside_subject']} purely_subtractive={r['cond3_purely_subtractive']} "
            f"shared_tokens={r['cond4_shared_tokens']}"
        )

    if fired:
        head = ["VERDICT: witness_dodge_suspected"]
        for r in fired:
            head.append(
                f"FIRE: {r['patch']} turned this gate GREEN by touching ONLY "
                f"{r['files']} -- none of which define a symbol this gate itself names "
                f"(S) -- purely subtractively ({r['removed']} removed / {r['added']} added), "
                f"and removed line(s) {r['fire_lines']!r} share token(s) {r['cond4_shared_tokens']} "
                f"with the declared red_signal."
            )
        return 1, head + [""] + evidence

    return 0, ["VERDICT: witness_clean"] + evidence


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=True, description=__doc__)
    parser.add_argument("--verify", help="path to the capsule's gate script")
    parser.add_argument("--repo", help="path to the target repository root")
    parser.add_argument(
        "--red-signal",
        dest="red_signal",
        default="",
        help="the capsule's declared red_signal",
    )
    parser.add_argument(
        "--patch",
        action="append",
        default=[],
        help="path to an already-proven-greening hypothesis patch (repeatable)",
    )
    parser.add_argument("--self-test", action="store_true", dest="self_test")
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit:
        return 2

    if args.self_test:
        return self_test()

    if not args.verify or not args.repo or not args.patch:
        print(
            "usage: check-witness-gate.py --verify <gate.sh> --repo <repo root> "
            "--red-signal <text> --patch <patch> [--patch <patch> ...]",
            file=sys.stderr,
        )
        return 2

    vpath = Path(args.verify)
    repo = Path(args.repo)
    if not vpath.is_file():
        print(f"usage error: gate script not found: {vpath}", file=sys.stderr)
        return 2
    if not repo.is_dir():
        print(f"usage error: repo root not a directory: {repo}", file=sys.stderr)
        return 2
    patch_paths = [Path(p) for p in args.patch]
    for p in patch_paths:
        if not p.is_file():
            print(f"usage error: patch not found: {p}", file=sys.stderr)
            return 2

    try:
        verify_text = vpath.read_text(encoding="utf-8", errors="replace")
        rc, report = analyze(verify_text, repo.resolve(), args.red_signal, patch_paths)
    except Exception as exc:  # noqa: BLE001 -- an internal error must never crash the pipeline
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print("\n".join(report))
    return rc


# ---------------------------------------------------------------------------
# self-test: the real #146/#159 shape, plus the traps NOTES-vacuous-green.md
# names by number (FB-1's #148 shape, the S-empty undetermined case, and a
# no-false-positive control where a purely subtractive patch shares no token
# with the red_signal).
# ---------------------------------------------------------------------------

# Reconstructs enough of amplifier_module_loop_pipeline for the resolver to
# find _check_goal_gate_has_retry / lint / parse_dot / Node -- the exact
# symbol list the real DEFINITION.verify.sh names (measured, NOTES-vacuous-
# green.md \u00a78.1).
_VALIDATION_PY = """\
class Node:
    pass


def _check_goal_gate_has_retry(graph, diags):
    pass


def lint(graph):
    return []
"""

_DOT_PARSER_PY = """\
def parse_dot(text):
    return None
"""

# The real DEFINITION.verify.sh's own text (abbreviated to the parts the
# resolver reads): names Node, _check_goal_gate_has_retry, lint, parse_dot.
_VERIFY_146 = """\
set -euo pipefail
LINT_OUTPUT=$(python3 - <<'PYEOF'
from amplifier_module_loop_pipeline.dot_parser import parse_dot
from amplifier_module_loop_pipeline.validation import lint, _check_goal_gate_has_retry, Node
graph = parse_dot(open("examples/pipelines/00-convergence-loop.dot").read())
diags = lint(graph)
hits = [d for d in diags if d.rule == "goal_gate_has_retry" and d.node_id == "test_gate"]
for d in hits:
    print(d.message)
PYEOF
)
if [ -n "$LINT_OUTPUT" ]; then
    echo "Node 'test_gate' has goal_gate=true but no retry_target"
    exit 1
fi
exit 0
"""

# hack A -- the EXACT #159 dodge: delete the single token goal_gate=true
# from the .dot example. 0 added, 1 removed, measured.
_HACK_A_DELETE_WITNESS = """\
diff --git a/examples/pipelines/00-convergence-loop.dot b/examples/pipelines/00-convergence-loop.dot
index b2d7582..d534cd7 100644
--- a/examples/pipelines/00-convergence-loop.dot
+++ b/examples/pipelines/00-convergence-loop.dot
@@ -58,7 +58,6 @@ digraph convergence_loop {
         shape=parallelogram,
         label="pytest",
         tool_command="pytest -q test_word_counter.py > test_output.txt 2>&1 && echo gate_pass || echo gate_fail",
-        goal_gate=true
     ]
"""

# hack B -- the legitimate root-cause fix: extend the rule in validation.py
# (a subject file, IN S). Additive, not subtractive.
_HACK_B_ROOT_CAUSE_FIX = """\
diff --git a/validation.py b/validation.py
--- a/validation.py
+++ b/validation.py
@@ -1,9 +1,12 @@
 class Node:
     pass


 def _check_goal_gate_has_retry(graph, diags):
+    for edge in graph.outgoing_edges("test_gate"):
+        if edge.loop_restart:
+            return
     pass


 def lint(graph):
     return []
"""

# #148's shape (FB-1's named risk, stale doc-line-refs): both hypotheses ADD
# lines (the design's own stated fact: "both its patches add lines").
_ENGINE_PY = """\
def _check_node_skip(node):
    return False


def _get_runs_on(node):
    return None


class StageStatus:
    pass
"""

_VERIFY_148 = """\
set -euo pipefail
python3 -c "from engine import StageStatus, _check_node_skip, _get_runs_on"
grep -q "Reference: engine.py:42" docs/CONTRACTS.md
"""

_HACK_A_148 = """\
diff --git a/docs/CONTRACTS.md b/docs/CONTRACTS.md
--- a/docs/CONTRACTS.md
+++ b/docs/CONTRACTS.md
@@ -40,3 +40,8 @@
 See engine.py.
+
+Reference: engine.py:42
+Reference: engine.py:43
+Reference: engine.py:44
+Reference: engine.py:45
+Reference: engine.py:46
"""

_HACK_B_148 = """\
diff --git a/docs/CONTRACTS.md b/docs/CONTRACTS.md
--- a/docs/CONTRACTS.md
+++ b/docs/CONTRACTS.md
@@ -40,3 +40,7 @@
 See engine.py.
+
+See: engine.py#_check_node_skip
+See: engine.py#_get_runs_on
+See: engine.py#StageStatus
+See: engine.py#extra
"""

# No-false-positive control: purely subtractive, outside S, but the removed
# line shares NO token with the red_signal -- condition 4 must fail.
_HACK_UNRELATED_DELETION = """\
diff --git a/examples/pipelines/00-convergence-loop.dot b/examples/pipelines/00-convergence-loop.dot
--- a/examples/pipelines/00-convergence-loop.dot
+++ b/examples/pipelines/00-convergence-loop.dot
@@ -1,4 +1,3 @@
 digraph convergence_loop {
-    // an unrelated stale comment line about formatting conventions
     start [shape=Mdiamond]
 }
"""


def _mk_repo(root: Path, *, with_source: bool = True) -> None:
    if not with_source:
        (root / "docs").mkdir(parents=True, exist_ok=True)
        (root / "docs" / "guide.md").write_text("# Guide\n")
        return
    (root / "validation.py").write_text(_VALIDATION_PY)
    (root / "dot_parser.py").write_text(_DOT_PARSER_PY)


def self_test() -> int:
    ok = True

    def check(
        name: str,
        verify_text: str,
        repo_files: dict,
        red_signal: str,
        patch_texts: list[str],
        expect_rc: int,
        must_contain: tuple[str, ...] = (),
    ) -> None:
        nonlocal ok
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for rel, content in repo_files.items():
                fp = root / rel
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(content)
            patch_paths = []
            for i, text in enumerate(patch_texts):
                pp = root / f"hyp{i}.patch"
                pp.write_text(text)
                patch_paths.append(pp)
            rc, report = analyze(verify_text, root, red_signal, patch_paths)
        blob = "\n".join(report)
        missing = [s for s in must_contain if s not in blob]
        if rc == expect_rc and not missing:
            print(f"ok:   {name} -> rc={rc} ({report[0]})")
        else:
            ok = False
            print(
                f"FAIL: {name} -> expected rc={expect_rc}, got rc={rc}; missing={missing}\n"
                f"--- report ---\n{blob}",
                file=sys.stderr,
            )

    # (1) THE LIVE CASE: hack A alone (the #159 dodge) -> FIRE.
    check(
        "#146/#159 shape: hack A deletes goal_gate=true, the ONLY hypothesis given",
        _VERIFY_146,
        {"validation.py": _VALIDATION_PY, "dot_parser.py": _DOT_PARSER_PY},
        "Node 'test_gate' has goal_gate=true but no retry_target",
        [_HACK_A_DELETE_WITNESS],
        1,
        ("VERDICT: witness_dodge_suspected", "goal_gate", "00-convergence-loop.dot"),
    )

    # (2) GREEN: the legitimate root-cause fix (hack B touches validation.py,
    # a subject file -- condition 2 fails) does NOT fire, even when it is
    # the only hypothesis given.
    check(
        "GREEN: legitimate root-cause fix (validation.py extension) does not fire",
        _VERIFY_146,
        {"validation.py": _VALIDATION_PY, "dot_parser.py": _DOT_PARSER_PY},
        "Node 'test_gate' has goal_gate=true but no retry_target",
        [_HACK_B_ROOT_CAUSE_FIX],
        0,
        ("VERDICT: witness_clean",),
    )

    # (2b) Both hypotheses present together (the real pipeline shape): A
    # fires, B does not -- the gate must still FIRE overall (any patch firing
    # is enough), and must name hack A specifically, not hack B.
    check(
        "both hypotheses present: A fires (dodge), B does not (root-cause) -> overall FIRE naming A",
        _VERIFY_146,
        {"validation.py": _VALIDATION_PY, "dot_parser.py": _DOT_PARSER_PY},
        "Node 'test_gate' has goal_gate=true but no retry_target",
        [_HACK_A_DELETE_WITNESS, _HACK_B_ROOT_CAUSE_FIX],
        1,
        ("VERDICT: witness_dodge_suspected", "hyp0.patch"),
    )

    # (3) NO FALSE BLOCK on FB-1's named risk / #148's shape: both patches
    # ADD lines (condition 3 fails on both).
    check(
        "FB-1 / #148 shape: both hypotheses ADD lines -- not purely subtractive",
        _VERIFY_148,
        {"engine.py": _ENGINE_PY, "docs/guide.md": "# Guide\n"},
        "DEFECT: stale line reference",
        [_HACK_A_148, _HACK_B_148],
        0,
        ("VERDICT: witness_clean",),
    )

    # (4) S EMPTY: the gate script names no identifier resolving to a
    # definition in this repo's own source -> undetermined, PASS.
    check(
        "S empty: no repo symbol resolves -- undetermined, biased to PASS",
        'set -euo pipefail\ngrep -q "## Installation" docs/guide.md\n',
        {},
        "DEFECT: heading missing",
        [_HACK_A_DELETE_WITNESS],
        0,
        ("VERDICT: undetermined",),
    )

    # (5) NO FALSE POSITIVE control: purely subtractive, outside S, but the
    # removed line shares no token with the red_signal -- condition 4 must
    # gate it out (else this checker would fire on ANY unrelated deletion).
    check(
        "control: purely subtractive deletion sharing NO token with red_signal -- must not fire",
        _VERIFY_146,
        {"validation.py": _VALIDATION_PY, "dot_parser.py": _DOT_PARSER_PY},
        "Node 'test_gate' has goal_gate=true but no retry_target",
        [_HACK_UNRELATED_DELETION],
        0,
        ("VERDICT: witness_clean",),
    )

    # usage-error self-test: nonexistent verify script -> rc 2
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        missing_verify = root / "nope.sh"
        rc = main(
            [
                "check-witness-gate.py",
                "--verify",
                str(missing_verify),
                "--repo",
                str(root),
                "--red-signal",
                "x",
                "--patch",
                str(root / "nope.patch"),
            ]
        )
        if rc == 2:
            print("ok:   missing verify script -> usage error rc=2")
        else:
            ok = False
            print(
                f"FAIL: missing verify script -> expected rc=2, got {rc}",
                file=sys.stderr,
            )

    print()
    if ok:
        print("check-witness-gate.py self-test: ALL PASSED")
        return 0
    print("check-witness-gate.py self-test: FAILURES ABOVE", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
