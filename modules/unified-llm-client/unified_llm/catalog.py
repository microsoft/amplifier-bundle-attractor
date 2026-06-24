"""Model catalog — advisory model lookup (Spec §2.9).

Unknown model strings pass through. The catalog is not restrictive.

``get_latest_model()`` selects by **release_date** (max, descending) with a
deterministic tiebreak on **model id** (lexicographic, descending).  It raises
``ValueError`` loudly when no candidate matches — never silently returns None.

Loading fails loudly if any catalog entry is missing or has a malformed
``release_date`` field; this ensures stale/incomplete data never silently
degrades "latest" selection.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from unified_llm.types import ModelInfo

_CATALOG: list[ModelInfo] | None = None
_ALIAS_MAP: dict[str, str] | None = None


def _parse_catalog(
    raw: list[dict[str, Any]],
) -> tuple[list[ModelInfo], dict[str, str]]:
    """Parse raw JSON catalog entries into ModelInfo objects.

    Raises:
        ValueError: If any entry is missing ``release_date`` or it cannot be
            parsed as an ISO-8601 date.  The error message names the offending
            model id so problems are easy to locate and fix.
    """
    models: list[ModelInfo] = []
    aliases: dict[str, str] = {}

    for entry in raw:
        model_id: str = entry.get("id", "<unknown>")

        # --- fail-loud on missing release_date ---
        if "release_date" not in entry:
            raise ValueError(
                f"unified_llm catalog: model {model_id!r} is missing required "
                f"'release_date' field.  Add an ISO-8601 date (YYYY-MM-DD) to "
                f"unified_llm/data/models.json."
            )

        raw_date = entry["release_date"]
        try:
            release_date = date.fromisoformat(str(raw_date))
        except (ValueError, TypeError) as exc:
            raise ValueError(
                f"unified_llm catalog: model {model_id!r} has malformed "
                f"'release_date' {raw_date!r} — expected ISO-8601 YYYY-MM-DD: {exc}"
            ) from exc

        model = ModelInfo(
            id=model_id,
            provider=entry["provider"],
            display_name=entry["display_name"],
            context_window=entry["context_window"],
            max_output=entry.get("max_output"),
            supports_tools=entry["supports_tools"],
            supports_vision=entry["supports_vision"],
            supports_reasoning=entry["supports_reasoning"],
            input_cost_per_million=entry.get("input_cost_per_million"),
            output_cost_per_million=entry.get("output_cost_per_million"),
            aliases=entry.get("aliases", []),
            release_date=release_date,
        )
        models.append(model)
        for alias in model.aliases:
            aliases[alias] = model.id

    return models, aliases


def _load_catalog() -> tuple[list[ModelInfo], dict[str, str]]:
    """Load the catalog from the shipped JSON data file (cached after first load)."""
    global _CATALOG, _ALIAS_MAP
    if _CATALOG is not None and _ALIAS_MAP is not None:
        return _CATALOG, _ALIAS_MAP

    data_path = Path(__file__).parent / "data" / "models.json"
    raw: list[dict[str, Any]] = json.loads(data_path.read_text())

    models, aliases = _parse_catalog(raw)

    _CATALOG = models
    _ALIAS_MAP = aliases
    return models, aliases


def get_model_info(model_id: str) -> ModelInfo | None:
    """Look up a model by ID or alias. Returns None if unknown."""
    models, aliases = _load_catalog()
    # Direct ID match
    for model in models:
        if model.id == model_id:
            return model
    # Alias match
    canonical = aliases.get(model_id)
    if canonical:
        for model in models:
            if model.id == canonical:
                return model
    return None


def list_models(provider: str | None = None) -> list[ModelInfo]:
    """List all known models, optionally filtered by provider.

    Returns models sorted by ``(release_date, id)`` descending — newest first,
    ties broken deterministically by id.
    """
    models, _ = _load_catalog()
    if provider is not None:
        models = [m for m in models if m.provider == provider]
    return sorted(models, key=lambda m: (m.release_date, m.id), reverse=True)


def get_latest_model(
    provider: str,
    capability: str | None = None,
) -> ModelInfo:
    """Return the most recently released model for *provider*.

    Selection is by **max ``release_date``** with a deterministic tiebreak on
    **model id** (lexicographic, descending), so equal-dated models always
    resolve to the same one regardless of JSON file order.

    Args:
        provider: Provider name (e.g. ``"anthropic"``, ``"openai"``,
            ``"gemini"``).
        capability: Optional capability filter — one of ``"reasoning"``,
            ``"vision"``, ``"tools"``.

    Returns:
        The newest matching ``ModelInfo``.

    Raises:
        ValueError: When no model matches *provider* (or the combination of
            *provider* + *capability*).  The message names both arguments so
            the caller can diagnose the problem immediately.
    """
    candidates = list_models(provider)

    if capability:
        cap_map = {
            "reasoning": lambda m: m.supports_reasoning,
            "vision": lambda m: m.supports_vision,
            "tools": lambda m: m.supports_tools,
        }
        filter_fn = cap_map.get(capability)
        if filter_fn:
            candidates = [m for m in candidates if filter_fn(m)]

    if not candidates:
        raise ValueError(
            f"unified_llm.get_latest_model: no model found for "
            f"provider={provider!r} capability={capability!r}.  "
            f"Check that the provider name is correct and that models.json "
            f"contains at least one entry for this provider"
            + (f" with {capability!r} support" if capability else "")
            + "."
        )

    # Deterministic selection: max release_date, tiebreak by id (descending).
    # list_models() already returns sorted descending, so candidates[0] is correct
    # — but we use max() with an explicit key to be unambiguous about the contract.
    return max(candidates, key=lambda m: (m.release_date, m.id))
