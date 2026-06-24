"""Live-backed model resolver for unified-llm-client.

Provides ``select_latest`` (source-agnostic selector) and ``resolve_latest``
(select against an adapter's own live list, eliminating the id-seam).

The version-sort approach — numeric/date-aware tokenization, uniform triple
representation to prevent int/str TypeError — is adapted from the
amplifier-bundle-routing-matrix ``resolver.py``.  Credit to that work for
proving the correct ordering across the claude-3.x → claude-sonnet-4 rename
and for the fnmatch-based family matching pattern.

# TODO: get_latest_model() live-backing is a thin future follow-up —
# replace the static models.json lookup in catalog.py with
# resolve_latest_for(provider, family_glob).
"""

from __future__ import annotations

import fnmatch
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Non-stable marker tokens
# ---------------------------------------------------------------------------
# A model id containing any of these substrings is considered NON-stable.
#
# IMPORTANT: a bare date embedded in an id (e.g. "gpt-4o-2024-08-06" or
# "claude-sonnet-4-20250514") is STABLE.  Dates are stripped by _version_key
# and used only as a low-priority tiebreak — they are NOT listed here.
#
# "-latest" is included because it denotes a rolling alias that tracks HEAD,
# not a specific stable release.
# ---------------------------------------------------------------------------

_NON_STABLE_MARKERS: frozenset[str] = frozenset(
    {
        "preview",
        "experimental",
        "exp",
        "beta",
        "alpha",
        "rc",
        "snapshot",
        "canary",
        "nightly",
        "-latest",
    }
)

# ---------------------------------------------------------------------------
# Date-suffix regexes
# ---------------------------------------------------------------------------
# Two formats are supported:
#   -YYYYMMDD      (e.g. "claude-sonnet-4-20250514", "claude-3-5-sonnet-20241022")
#   -YYYY-MM-DD    (e.g. "gpt-4o-2024-08-06")
#
# These are applied by _version_key to strip the date before tokenizing the
# structural part of the model id, so that version ordering is driven by the
# *name* tokens (which model family/generation) and the date is only a
# low-priority tiebreak.
#
# The ISO pattern (-YYYY-MM-DD) is tried first because "-2024-08-06" would
# trivially not match the 8-digit pattern (it has hyphens between segments).
# ---------------------------------------------------------------------------

_DATE_ISO_RE = re.compile(r"-(\d{4})-(\d{2})-(\d{2})$")  # -YYYY-MM-DD
_DATE_8_RE = re.compile(r"-(\d{4})(\d{2})(\d{2})$")  # -YYYYMMDD

# Splits a string into alternating text/digit runs
_DIGIT_SPLIT_RE = re.compile(r"(\d+)")


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def _is_glob(s: str) -> bool:
    """Return True if *s* contains shell-glob special characters (``* ? [``)."""
    return bool(set(s) & {"*", "?", "["})


