"""Condition expression language for edge routing.

Minimal boolean expression evaluator for edge conditions.
Supports = (equals), != (not equals), and && (AND conjunction).

Spec coverage: Section 10 (Condition Expression Language), CEXPR-001–011
"""

from __future__ import annotations

from .context import PipelineContext
from .outcome import Outcome


def parse_condition(condition: str) -> list[tuple[str, str, str]]:
    """Parse a condition expression into ``(key, op, value)`` clause triples.

    This is the single grammar entry point for the condition expression
    language.  Both the runtime evaluator (``evaluate_condition``) and the
    static lint rules (``validation.py`` TOPO-001..005) consume it, so the
    grammar cannot drift between engine routing and lint analysis.

    The grammar (spec Section 10): one or more clauses joined by ``&&``
    (the ONLY conjunction operator — comma is NOT an AND separator; a value
    containing a comma is compared literally as a single clause, e.g.
    ``context.x=a,b`` matches the value ``"a,b"``).  Each clause is one of:

      - ``key!=value``  — inequality check  → ``(key, "!=", value)``
      - ``key=value``   — equality check    → ``(key, "=", value)``
      - ``key``         — truthiness check  → ``(key, "truthy", "")``

    ``!=`` is checked before ``=`` (since ``=`` is a substring of ``!=``).
    Whitespace around ``&&`` and around operators is stripped.  Clauses that
    are empty after stripping are silently skipped.  An empty condition
    yields an empty clause list (which evaluates as always-true).
    """
    clauses: list[tuple[str, str, str]] = []
    if not condition:
        return clauses
    for piece in condition.split("&&"):
        clause = piece.strip()
        if not clause:
            continue
        if "!=" in clause:
            key, value = clause.split("!=", maxsplit=1)
            clauses.append((key.strip(), "!=", value.strip()))
        elif "=" in clause:
            key, value = clause.split("=", maxsplit=1)
            clauses.append((key.strip(), "=", value.strip()))
        else:
            # Bare key: truthiness check
            clauses.append((clause, "truthy", ""))
    return clauses


def evaluate_condition(
    condition: str,
    outcome: Outcome,
    context: PipelineContext,
) -> bool:
    """Evaluate a condition expression against outcome and context.

    Returns True if the condition passes (edge is eligible).
    An empty condition always returns True.

    Spec Section 10.5: Evaluation algorithm.  Parsing is delegated to
    ``parse_condition`` (the shared grammar entry point).
    """
    if not condition or not condition.strip():
        return True

    for key, op, value in parse_condition(condition):
        if not _evaluate_clause(key, op, value, outcome, context):
            return False
    return True


def _evaluate_clause(
    key: str,
    op: str,
    value: str,
    outcome: Outcome,
    context: PipelineContext,
) -> bool:
    """Evaluate a single parsed ``(key, op, value)`` clause.

    Spec Section 10.5: evaluate_clause algorithm.
    """
    resolved = _resolve_key(key, outcome, context)
    if op == "!=":
        return resolved != value
    if op == "=":
        return resolved == value
    # "truthy": bare key — check if truthy
    return bool(resolved)


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
