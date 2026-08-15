#!/usr/bin/env python3
"""Admit or reject the drift-review workers' findings -- the Layer-3 shape gate.

``drift-review.dot`` is the executor for ``docs/QUALITY_PROTOCOL.md`` section 5,
Layer 3: the periodic holistic semantic review that reads the whole repo against
the canonical spec and the stated vision. Layers 0-2 pin numbers and ledgered
behavior; Layer 3 catches what only judgment sees -- a paragraph teaching retired
behavior, an example contradicting doctrine, vocabulary drifting from the spec's.

Judgment is exactly what cannot be trusted to grade itself. So the review workers
(LLM ``box`` nodes) propose findings, and this script -- run by the
``findings_gate`` parallelogram, outside every worker's context -- decides which
ones are *shaped*: **every finding must cite ``file:line`` on BOTH sides**, the
drifting surface AND the normative passage it contradicts, with a quote that
actually resolves against the tree at the cited location.

That is the whole contract, and it is deliberately about shape rather than truth.
This gate cannot know whether a contradiction is real; a human triages that
(``README.md``). What it *can* do is make an unfalsifiable finding structurally
impossible to ship: a finding whose citations do not resolve is not a finding,
it is a claim, and a Layer-3 review that emits claims is worth nothing to the
person who has to act on it.

Why the normative side is a closed set (the ``contradicts.file`` rule): "drift"
in this repo is defined against named sources of truth -- the vendored spec
(Layer 0), the stated vision, and the two ledgers. A finding that one doc
disagrees with another doc is a proofreading note; a finding that a doc
disagrees with ``specs/canonical/`` is drift. Forcing the citation into that set
is what keeps Layer 3 measuring the same thing Layers 0-2 measure, one level up.

Token contract (Idiom A -- always exit 0 so ``tool.last_line`` is always fresh):

  findings_ok         every class file parsed and every finding is shaped;
                      ``findings.json`` was written; proceed to consolidate
  findings_bad        at least one class file or finding is malformed; route
                      back to the revise worker with the per-finding reasons
  revise_exhausted    too many malformed rounds; stop revising, go to postmortem

A nonzero exit means this gate could not run at all (the raw directory is
missing, the repo root is not a directory, the report is unwritable). That is a
genuine tool failure and the graph routes it through ``outcome=fail`` to the
loud terminal -- deliberately distinct from ``findings_bad``, which is a
*judgement* about the workers' output.

Side effects, all under ``--state-dir``:

  findings-report.txt   per-file, per-finding ACCEPT/REJECT with the reason
                        (written on every run -- the revise worker's only input)
  findings.json         the consolidated corpus, written ONLY on findings_ok
  revise-iter           the corrective-loop counter (the budget wall)

Usage:
    check_findings.py --raw-dir .drift-review/raw \\
                      --repo-root . \\
                      --state-dir .drift-review \\
                      --classes core-docs,examples,guidance,ledgers \\
                      --max-revisions 2
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# The fixed vocabularies. A finding that invents a severity is not triageable:
# the human reading the report sorts by this field.
# ---------------------------------------------------------------------------

#: Severity vocabulary. Fixed, ordered most-severe first.
SEVERITIES: tuple[str, ...] = ("critical", "high", "medium", "low")

#: The normative sources a finding is allowed to cite on its `contradicts` side.
#: Prefix match against the repo-relative posix path. This IS the definition of
#: "drift" for Layer 3: movement away from one of these, not from a sibling doc.
NORMATIVE_PREFIXES: tuple[str, ...] = (
    "specs/canonical/",
    "specs/conformance/",
    "specs/EXTENSIONS.md",
    "SPEC_CONFORMANCE.md",
    "docs/VISION.md",
)

#: Finding ids: short, unique, human-quotable in an issue title.
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")

#: A quote shorter than this cannot anchor anything -- "the" appears everywhere.
MIN_QUOTE_CHARS = 16

#: `why` has to carry the argument, not just repeat the title.
MIN_WHY_CHARS = 40

#: `title` has to be a sentence, not a word.
MIN_TITLE_CHARS = 12

#: Quote resolution window around the cited line, in lines. Generous enough that
#: a reflowed markdown paragraph still resolves, tight enough that the citation
#: still means something: a quote that only appears 40 lines away is a wrong
#: citation, and a wrong citation is the failure this gate exists to catch.
WINDOW_BEFORE = 2
WINDOW_AFTER = 6

_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Collapse whitespace so a reflowed quote still matches its source."""
    return _WS_RE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# Citation resolution -- the load-bearing half
