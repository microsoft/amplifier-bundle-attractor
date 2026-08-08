#!/usr/bin/env python3
"""check-degenerate-hack.py -- mechanical DEGENERACY tripwire for a hypothesis
patch (capsule.dot's mutate_gate_b / degenerate_gate).

THE FINDING THIS CLOSES (adversarial review of PR #152, a `$key`
prefix-substitution capsule): DEFINITION.verify.sh asserted, for
substitute_context(), both "the corruption is gone" AND "real substitution
still happens" -- a real functionality/regression pair. For expand_params()
it asserted ONLY "the corrupted string does not appear." hypothesis-b.patch
stubbed expand_params() to `return text` (the parameter, unchanged, doing
nothing) and the gate went fully GREEN: EXIT=0, 12 passed. The patch's OWN
comments said "the entire feature is silently disabled." Two independently-
shaped hacks both greening a gate is supposed to be mechanical evidence the
gate is bound to BEHAVIOR, not to one guessed implementation (capsule.dot's
own two-hypothesis check) -- but that inference only holds if both hacks
still DO something. A hack that deletes the feature and still passes proves
the opposite: the gate has a coverage hole a no-op walks straight through.

CONTRACT (mirrors backlog/check-upstream-leaks.sh's own: read before
wiring): takes exactly one argument, a path to a unified diff (`git diff`
format, the shape mutate/mutate_b already write). Exit 0 = no degenerate
signal found (treat hack B as substantive -- proceed to discrimination_check).
Exit 1 = a degenerate signal was found (STDOUT names it; capsule.dot routes
this to triage as a FAILURE, never a silent pass, never a hard terminal
halt -- the honest read is "the gate under test is not specified tightly
enough," which is a call for the AUTHOR to tighten DEFINITION.verify.sh, not
proof hack B is invalid). Exit 2 = usage/self-test failure (infra problem,
not a finding).

WHAT COUNTS AS DEGENERATE (mechanical, three signals, deliberately narrow --
biased toward FALSE NEGATIVE over false positive; see the docstring on
`--self-test` for why):

  1. STUB: a hunk's added lines collapse to a trivial no-op (`pass`, `...`,
     bare `return`, `return None`, `return ''`/`""`/`[]`/`{}`, or a bare
     `return <name>` where `<name>` is one of the enclosing function's own
     PARAMETERS -- i.e. a verbatim passthrough of the input, indistinguishable
     from the function never having run) AND that trivial body is REPLACING
     at least one real removed line. A hardcoded literal return (`return 42`,
     `return "FIXED"`) does NOT match this -- that is the ordinary crude-hack
     style mutate/mutate_b already explicitly invite, and it still requires
     the patch to have produced SOME specific value, not nothing.

  2. DELETED: a function whose `def NAME(` line is itself removed in a hunk,
     and `NAME` never reappears in any ADDED `def` line anywhere else in the
     whole patch -- the function is simply gone, not replaced.

  3. GUARD-REMOVED: a removed line matching a bare `assert` or `raise
     ...Error` with no compensating assert/raise added in the same hunk --
     a safety check silently dropped, not tightened or relocated.

WHAT IS DELIBERATELY *NOT* FLAGGED (the false-positive trap named in the
task): a hunk that REMOVES lines and adds NOTHING (a broken special case
simply deleted, the surrounding real logic left intact) never reaches signal
1 (it requires added lines to judge trivial) and won't match 2 or 3 unless
the removed lines are literally a `def` line or an assert/raise -- so
"delete a broken branch, keep the real computation" reads as ordinary
simplification, not degeneracy. This is a real, acknowledged limit: from
diff text alone, "the correct fix is now an identity return" and "the
feature was deleted and the gate never noticed" can be textually
IDENTICAL. This script can only ever SUSPECT, never adjudicate -- which is
exactly why capsule.dot routes a hit back through triage/diagnose to a
human decision point rather than silently trusting or silently rejecting it.

Self-test (proves the checker catches the known defect AND does not flag
the two things it must not):
    python3 runner/check-degenerate-hack.py --self-test
"""

from __future__ import annotations

import re
import sys
import tempfile
from pathlib import Path

HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@ ?(.*)$")
DEF_RE = re.compile(r"\bdef\s+(\w+)\s*\(([^)]*)\)")
ASSERT_RE = re.compile(r"^(assert\b|raise\s+\w*Error\b)")
UNCONDITIONAL_TRIVIAL_RE = re.compile(
    r"^(pass|\.\.\.|return|return\s+none|return\s+''|return\s+\"\"|return\s+\[\]|return\s+\{\})$",
    re.IGNORECASE,
)
BARE_RETURN_RE = re.compile(r"^return\s+(\w+)$", re.IGNORECASE)


