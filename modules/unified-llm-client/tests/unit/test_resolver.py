"""Gate tests for unified_llm/resolver.py.

These 4 tests encode tester-breaker's bug inputs.  They FAIL before
``unified_llm/resolver.py`` exists; they all PASS once the selector is
implemented correctly.

Run: uv run pytest tests/unit/test_resolver.py -v
"""

from __future__ import annotations

import pytest

from unified_llm.resolver import _is_glob, _version_key, select_latest


# ---------------------------------------------------------------------------
# Gate #2 — OpenAI mixed date shapes
# ---------------------------------------------------------------------------


class TestGate2OpenAIDateShapes:
    """Mixed dated/undated/gpt-4-0613 model ids do not crash; newest dated wins."""

    def test_picks_newest_dated_version(self) -> None:
        """gpt-4o-2024-08-06 wins because it has the latest date suffix.

        Key semantics documented here so this never drifts silently:
        - Candidates after fnmatch("gpt-4o*"): gpt-4o-2024-05-13,
          gpt-4o-2024-08-06, gpt-4o  (gpt-4-0613 is filtered out by glob)
        - _version_key strips the date suffix and stores it as a low-priority
          tiebreak AFTER the structural version tokens.
        - All three have identical structural tokens for 'gpt-4o'.
        - date tiebreak: (2024,8,6) > (2024,5,13) > () (no-date = empty tuple,
          which sorts LOWER than any non-empty tuple in Python).
        - Winner: gpt-4o-2024-08-06.
        """
        result = select_latest(
            ["gpt-4o-2024-05-13", "gpt-4o-2024-08-06", "gpt-4o", "gpt-4-0613"],
            "gpt-4o*",
        )
        assert result == "gpt-4o-2024-08-06"

    def test_no_crash_on_mixed_shapes(self) -> None:
        """All id shapes are handled without TypeError."""
        # Just calling the function is the assertion; any exception = fail.
        result = select_latest(
            ["gpt-4o-2024-05-13", "gpt-4o-2024-08-06", "gpt-4o", "gpt-4-0613"],
            "gpt-4o*",
        )
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Gate #3 — Preview exclusion
# ---------------------------------------------------------------------------


class TestGate3PreviewExclusion:
    """stable_only=True excludes preview ids; stable_only=False includes them."""

    def test_stable_only_excludes_preview(self) -> None:
        """Preview id is filtered when stable_only=True."""
        result = select_latest(
            ["claude-opus-4-8", "claude-opus-4-9-preview"],
            "*opus*",
            stable_only=True,
        )
        assert result == "claude-opus-4-8"

    def test_stable_false_includes_preview(self) -> None:
        """Preview id survives and wins when stable_only=False.

        claude-opus-4-9 > claude-opus-4-8 in structural version sort (9 > 8),
        even though the id has the '-preview' suffix.
        """
        result = select_latest(
            ["claude-opus-4-8", "claude-opus-4-9-preview"],
            "*opus*",
            stable_only=False,
        )
        assert result == "claude-opus-4-9-preview"


# ---------------------------------------------------------------------------
# Gate #4 — Tokenizer totality: 3.x → 4 rename does not raise TypeError
# ---------------------------------------------------------------------------


class TestGate4TokenizerTotality:
    """_version_key is total — never raises TypeError on any mixed input."""

    def test_sorted_does_not_raise(self) -> None:
        """sorted() over mixed claude-3.x / claude-sonnet-4 shapes must not raise."""
        mixed = ["claude-3-5-sonnet-20241022", "claude-sonnet-4-20250514"]
        # This line is the gate: TypeError here means int/str comparison in key.
        sorted_result = sorted(mixed, key=_version_key)
        assert isinstance(sorted_result, list)
        assert len(sorted_result) == 2

    def test_select_latest_picks_sonnet4(self) -> None:
        """claude-sonnet-4-20250514 wins over claude-3-5-sonnet-20241022.

        Version key explanation:
        - claude-3-5-sonnet-20241022: strip -20241022, tokens for 'claude-3-5-sonnet'.
          First triple: (0, 0, 'claude-').
        - claude-sonnet-4-20250514: strip -20250514, tokens for 'claude-sonnet-4'.
          First triple: (0, 0, 'claude-sonnet-').
        - 'claude-sonnet-' > 'claude-' (longer prefix, 'sonnet' chars follow '-').
        - Therefore claude-sonnet-4-20250514 has the larger key → wins.
        """
        result = select_latest(
            ["claude-3-5-sonnet-20241022", "claude-sonnet-4-20250514"],
            "*sonnet*",
        )
        assert result == "claude-sonnet-4-20250514"


