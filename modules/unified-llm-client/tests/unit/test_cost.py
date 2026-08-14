"""Tests for unified_llm._cost — per-call USD cost computation and injection.

Covers the three public cost surfaces:
  * ``compute_cost()`` — the calculator itself.
  * ``Usage.cost_usd`` + ``Usage.__add__`` — None-propagating aggregation.
  * ``Client.complete()`` / ``StreamAccumulator.response()`` — injection of a
    computed cost when the adapter did not supply one.

Expected values are derived programmatically from the module's own ``_RATES``
table (never hard-coded from an external source), so a rate correction updates
test and code together while the *shape* of the arithmetic stays pinned.
"""

import asyncio
from collections.abc import AsyncIterator
from decimal import Decimal

from unified_llm import compute_cost
from unified_llm._cost import _FAST_ELIGIBLE_MODELS, _PER_M, _RATES
from unified_llm.catalog import get_model_info
from unified_llm.client import Client
from unified_llm.types import (
    FinishReason,
    Message,
    Request,
    Response,
    StreamAccumulator,
    StreamEvent,
    StreamEventType,
    Usage,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_KNOWN_MODEL = "claude-sonnet-4-5"
_UNKNOWN_MODEL = "definitely-not-a-real-model-xyz"
# Catalog fixture carrying null cost dimensions.
_NULL_RATE_MODEL = "preview-model-cost-unknown"


def _expected(
    model: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    *,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Decimal:
    """Expected cost, derived term-by-term from the module's own rate table.

    Mirrors the documented arithmetic: each token class is priced per million
    tokens, i.e. ``tokens * rate_per_million / 1_000_000``, then summed.
    """
    rates = _RATES[model]
    total = Decimal(input_tokens) * rates["input_per_m"] / _PER_M
    total += Decimal(output_tokens) * rates["output_per_m"] / _PER_M
    if cache_read_tokens:
        total += Decimal(cache_read_tokens) * rates["cache_read_per_m"] / _PER_M
    if cache_write_tokens:
        total += Decimal(cache_write_tokens) * rates["cache_write_per_m"] / _PER_M
    return total


def _first_fast_eligible() -> str:
    """A deterministically-chosen model from the fast-eligible set."""
    return min(_FAST_ELIGIBLE_MODELS)


def _first_fast_ineligible() -> str:
    """A priced model that is deliberately NOT fast-eligible."""
    return min(set(_RATES) - _FAST_ELIGIBLE_MODELS)


# ---------------------------------------------------------------------------
# compute_cost — known models
# ---------------------------------------------------------------------------


class TestComputeCostKnownModel:
    """compute_cost() prices a model present in the rate table."""

    def test_known_model_exact_decimal(self) -> None:
        """1.2M input + 300k output priced at the table's per-million rates.

        For claude-sonnet-4-5 ($3.00 in / $15.00 out per 1M tokens) that is
        1_200_000 * 3.00 / 1e6 + 300_000 * 15.00 / 1e6 = 3.60 + 4.50 = 8.10.
        """
        result = compute_cost(_KNOWN_MODEL, 1_200_000, 300_000)
        assert result == _expected(_KNOWN_MODEL, 1_200_000, 300_000)
        assert result == Decimal("8.10")

    def test_result_is_decimal_never_float(self) -> None:
        """Cost is exact Decimal money, never a lossy float."""
        result = compute_cost(_KNOWN_MODEL, 1_000, 500)
        assert isinstance(result, Decimal)
        assert not isinstance(result, float)

    def test_input_and_output_priced_at_different_rates(self) -> None:
        """Output tokens are not silently priced at the input rate."""
        rates = _RATES[_KNOWN_MODEL]
        assert rates["output_per_m"] != rates["input_per_m"], (
            "fixture model must have distinct in/out rates for this test to bite"
        )
        input_only = compute_cost(_KNOWN_MODEL, 100_000, 0)
        output_only = compute_cost(_KNOWN_MODEL, 0, 100_000)
        assert input_only != output_only
        assert input_only == _expected(_KNOWN_MODEL, 100_000, 0)
        assert output_only == _expected(_KNOWN_MODEL, 0, 100_000)

    def test_cache_read_tokens_priced_separately(self) -> None:
        """cache_read_tokens add their own (cheaper) line item."""
        base = compute_cost(_KNOWN_MODEL, 10_000, 1_000)
        with_cache = compute_cost(_KNOWN_MODEL, 10_000, 1_000, cache_read_tokens=50_000)
        assert with_cache == _expected(
            _KNOWN_MODEL, 10_000, 1_000, cache_read_tokens=50_000
        )
        assert base is not None and with_cache is not None
        assert with_cache > base

    def test_cache_write_tokens_priced_separately(self) -> None:
        """cache_write_tokens add their own (premium) line item."""
        base = compute_cost(_KNOWN_MODEL, 10_000, 1_000)
        with_cache = compute_cost(
            _KNOWN_MODEL, 10_000, 1_000, cache_write_tokens=50_000
        )
        assert with_cache == _expected(
            _KNOWN_MODEL, 10_000, 1_000, cache_write_tokens=50_000
        )
        assert base is not None and with_cache is not None
        assert with_cache > base

    def test_cache_read_is_cheaper_than_cache_write(self) -> None:
        """The two cache dimensions are not collapsed onto one rate."""
        read = compute_cost(_KNOWN_MODEL, 0, 0, cache_read_tokens=1_000_000)
        write = compute_cost(_KNOWN_MODEL, 0, 0, cache_write_tokens=1_000_000)
        assert read is not None and write is not None
        assert read < write

    def test_zero_tokens_on_known_model_is_zero_not_none(self) -> None:
        """A free call on a priced model is Decimal('0') — knowably zero."""
        result = compute_cost(_KNOWN_MODEL, 0, 0)
        assert result is not None
        assert result == Decimal(0)


# ---------------------------------------------------------------------------
# compute_cost — unknown / unpriced models
# ---------------------------------------------------------------------------


class TestComputeCostUnknownModel:
    """Unknown pricing yields None — never zero, never a partial estimate."""

    def test_unknown_model_returns_none(self) -> None:
        assert compute_cost(_UNKNOWN_MODEL, 1_000, 200) is None

    def test_unknown_model_none_is_not_zero(self) -> None:
        """None ('pricing unknown') is distinct from a genuinely free call."""
        result = compute_cost(_UNKNOWN_MODEL, 1_000, 200)
        assert result is None
        assert result != Decimal(0)

    def test_unknown_model_never_returns_float(self) -> None:
        result = compute_cost(_UNKNOWN_MODEL, 1_000, 200)
        assert result is None or isinstance(result, Decimal)

    def test_catalog_model_with_null_rate_dimensions_returns_none(self) -> None:
        """A catalogued model whose cost dimensions are null prices as None.

        Reached through the public catalog surface: the entry exists and is
        discoverable, but both cost-per-million dimensions are null, so
        compute_cost() must decline rather than invent a number.
        """
        info = get_model_info(_NULL_RATE_MODEL)
        assert info is not None, f"{_NULL_RATE_MODEL} must exist in the catalog"
        assert info.input_cost_per_million is None
        assert info.output_cost_per_million is None

        assert compute_cost(info.id, 500, 100) is None

    def test_null_rate_model_is_absent_from_rate_table(self) -> None:
        """The structural reason the null-rate model prices as None."""
        assert _NULL_RATE_MODEL not in _RATES


# ---------------------------------------------------------------------------
# compute_cost — fast mode
# ---------------------------------------------------------------------------


class TestComputeCostFastMode:
    """speed='fast' doubles cost for eligible models only."""

    def test_fast_doubles_cost_for_eligible_model(self) -> None:
        model = _first_fast_eligible()
        base = compute_cost(model, 100_000, 20_000)
        fast = compute_cost(model, 100_000, 20_000, speed="fast")
        assert base is not None and fast is not None
        assert fast == base * 2

    def test_fast_ignored_for_ineligible_model(self) -> None:
        """A priced-but-not-fast-eligible model ignores speed='fast'."""
        model = _first_fast_ineligible()
        base = compute_cost(model, 100_000, 20_000)
        fast = compute_cost(model, 100_000, 20_000, speed="fast")
        assert base is not None and fast is not None
        assert fast == base

    def test_non_fast_speed_leaves_cost_unchanged(self) -> None:
        """Only the literal 'fast' speed triggers the multiplier."""
        model = _first_fast_eligible()
        base = compute_cost(model, 100_000, 20_000)
        assert compute_cost(model, 100_000, 20_000, speed=None) == base
        assert compute_cost(model, 100_000, 20_000, speed="standard") == base

    def test_fast_on_unknown_model_still_none(self) -> None:
        """Fast mode does not conjure pricing for an unpriced model."""
        assert compute_cost(_UNKNOWN_MODEL, 100, 100, speed="fast") is None

    def test_fast_eligible_set_is_a_subset_of_priced_models(self) -> None:
        """Every fast-eligible model has rates to double."""
        assert _FAST_ELIGIBLE_MODELS <= set(_RATES)


# ---------------------------------------------------------------------------
# Usage.cost_usd — None-propagating aggregation
# ---------------------------------------------------------------------------


def _usage(cost: Decimal | None, tokens: int = 10) -> Usage:
    return Usage(
        input_tokens=tokens,
        output_tokens=tokens,
        total_tokens=tokens * 2,
        cost_usd=cost,
    )


class TestUsageCostField:
    """Usage carries an optional Decimal cost."""

    def test_cost_usd_defaults_to_none(self) -> None:
        usage = Usage(input_tokens=1, output_tokens=1, total_tokens=2)
        assert usage.cost_usd is None

    def test_cost_usd_round_trips_decimal(self) -> None:
        usage = _usage(Decimal("1.25"))
        assert usage.cost_usd == Decimal("1.25")
        assert isinstance(usage.cost_usd, Decimal)


class TestUsageAdditionCostPropagation:
    """Usage.__add__ propagates None for cost — unlike token fields."""

    def test_decimal_plus_decimal_sums(self) -> None:
        total = _usage(Decimal("0.50")) + _usage(Decimal("0.25"))
        assert total.cost_usd == Decimal("0.75")

    def test_none_plus_decimal_poisons_to_none(self) -> None:
        """An unknown-cost step makes the aggregate cost unknown."""
        total = _usage(None) + _usage(Decimal("0.25"))
        assert total.cost_usd is None

    def test_decimal_plus_none_poisons_to_none(self) -> None:
        """Poisoning is order-independent."""
        total = _usage(Decimal("0.25")) + _usage(None)
        assert total.cost_usd is None

    def test_none_plus_none_is_none(self) -> None:
        total = _usage(None) + _usage(None)
        assert total.cost_usd is None

    def test_poisoned_cost_never_degrades_to_zero(self) -> None:
        """The None total is not silently coerced into Decimal('0')."""
        total = _usage(None) + _usage(Decimal("0.25"))
        assert total.cost_usd is None
        assert total.cost_usd != Decimal(0)

    def test_token_fields_still_add_when_cost_is_none(self) -> None:
        """Cost's None-propagation must not leak into token aggregation."""
        total = _usage(None, tokens=10) + _usage(Decimal("0.25"), tokens=5)
        assert total.input_tokens == 15
        assert total.output_tokens == 15
        assert total.total_tokens == 30
        assert total.cost_usd is None


# ---------------------------------------------------------------------------
# Client.complete() — cost injection
# ---------------------------------------------------------------------------


class _CostMockAdapter:
    """Minimal ProviderAdapter returning a caller-supplied Response."""

    def __init__(self, response: Response, name: str = "mock") -> None:
        self._name = name
        self._response = response
        self.call_count = 0

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, request: Request) -> Response:
        self.call_count += 1
        return self._response

    async def stream(self, request: Request) -> AsyncIterator[StreamEvent]:
        yield StreamEvent(type=StreamEventType.FINISH)

    async def close(self) -> None:
        pass

    async def initialize(self) -> None:
        pass

    def supports_tool_choice(self, mode: str) -> bool:
        return True


def _adapter_response(model: str, usage: Usage, provider: str = "mock") -> Response:
    return Response(
        id="r1",
        model=model,
        provider=provider,
        message=Message.assistant("hello"),
        finish_reason=FinishReason(reason="stop"),
        usage=usage,
    )


def _complete_with(response: Response) -> Response:
    adapter = _CostMockAdapter(response)
    client = Client(providers={"mock": adapter}, default_provider="mock")
    request = Request(model=response.model, messages=[Message.user("hi")])
    return asyncio.run(client.complete(request))


class TestClientCompleteCostInjection:
    """Client.complete() fills in the cost_usd the adapter did not set."""

    def test_injects_computed_cost_when_adapter_omits_it(self) -> None:
        usage = Usage(input_tokens=200_000, output_tokens=50_000, total_tokens=250_000)
        assert usage.cost_usd is None  # precondition: adapter set nothing

        result = _complete_with(_adapter_response(_KNOWN_MODEL, usage))

        assert result.usage.cost_usd == _expected(_KNOWN_MODEL, 200_000, 50_000)
        assert isinstance(result.usage.cost_usd, Decimal)

    def test_injection_includes_cache_dimensions(self) -> None:
        """Cache token counts are forwarded into the computation, not dropped."""
        usage = Usage(
            input_tokens=10_000,
            output_tokens=2_000,
            total_tokens=12_000,
            cache_read_tokens=80_000,
            cache_write_tokens=40_000,
        )
        result = _complete_with(_adapter_response(_KNOWN_MODEL, usage))

        assert result.usage.cost_usd == _expected(
            _KNOWN_MODEL,
            10_000,
            2_000,
            cache_read_tokens=80_000,
            cache_write_tokens=40_000,
        )
        # Strictly above the cacheless price, so the cache terms really landed.
        assert result.usage.cost_usd > _expected(_KNOWN_MODEL, 10_000, 2_000)

    def test_adapter_supplied_cost_is_preserved_not_recomputed(self) -> None:
        """An adapter that priced the call wins; the client does not overwrite."""
        adapter_cost = Decimal("42.4242")
        usage = Usage(
            input_tokens=200_000,
            output_tokens=50_000,
            total_tokens=250_000,
            cost_usd=adapter_cost,
        )
        result = _complete_with(_adapter_response(_KNOWN_MODEL, usage))

        assert result.usage.cost_usd == adapter_cost
        # Guard against a recompute that happens to agree by accident.
        assert result.usage.cost_usd != _expected(_KNOWN_MODEL, 200_000, 50_000)

    def test_unknown_model_leaves_cost_none(self) -> None:
        """No pricing means no invented number on the response."""
        usage = Usage(input_tokens=1_000, output_tokens=500, total_tokens=1_500)
        result = _complete_with(_adapter_response(_UNKNOWN_MODEL, usage))

        assert result.usage.cost_usd is None

    def test_injection_preserves_token_fields(self) -> None:
        """Rebuilding usage with a cost must not lose the token counts."""
        usage = Usage(
            input_tokens=200_000,
            output_tokens=50_000,
            total_tokens=250_000,
            reasoning_tokens=7,
            cache_read_tokens=11,
            cache_write_tokens=13,
            raw={"provider_field": "kept"},
        )
        result = _complete_with(_adapter_response(_KNOWN_MODEL, usage))

        assert result.usage.input_tokens == 200_000
        assert result.usage.output_tokens == 50_000
        assert result.usage.total_tokens == 250_000
        assert result.usage.reasoning_tokens == 7
        assert result.usage.cache_read_tokens == 11
        assert result.usage.cache_write_tokens == 13
        assert result.usage.raw == {"provider_field": "kept"}
        assert result.usage.cost_usd is not None


# ---------------------------------------------------------------------------
# StreamAccumulator.response() — cost injection
# ---------------------------------------------------------------------------


def _accumulate(model: str, usage: Usage) -> Response:
    """Drive an accumulator through a minimal text stream and finish it."""
    acc = StreamAccumulator()
    acc.process(StreamEvent(type=StreamEventType.TEXT_START, text_id="t1"))
    acc.process(StreamEvent(type=StreamEventType.TEXT_DELTA, delta="hi", text_id="t1"))
    acc.process(StreamEvent(type=StreamEventType.TEXT_END, text_id="t1"))
    acc.process(
        StreamEvent(
            type=StreamEventType.FINISH,
            finish_reason=FinishReason(reason="stop"),
            usage=usage,
            response=_adapter_response(model, usage),
        )
    )
    return acc.response()


class TestStreamAccumulatorCostInjection:
    """StreamAccumulator.response() computes cost when the stream omitted it."""

    def test_computes_cost_when_unset(self) -> None:
        usage = Usage(input_tokens=200_000, output_tokens=50_000, total_tokens=250_000)
        resp = _accumulate(_KNOWN_MODEL, usage)

        assert resp.usage.cost_usd == _expected(_KNOWN_MODEL, 200_000, 50_000)
        assert isinstance(resp.usage.cost_usd, Decimal)

    def test_preserves_cost_already_set_on_the_stream(self) -> None:
        stream_cost = Decimal("7.7777")
        usage = Usage(
            input_tokens=200_000,
            output_tokens=50_000,
            total_tokens=250_000,
            cost_usd=stream_cost,
        )
        resp = _accumulate(_KNOWN_MODEL, usage)

        assert resp.usage.cost_usd == stream_cost
        assert resp.usage.cost_usd != _expected(_KNOWN_MODEL, 200_000, 50_000)

    def test_unknown_model_leaves_cost_none(self) -> None:
        usage = Usage(input_tokens=1_000, output_tokens=500, total_tokens=1_500)
        resp = _accumulate(_UNKNOWN_MODEL, usage)

        assert resp.usage.cost_usd is None

    def test_no_model_seen_leaves_cost_none(self) -> None:
        """Without a model id there is nothing to price against."""
        acc = StreamAccumulator()
        acc.process(
            StreamEvent(
                type=StreamEventType.FINISH,
                finish_reason=FinishReason(reason="stop"),
                usage=Usage(
                    input_tokens=200_000, output_tokens=50_000, total_tokens=250_000
                ),
            )
        )
        resp = acc.response()

        assert resp.model == ""
        assert resp.usage.cost_usd is None

    def test_accumulated_content_survives_cost_injection(self) -> None:
        """Rebuilding usage must not disturb the assembled response."""
        usage = Usage(input_tokens=200_000, output_tokens=50_000, total_tokens=250_000)
        resp = _accumulate(_KNOWN_MODEL, usage)

        assert resp.text == "hi"
        assert resp.finish_reason.reason == "stop"
        assert resp.usage.total_tokens == 250_000


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------


class TestCostPublicExport:
    """compute_cost is reachable from the package root."""

    def test_compute_cost_exported_from_package(self) -> None:
        import unified_llm

        assert hasattr(unified_llm, "compute_cost")
        assert "compute_cost" in unified_llm.__all__
