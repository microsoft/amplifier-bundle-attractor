"""Condition expression language for edge routing.

Minimal boolean expression evaluator for edge conditions.
Supports = (equals), != (not equals), and && (AND conjunction).

Spec coverage: Section 10 (Condition Expression Language), CEXPR-001–011
"""

from __future__ import annotations

from .context import PipelineContext
from .outcome import Outcome


def evaluate_condition(
    condition: str,
    outcome: Outcome,
    context: PipelineContext,
) -> bool:
    """Evaluate a condition expression against outcome and context.

    Returns True if the condition passes (edge is eligible).
    An empty condition always returns True.

    Spec Section 10.5: Evaluation algorithm.
    """
    if not condition or not condition.strip():
        return True

    # Split on '&&' — the ONLY conjunction operator (spec §10, §10.7).
    # Comma is NOT an AND separator; a value containing a comma is compared
    # literally as a single clause (e.g. context.x=a,b matches the value "a,b").
    raw_clauses: list[str] = []
    for piece in condition.split("&&"):
        piece = piece.strip()
        if piece:
            raw_clauses.append(piece)

    for clause in raw_clauses:
        if not _evaluate_clause(clause, outcome, context):
            return False
    return True


def _evaluate_clause(
    clause: str,
    outcome: Outcome,
    context: PipelineContext,
) -> bool:
    """Evaluate a single Key Operator Value clause.

    Spec Section 10.5: evaluate_clause algorithm.
    """
    # Check for != before = (since = is a substring of !=)
    if "!=" in clause:
        key, value = clause.split("!=", maxsplit=1)
        return _resolve_key(key.strip(), outcome, context) != value.strip()
    elif "=" in clause:
        key, value = clause.split("=", maxsplit=1)
        return _resolve_key(key.strip(), outcome, context) == value.strip()
    else:
        # Bare key: check if truthy
        return bool(_resolve_key(clause.strip(), outcome, context))


def _resolve_key(
    key: str,
    outcome: Outcome,
    context: PipelineContext,
) -> str:
    """Resolve a key to its string value.

    Spec Section 10.4: Variable Resolution.
    """
    if key == "outcome":
        # preferred_label carries custom outcome values from the agent
        # (e.g., "yes", "process", "done") set via report_outcome tool.
        # Fall back to the status enum value for standard routing.
        return outcome.preferred_label or outcome.status.value

    if key == "preferred_label":
        return outcome.preferred_label or ""

    if key.startswith("context."):
        # Try with full key first
        value = context.get(key)
        if value is not None:
            return str(value)
        # Try without "context." prefix
        short_key = key[len("context.") :]
        value = context.get(short_key)
        if value is not None:
            return str(value)
        return ""

    # Direct context lookup for unqualified keys
    value = context.get(key)
    if value is not None:
        return str(value)
    return ""
