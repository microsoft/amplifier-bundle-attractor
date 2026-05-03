"""Unified context substitution for pipeline attribute strings.

M5: Single substitution policy shared by tool, human, and transforms handlers.

Resolution table:
  ${key}  (brace form)  — any chars including dots; present → value, absent → literal
  $key    (bare form)   — any key including dotted; present → value, absent → literal
  $$                    — escape sequence → literal "$"

Both forms accept dotted keys (e.g. ${tool.output}, $tool.output).
Absent keys leave the token unchanged (literal pass-through).  No exceptions
are raised on missing keys; failures are caught by the engine's eager scan
(M2) before handlers run.

Key ordering: longest keys replaced first to avoid partial matches when a
shorter key is a prefix of a longer one (e.g. "tool" vs "tool.output").
"""

from __future__ import annotations

import re
from collections.abc import Mapping


def substitute_context(text: str, snapshot: Mapping[str, object]) -> str:
    """Replace ``$key`` and ``${key}`` tokens in *text* with context values.

    Args:
        text:     The template string containing ``$var`` or ``${var}`` tokens.
        snapshot: Context snapshot mapping key → value.  Only non-None values
                  are substituted; None-valued keys are treated as absent.

    Returns:
        Text with all resolvable tokens replaced.  Tokens whose key is not
        in the snapshot (or whose value is None) are left unchanged.

    Examples:
        >>> substitute_context("hello ${name}", {"name": "world"})
        'hello world'
        >>> substitute_context("${tool.output}", {"tool.output": "ok"})
        'ok'
        >>> substitute_context("$tool.output", {"tool.output": "ok"})
        'ok'
        >>> substitute_context("missing ${x}", {})
        'missing ${x}'
        >>> substitute_context("literal $$", {})
        'literal $'
    """
    if not text or "$" not in text:
        return text

    # Phase 1: ${key} form — brace-delimited, unambiguous for dotted keys.
    def _replace_braced(m: re.Match) -> str:  # type: ignore[type-arg]
        key = m.group(1)
        val = snapshot.get(key)
        return str(val) if val is not None else m.group(0)

    text = re.sub(r"\$\{([^}]+)\}", _replace_braced, text)

    # Phase 2: $key form — replace longest keys first to avoid partial matches.
    # e.g. replace "$tool.output" before "$tool" so the longer token wins.
    for key in sorted(snapshot.keys(), key=len, reverse=True):
        val = snapshot.get(key)
        if val is not None and f"${key}" in text:
            text = text.replace(f"${key}", str(val))

    # Phase 3: $$ escape → literal $.
    text = text.replace("$$", "$")

    return text


def extract_refs(text: str) -> set[str]:
    """Extract all ``${key}`` and ``$key`` reference tokens from *text*.

    Used by the engine's eager pre-execution scan (M2) to determine which
    context keys a node depends on before its handler is invoked.

    Args:
        text: Attribute string to scan for token references.

    Returns:
        Set of key names referenced by the text (without $ or braces).

    Examples:
        >>> sorted(extract_refs("curl ${server.url}/path?token=$api.key"))
        ['api.key', 'server.url']
        >>> extract_refs("no refs here")
        set()
        >>> extract_refs("${x} and $y")
        {'x', 'y'}
    """
    if not text or "$" not in text:
        return set()

    refs: set[str] = set()

    # ${key} form — capture everything inside braces.
    for m in re.finditer(r"\$\{([^}]+)\}", text):
        refs.add(m.group(1))

    # $key form — token ends at non-word/non-dot character boundary.
    # Pattern: $ followed by word chars and dots (key separator).
    # Excludes braced tokens already captured above.
    remaining = re.sub(r"\$\{[^}]+\}", "", text)  # remove ${} tokens first
    for m in re.finditer(r"\$([A-Za-z_][A-Za-z0-9_.]*)", remaining):
        refs.add(m.group(1))

    return refs
