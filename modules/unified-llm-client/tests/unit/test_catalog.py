"""Tests for unified_llm.catalog — model catalog and lookup functions."""

from datetime import date

import pytest

import unified_llm.catalog as _cat
from unified_llm.catalog import _parse_catalog, get_latest_model, get_model_info, list_models
from unified_llm.types import ModelInfo


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _model(
    id: str,
    provider: str,
    release_date: date,
    *,
    supports_reasoning: bool = False,
    supports_vision: bool = True,
    supports_tools: bool = True,
) -> ModelInfo:
    """Build a minimal ModelInfo for in-memory catalog tests."""
    return ModelInfo(
        id=id,
        provider=provider,
        display_name=f"Test {id}",
        context_window=100_000,
        supports_tools=supports_tools,
        supports_vision=supports_vision,
        supports_reasoning=supports_reasoning,
        release_date=release_date,
    )


def _inject_catalog(monkeypatch, models: list[ModelInfo]) -> None:
    """Replace the module-level catalog cache with *models* for one test."""
    aliases: dict[str, str] = {}
    for m in models:
        for a in m.aliases:
            aliases[a] = m.id
    monkeypatch.setattr(_cat, "_CATALOG", models)
    monkeypatch.setattr(_cat, "_ALIAS_MAP", aliases)


# ---------------------------------------------------------------------------
# Existing test classes (unchanged behaviour, updated for new contract)
# ---------------------------------------------------------------------------


class TestGetModelInfo:
    """Spec §2.9 — get_model_info() lookup."""

    def test_known_model(self) -> None:
        info = get_model_info("claude-sonnet-4-20250514")
        assert info is not None
        assert info.provider == "anthropic"
        assert info.supports_tools is True

    def test_unknown_model_returns_none(self) -> None:
        """Spec: Unknown model strings pass through — catalog is advisory."""
        assert get_model_info("nonexistent-model-xyz") is None

    def test_alias_lookup(self) -> None:
        """Spec: Models have aliases for shorthand."""
        # The catalog should have at least one alias defined
        all_models = list_models()
        models_with_aliases = [m for m in all_models if m.aliases]
        if models_with_aliases:
            model = models_with_aliases[0]
            alias = model.aliases[0]
            result = get_model_info(alias)
            assert result is not None
            assert result.id == model.id


class TestListModels:
    """Spec §2.9 — list_models() with optional provider filter."""

    def test_list_all(self) -> None:
        models = list_models()
        assert len(models) > 0

    def test_filter_by_provider(self) -> None:
        anthropic_models = list_models(provider="anthropic")
        assert len(anthropic_models) > 0
        assert all(m.provider == "anthropic" for m in anthropic_models)

    def test_filter_unknown_provider(self) -> None:
        assert list_models(provider="nonexistent") == []


class TestGetLatestModel:
    """Spec §2.9 — get_latest_model() for each provider."""

    def test_latest_anthropic(self) -> None:
        model = get_latest_model("anthropic")
        assert model.provider == "anthropic"

    def test_latest_openai(self) -> None:
        model = get_latest_model("openai")
        assert model.provider == "openai"

    def test_latest_gemini(self) -> None:
        model = get_latest_model("gemini")
        assert model.provider == "gemini"

    def test_latest_unknown_provider_raises(self) -> None:
        """Unknown provider must raise ValueError, NOT return None."""
        with pytest.raises(ValueError, match="provider="):
            get_latest_model("nonexistent")

    def test_latest_with_capability_filter(self) -> None:
        model = get_latest_model("anthropic", capability="reasoning")
        assert model.supports_reasoning is True


# ---------------------------------------------------------------------------
# New backstop tests — all keyless, exercising recency + hardening
# ---------------------------------------------------------------------------


class TestGetLatestModelRecency:
    """The core red→green proof: recency, not file order."""

    def test_get_latest_model_uses_recency_not_file_order(
        self, monkeypatch
    ) -> None:
        """An older model listed FIRST must NOT beat a newer model listed SECOND.

        This test FAILS against the original candidates[0] implementation and
        PASSES against the release_date-based max() implementation.
        """
        older = _model("model-2023", "testprov", date(2023, 1, 1))
        newer = _model("model-2025", "testprov", date(2025, 6, 1))
        # Deliberate: older appears FIRST in the list (as it would in the old JSON)
        _inject_catalog(monkeypatch, [older, newer])

        result = get_latest_model("testprov")
        assert result.id == "model-2025", (
            f"Expected newer model 'model-2025' but got {result.id!r} — "
            "get_latest_model is using file order instead of release_date"
        )

    def test_list_models_returns_newest_first(self, monkeypatch) -> None:
        """list_models() must return descending by release_date."""
        older = _model("old", "testprov", date(2022, 3, 1))
        newer = _model("new", "testprov", date(2024, 11, 1))
        _inject_catalog(monkeypatch, [older, newer])

        result = list_models(provider="testprov")
        assert result[0].id == "new"
        assert result[1].id == "old"