def parse_hunks(text: str) -> list[dict]:
    """Split a unified diff into hunks. Keeps an ORDERED tagged line list
    (each line's diff marker + content) alongside the removed/added/context
    convenience buckets, so callers can reconstruct "what the new file's
    body actually reads as post-patch" (context + added, in original
    order) -- not just "what got added", which misses the common case
    where a trivial line (e.g. a bare `return text`) was ALREADY present
    unchanged and everything else around it was simply deleted.
    """
    hunks: list[dict] = []
    cur: dict | None = None
    for line in text.splitlines():
        m = HUNK_RE.match(line)
        if m:
            if cur is not None:
                hunks.append(cur)
            cur = {
                "header": m.group(1),
                "removed": [],
                "added": [],
                "context": [],
                "ordered": [],
            }
            continue
        if cur is None:
            continue
        if line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            cur["added"].append(line[1:])
            cur["ordered"].append(("+", line[1:]))
        elif line.startswith("-"):
            cur["removed"].append(line[1:])
            cur["ordered"].append(("-", line[1:]))
        elif line.startswith(" "):
            cur["context"].append(line[1:])
            cur["ordered"].append((" ", line[1:]))
        # lines with no marker (rare, e.g. "\ No newline at end of file") ignored
    if cur is not None:
        hunks.append(cur)
    return hunks


def body_lines(ordered: list[tuple[str, str]], tags: str) -> list[str]:
    """Non-blank, non-def-line lines whose diff tag is in `tags`, in order."""
    out = []
    for tag, content in ordered:
        if tag not in tags:
            continue
        stripped = content.strip()
        if not stripped or DEF_RE.search(content):
            continue
        out.append(stripped)
    return out


def nonblank(lines: list[str]) -> list[str]:
    return [l.strip() for l in lines if l.strip()]


def find_def(*line_groups: list[str]) -> tuple[str | None, list[str]]:
    """Find the first `def NAME(params)` across the given line groups, in order."""
    for lines in line_groups:
        for l in lines:
            m = DEF_RE.search(l)
            if m:
                params = [
                    p.split("=", 1)[0].split(":", 1)[0].strip()
                    for p in m.group(2).split(",")
                    if p.strip() and p.strip() != "self"
                ]
                return m.group(1), params
    return None, []


def is_trivial_added_line(line: str, params: list[str]) -> bool:
    ll = line.strip()
    if UNCONDITIONAL_TRIVIAL_RE.match(ll):
        return True
    m = BARE_RETURN_RE.match(ll)
    return bool(m and m.group(1) in params)


def analyze(text: str) -> list[str]:
    """Return a list of human-readable degeneracy findings (empty = clean)."""
    hunks = parse_hunks(text)
    findings: list[str] = []
    all_added_text = "\n".join(l for h in hunks for l in h["added"])

    for h in hunks:
        name, params = find_def([h["header"]], h["context"], h["removed"], h["added"])
        removed_nb = nonblank(h["removed"])
        added_nb = nonblank(h["added"])

        # Signal 1: STUB -- the hunk's POST-PATCH body (context + added, in
        # order, i.e. what the function actually reads as after applying
        # this hunk) collapses to a trivial no-op, and the PRE-PATCH body
        # (context + removed) had strictly more real content. This catches
        # both shapes: a NEW trivial line replacing removed content, AND the
        # common realistic case where the trivial line (e.g. a bare `return
        # text`) was ALREADY present unchanged and everything else was
        # simply deleted around it (nothing "added" at all in that case --
        # a pure deletion that exposes a passthrough that was always there).
        new_body = body_lines(h["ordered"], "+ ")
        old_body = body_lines(h["ordered"], "- ")
        if (
            new_body
            and len(new_body) <= 2
            and all(is_trivial_added_line(l, params) for l in new_body)
            and len(old_body) > len(new_body)
            and removed_nb
        ):
            label = f"function '{name}'" if name else "this hunk"
            findings.append(
                f"STUB: {label}'s body after this patch is a trivial no-op "
                f"{new_body!r}, down from {len(old_body)} real line(s) "
                f"before it {old_body!r}"
            )

        # Signal 2: DELETED -- def line itself removed, name never re-added anywhere.
        if name:
            removed_def_here = any(
                re.search(rf"\bdef\s+{re.escape(name)}\s*\(", l) for l in h["removed"]
            )
            if (
                removed_def_here
                and f"def {name}(" not in all_added_text
                and not re.search(rf"\bdef\s+{re.escape(name)}\s*\(", all_added_text)
            ):
                findings.append(
                    f"DELETED: function '{name}' removed in this hunk and never "
                    f"reappears (as a def) anywhere added in the patch"
                )

        # Signal 3: GUARD-REMOVED -- assert/raise dropped, nothing compensating added.
        removed_guards = [l for l in removed_nb if ASSERT_RE.match(l)]
        added_guards = [l for l in added_nb if ASSERT_RE.match(l)]
        if removed_guards and not added_guards:
            findings.append(
                f"GUARD-REMOVED: assertion/guard removed with no compensating "
                f"guard added: {removed_guards!r}"
            )

    return findings


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[1] == "--self-test":
        return self_test()
    if len(argv) != 2:
        print(
            "usage: check-degenerate-hack.py <patch-file> | --self-test",
            file=sys.stderr,
        )
        return 2
    path = Path(argv[1])
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"cannot read patch file '{path}': {e}", file=sys.stderr)
        return 2
    if not text.strip():
        print(f"patch file '{path}' is empty", file=sys.stderr)
        return 2
    if not HUNK_RE.search(text.replace("@@ -", "@@ -")) and "@@ " not in text:
        print(
            f"no '@@' diff hunks found in '{path}' -- not a unified diff?",
            file=sys.stderr,
        )
        return 2

    findings = analyze(text)
    if findings:
        print("DEGENERATE HACK SUSPECTED:")
        for f in findings:
            print(f" - {f}")
        return 1
    print(
        "no degenerate-hack signal found (stub / deleted function / removed guard) -- treated as substantive"
    )
    return 0


