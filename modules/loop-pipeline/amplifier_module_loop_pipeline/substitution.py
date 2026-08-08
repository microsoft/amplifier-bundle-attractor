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

Spec note (§4.5):
    The NLSpec defines ``$goal`` as the only template variable in codergen
    handler prompts.  M5 generalises this to all context keys in
    tool_command strings — a conscious extension beyond the written spec.

Implications for pipeline authors:
    When a tool_command runs under ``set -eu`` bash, any token whose key
    is absent from context survives as a literal string (e.g. the four
    characters ``$foo``).  Bash then treats it as an unbound shell variable
    and exits with "parameter not set" (exit 2).

    The universally-portable defence is shell-default syntax in the bash::

        ${optional_var:-fallback_value}

    This works regardless of invocation path — dot-graph resolver,
    attractor-as-tool in an AmplifierSession, custom resolver, or
    ``attractor run`` CLI.  No upstream abstraction layer needs to be
    relied upon.

Implications for resolver authors:
    Resolvers MAY pre-seed context with declared defaults from their own
    schema before the engine runs (e.g. the dot-graph resolver reads
    ``default:`` fields from resolver.yaml and injects them into context
    at dispatch time).  This is an enhancement on top of the universal
    shell-default baseline, not a replacement for it.

Three-layer default pattern:
    1. Universal floor — shell defaults in bash (works for all consumers).
    2. Bridge enforcement — resolver seeds context with schema-declared
       defaults at dispatch (protects direct API callers that bypass UI).
    3. UI affordance — resolver emits A2UI ``defaultValue`` on schema
       components so users see pre-filled fields without knowing the
       underlying attractor mechanics.

    The engine participates only at layer 1, via the absent-key
    pass-through described above.  Layers 2 and 3 are resolver concerns.
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
    #
    # The negative lookahead's word-character class (A-Za-z0-9_) ensures
    # token-boundary awareness unconditionally: "$name" only matches when the
    # next character is not a valid key-name character, so "$name_suffix" is
    # never corrupted when only "name" is in the snapshot.
    #
    # The lookahead additionally excludes "." — but ONLY for keys that have a
    # longer dotted sibling actually present in the snapshot (e.g. "tool" when
    # "tool.last_line" is also a snapshot key). That protects the case where
    # the longer key's own value happens to be None (so it is skipped by the
    # "val is not None" guard below and never gets its turn to substitute):
    # without this exclusion, substituting "$tool" would partially corrupt the
    # still-literal "$tool.last_line" token into "X.last_line" — a fresh
    # corruption of the same class this function exists to prevent.
    #
    # For any other key, a "." is ordinary text (a filename extension, a
    # sentence-ending period) and must NOT block substitution — that was the
    # regression: "$name.txt" with only "name" defined must become
    # "report.txt", not stay literal, because no "name.*" key exists in the
    # snapshot to disambiguate against.
    keys_with_dotted_sibling = {
        key
        for key in snapshot
        if any(other != key and other.startswith(key + ".") for other in snapshot)
    }

    for key in sorted(snapshot.keys(), key=len, reverse=True):
        val = snapshot.get(key)
        if val is not None and f"${key}" in text:
            _val = str(val)
            boundary_chars = (
                "A-Za-z0-9_." if key in keys_with_dotted_sibling else "A-Za-z0-9_"
            )
            text = re.sub(
                r"\$" + re.escape(key) + r"(?![" + boundary_chars + r"])",
                lambda m: _val,
                text,
            )

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