class TestGetLatestModelTiebreak:
    """Equal release_date resolves deterministically by id."""

    def test_get_latest_model_deterministic_on_tie(self, monkeypatch) -> None:
        """Two models with the same release_date must always yield the same winner.

        Tiebreak rule: lexicographically GREATER id wins (matches key in max()).
        Calling twice must return the identical result regardless of list order.
        """
        same_date = date(2025, 1, 15)
        alpha = _model("zzz-model", "testprov", same_date)
        beta = _model("aaa-model", "testprov", same_date)

        # First ordering
        _inject_catalog(monkeypatch, [alpha, beta])
        result_1 = get_latest_model("testprov")

        # Reversed ordering
        _inject_catalog(monkeypatch, [beta, alpha])
        result_2 = get_latest_model("testprov")

        assert result_1.id == result_2.id, (
            "get_latest_model returned different winners for identical catalogs "
            "in different order — tiebreak is not deterministic"
        )
        # "zzz" > "aaa" lexicographically, so zzz-model must win
        assert result_1.id == "zzz-model", (
            f"Tiebreak rule: greater id wins; expected 'zzz-model', got {result_1.id!r}"
        )


class TestCatalogIntegrity:
    """The real catalog must satisfy structural invariants."""

    def test_every_catalog_model_has_valid_release_date(self) -> None:
        """Every entry in the real models.json must have a valid date object."""
        models = list_models()
        assert models, "Catalog is empty — models.json missing?"
        for m in models:
            assert isinstance(m.release_date, date), (
                f"Model {m.id!r}: release_date is {type(m.release_date)!r}, "
                f"expected datetime.date"
            )
            # Sanity bound: no model exists before 2020 or in the far future
            assert m.release_date >= date(2020, 1, 1), (
                f"Model {m.id!r}: release_date {m.release_date} looks wrong "
                f"(earlier than 2020-01-01)"
            )
            assert m.release_date <= date(2030, 1, 1), (
                f"Model {m.id!r}: release_date {m.release_date} looks wrong "
                f"(later than 2030-01-01)"
            )


class TestGetLatestModelFailLoud:
    """get_latest_model must raise, never return None or raise bare max() error."""

    def test_get_latest_model_empty_provider_raises(self, monkeypatch) -> None:
        """Unknown provider → legible ValueError mentioning provider=."""
        _inject_catalog(monkeypatch, [])  # completely empty catalog
        with pytest.raises(ValueError) as exc_info:
            get_latest_model("ghost-provider")
        msg = str(exc_info.value)
        assert "provider=" in msg, (
            f"Error message must mention provider=, got: {msg!r}"
        )
        assert "ghost-provider" in msg, (
            f"Error message must include the provider name, got: {msg!r}"
        )

    def test_get_latest_model_unknown_provider_from_real_catalog_raises(
        self,
    ) -> None:
        """Real catalog + unknown provider → legible ValueError."""
        with pytest.raises(ValueError) as exc_info:
            get_latest_model("definitely-not-a-real-provider")
        msg = str(exc_info.value)
        assert "provider=" in msg

    def test_get_latest_model_impossible_capability_raises(
        self, monkeypatch
    ) -> None:
        """Valid provider + capability that no model satisfies → legible ValueError."""
        no_reasoning = _model("plain-model", "testprov", date(2025, 1, 1), supports_reasoning=False)
        _inject_catalog(monkeypatch, [no_reasoning])
        with pytest.raises(ValueError) as exc_info:
            get_latest_model("testprov", capability="reasoning")
        msg = str(exc_info.value)
        assert "provider=" in msg
        assert "capability=" in msg


class TestParseFailLoud:
    """_parse_catalog raises loudly on bad input, naming the offending id."""

    def test_missing_release_date_fails_loud(self) -> None:
        """An entry without release_date must raise ValueError naming the model id."""
        raw = [
            {
                "id": "bad-model-no-date",
                "provider": "testprov",
                "display_name": "Bad",
                "context_window": 100000,
                "supports_tools": True,
                "supports_vision": False,
                "supports_reasoning": False,
                # deliberately omitted: "release_date"
            }
        ]
        with pytest.raises(ValueError) as exc_info:
            _parse_catalog(raw)
        msg = str(exc_info.value)
        assert "bad-model-no-date" in msg, (
            f"Error must name the offending model id; got: {msg!r}"
        )
        assert "release_date" in msg, (
            f"Error must mention 'release_date'; got: {msg!r}"
        )

    def test_malformed_release_date_fails_loud(self) -> None:
        """An entry with an unparseable release_date must raise ValueError."""
        raw = [
            {
                "id": "bad-date-model",
                "provider": "testprov",
                "display_name": "Bad Date",
                "context_window": 100000,
                "supports_tools": True,
                "supports_vision": False,
                "supports_reasoning": False,
                "release_date": "not-a-date",
            }
        ]
        with pytest.raises(ValueError) as exc_info:
            _parse_catalog(raw)
        msg = str(exc_info.value)
        assert "bad-date-model" in msg
