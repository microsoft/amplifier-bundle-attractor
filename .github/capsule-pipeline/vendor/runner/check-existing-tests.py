#!/usr/bin/env python3
"""check-existing-tests.py -- mechanical FOLD-IN tripwire for a work capsule's
gate script.

NO LONGER A BLOCKING GATE (lean rebuild, council 2026-08-07): the
existing_test_gate node this script backed was removed from
runner/capsule.dot. This script survives as a TOOL THE CRITIC MAY RUN:
capsule.dot's critique node may invoke it while judging whether the gate
ignores on-topic existing tests, and treats its output (any exit code) as
ADVISORY INPUT to a judgment, never as a verdict.

THE DEFECT CLASS THIS CLOSES (four reviewed capsules, n=4, all four sharing
one habit): the authored DEFINITION.verify.sh does not run the tests that
ALREADY exist in the target repo and already exercise the exact code the
capsule concerns -- and every one of the four relegated running them to a
"Human reviewer criteria (not automated)" section of DEFINITION.md, for a
human who does not exist under the autonomous-implementer model these
capsules feed.

  #152 (issue #144): tests/test_param_expansion.py shipped in the same repo
       and caught the degenerate expand_params() stub outright (6 failed) --
       the authored gate ran only tests/test_unified_substitution.py.
  #143 (issue #142): test_backend.py / test_attribute_passthrough.py already
       exercise the exact idiom the gate uses -- verify.sh never invoked
       pytest at all.
  #149 (issue #146): test_validation.py unit-tests the exact rule and
       test_examples_lint_clean.py sweeps every example -- neither was run.
  #148 (issue #145): a docs file; no tests exist. Genuinely n/a -- and this
       checker must be able to say so ON ITS OWN EVIDENCE.

WHY A CHECKER AND NOT A PROMPT: capsule.dot's author prompt ALREADY says to
search the target's test tree and "If one exists, FOLD IT IN." The prose is
good and it did not bind (n=4). Primer foot-gun #13: prose rules are
advisory; executed gates change behavior. The trap to avoid is the one
capsule.dot's own header names -- the RETIRED "## Fix-shape independence"
written-section requirement proved that demanding a written SELF-ATTESTATION
("prove you searched") changes nothing measurable while burning budget on
false blocks. So this checker asks the author to attest to NOTHING. It reads
the shipped artifact and the target repo and answers a question about
observable facts:

    Given the subject THE GATE SCRIPT ITSELF DECLARES (the source symbols and
    source paths its own text names), does a test file exist in this repo
    that references that subject -- and if so, does the gate script invoke it?

Both halves are properties of files on disk. Nothing is self-reported, so
there is nothing to game by writing a better paragraph.

CONTRACT (mirrors check-degenerate-hack.py's and
backlog/check-upstream-leaks.sh's own: read before wiring):

    check-existing-tests.py --verify <path/to/DEFINITION.verify.sh> \
                            --repo <target repo root>
    check-existing-tests.py --self-test

  exit 0 -- PASS. The FIRST stdout line is `VERDICT: folded_in` (an on-topic
            existing test IS invoked, or the script runs the whole suite) or
            `VERDICT: no_on_topic_tests` (this checker SEARCHED and found
            none -- the report says what it searched, so the absence is
            evidence, not an assertion). The rest of stdout is the evidence.
  exit 1 -- BLOCK. At least one existing test file references a subject
            symbol the gate script itself names, and NO invoked test covers
            that symbol. STDOUT names the symbol, the file, and the line to
            add. capsule.dot routes this to triage -> author.
  exit 2 -- usage/self-test failure (infra problem, not a finding).
            capsule.dot deliberately treats this as PASS-with-a-finding, NOT
            as a block -- see BIAS below.

BIAS: FALSE NEGATIVE, DELIBERATELY. A checker that blocks legitimate capsules
teaches authors to route around it, which is strictly worse than not having
it. Every ambiguity resolves toward PASS, and every PASS that was not a
positive confirmation writes a finding saying why:

  - no test tree, or no test files              -> PASS (no_on_topic_tests)
  - gate script names no symbol that resolves
    to a definition in this repo's own source   -> PASS (no_on_topic_tests)
  - no test file references any such symbol     -> PASS (no_on_topic_tests)
  - the gate script invokes a test runner with
    no specific file argument (`pytest -q`,
    `make test`, `cargo test`)                  -> PASS (folded_in): it is
    already running everything, including the on-topic tests
  - a subject symbol has SOME invoked on-topic
    test                                        -> that symbol is satisfied;
    the checker never demands you run a second file for the same symbol
  - repo too large to scan within the caps      -> PASS (no_on_topic_tests,
    reason recorded)
  - anything unexpected                         -> exit 2, which the gate
    treats as PASS-with-a-finding

RELEVANCE IS SYMBOL-DRIVEN, AND THAT IS A REAL LIMIT: a test that exercises
the subject surface without ever naming a symbol the gate script also names
is invisible to this checker. That is the deliberate false-negative side of
the trade. Broadening relevance (module stems, import graphs, path heuristics)
would find more tests and would also start blocking capsules for tests that
are not actually on-topic -- the exact failure mode that makes a gate get
routed around. Narrow and trusted beats broad and gamed.

Self-test (proves it catches the known defect AND does not flag the three
things it must not):
    python3 runner/check-existing-tests.py --self-test
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Scan caps. A capsule run happens inside somebody else's repo; this gate sits
# in the cheap-before-expensive chain and must stay cheap. Exceeding a cap is
# never a block -- it is a PASS with the reason recorded.
# ---------------------------------------------------------------------------
MAX_FILES_SCANNED = 20000
MAX_TEST_FILES = 3000
MAX_SOURCE_FILES = 8000
MAX_FILE_BYTES = 512 * 1024
MAX_SUBJECT_SYMBOLS = 25
# A symbol defined in more than this many source files is a common name
# (`main`, `run`, `handler`), not this capsule's subject.
MAX_DEFINING_FILES = 3

SKIP_DIRS = {
    ".git",
    ".ai",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".eggs",
    "site-packages",
    "build",
    "dist",
    "target",
    ".gradle",
    ".idea",
    ".vscode",
    ".next",
    ".cache",
    "coverage",
    "htmlcov",
    "vendor",
}

TEST_DIR_NAMES = {"tests", "test", "spec", "specs", "__tests__", "testing"}

SOURCE_SUFFIXES = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".go",
    ".rb",
    ".rs",
    ".java",
    ".kt",
    ".scala",
    ".c",
    ".h",
    ".cc",
    ".cpp",
    ".hpp",
    ".cs",
    ".php",
    ".swift",
    ".sh",
    ".bash",
    ".lua",
    ".pl",
    ".ex",
    ".exs",
}

# Test-runner invocations. If one of these appears with NO specific test file
# argument, the script is running the whole suite and there is nothing to fold
# in -- it already folded everything in.
RUNNER_RE = re.compile(
    r"\b("
    r"pytest|py\.test|unittest|tox|nox|"
    r"go\s+test|cargo\s+test|dotnet\s+test|"
    r"npm\s+(?:run\s+)?test|yarn\s+test|pnpm\s+(?:run\s+)?test|"
    r"make\s+\S*test|"
    r"rspec|jest|vitest|mocha|ava|phpunit|ctest|"
    r"gradle\s+\S*test|mvn\s+\S*test"
    r")\b"
)

# Definition forms, across the languages above. NAME is substituted in.
DEF_TEMPLATES = (
    r"\bdef\s+{n}\s*\(",
    r"\bclass\s+{n}\s*[:(\s]",
    r"\bfunction\s+{n}\s*\(",
    r"\bfunc\s+(?:\([^)]*\)\s*)?{n}\s*\(",
    r"\bfn\s+{n}\s*[<(]",
    r"\bsub\s+{n}\s*[{{(]",
    r"\b(?:const|let|var)\s+{n}\s*=\s*(?:async\s*)?(?:function|\()",
    r"^\s*{n}\s*\(\s*\)\s*\{{",  # POSIX shell function
)

# Identifiers a gate script mentions for reasons that have nothing to do with
# the subject under test. Kept small on purpose: the real filter is "does this
# identifier resolve to a definition in the repo's own non-test source?".
STOPWORDS = {
    "bash",
    "echo",
    "exit",
    "else",
    "elif",
    "then",
    "fail",
    "true",
    "false",
    "null",
    "none",
    "self",
    "test",
    "tests",
    "main",
    "print",
    "python",
    "python3",
    "pipefail",
    "euo",
    "set",
    "grep",
    "sed",
    "awk",
    "cat",
    "head",
    "tail",
    "sort",
    "uniq",
    "find",
    "mkdir",
    "rmdir",
    "chmod",
    "command",
    "return",
    "import",
    "from",
    "assert",
    "raise",
    "sys",
    "argv",
    "stderr",
    "stdout",
    "usr",
    "env",
    "local",
    "readonly",
    "export",
    "unset",
    "while",
    "done",
    "case",
    "esac",
    "function",
    "verify",
    "definition",
    "repo",
    "root",
    "infra",
    "defect",
    "signal",
    "value",
    "result",
    "output",
    "input",
    "failures",
    "append",
    "format",
    "string",
    "bytes",
    "path",
    "file",
    "dir",
    "temp",
    "tmp",
}


# Every no_on_topic_tests report carries this line. #148's target was a docs
# file with genuinely no tests -- that case must PASS, but "no tests exist"
# must be a conclusion this checker reached by looking, never a claim the
# author made. A silent skip is the same failure wearing a different hat.
DETERMINED = (
    "DETERMINATION: this absence was established by THIS CHECKER reading the tree "
    "above, not asserted by the capsule's author."
)


class ScanLimit(Exception):
    """A cap was hit. Never a block -- resolves to PASS with the reason said."""


def _read(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def is_test_path(rel: str) -> bool:
    """True if this repo-relative path looks like a test artifact.

    Two independent signals, either is enough: it lives under a directory
    conventionally named for tests, or its own filename is conventionally
    named for tests.
    """
    parts = rel.split("/")
    if any(p.lower() in TEST_DIR_NAMES for p in parts[:-1]):
        return True
    stem = parts[-1].rsplit(".", 1)[0].lower()
    return stem.startswith(("test_", "test-")) or stem.endswith(
        ("_test", "-test", ".test", "_spec", "-spec", ".spec")
    )


def walk_repo(repo: Path) -> tuple[list[str], list[str]]:
    """Return (test_files, source_files) as repo-relative POSIX paths."""
    tests: list[str] = []
    sources: list[str] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".git"))
        for fn in sorted(filenames):
            seen += 1
            if seen > MAX_FILES_SCANNED:
                raise ScanLimit(f"more than {MAX_FILES_SCANNED} files under the repo root")
            full = Path(dirpath) / fn
            try:
                rel = full.relative_to(repo).as_posix()
            except ValueError:
                continue
            if full.suffix.lower() not in SOURCE_SUFFIXES:
                continue
            if is_test_path(rel):
                tests.append(rel)
            else:
                sources.append(rel)
    if len(tests) > MAX_TEST_FILES:
        raise ScanLimit(f"more than {MAX_TEST_FILES} test files")
    if len(sources) > MAX_SOURCE_FILES:
        raise ScanLimit(f"more than {MAX_SOURCE_FILES} non-test source files")
    return tests, sources


def path_named_by(verify_text: str, rel: str) -> bool:
    """Does the gate script name this repo file?

    Suffix match on path-component boundaries, NOT an exact repo-relative
    match: a real gate script routinely `cd`s into a subproject first and then
    refers to `tests/test_x.py` (PR #152's own does exactly that), so the
    literal repo-relative prefix never appears in its text.
    """
    parts = rel.split("/")
    for i in range(len(parts)):
        suffix = "/".join(parts[i:])
        if suffix in verify_text:
            return True
    return False


def candidate_identifiers(verify_text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"[A-Za-z_][A-Za-z0-9_]{3,}", verify_text):
        tok = m.group(0)
        low = tok.lower()
        if low in STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def resolve_subject_symbols(
    verify_text: str, repo: Path, sources: list[str]
) -> tuple[dict[str, list[str]], list[str]]:
    """Symbols the GATE SCRIPT ITSELF names that are DEFINED in this repo's
    own non-test source. This is the capsule's declared subject, read off the
    shipped artifact -- never self-reported.

    Returns (symbol -> defining source files, subject source paths named).
    """
    subject_paths = [rel for rel in sources if path_named_by(verify_text, rel)]
    cands = candidate_identifiers(verify_text)
    if not cands:
        return {}, subject_paths

    cand_set = set(cands)
    alternation = "|".join(re.escape(c) for c in sorted(cand_set, key=len, reverse=True))
    patterns = [
        re.compile(tpl.format(n=f"(?P<name>{alternation})"), re.MULTILINE)
        for tpl in DEF_TEMPLATES
    ]

    defined: dict[str, set[str]] = {}
    # Files the script named directly are the strongest evidence of subject;
    # scan them first so they always survive the MAX_SUBJECT_SYMBOLS cut.
    ordered = subject_paths + [s for s in sources if s not in set(subject_paths)]
    for rel in ordered:
        text = _read(repo / rel)
        if not text:
            continue
        for pat in patterns:
            for m in pat.finditer(text):
                name = m.group("name")
                if name in cand_set:
                    defined.setdefault(name, set()).add(rel)

    subject_files = set(subject_paths)

    def rank(item: tuple[str, set[str]]) -> tuple[int, int, str]:
        name, files = item
        # Prefer symbols defined in a file the gate script itself named, then
        # the most specific (fewest defining files), then stable by name.
        return (0 if files & subject_files else 1, len(files), name)

    kept: dict[str, list[str]] = {}
    for name, files in sorted(defined.items(), key=rank):
        if len(files) > MAX_DEFINING_FILES:
            continue
        kept[name] = sorted(files)
        if len(kept) >= MAX_SUBJECT_SYMBOLS:
            break
    return kept, subject_paths


def invoked_tests(verify_text: str, tests: list[str]) -> tuple[set[str], bool, bool]:
    """Which test files does the gate script actually run?

    Returns (named test files, whole_suite, has_runner).
    """
    has_runner = bool(RUNNER_RE.search(verify_text))
    named = {rel for rel in tests if path_named_by(verify_text, rel)}

    # A test DIRECTORY named as a runner argument sweeps everything under it.
    dir_swept: set[str] = set()
    if has_runner:
        for rel in tests:
            parts = rel.split("/")
            for i in range(len(parts) - 1):
                for j in range(i + 1, len(parts)):
                    d = "/".join(parts[i:j])
                    if not d:
                        continue
                    if re.search(rf"(?<![\w/]){re.escape(d)}/?(?=[\s'\"]|$)", verify_text):
                        dir_swept.add(rel)
    named |= dir_swept

    # A runner with no specific test file argument runs the whole suite.
    whole_suite = has_runner and not named
    return named, whole_suite, has_runner


def analyze(verify_text: str, repo: Path) -> tuple[int, list[str]]:
    """Return (exit_code, report_lines). report_lines[0] is the VERDICT line."""
    try:
        tests, sources = walk_repo(repo)
    except ScanLimit as exc:
        return 0, [
            "VERDICT: no_on_topic_tests",
            f"REASON: repo scan cap hit ({exc}) -- this checker will not block on an",
            "  incomplete scan. Biased to false-negative by design; recorded, not enforced.",
        ]

    if not tests:
        return 0, [
            "VERDICT: no_on_topic_tests",
            "REASON: no test files exist in this repo.",
            f"SEARCHED: {len(sources)} non-test source files under the repo root;",
            f"  directories named {sorted(TEST_DIR_NAMES)} and files named test_*/*_test/*_spec.",
            DETERMINED,
        ]

    symbols, subject_paths = resolve_subject_symbols(verify_text, repo, sources)

    if not symbols:
        return 0, [
            "VERDICT: no_on_topic_tests",
            "REASON: the gate script names no identifier that resolves to a function/class",
            "  definition in this repo's own non-test source, so this checker cannot",
            "  determine a subject to look for tests about.",
            f"SEARCHED: {len(tests)} test files, {len(sources)} non-test source files.",
            f"SOURCE PATHS THE GATE SCRIPT NAMES: {subject_paths or '(none)'}",
            "  Biased to false-negative: undetermined relevance passes and is recorded.",
            DETERMINED,
        ]

    on_topic: dict[str, list[str]] = {}
    test_text: dict[str, str] = {}
    for rel in tests:
        test_text[rel] = _read(repo / rel)
    for sym in symbols:
        word = re.compile(rf"\b{re.escape(sym)}\b")
        hits = [rel for rel in tests if word.search(test_text[rel])]
        if hits:
            on_topic[sym] = hits

    searched = (
        f"SEARCHED: {len(tests)} test files for {len(symbols)} subject symbol(s) "
        f"the gate script itself names: {sorted(symbols)}"
    )

    if not on_topic:
        return 0, [
            "VERDICT: no_on_topic_tests",
            "REASON: no existing test file references any subject symbol the gate script",
            "  names -- there is genuinely nothing on-topic to fold in.",
            searched,
            DETERMINED,
        ]

    named, whole_suite, has_runner = invoked_tests(verify_text, tests)

    if whole_suite:
        return 0, [
            "VERDICT: folded_in",
            "REASON: the gate script invokes a test runner with no specific test-file",
            "  argument -- it already runs the whole suite, which includes every",
            "  on-topic test below.",
            searched,
            f"ON-TOPIC TESTS COVERED: {sorted({f for v in on_topic.values() for f in v})}",
        ]

    unrun: list[tuple[str, list[str]]] = []
    covered: list[tuple[str, str]] = []
    for sym, files in sorted(on_topic.items()):
        hit = sorted(set(files) & named)
        if hit:
            covered.append((sym, hit[0]))
        else:
            unrun.append((sym, sorted(files)))

    if not unrun:
        return 0, [
            "VERDICT: folded_in",
            "REASON: every subject symbol with existing on-topic test coverage has at",
            "  least one of those tests invoked by the gate script.",
            searched,
            *[f"  {sym}: covered by {rel} (invoked)" for sym, rel in covered],
        ]

    lines = [
        "VERDICT: unrun_on_topic_tests",
        "The gate script declares a subject (by naming these symbols in its own text)",
        "for which this repo ALREADY ships tests -- and the gate script does not run",
        "them. Existing tests encode the neighbouring expectations a fresh bespoke",
        "assertion does not know about; they are free coverage and they are exactly",
        "what catches a degenerate hack that satisfies a narrow new assertion.",
        "",
        searched,
        "",
        "UNRUN ON-TOPIC TESTS:",
    ]
    for sym, files in unrun:
        lines.append(f"  symbol '{sym}' (defined in {', '.join(symbols[sym])})")
        for rel in files:
            lines.append(f"      -> {rel}   NOT invoked by DEFINITION.verify.sh")
    lines += [
        "",
        "FIX: have DEFINITION.verify.sh actually run at least one of the files above",
        "for each symbol listed (running the whole suite also satisfies this check).",
        "Treat their failure as the gate's own RED, not as an infrastructure error --",
        "an existing test that fails is the defect reproducing, which is the point.",
    ]
    if covered:
        lines += ["", "ALREADY COVERED (no action needed):"]
        lines += [f"  {sym}: {rel}" for sym, rel in covered]
    if not has_runner:
        lines += [
            "",
            "NOTE: the gate script invokes no recognized test runner at all.",
        ]
    return 1, lines


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(add_help=True, description=__doc__)
    parser.add_argument("--verify", help="path to the capsule's gate script")
    parser.add_argument("--repo", help="path to the target repository root")
    parser.add_argument("--self-test", action="store_true", dest="self_test")
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit:
        return 2

    if args.self_test:
        return self_test()

    if not args.verify or not args.repo:
        print("usage: check-existing-tests.py --verify <gate.sh> --repo <repo root>", file=sys.stderr)
        return 2

    vpath = Path(args.verify)
    repo = Path(args.repo)
    if not vpath.is_file():
        print(f"usage error: gate script not found: {vpath}", file=sys.stderr)
        return 2
    if not repo.is_dir():
        print(f"usage error: repo root not a directory: {repo}", file=sys.stderr)
        return 2

    try:
        verify_text = vpath.read_text(encoding="utf-8", errors="replace")
        rc, report = analyze(verify_text, repo.resolve())
    except Exception as exc:  # noqa: BLE001 -- an internal error must never block
        print(f"internal error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print("\n".join(report))
    return rc


# ---------------------------------------------------------------------------
# self-test fixtures -- the real shapes, reconstructed
# ---------------------------------------------------------------------------

_SUBSTITUTION_PY = '''\
def substitute_context(text, snapshot):
    for k, v in snapshot.items():
        text = text.replace("$" + k, str(v))
    return text
'''

_TRANSFORMS_PY = '''\
def expand_params(text, params):
    for k, v in params.items():
        text = text.replace(k, v)
    return text
'''

_TEST_UNIFIED = '''\
from substitution import substitute_context


def test_substitute_context_basic():
    assert substitute_context("$a", {"a": "1"}) == "1"
'''

_TEST_PARAM_EXPANSION = '''\
from transforms import expand_params


def test_expand_params_basic():
    assert expand_params("$framework app", {"$framework": "FastAPI"}) == "FastAPI app"
'''

# PR #152's own shape: a bespoke assertion block plus ONE existing test file,
# while a second existing test file covering the other named symbol is ignored.
_VERIFY_152 = '''\
set -euo pipefail
python3 - <<'EOF'
from substitution import substitute_context
from transforms import expand_params
assert "$name_suffix" in substitute_context("$name $name_suffix", {"name": "A"})
assert "Alice_suffix" not in expand_params("$name_suffix", {"name": "Alice"})
EOF
pytest tests/test_unified_substitution.py -q
'''

_VERIFY_152_FOLDED = _VERIFY_152 + "pytest tests/test_param_expansion.py -q\n"

# The whole-suite shape: a runner with no specific file argument.
_VERIFY_WHOLE_SUITE = '''\
set -euo pipefail
python3 -c "from substitution import substitute_context; from transforms import expand_params"
pytest -q
'''

# The #143 shape: never invokes a test runner at all.
_VERIFY_NO_RUNNER = '''\
set -euo pipefail
python3 - <<'EOF'
from transforms import expand_params
assert expand_params("x", {}) == "x"
EOF
'''

# The #148 shape: the subject is a docs file; no code symbols, no tests.
_VERIFY_DOCS = '''\
set -euo pipefail
grep -q "## Installation" docs/guide.md || { echo "DEFECT: heading missing"; exit 1; }
'''


def _mk_repo(root: Path, *, with_tests: bool = True, docs_only: bool = False) -> None:
    if docs_only:
        (root / "docs").mkdir(parents=True, exist_ok=True)
        (root / "docs" / "guide.md").write_text("# Guide\n\nno install section\n")
        return
    (root / "substitution.py").write_text(_SUBSTITUTION_PY)
    (root / "transforms.py").write_text(_TRANSFORMS_PY)
    if with_tests:
        (root / "tests").mkdir(parents=True, exist_ok=True)
        (root / "tests" / "test_unified_substitution.py").write_text(_TEST_UNIFIED)
        (root / "tests" / "test_param_expansion.py").write_text(_TEST_PARAM_EXPANSION)


def self_test() -> int:
    ok = True

    def check(name: str, verify_text: str, expect_rc: int, must_contain: tuple[str, ...] = (), **mk) -> None:
        nonlocal ok
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _mk_repo(root, **mk)
            rc, report = analyze(verify_text, root)
        blob = "\n".join(report)
        missing = [s for s in must_contain if s not in blob]
        if rc == expect_rc and not missing:
            print(f"ok:   {name} -> rc={rc} ({report[0]})")
            return
        ok = False
        print(
            f"FAIL: {name} -> expected rc={expect_rc}, got rc={rc}; "
            f"missing={missing}\n--- report ---\n{blob}",
            file=sys.stderr,
        )

    check(
        "PR #152 shape: existing tests/test_param_expansion.py never invoked",
        _VERIFY_152,
        1,
        ("VERDICT: unrun_on_topic_tests", "expand_params", "tests/test_param_expansion.py"),
    )
    check(
        "PR #152 shape, folded in: both on-topic test files invoked",
        _VERIFY_152_FOLDED,
        0,
        ("VERDICT: folded_in",),
    )
    check(
        "TRAP: whole-suite runner (`pytest -q`, no file arg) already covers everything",
        _VERIFY_WHOLE_SUITE,
        0,
        ("VERDICT: folded_in", "whole suite"),
    )
    check(
        "#143 shape: no test runner invoked at all, on-topic tests exist",
        _VERIFY_NO_RUNNER,
        1,
        ("VERDICT: unrun_on_topic_tests", "no recognized test runner"),
    )
    check(
        "#148 shape: docs-file subject in a repo that DOES have tests -- no code symbol "
        "resolves, so nothing on-topic exists and the checker says so",
        _VERIFY_DOCS,
        0,
        ("VERDICT: no_on_topic_tests", "SEARCHED: 2 test files", DETERMINED),
    )
    check(
        "TRAP: repo with no test tree at all -> pass, absence determined by walking",
        _VERIFY_152,
        0,
        ("VERDICT: no_on_topic_tests", "no test files exist", DETERMINED),
        with_tests=False,
    )
    check(
        "TRAP: docs-only repo (no source, no tests) -> pass, absence determined",
        _VERIFY_DOCS,
        0,
        ("VERDICT: no_on_topic_tests", "no test files exist", DETERMINED),
        docs_only=True,
    )

    # usage-error self-test: nonexistent gate script -> rc 2
    with tempfile.TemporaryDirectory() as td:
        missing = Path(td) / "nope.sh"
        rc = main(["check-existing-tests.py", "--verify", str(missing), "--repo", td])
        if rc == 2:
            print("ok:   missing gate script -> usage error rc=2")
        else:
            ok = False
            print(f"FAIL: missing gate script -> expected rc=2, got {rc}", file=sys.stderr)

    print()
    if ok:
        print("check-existing-tests.py self-test: ALL PASSED")
        return 0
    print("check-existing-tests.py self-test: FAILURES ABOVE", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