# ---------------------------------------------------------------------------


def resolve_in_tree(repo_root: Path, rel: str) -> tuple[Path | None, str | None]:
    """Resolve a repo-relative path, refusing anything that escapes the tree."""
    if not isinstance(rel, str) or not rel.strip():
        return None, "path is empty"
    if rel.startswith("/"):
        return None, f"'{rel}' is absolute; citations must be repo-relative"
    candidate = (repo_root / rel).resolve()
    try:
        candidate.relative_to(repo_root.resolve())
    except ValueError:
        return None, f"'{rel}' resolves outside the repository root"
    if not candidate.exists():
        return None, f"'{rel}' does not exist in the tree"
    if not candidate.is_file():
        return None, f"'{rel}' is not a regular file"
    return candidate, None


def check_citation(repo_root: Path, cite: Any, side: str) -> list[str]:
    """Validate one side of a finding: file exists, line is in range, quote resolves."""
    if not isinstance(cite, dict):
        return [f"{side}: expected an object with file/line/quote, got {type(cite).__name__}"]

    missing = [key for key in ("file", "line", "quote") if key not in cite]
    if missing:
        return [f"{side}: missing required field '{key}'" for key in missing]

    rel = cite["file"]
    path, why = resolve_in_tree(repo_root, rel if isinstance(rel, str) else "")
    if path is None:
        return [f"{side}.file: {why}"]

    line = cite["line"]
    if isinstance(line, bool) or not isinstance(line, int):
        return [f"{side}.line: expected an integer line number, got {line!r}"]

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:  # pragma: no cover - unreadable file inside the tree
        return [f"{side}.file: '{rel}' could not be read: {exc}"]

    if line < 1 or line > len(lines):
        return [f"{side}.line: {rel} has {len(lines)} lines; cited line {line} is out of range"]

    quote = cite["quote"]
    if not isinstance(quote, str):
        return [f"{side}.quote: expected a string, got {type(quote).__name__}"]
    needle = normalize(quote)
    if len(needle) < MIN_QUOTE_CHARS:
        return [
            f"{side}.quote: {len(needle)} characters after whitespace normalization; "
            f"at least {MIN_QUOTE_CHARS} are required for the quote to anchor the citation"
        ]

    lo = max(0, line - 1 - WINDOW_BEFORE)
    hi = min(len(lines), line + WINDOW_AFTER)
    window = normalize(" ".join(lines[lo:hi]))
    if needle not in window:
        return [
            f"{side}: the quote does not appear at {rel}:{line} "
            f"(searched lines {lo + 1}-{hi}). Quote the text that is actually there, "
            "or cite the line where the text you quoted lives."
        ]
    return []


# ---------------------------------------------------------------------------
# Finding shape
# ---------------------------------------------------------------------------


def check_finding(
    repo_root: Path, finding: Any, expected_class: str, seen_ids: set[str]
) -> list[str]:
    """Validate one finding. Returns human-readable rejection reasons."""
    if not isinstance(finding, dict):
        return [f"expected a finding object, got {type(finding).__name__}"]

    required = ("id", "severity", "class", "title", "drift", "contradicts", "why")
    missing = [key for key in required if key not in finding]
    if missing:
        return [f"missing required field '{key}'" for key in missing]

    errors: list[str] = []

    fid = finding["id"]
    if not isinstance(fid, str) or not ID_RE.match(fid):
        errors.append(
            f"id: {fid!r} is not a short slug matching {ID_RE.pattern} "
            "(3-64 chars: letters, digits, dot, underscore, hyphen)"
        )
    elif fid in seen_ids:
        errors.append(f"id: '{fid}' is used more than once; finding ids must be unique")
    else:
        seen_ids.add(fid)

    severity = finding["severity"]
    if severity not in SEVERITIES:
        errors.append(f"severity: {severity!r} is not one of {list(SEVERITIES)}")

    cls = finding["class"]
    if cls != expected_class:
        errors.append(f"class: {cls!r} does not match this file's class '{expected_class}'")

    title = finding["title"]
    if not isinstance(title, str) or len(title.strip()) < MIN_TITLE_CHARS:
        errors.append(f"title: must be at least {MIN_TITLE_CHARS} characters of real summary")

    why = finding["why"]
    if not isinstance(why, str) or len(why.strip()) < MIN_WHY_CHARS:
        errors.append(
            f"why: must be at least {MIN_WHY_CHARS} characters stating what the "
            "contradiction IS -- a citation pair with no argument is not a finding"
        )

    errors.extend(check_citation(repo_root, finding["drift"], "drift"))
    errors.extend(check_citation(repo_root, finding["contradicts"], "contradicts"))

    drift = finding["drift"]
    contradicts = finding["contradicts"]
    if isinstance(drift, dict) and isinstance(contradicts, dict):
        normative = contradicts.get("file")
        if isinstance(normative, str) and not normative.startswith(NORMATIVE_PREFIXES):
            errors.append(
                f"contradicts.file: '{normative}' is not a normative source. Layer 3 measures "
                f"drift against {list(NORMATIVE_PREFIXES)}; a disagreement between two "
                "non-normative surfaces is a proofreading note, not drift. Cite the spec, "
                "vision or ledger passage the surface actually contradicts."
            )
        if drift.get("file") == contradicts.get("file"):
            errors.append(
                "drift.file and contradicts.file are the same file; a finding must name the "
                "drifting surface AND the separate normative passage it contradicts"
            )

    return errors


