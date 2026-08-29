#!/usr/bin/env python3
"""Admit or reject the intake triage record for objective-runner.dot.

This is the code half of the objective layer's first routing decision. The
``frame`` node (an LLM worker) writes ``.objective/triage.json``; this script --
run by the ``triage_gate`` parallelogram node, outside the worker's context --
validates it against ``triage-schema.json`` and prints exactly ONE routing token
on stdout.

Why routing runs on a validated artifact rather than on the worker's own
self-reported ``preferred_label`` (formerly settable via the now-removed
``report_outcome`` tool, engine 0.2.0): verification inside the context that
produced the evidence is not verification (docs/PIPELINE_DESIGN_PRINCIPLES.md
section 0). The worker proposes; this gate admits.

Token contract (Idiom A -- always exit 0 so ``tool.last_line`` is always fresh):

  bugfix | feature | refactor | testgen | review | compose | redirect
                          the admitted shape; route to that lane / path
  triage_bad              the record is missing or malformed; re-frame
  triage_exhausted        too many malformed records; stop re-framing

A nonzero exit means this gate could not run at all (schema unreadable, state
directory unwritable). That is a genuine tool failure and the graph routes it to
the postmortem path via ``outcome=fail`` -- deliberately distinct from
``triage_bad``, which is a *judgement* about the record.

Side effects on an admitted record, all under ``--state-dir``:

  shape              the admitted token (audit trail)
  evidence-command   the definition-of-done command text, for the evidence gate
                     to re-run itself (absent for the redirect shape)
  evidence-command.sha256
                     the sha-pin of the file above, recorded at the moment it was
                     admitted. ``evidence_gate`` re-hashes before re-running, so a
                     child that overwrites the evidence command mid-run is caught
                     rather than obeyed. Anti-accident, not anti-adversary: the
                     workspace is child-writable, so a determined child could
                     update the pin too (see compose-contract.md, "The pin, and
                     what it is not").
  triage-report.txt  human-readable admission report (written on every run)

Usage:
    validate_triage.py --triage .objective/triage.json \\
                       --schema /abs/path/to/triage-schema.json \\
                       --state-dir .objective \\
                       --max-frames 2
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

COMPOSE_DOD_COMMAND = "bash .objective/gen/dod.sh"


# ---------------------------------------------------------------------------
# The JSON Schema subset (see triage-schema.json's `schema_dialect` field)
# ---------------------------------------------------------------------------


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, (int, float)):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def validate_against_schema(instance: Any, schema: dict[str, Any], path: str = "") -> list[str]:
    """Validate ``instance`` against the supported schema subset.

    Supports exactly: ``type`` (object/string), ``required``, ``properties``,
    ``enum``, ``min_length``. Anything else in the schema document is treated as
    documentation and ignored -- deliberately, so the schema file can carry prose
    for humans without the validator pretending to enforce it.
    """
    errors: list[str] = []
    where = path or "<root>"

    expected_type = schema.get("type")
    if expected_type and _type_name(instance) != expected_type:
        return [f"{where}: expected type {expected_type}, got {_type_name(instance)}"]

    if expected_type == "object":
        assert isinstance(instance, dict)
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{where}: missing required field '{key}'")
        for key, subschema in schema.get("properties", {}).items():
            if key in instance:
                child = f"{path}.{key}" if path else key
                errors.extend(validate_against_schema(instance[key], subschema, child))

    if expected_type == "string":
        assert isinstance(instance, str)
        enum = schema.get("enum")
        if enum is not None and instance not in enum:
            errors.append(f"{where}: '{instance}' is not one of {enum}")
        min_length = schema.get("min_length")
        if min_length is not None and len(instance.strip()) < min_length:
            errors.append(
                f"{where}: string is shorter than min_length {min_length} "
                f"(got {len(instance.strip())})"
            )

    return errors


# ---------------------------------------------------------------------------
# Cross-field rules (triage-schema.json `cross_field_rules`)
# ---------------------------------------------------------------------------


def check_cross_field_rules(record: dict[str, Any]) -> list[str]:
    """Apply the rules a per-field schema cannot express."""
    errors: list[str] = []
    shape = record.get("shape")
    evidence_command = record.get("evidence_command")

    # CF-1: no attractor without machine evidence.
    if (
        isinstance(shape, str)
        and shape != "redirect"
        and isinstance(evidence_command, str)
        and evidence_command.strip() == "NONE"
    ):
        errors.append(
            "CF-1: shape is '"
            + shape
            + "' but evidence_command is NONE. An objective with no machine-checkable "
            "definition of done cannot be gated on evidence -- the honest shape is "
            "'redirect'."
        )

    # CF-2: the composed child's DoD lives at a fixed path.
    if (
        shape == "compose"
        and isinstance(evidence_command, str)
        and evidence_command.strip() != COMPOSE_DOD_COMMAND
    ):
        errors.append(
            "CF-2: shape is 'compose' so evidence_command must be exactly "
            f"'{COMPOSE_DOD_COMMAND}' (got '{evidence_command.strip()}'). The parent "
            "evidence gate re-runs the DoD from a fixed path so it never has to trust "
            "what the composer wrote about itself."
        )

    # CF-3: the three-question answers must be machine-auditable.
    three_question = record.get("three_question")
    if isinstance(three_question, dict):
        for key in ("cycle", "evidence_gate", "bad_day"):
            answer = three_question.get(key)
            if not isinstance(answer, str):
                continue
            first = answer.strip().lower().lstrip("*_` ")
            if not first.startswith(("yes", "no")):
                errors.append(
                    f"CF-3: three_question.{key} must begin with 'yes' or 'no' "
                    f"(got: {answer.strip()[:60]!r})"
                )

    return errors


# ---------------------------------------------------------------------------
# Fuse -- bounded re-framing
# ---------------------------------------------------------------------------


def bump_frame_counter(state_dir: Path) -> int:
    counter = state_dir / "frame-iter"
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
    parser = argparse.ArgumentParser(description=__doc__, add_help=True)
    parser.add_argument("--triage", required=True, help="path to the triage record JSON")
    parser.add_argument("--schema", required=True, help="path to triage-schema.json")
    parser.add_argument(
        "--state-dir",
        default=".objective",
        help="directory for the gate's own bookkeeping (default: .objective)",
    )
    parser.add_argument(
        "--max-frames",
        type=int,
        default=2,
        help="how many malformed records to tolerate before giving up (default: 2)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    # --- things that make this gate UNABLE to run: exit nonzero, print nothing.
    try:
        schema = json.loads(Path(args.schema).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"validate_triage: cannot read schema {args.schema}: {exc}", file=sys.stderr)
        return 2

    state_dir = Path(args.state_dir)
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"validate_triage: cannot create state dir {state_dir}: {exc}", file=sys.stderr)
        return 2

    report_path = state_dir / "triage-report.txt"

    # --- things that make the RECORD bad: exit 0, print a judgement token.
    errors: list[str] = []
    record: Any = None
    triage_path = Path(args.triage)
    try:
        record = json.loads(triage_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"{triage_path}: file not found -- the frame node wrote no triage record")
    except OSError as exc:
        errors.append(f"{triage_path}: unreadable: {exc}")
    except json.JSONDecodeError as exc:
        errors.append(f"{triage_path}: not valid JSON: {exc}")

    if not errors:
        errors.extend(validate_against_schema(record, schema))
    if not errors and isinstance(record, dict):
        errors.extend(check_cross_field_rules(record))

    if errors:
        attempt = bump_frame_counter(state_dir)
        token = "triage_bad" if attempt <= args.max_frames else "triage_exhausted"
        report = [
            "TRIAGE REJECTED",
            f"record:   {triage_path}",
            f"attempt:  {attempt} of {args.max_frames} permitted",
            f"verdict:  {token}",
            "",
            "Findings (fix these and rewrite the record):",
            *(f"  - {e}" for e in errors),
        ]
        report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
        print(token, end="")
        return 0

    assert isinstance(record, dict)
    shape = str(record["shape"])
    evidence_command = str(record["evidence_command"]).strip()

    (state_dir / "shape").write_text(shape + "\n", encoding="utf-8")
    evidence_path = state_dir / "evidence-command"
    pin_path = state_dir / "evidence-command.sha256"
    if shape == "redirect":
        # Nothing for the evidence gate to re-run: the deliverable is the diagnosis.
        evidence_path.unlink(missing_ok=True)
        pin_path.unlink(missing_ok=True)
    else:
        evidence_path.write_text(evidence_command + "\n", encoding="utf-8")
        # Pin the bytes we just published. The evidence gate re-hashes this file
        # before it re-runs the command, so "the check triage admitted" and "the
        # check the parent ran" are the same question.
        pin_path.write_text(
            hashlib.sha256(evidence_path.read_bytes()).hexdigest() + "\n", encoding="utf-8"
        )

    report = [
        "TRIAGE ADMITTED",
        f"record:           {triage_path}",
        f"shape:            {shape}",
        f"evidence_command: {evidence_command}",
        "",
        "Three-question test (docs/PIPELINE_DESIGN_PRINCIPLES.md section 0):",
        f"  cycle:         {record['three_question']['cycle']}",
        f"  evidence_gate: {record['three_question']['evidence_gate']}",
        f"  bad_day:       {record['three_question']['bad_day']}",
        "",
        f"rationale: {record['rationale']}",
    ]
    report_path.write_text("\n".join(report) + "\n", encoding="utf-8")
    print(shape, end="")
    return 0


if __name__ == "__main__":  # pragma: no cover - thin CLI wrapper
    raise SystemExit(main())