# ---------------------------------------------------------------------------
# Gate: empty/raise — unmatched pattern and all-filtered each raise ValueError
# with a message that mentions the pattern.
# ---------------------------------------------------------------------------


class TestEmptyRaisesLegibleError:
    """select_latest raises ValueError (mentioning the pattern) on empty results."""

    def test_unmatched_pattern_raises(self) -> None:
        """No candidates after glob filter → ValueError naming the pattern."""
        with pytest.raises(ValueError, match="does-not-exist-pattern"):
            select_latest(["gpt-4o", "gpt-4-turbo"], "does-not-exist-pattern*")

    def test_all_filtered_stable_raises(self) -> None:
        """All candidates are non-stable → ValueError with mention of stable_only."""
        with pytest.raises(ValueError, match="stable_only"):
            select_latest(
                ["claude-opus-4-9-preview"],
                "*opus*",
                stable_only=True,
            )

    def test_error_mentions_pattern_on_filter(self) -> None:
        """ValueError from stable filter must mention the pattern string."""
        pattern = "*experimental-model*"
        with pytest.raises(ValueError) as exc_info:
            select_latest(
                ["model-experimental-v1", "model-experimental-v2"],
                pattern,
                stable_only=True,
            )
        assert "experimental-model" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Structural unit tests for the helper functions
# ---------------------------------------------------------------------------


class TestIsGlob:
    def test_star(self) -> None:
        assert _is_glob("*opus*") is True

    def test_question(self) -> None:
        assert _is_glob("claude-?") is True

    def test_bracket(self) -> None:
        assert _is_glob("gpt-[34]") is True

    def test_plain_id(self) -> None:
        assert _is_glob("claude-sonnet-4-20250514") is False

    def test_empty(self) -> None:
        assert _is_glob("") is False


class TestExactIdMatch:
    """When pattern is not a glob, exact id membership is used."""

    def test_exact_match(self) -> None:
        result = select_latest(
            ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
            "gpt-4o",
        )
        assert result == "gpt-4o"

    def test_exact_no_match_raises(self) -> None:
        with pytest.raises(ValueError):
            select_latest(["gpt-4o", "gpt-4-turbo"], "gpt-4o-mini")


# ---------------------------------------------------------------------------
# Part C — id-seam structural test (resolve_latest)
# ---------------------------------------------------------------------------


class TestIdSeamStructural:
    """resolve_latest must return an id that is in the adapter's own list_models().

    This encodes the 'lister == generator' invariant without any network call.
    """

    @pytest.mark.asyncio
    async def test_resolve_latest_id_in_lister_namespace(self) -> None:
        """Resolved id is always a member of the adapter's own list_models() output."""
        from unified_llm.resolver import resolve_latest

        known_ids = [
            "claude-opus-4-5",
            "claude-opus-4-8",
            "claude-opus-4-9-preview",
        ]

        class FakeAdapter:
            async def list_models(self) -> list[str]:
                return list(known_ids)

        fake = FakeAdapter()
        resolved = await resolve_latest(fake, "*opus*", stable_only=True)

        # The resolved id MUST be a member of the lister's own namespace
        assert resolved in known_ids, (
            f"resolve_latest returned {resolved!r} which is not in the adapter's "
            f"list_models() output {known_ids!r} — id-seam violation"
        )

    @pytest.mark.asyncio
    async def test_resolve_latest_stable_excludes_preview(self) -> None:
        """Resolved id under stable_only=True is never a preview/experimental."""
        from unified_llm.resolver import resolve_latest

        class FakeAdapter:
            async def list_models(self) -> list[str]:
                return [
                    "claude-opus-4-5",
                    "claude-opus-4-8",
                    "claude-opus-4-9-preview",
                ]

        resolved = await resolve_latest(FakeAdapter(), "*opus*", stable_only=True)
        assert "preview" not in resolved.lower(), (
            f"resolve_latest(stable_only=True) resolved to non-stable: {resolved!r}"
        )
        assert resolved == "claude-opus-4-8"