# ---------------------------------------------------------------------------
# Class files
# ---------------------------------------------------------------------------


def check_class_file(
    repo_root: Path, path: Path, expected_class: str, seen_ids: set[str]
) -> tuple[list[str], list[dict[str, Any]], list[str]]:
    """Validate one ``<class>.json`` file. Returns (errors, accepted findings, swept)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return (
            [
                f"{path.name}: not written. The '{expected_class}' review worker produced no "
                "output at all; re-run that class's review, or record honestly what it swept "
                "with an empty findings list."
            ],
            [],
            [],
        )
    except OSError as exc:
        return ([f"{path.name}: unreadable: {exc}"], [], [])
    except json.JSONDecodeError as exc:
        return ([f"{path.name}: not valid JSON: {exc}"], [], [])

    if not isinstance(raw, dict):
        return ([f"{path.name}: expected a JSON object, got {type(raw).__name__}"], [], [])

    errors: list[str] = []
    if raw.get("class") != expected_class:
        errors.append(f"{path.name}: 'class' must be '{expected_class}', got {raw.get('class')!r}")

    swept = raw.get("swept")
    if not isinstance(swept, list) or not swept:
        errors.append(
            f"{path.name}: 'swept' must be a non-empty list of the repo-relative paths this "
            "class actually read. A zero-finding class still has to say what it swept -- "
            "otherwise 'no drift found' and 'nothing was looked at' are the same record."
        )
        swept = []
    else:
        for entry in swept:
            resolved, why = resolve_in_tree(repo_root, entry if isinstance(entry, str) else "")
            if resolved is None:
                errors.append(f"{path.name}: swept entry {entry!r}: {why}")

    findings = raw.get("findings")
    if not isinstance(findings, list):
        errors.append(f"{path.name}: 'findings' must be a list (use [] when nothing was found)")
        return (errors, [], [s for s in swept if isinstance(s, str)])

    accepted: list[dict[str, Any]] = []
    for index, finding in enumerate(findings):
        reasons = check_finding(repo_root, finding, expected_class, seen_ids)
        label = finding.get("id") if isinstance(finding, dict) else None
        where = f"{path.name}[{index}]" + (f" ({label})" if isinstance(label, str) else "")
        if reasons:
            errors.extend(f"{where}: {reason}" for reason in reasons)
        else:
            assert isinstance(finding, dict)
            accepted.append(finding)

    return (errors, accepted, [s for s in swept if isinstance(s, str)])


# ---------------------------------------------------------------------------
# Fuse -- bounded revision
# ---------------------------------------------------------------------------


def bump_revise_counter(state_dir: Path) -> int:
    counter = state_dir / "revise-iter"
    try:
        current = int(counter.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        current = 0
    current += 1
    counter.write_text(f"{current}\n", encoding="utf-8")
    return current


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate drift-review findings", add_help=True)
    parser.add_argument("--raw-dir", required=True, help="directory holding <class>.json files")
    parser.add_argument("--repo-root", default=".", help="repository the citations resolve against")
    parser.add_argument("--state-dir", default=".drift-review", help="gate bookkeeping directory")
    parser.add_argument(
        "--classes",
        default="core-docs,examples,guidance,ledgers",
        help="comma-separated surface classes, each expected to produce <class>.json",
    )
    parser.add_argument(
        "--max-revisions",
        type=int,
        default=2,
        help="how many malformed rounds to tolerate before giving up (default: 2)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # --- things that make this gate UNABLE to run: exit nonzero, print nothing.
    repo_root = Path(args.repo_root)
    if not repo_root.is_dir():
        print(f"check_findings: --repo-root {repo_root} is not a directory", file=sys.stderr)
        return 2

    raw_dir = Path(args.raw_dir)
    if not raw_dir.is_dir():
        print(
            f"check_findings: --raw-dir {raw_dir} is not a directory; preflight is supposed to "
            "create it, so this is a machinery failure rather than a bad finding",
            file=sys.stderr,
        )
        return 2

    state_dir = Path(args.state_dir)
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"check_findings: cannot create state dir {state_dir}: {exc}", file=sys.stderr)
        return 2

    classes = [c.strip() for c in args.classes.split(",") if c.strip()]
    if not classes:
        print("check_findings: --classes resolved to an empty list", file=sys.stderr)
        return 2

    # --- things that make the FINDINGS bad: exit 0, print a judgement token.
    errors: list[str] = []
    accepted: list[dict[str, Any]] = []
    swept_by_class: dict[str, list[str]] = {}
    seen_ids: set[str] = set()

    for cls in classes:
        cls_errors, cls_findings, cls_swept = check_class_file(
            repo_root, raw_dir / f"{cls}.json", cls, seen_ids
        )
        errors.extend(cls_errors)
        accepted.extend(cls_findings)
        swept_by_class[cls] = cls_swept

    report_path = state_dir / "findings-report.txt"
    findings_path = state_dir / "findings.json"

    if errors:
        attempt = bump_revise_counter(state_dir)
        token = "findings_bad" if attempt <= args.max_revisions else "revise_exhausted"
        report = [
            "FINDINGS REJECTED",
            f"raw dir:  {raw_dir}",
            f"classes:  {', '.join(classes)}",
            f"attempt:  {attempt} of {args.max_revisions} permitted",
            f"verdict:  {token}",
            "",
            "Every finding must cite file:line on BOTH sides -- the drifting surface and the",
            "normative passage it contradicts -- with quotes that resolve against this tree.",
            "",
            "Rejections (fix exactly these; leave everything else alone):",
            *(f"  - {e}" for e in errors),
        ]
        try:
            report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"check_findings: cannot write {report_path}: {exc}", file=sys.stderr)
            return 2
        # A rejected round must not leave a stale corpus behind for a later gate
        # to mistake for this round's output.
        findings_path.unlink(missing_ok=True)
        print(token, end="")
        return 0

    accepted.sort(key=lambda f: (SEVERITIES.index(str(f["severity"])), str(f["id"])))
    corpus = {
        "schema": "drift-review/findings/1",
        "repo_root": str(repo_root),
        "classes": classes,
        "swept": swept_by_class,
        "finding_count": len(accepted),
        "findings": accepted,
    }
    try:
        findings_path.write_text(json.dumps(corpus, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"check_findings: cannot write {findings_path}: {exc}", file=sys.stderr)
        return 2

    swept_total = sum(len(v) for v in swept_by_class.values())
    listing = [f"  {f['id']}  {str(f['severity']):<8}  {f['title']}" for f in accepted] or [
        "  (none -- a clean sweep is a result, not a failure)"
    ]
    report = [
        "FINDINGS ADMITTED",
        f"raw dir:  {raw_dir}",
        f"classes:  {', '.join(classes)}",
        f"findings: {len(accepted)}",
        f"swept:    {swept_total} surfaces",
        "",
        "Per class:",
        *(
            f"  {cls}: {sum(1 for f in accepted if f['class'] == cls)} finding(s), "
            f"{len(swept_by_class[cls])} surface(s) swept"
            for cls in classes
        ),
        "",
        "Admitted findings (id / severity / title):",
        *listing,
    ]
    try:
        report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"check_findings: cannot write {report_path}: {exc}", file=sys.stderr)
        return 2

    print("findings_ok", end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())