def _version_key(model_id: str) -> tuple[Any, ...]:
    """Return a sortable key for *model_id*.

    Properties guaranteed by this implementation:

    **TOTAL** — never raises ``TypeError: '<' not supported between 'int' and 'str'``
    on any mix of model-id shapes (e.g. ``gpt-4o``, ``gpt-4o-2024-08-06``,
    ``claude-3-5-sonnet-20241022``, ``claude-sonnet-4-20250514``).

    **Date-aware tiebreak** — trailing ``-YYYYMMDD`` or ``-YYYY-MM-DD`` suffixes
    are stripped and stored as a low-priority ``(year, month, day)`` tiebreak,
    so models in the *same family* are sorted by date when their structural
    version tokens are identical.  Ids without a date suffix have ``date_key=()``
    which sorts *lower* than any real date (empty tuple < any non-empty tuple
    in Python).

    **Uniform triples** — each remaining token is mapped to a 3-tuple of uniform
    type so positions never compare int to str:
      - digit run → ``(1, int(run), "")``
      - text  run → ``(0, 0, run.lower())``

    Returned key layout (for ``max()`` — larger = newer):
      ``(tuple_of_triples, date_key_tuple, full_model_id)``

    Examples of ordering (ascending, so newer is LAST here):

    >>> sorted([
    ...     "claude-3-5-sonnet-20241022",
    ...     "claude-sonnet-4-20250514",
    ... ], key=_version_key)
    ['claude-3-5-sonnet-20241022', 'claude-sonnet-4-20250514']

    >>> sorted([
    ...     "gpt-4o-2024-05-13",
    ...     "gpt-4o-2024-08-06",
    ...     "gpt-4o",
    ... ], key=_version_key)
    ['gpt-4o', 'gpt-4o-2024-05-13', 'gpt-4o-2024-08-06']
    """
    lo = model_id.lower()

    # --- strip trailing date suffix (ISO preferred; fall back to 8-digit) ---
    date_key: tuple[int, ...] = ()  # empty < any real date when used in max()
    m = _DATE_ISO_RE.search(lo)
    if m:
        date_key = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        lo = lo[: m.start()]
    else:
        m2 = _DATE_8_RE.search(lo)
        if m2:
            date_key = (int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
            lo = lo[: m2.start()]

    # --- tokenize remaining string into alternating text / digit runs ---
    parts = _DIGIT_SPLIT_RE.split(lo)
    triples: list[tuple[int, int, str]] = []
    for i, part in enumerate(parts):
        if not part:
            continue
        if i % 2 == 1:
            # Odd-index: captured digit group
            triples.append((1, int(part), ""))
        else:
            # Even-index: text run
            triples.append((0, 0, part))

    return (tuple(triples), date_key, model_id)


def _is_non_stable(model_id: str) -> bool:
    """Return True if *model_id* contains any non-stable marker token."""
    lo = model_id.lower()
    for marker in _NON_STABLE_MARKERS:
        if marker in lo:
            return True
    return False


# ---------------------------------------------------------------------------
# Core selector — source-agnostic (Part A)
# ---------------------------------------------------------------------------


def select_latest(
    model_ids: list[str],
    pattern: str,
    *,
    stable_only: bool = True,
) -> str:
    """Select the latest model from *model_ids* matching *pattern*.

    This is a **pure function** (no I/O, stdlib-only).  It can be composed
    with any source of model ids — a live API list, a static catalog, a test
    fixture.

    Args:
        model_ids: List of candidate model id strings.
        pattern: Either an exact model id (membership check, case-sensitive)
            or a shell-glob (``fnmatch``, both sides lowercased).  Glob
            characters are ``*``, ``?``, ``[``.
        stable_only: When ``True`` (default), model ids whose lowercased form
            contains any non-stable marker token are excluded before picking
            the latest.  See ``_NON_STABLE_MARKERS`` for the full list.

    Returns:
        The latest matching model id (as it appears in *model_ids*).

    Raises:
        ValueError: If no model ids match *pattern*, or if *stable_only=True*
            and all matching ids are non-stable.  The message names *pattern*
            and (for the stable-filter case) states ``stable_only=True`` so
            callers can diagnose the problem immediately.
    """
    # --- candidate selection ---
    if _is_glob(pattern):
        lo_pattern = pattern.lower()
        candidates = [m for m in model_ids if fnmatch.fnmatch(m.lower(), lo_pattern)]
    else:
        candidates = [m for m in model_ids if m == pattern]

    if not candidates:
        raise ValueError(
            f"select_latest: no model ids match pattern {pattern!r}. "
            f"Checked {len(model_ids)} model id(s).  "
            f"Verify the pattern and ensure the model list is not empty."
        )

    # --- stable filter ---
    if stable_only:
        stable_candidates = [m for m in candidates if not _is_non_stable(m)]
        if not stable_candidates:
            raise ValueError(
                f"select_latest: pattern {pattern!r} matched {len(candidates)} "
                f"model id(s) but all were filtered as non-stable "
                f"(stable_only=True).  "
                f"Matched ids: {candidates}.  "
                f"Set stable_only=False to include preview/experimental models."
            )
        candidates = stable_candidates

    # --- pick latest ---
    return max(candidates, key=_version_key)


# ---------------------------------------------------------------------------
# Adapter-coupled resolver — eliminates the id-seam (Part C)
# ---------------------------------------------------------------------------


async def resolve_latest(
    adapter: Any,
    pattern: str,
    *,
    stable_only: bool = True,
) -> str:
    """Resolve the latest model matching *pattern* from *adapter*'s own live list.

    The adapter's ``list_models()`` and ``complete()``/``stream()`` use the
    **same SDK client**, so the returned id is guaranteed to be in the same
    id namespace as what the adapter uses for generation.  This eliminates the
    *id-seam* — the bug where a lister from one source returns different id
    shapes than a generator from another (e.g. list returns ``gpt-4o-2024-08-06``
    but generation expects ``gpt-4o``, causing a silent 404).

    Args:
        adapter: A provider adapter instance with an async ``list_models()``
            method that returns ``list[str]``.
        pattern: Exact model id or shell-glob (e.g. ``"*sonnet*"``,
            ``"claude-opus-4-*"``).
        stable_only: Exclude non-stable variants (preview, experimental, …)
            when ``True`` (default).

    Returns:
        Latest stable (or any, if *stable_only=False*) model id matching
        *pattern* from the adapter's live list.

    Raises:
        ValueError: If no model matches, or all matches are filtered.
        AttributeError: If *adapter* does not have ``list_models()``.
    """
    model_ids: list[str] = await adapter.list_models()
    return select_latest(model_ids, pattern, stable_only=stable_only)


async def resolve_latest_for(
    provider: str,
    pattern: str,
    *,
    stable_only: bool = True,
) -> str:
    """Convenience: build adapter from environment and resolve latest model.

    Uses ``Client.from_env()`` — the same factory used for generation — to
    obtain the adapter for *provider*, then calls ``resolve_latest``.  Because
    the lister and generator share the same adapter instance, ids are
    generation-compatible by construction.

    Args:
        provider: Provider name matching an env-key-detected adapter, e.g.
            ``"anthropic"``, ``"openai"``, ``"gemini"``.
        pattern: Exact model id or shell-glob.
        stable_only: Exclude non-stable variants when ``True`` (default).

    Returns:
        Latest stable model id matching *pattern* for *provider*.

    Raises:
        ValueError: If the provider has no adapter (API key absent) or no
            model matches the pattern.
        ConfigurationError: If no API keys are found at all.
    """
    from unified_llm.client import Client

    client = Client.from_env()
    adapter = client.providers.get(provider)
    if adapter is None:
        available = list(client.providers.keys())
        raise ValueError(
            f"resolve_latest_for: no adapter found for provider {provider!r}.  "
            f"Providers available (check that API key is set): {available}"
        )
    return await resolve_latest(adapter, pattern, stable_only=stable_only)
