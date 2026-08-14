# unified-llm-client

Provider-agnostic LLM client library.

## Setup
```bash
cd unified-llm-client
python -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .
python -m pip install anthropic google-genai
```

## Tests
```bash
. .venv/bin/activate
pytest -q
```

Provider adapter tests require `anthropic` and `google-genai`. If those packages are missing, tests will fail on import.

---

## Cost exposure

The library exposes per-call USD cost through three public surfaces:
`compute_cost`, `usage.cost_usd` on responses, and the `cost_usd` key on
`provider:response` events emitted by `DirectProviderBackend`.

### `compute_cost`

```python
from decimal import Decimal
from unified_llm import compute_cost

# Returns Decimal for a known model
cost = compute_cost("claude-sonnet-4-5-20250929", 1000, 200)
# Decimal('0.006')

# Returns None for an unknown model -- never 0, never a float
unknown = compute_cost("my-custom-model", 1000, 200)
# None

# Cache tokens and fast mode
cost_with_cache = compute_cost(
    "claude-sonnet-4-5-20250929",
    input_tokens=500,
    output_tokens=100,
    cache_read_tokens=2000,
    cache_write_tokens=300,
)
# Decimal: input + output + cache_read + cache_write costs combined

# Fast mode (2x multiplier on eligible models)
fast_cost = compute_cost("claude-opus-4-8", 1000, 200, speed="fast")
```

**Signature:**
```
compute_cost(model, input_tokens=0, output_tokens=0, *,
             cache_read_tokens=0, cache_write_tokens=0, speed=None)
    -> Decimal | None
```

**What `None` means:** pricing unknown for this model. `None` is semantically
distinct from `Decimal('0')` (a free call). A `None` result never means zero
cost -- it means the rate table has no entry for the model.

### `response.usage.cost_usd`

`Client.complete()` and the streaming accumulator automatically populate
`usage.cost_usd` from the model id and token counts returned by the adapter.
The adapter does not need to set it.

```python
import asyncio
from unified_llm import (
    Client, Request, Message, Response, FinishReason, Usage,
    StreamEvent, StreamEventType,
)

# Minimal mock adapter -- no API key or network required
class _MockAdapter:
    name = "mock"

    async def complete(self, request):
        return Response(
            id="r1",
            model=request.model,
            provider="mock",
            message=Message.assistant("Hello!"),
            finish_reason=FinishReason(reason="stop"),
            # cost_usd is intentionally absent; Client.complete() injects it
            usage=Usage(input_tokens=100, output_tokens=50, total_tokens=150),
        )

    async def stream(self, request):
        # stream() is not used in this example
        return
        yield  # make it an async generator

    async def close(self): pass
    async def initialize(self): pass
    def supports_tool_choice(self, mode): return True

client = Client(providers={"mock": _MockAdapter()}, default_provider="mock")

request = Request(
    model="claude-sonnet-4-5-20250929",
    provider="mock",
    messages=[Message.user("Hello")],
)

async def main():
    response = await client.complete(request)
    print(response.usage.cost_usd)
    # Decimal('0.00105') for claude-sonnet-4-5-20250929 with 100 input + 50 output tokens

asyncio.run(main())
```

**Streaming accumulator path:**

```python
import asyncio
from unified_llm import (
    Client, Request, Message, Response, FinishReason, Usage,
    StreamEvent, StreamEventType, StreamAccumulator,
)

# Minimal mock adapter that yields stream events
class _MockStreamAdapter:
    name = "mock"

    async def complete(self, request):
        raise NotImplementedError

    async def stream(self, request):
        yield StreamEvent(type=StreamEventType.TEXT_START)
        yield StreamEvent(type=StreamEventType.TEXT_DELTA, delta="Hello!")
        yield StreamEvent(type=StreamEventType.TEXT_END)
        yield StreamEvent(
            type=StreamEventType.FINISH,
            finish_reason=FinishReason(reason="stop"),
            usage=Usage(input_tokens=100, output_tokens=50, total_tokens=150),
            response=Response(
                id="r1",
                model=request.model,
                provider="mock",
                message=Message.assistant("Hello!"),
                finish_reason=FinishReason(reason="stop"),
                usage=Usage(input_tokens=100, output_tokens=50, total_tokens=150),
            ),
        )

    async def close(self): pass
    async def initialize(self): pass
    def supports_tool_choice(self, mode): return True

async def main():
    client = Client(providers={"mock": _MockStreamAdapter()}, default_provider="mock")
    request = Request(
        model="claude-sonnet-4-5-20250929",
        provider="mock",
        messages=[Message.user("Hello")],
    )
    acc = StreamAccumulator()
    async for event in client.stream(request):
        acc.process(event)
    response = acc.response()
    print(response.usage.cost_usd)
    # Decimal('0.00105') for claude-sonnet-4-5-20250929 with 100 input + 50 output tokens

asyncio.run(main())
```

### `Usage` addition and `None` propagation

When summing `Usage` objects across multiple steps, any `None` cost operand
yields a `None` total. This prevents a partial sum from being mistaken for
a complete cost.

```python
from decimal import Decimal
from unified_llm import Usage

u1 = Usage(input_tokens=100, output_tokens=50, total_tokens=150, cost_usd=Decimal("0.01"))
u2 = Usage(input_tokens=200, output_tokens=80, total_tokens=280, cost_usd=None)

total = u1 + u2
print(total.cost_usd)   # None  (any None operand -> None total)
print(total.input_tokens)  # 300  (token fields sum normally)

# Decimal + Decimal -> Decimal sum
u3 = Usage(input_tokens=100, output_tokens=50, total_tokens=150, cost_usd=Decimal("0.01"))
u4 = Usage(input_tokens=200, output_tokens=80, total_tokens=280, cost_usd=Decimal("0.02"))
print((u3 + u4).cost_usd)  # Decimal('0.03')
```

### `provider:response` event `cost_usd` key

When `DirectProviderBackend` emits a `provider:response` event, the payload
carries a top-level `"cost_usd"` key equal to `response.usage.cost_usd`.
The key is always present; the value is `Decimal` for known models and `None`
for unknown ones. An absent key is never emitted.

```
# Payload structure (schema -- not executable Python; Decimal values shown as strings)
{
    "provider": "anthropic",
    "model": "claude-sonnet-4-5-20250929",
    "node_id": "my_node",
    "usage": {
        "input_tokens": 1000,
        "output_tokens": 200,
        "total_tokens": 1200,
        "reasoning_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
    },
    "finish_reason": "stop",
    "text_length": 42,
    "step_count": 1,
    "cost_usd": "0.006",   # Decimal for known models; None when model is unknown
}
```