# ---------------------------------------------------------------------------
# self-test: proves the checker catches the known defect (PR #152 shape) and
# does NOT flag the two things it must not (the false-positive trap).
# ---------------------------------------------------------------------------
PASSTHROUGH_STUB_PATCH = """\
diff --git a/lib.py b/lib.py
--- a/lib.py
+++ b/lib.py
@@ -10,6 +10,3 @@ def expand_params(text, params):
-    for k, v in params.items():
-        text = text.replace('$' + k, v)
-    return text
+    return text
"""

LEGIT_BROKEN_SPECIAL_CASE_PATCH = """\
diff --git a/lib.py b/lib.py
--- a/lib.py
+++ b/lib.py
@@ -1,7 +1,5 @@ def compute(x):
 def compute(x):
-    if x == 42:
-        return -1
     if x < 0:
         return 0
     return x * 2
"""

LEGIT_HARDCODE_HACK_PATCH = """\
diff --git a/lib.py b/lib.py
--- a/lib.py
+++ b/lib.py
@@ -1,3 +1,3 @@ def compute(x):
 def compute(x):
-    return broken(x)
+    return 42
"""

DELETED_FUNCTION_PATCH = """\
diff --git a/lib.py b/lib.py
--- a/lib.py
+++ b/lib.py
@@ -5,6 +5,3 @@
-def expand_params(text, params):
-    for k, v in params.items():
-        text = text.replace('$' + k, v)
-    return text
"""


def self_test() -> int:
    ok = True

    def check(name: str, patch: str, expect_degenerate: bool) -> None:
        nonlocal ok
        findings = analyze(patch)
        got = bool(findings)
        if got == expect_degenerate:
            print(f"ok:   {name} -> {'flagged' if got else 'clean'} as expected")
        else:
            ok = False
            print(
                f"FAIL: {name} -> expected {'flagged' if expect_degenerate else 'clean'}, "
                f"got {'flagged' if got else 'clean'} ({findings})",
                file=sys.stderr,
            )

    check(
        "PR #152 shape: expand_params stubbed to bare passthrough",
        PASSTHROUGH_STUB_PATCH,
        True,
    )
    check("function deleted outright, never re-added", DELETED_FUNCTION_PATCH, True)
    check(
        "TRAP: legit fix removes a broken special case, real logic intact",
        LEGIT_BROKEN_SPECIAL_CASE_PATCH,
        False,
    )
    check(
        "legit crude hack: hardcoded literal return (expected hack style)",
        LEGIT_HARDCODE_HACK_PATCH,
        False,
    )

    # usage-error self-test: nonexistent file -> rc 2
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "nope.patch"
        rc = main(["check-degenerate-hack.py", str(missing)])
        if rc == 2:
            print("ok:   missing patch file -> usage error rc=2")
        else:
            ok = False
            print(
                f"FAIL: missing patch file -> expected rc=2, got {rc}", file=sys.stderr
            )

    print()
    if ok:
        print("check-degenerate-hack.py self-test: ALL PASSED")
        return 0
    print("check-degenerate-hack.py self-test: FAILURES ABOVE", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
