#!/usr/bin/env python3
"""Admit or reject the drift-review workers' findings -- the Layer-3 shape gate.

``drift-review.dot`` is the executor for ``docs/OPERATIONS.md`` section 5,
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

Coverage is MEASURED, not attested. Each reviewer reports what it swept; this
gate reconciles that array against ``inventory/<class>.txt`` -- the list the
``inventory`` node wrote before any reviewer ran -- and publishes the fraction.
Out-of-class normative sources and duplicate entries are counted separately, so
the headline can no longer say "129 surfaces swept" about 118 real ones. What it
deliberately does NOT do is *reject* a partial sweep: it can compare the array
to the inventory but cannot check the reading, so a pass/fail bar there would
buy a full-looking array rather than a full sweep. An honest partial sweep is a
fine outcome; an unmarked one is not (``class_coverage``).

Side effects, all under ``--state-dir``:

  findings-report.txt   per-file, per-finding ACCEPT/REJECT with the reason,
                        plus the COVERAGE reconciliation (written on every run
                        -- the revise worker's only input)
  findings.json         the consolidated corpus, written ONLY on findings_ok
  coverage.txt          one honest ``class: swept/inventory (pct)`` line per
                        class, written ONLY on findings_ok; ``report_gate``
                        requires report.md to carry each line verbatim, which
                        is what stops the deliverable and the record from
                        publishing two different numbers for the same run
  revise-iter           the corrective-loop counter (the budget wall)

Usage:
    check_findings.py --raw-dir .drift-review/raw \\
                      --repo-root . \\
                      --state-dir .drift-review \\
                      --inventory-dir .drift-review/inventory \\
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

#: How many unswept paths to name individually before switching to a count.
#: Enough that a small gap is actionable; bounded so a 52-file gap does not
#: bury the rest of the report.
MAX_UNSWEPT_NAMED = 12

_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Collapse whitespace so a reflowed quote still matches its source."""
    return _WS_RE.sub(" ", text).strip()


# ---------------------------------------------------------------------------
# Citation resolution -- the load-bearing half
# ---------------------------------------------------------------------------


def resolve_in_tree(repo_root: Path, rel: str) -> tuple[Path | None, str | None, str | None]:
    """Resolve a repo-relative path, refusing anything that escapes the tree.

    Returns ``(path, resolved_rel, why)``. ``resolved_rel`` is the citation's
    repo-relative posix form *after* resolution; ``why`` is the rejection
    reason, and is set exactly when the other two are ``None``.

    Handing back the resolved form is load-bearing rather than a convenience.
    Every rule downstream that reasons about *which file* a citation names has
    to judge the file, and the raw citation string is not the file:

      * ``specs/canonical/../../docs/OPERATIONS.md`` satisfies a
        ``startswith("specs/canonical/")`` test on its raw form while pointing
        clean out of the closed normative set.
      * ``specs/canonical/../../README.md`` is a different *string* from
        ``README.md`` while being the same *file*, which is enough to walk a
        finding past the rule that the drifting surface and the passage it
        contradicts must be two different files.

    Both were admissions while the callers tested the raw string. A closed set
    is only as closed as the path it is closed over.
    """
    if not isinstance(rel, str) or not rel.strip():
        return None, None, "path is empty"
    if rel.startswith("/"):
        return None, None, f"'{rel}' is absolute; citations must be repo-relative"
    candidate = (repo_root / rel).resolve()
    try:
        resolved_rel = candidate.relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return None, None, f"'{rel}' resolves outside the repository root"
    if not candidate.exists():
        return None, None, f"'{rel}' does not exist in the tree"
    if not candidate.is_file():
        return None, None, f"'{rel}' is not a regular file"
    return candidate, resolved_rel, None


def name_path(raw: Any, resolved: str) -> str:
    """Render a citation path, making the traversal visible when one was used."""
    return f"'{raw}'" if raw == resolved else f"'{raw}' (which resolves to '{resolved}')"


def check_citation(repo_root: Path, cite: Any, side: str) -> tuple[list[str], str | None]:
    """Validate one side of a finding: file exists, line is in range, quote resolves.

    Returns ``(errors, resolved_rel)``. ``resolved_rel`` is populated whenever
    the cited path itself resolved inside the tree -- deliberately including the
    cases where a *later* check (line, quote) then failed, because the
    closed-normative-set and different-files rules in ``check_finding`` are
    about the FILE, and they must still judge the file the citation names.
    """
    if not isinstance(cite, dict):
        return (
            [f"{side}: expected an object with file/line/quote, got {type(cite).__name__}"],
            None,
        )

    missing = [key for key in ("file", "line", "quote") if key not in cite]
    if missing:
        return ([f"{side}: missing required field '{key}'" for key in missing], None)

    rel = cite["file"]
    path, resolved_rel, why = resolve_in_tree(repo_root, rel if isinstance(rel, str) else "")
    if path is None or resolved_rel is None:
        return ([f"{side}.file: {why}"], None)

    line = cite["line"]
    if isinstance(line, bool) or not isinstance(line, int):
        return ([f"{side}.line: expected an integer line number, got {line!r}"], resolved_rel)

    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:  # pragma: no cover - unreadable file inside the tree
        return ([f"{side}.file: '{rel}' could not be read: {exc}"], resolved_rel)

    if line < 1 or line > len(lines):
        return (
            [f"{side}.line: {rel} has {len(lines)} lines; cited line {line} is out of range"],
            resolved_rel,
        )

    quote = cite["quote"]
    if not isinstance(quote, str):
        return ([f"{side}.quote: expected a string, got {type(quote).__name__}"], resolved_rel)
    needle = normalize(quote)
    if len(needle) < MIN_QUOTE_CHARS:
        return (
            [
                f"{side}.quote: {len(needle)} characters after whitespace normalization; "
                f"at least {MIN_QUOTE_CHARS} are required for the quote to anchor the citation"
            ],
            resolved_rel,
        )

    lo = max(0, line - 1 - WINDOW_BEFORE)
    hi = min(len(lines), line + WINDOW_AFTER)
    window = normalize(" ".join(lines[lo:hi]))
    if needle not in window:
        return (
            [
                f"{side}: the quote does not appear at {rel}:{line} "
                f"(searched lines {lo + 1}-{hi}). Quote the text that is actually there, "
                "or cite the line where the text you quoted lives."
            ],
            resolved_rel,
        )
    return ([], resolved_rel)


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

    drift_errors, drift_rel = check_citation(repo_root, finding["drift"], "drift")
    contradicts_errors, contradicts_rel = check_citation(
        repo_root, finding["contradicts"], "contradicts"
    )
    errors.extend(drift_errors)
    errors.extend(contradicts_errors)

    # BOTH rules below judge the RESOLVED repo-relative path, never the raw
    # citation string. A citation that did not resolve has already been rejected
    # by check_citation above, so declining to guess at its intent here costs
    # nothing; testing the raw string, by contrast, cost two admissions
    # (see resolve_in_tree).
    drift = finding["drift"]
    contradicts = finding["contradicts"]
    if contradicts_rel is not None and not contradicts_rel.startswith(NORMATIVE_PREFIXES):
        raw = contradicts.get("file") if isinstance(contradicts, dict) else contradicts_rel
        errors.append(
            f"contradicts.file: {name_path(raw, contradicts_rel)} is not a normative source. "
            f"Layer 3 measures drift against {list(NORMATIVE_PREFIXES)}; a disagreement between "
            "two non-normative surfaces is a proofreading note, not drift. Cite the spec, "
            "vision or ledger passage the surface actually contradicts."
        )
    if drift_rel is not None and drift_rel == contradicts_rel:
        raw_drift = drift.get("file") if isinstance(drift, dict) else drift_rel
        raw_contradicts = contradicts.get("file") if isinstance(contradicts, dict) else drift_rel
        errors.append(
            f"drift.file {name_path(raw_drift, drift_rel)} and contradicts.file "
            f"{name_path(raw_contradicts, drift_rel)} are the same file; a finding must name "
            "the drifting surface AND the separate normative passage it contradicts"
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
            entry_rel = entry if isinstance(entry, str) else ""
            resolved, _resolved_rel, why = resolve_in_tree(repo_root, entry_rel)
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
# Coverage -- reconciling what a reviewer SAYS it swept against what it was
# scoped to
#
# `swept` arrives as an attestation. The `inventory` node already wrote, to
# disk, the exact list of paths each class was scoped to. Both sides are
# therefore in this gate's hands, outside every reviewer's context, and the
# comparison is a set operation -- so leaving `swept` unreconciled was leaving a
# measurement uncollected, not a hard problem unsolved.
#
# WHAT THIS DELIBERATELY DOES NOT DO: reject a partial sweep. See
# `class_coverage` for the argument; the short version is that this gate can
# check the ARRAY against the inventory but cannot check the READING, so a
# pass/fail bar on coverage would buy a full-looking array rather than a full
# sweep -- an unfalsifiable claim, which is the exact species of thing the rest
# of this script exists to make impossible.
# ---------------------------------------------------------------------------


def load_inventory(inventory_dir: Path, cls: str) -> tuple[set[str] | None, str | None]:
    """Read one class's inventory list. Returns ``(paths, why_unreadable)``."""
    path = inventory_dir / f"{cls}.txt"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, f"{path} does not exist"
    except OSError as exc:
        return None, f"{path} could not be read: {exc}"
    entries = {line.strip() for line in text.splitlines() if line.strip()}
    if not entries:
        return None, f"{path} is empty"
    return entries, None


def class_coverage(cls: str, swept: list[str], inventory: set[str]) -> dict[str, Any]:
    """Measure one class's reported sweep against the inventory it was scoped to.

    Four numbers, each answering a question the bare array length silently
    conflated:

    ``in_class``
        unique reported paths that ARE in this class's inventory. This is the
        only population that may be called "surfaces of this class swept", and
        it is what the headline count is now built from.
    ``out_of_class``
        reported paths that resolve in the tree but belong to no part of this
        class's inventory. These are legitimately *read* -- every reviewer's
        prompt tells it to open the canonical spec, the vision and the ledgers
        for context -- but they are not surfaces of the class under review, and
        counting them as such inflates the sweep. Reported separately.
    ``duplicates``
        paths named more than once in the same array. Bookkeeping, not
        dishonesty, but it inflates a count just as effectively.
    ``unswept``
        inventory paths the array never names. **This is the finding.** The
        first live run left 52 of 114 `examples` files here and reported a
        clean four-class sweep.

    Why this REPORTS rather than REJECTS. A hard ``swept >= inventory`` bar is
    checkable, and it is the wrong instrument: what it checks is whether the
    *array* covers the inventory, not whether the reviewer *read* the files.
    The cheapest way to satisfy it is to paste the inventory into ``swept``,
    which this gate cannot distinguish from a real sweep -- and the repair
    worker that a rejection routes to is explicitly forbidden from reviewing,
    so the only repair available to it IS the paste. That is a gate satisfiable
    by the thing it grades, which is the shape this exemplar exists to refuse
    (README, "Verification lives outside the worker's context").

    An honest partial sweep is a fine outcome; an unmarked one is not. So the
    fraction is measured, published, and carried into the deliverable by a
    gate -- and the reader of ``report.md`` can now tell "swept 114 files, found
    3 issues" from "swept 62 files, found 3 issues", which are very different
    reports.
    """
    unique = sorted(set(swept))
    counts: dict[str, int] = {}
    for entry in swept:
        counts[entry] = counts.get(entry, 0) + 1
    in_class = sorted(p for p in unique if p in inventory)
    return {
        "class": cls,
        "inventory": len(inventory),
        "reported": len(swept),
        "in_class": len(in_class),
        "in_class_paths": in_class,
        "out_of_class": sorted(p for p in unique if p not in inventory),
        "duplicates": sorted(p for p, n in counts.items() if n > 1),
        "unswept": sorted(inventory - set(unique)),
    }


def coverage_line(entry: dict[str, Any]) -> str:
    """The one line per class that report.md is required to carry, verbatim.

    Deliberately the smallest honest statement that cannot be rounded up:
    swept-of-inventory and the percentage. ``report_gate`` greps for exactly
    this string, so the number in the deliverable is the number this gate
    measured -- closing the gap where ``report.md`` said ``guidance | 28`` and
    the admission record said ``guidance: 33`` for the same run.
    """
    total = int(entry["inventory"])
    swept = int(entry["in_class"])
    pct = (100 * swept // total) if total else 0
    return f"{entry['class']}: {swept}/{total} ({pct}%)"


def coverage_report_lines(coverage: list[dict[str, Any]]) -> list[str]:
    """The COVERAGE section of findings-report.txt."""
    lines = [
        "COVERAGE (reported sweep reconciled against the inventory on disk):",
    ]
    for entry in coverage:
        lines.append(f"  {coverage_line(entry)}")
        if entry["unswept"]:
            names = entry["unswept"]
            shown = names[:MAX_UNSWEPT_NAMED]
            tail = "" if len(names) <= MAX_UNSWEPT_NAMED else f", and {len(names) - len(shown)} more"
            lines.append(f"    NOT swept ({len(names)}): {', '.join(shown)}{tail}")
        if entry["out_of_class"]:
            lines.append(
                f"    read but out of class ({len(entry['out_of_class'])}, not counted as "
                f"surfaces of '{entry['class']}'): {', '.join(entry['out_of_class'])}"
            )
        if entry["duplicates"]:
            lines.append(
                f"    listed more than once ({len(entry['duplicates'])}, counted once): "
                f"{', '.join(entry['duplicates'])}"
            )
    return lines


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
        "--inventory-dir",
        default=None,
        help=(
            "directory holding <class>.txt, the surface lists the inventory node wrote "
            "(default: <state-dir>/inventory). Reconciliation without it is not "
            "reconciliation, so its absence is a machinery failure, not a verdict"
        ),
    )
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

    # The inventory is the other half of every coverage number below. Without it
    # this gate can only repeat what the reviewers claimed, which is the state
    # issue #244 describes -- so its absence is a MACHINERY failure (exit 2, no
    # token, routed to the loud terminal), never a quiet degradation to
    # attestation-only. The inventory node runs before every reviewer, so a
    # missing list means something upstream broke, not that a reviewer misbehaved.
    inventory_dir = Path(args.inventory_dir) if args.inventory_dir else state_dir / "inventory"
    inventories: dict[str, set[str]] = {}
    for cls in classes:
        entries, why = load_inventory(inventory_dir, cls)
        if entries is None:
            print(
                f"check_findings: cannot reconcile the '{cls}' sweep because {why}. The "
                "inventory node writes these lists before any reviewer runs; without them "
                "'swept' is an unchecked claim and this gate would be reporting a coverage "
                "number it did not measure.",
                file=sys.stderr,
            )
            return 2
        inventories[cls] = entries

    # --- things that make the FINDINGS bad: exit 0, print a judgement token.
    errors: list[str] = []
    accepted: list[dict[str, Any]] = []
    swept_by_class: dict[str, list[str]] = {}
    coverage: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for cls in classes:
        cls_errors, cls_findings, cls_swept = check_class_file(
            repo_root, raw_dir / f"{cls}.json", cls, seen_ids
        )
        errors.extend(cls_errors)
        accepted.extend(cls_findings)
        swept_by_class[cls] = cls_swept

        entry = class_coverage(cls, cls_swept, inventories[cls])
        coverage.append(entry)
        # The ONE coverage rule with teeth, and it is the rule this gate already
        # had -- "a class must say what it swept" -- corrected to measure the
        # inventory instead of the array length. A reviewer whose whole `swept`
        # array is out-of-class context files read nothing of its own class, and
        # that is not a partial sweep, it is an absent one. Everything else about
        # coverage is reported rather than adjudicated (see `class_coverage`).
        # Guarded on a NON-EMPTY array: a class file that was never written, or
        # whose `swept` is empty, is already named by its own rule above, and
        # two errors for one cause is a worse repair brief than one.
        if cls_swept and entry["in_class"] == 0 and entry["inventory"]:
            errors.append(
                f"{cls}.json: 'swept' names {entry['reported']} path(s) but NONE of them are "
                f"in {inventory_dir / (cls + '.txt')}, which lists the {entry['inventory']} "
                f"surface(s) this class was scoped to. Reading the normative sources for "
                f"context is expected and they belong in 'swept'; reading none of the class "
                f"itself means there was no review of '{cls}' to report."
            )

    report_path = state_dir / "findings-report.txt"
    findings_path = state_dir / "findings.json"
    coverage_path = state_dir / "coverage.txt"

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
            "",
            *coverage_report_lines(coverage),
        ]
        try:
            report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"check_findings: cannot write {report_path}: {exc}", file=sys.stderr)
            return 2
        # A rejected round must not leave a stale corpus behind for a later gate
        # to mistake for this round's output. The same is true of the coverage
        # contract: report_gate requires report.md to carry these lines
        # verbatim, so a stale copy would let a later round be admitted against
        # an earlier round's numbers.
        findings_path.unlink(missing_ok=True)
        coverage_path.unlink(missing_ok=True)
        print(token, end="")
        return 0

    accepted.sort(key=lambda f: (SEVERITIES.index(str(f["severity"])), str(f["id"])))
    corpus = {
        "schema": "drift-review/findings/2",
        "repo_root": str(repo_root),
        "classes": classes,
        "swept": swept_by_class,
        # The reconciliation, carried in the corpus itself so that every
        # downstream reader -- the consolidate worker, the report gate, and a
        # human opening the artifact directory a month later -- is looking at
        # the same measured numbers rather than re-deriving them or, as in the
        # first live run, quietly disagreeing about them.
        "coverage": coverage,
        "finding_count": len(accepted),
        "findings": accepted,
    }
    try:
        findings_path.write_text(json.dumps(corpus, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        print(f"check_findings: cannot write {findings_path}: {exc}", file=sys.stderr)
        return 2

    try:
        coverage_path.write_text(
            "\n".join(coverage_line(entry) for entry in coverage) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        print(f"check_findings: cannot write {coverage_path}: {exc}", file=sys.stderr)
        return 2

    # THE HEADLINE, rebuilt so it cannot overstate. The old number was the sum
    # of the reported array lengths, which counted normative context files and
    # duplicate entries as swept surfaces of the class -- the first live run
    # published "129 surfaces swept" against 118 real in-class surfaces and a
    # 170-file inventory. Every population is now named separately, and the
    # denominator is always present.
    swept_total = sum(int(c["in_class"]) for c in coverage)
    inventory_total = sum(int(c["inventory"]) for c in coverage)
    out_of_class_total = sum(len(c["out_of_class"]) for c in coverage)
    duplicate_total = sum(len(c["duplicates"]) for c in coverage)
    pct = (100 * swept_total // inventory_total) if inventory_total else 0
    listing = [f"  {f['id']}  {str(f['severity']):<8}  {f['title']}" for f in accepted] or [
        "  (none -- a clean sweep is a result, not a failure)"
    ]
    report = [
        "FINDINGS ADMITTED",
        f"raw dir:  {raw_dir}",
        f"classes:  {', '.join(classes)}",
        f"findings: {len(accepted)}",
        f"swept:    {swept_total} of {inventory_total} inventoried in-class surfaces ({pct}%)",
        f"also:     {out_of_class_total} out-of-class source(s) read, "
        f"{duplicate_total} duplicate entr(y/ies) counted once",
        "",
        "Per class:",
        *(
            f"  {cls}: {sum(1 for f in accepted if f['class'] == cls)} finding(s), "
            f"{entry['in_class']} of {entry['inventory']} surface(s) swept"
            for cls, entry in zip(classes, coverage, strict=True)
        ),
        "",
        *coverage_report_lines(coverage),
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
