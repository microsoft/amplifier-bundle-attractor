set -euo pipefail

# DEFINITION.verify.sh — cost-exposure-unified-llm-loop-pipeline
#
# Invoked as: bash .ai/capsule/DEFINITION.verify.sh
# CWD: target repo root
# Writes: .ai/census (one "AC-N: MET|UNMET" line per AC, nothing else)
# Exit 0 iff all rows MET; exit 1 if any UNMET; exit >=2 on infra failure.

REPO_ROOT="$(pwd)"
CENSUS=".ai/census"

# ---------------------------------------------------------------------------
# Infrastructure checks (exit >=2 only for truly absent system tooling)
# ---------------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "INFRA: python3 not found" >&2
    exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "INFRA: uv not found" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Locate the provider oracle — the Anthropic _cost.py cache artifact.
# ---------------------------------------------------------------------------
ORACLE_CANDIDATE="/home/runner/.amplifier/cache/amplifier-module-provider-anthropic-5181591dcf06d076/amplifier_module_provider_anthropic/_cost.py"
ORACLE_DIR=""
if [ -f "$ORACLE_CANDIDATE" ]; then
    ORACLE_DIR="$(dirname "$(dirname "$ORACLE_CANDIDATE")")"
else
    FOUND=$(find /home/runner/.amplifier/cache -name "_cost.py" -path "*/amplifier_module_provider_anthropic/*" 2>/dev/null | head -1 || true)
    if [ -n "$FOUND" ]; then
        ORACLE_DIR="$(dirname "$(dirname "$FOUND")")"
    fi
fi

# ---------------------------------------------------------------------------
# Set up an isolated venv with the repo's own packages.
# We install: unified-llm-client, loop-pipeline, pytest, pytest-asyncio.
# ---------------------------------------------------------------------------
VENV_DIR="${REPO_ROOT}/.ai/capsule/_venv_gate"
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR" >/dev/null 2>&1 || {
        echo "INFRA: failed to create venv" >&2
        exit 2
    }
fi

VENV_PY="$VENV_DIR/bin/python"
VENV_PIP="$VENV_DIR/bin/pip"

# Install unified-llm-client from repo tree (hermetic: never from ambient env)
"$VENV_PIP" install --quiet \
    "${REPO_ROOT}/modules/unified-llm-client" \
    >/dev/null 2>&1 || {
    echo "INFRA: failed to install unified-llm-client" >&2
    exit 2
}

# Install loop-pipeline (depends on unified-llm-client already in venv)
"$VENV_PIP" install --quiet \
    "${REPO_ROOT}/modules/loop-pipeline" \
    >/dev/null 2>&1 || {
    echo "INFRA: failed to install loop-pipeline" >&2
    exit 2
}

# Install pytest + asyncio plugin (needed for AC-6 existing-test run)
"$VENV_PIP" install --quiet "pytest>=8.0.0" "pytest-asyncio>=1.0.0" \
    >/dev/null 2>&1 || {
    echo "INFRA: failed to install pytest" >&2
    exit 2
}

# ---------------------------------------------------------------------------
# Write a Python probe script to a temp file (avoids heredoc variable issues)
# ---------------------------------------------------------------------------
PROBE_SCRIPT="${REPO_ROOT}/.ai/capsule/_probe_$$.py"
trap 'rm -f "$PROBE_SCRIPT"' EXIT

# Note: ORACLE_DIR and REPO_ROOT are shell variables expanded into the Python
# source below. VENV_PY is used for the subprocess call in AC-6.
cat > "$PROBE_SCRIPT" << 'PYEOF_MARKER'
import sys
import os
import random
import time
import decimal
from decimal import Decimal
import subprocess
import types
import asyncio
from dataclasses import dataclass, field as dc_field
from typing import Any

REPO_ROOT = sys.argv[1]
ORACLE_DIR = sys.argv[2]
VENV_PY = sys.argv[3]
CENSUS_PATH = os.path.join(REPO_ROOT, ".ai", "census")

results = {}

# ---------------------------------------------------------------------------
# Runtime token-count generator (verification licensing: never fixed literals)
# ---------------------------------------------------------------------------
_rng = random.Random(int(time.time() * 1000) ^ os.getpid())

def _rand_tokens(lo=50, hi=2000):
    return _rng.randint(lo, hi)

# ---------------------------------------------------------------------------
# Load provider oracle (independent of unified_llm)
# ---------------------------------------------------------------------------
oracle_compute_cost = None
oracle_fast_eligible = None
if ORACLE_DIR and os.path.isdir(ORACLE_DIR):
    if ORACLE_DIR not in sys.path:
        sys.path.insert(0, ORACLE_DIR)
    try:
        from amplifier_module_provider_anthropic._cost import (
            compute_cost as _oracle_compute_cost,
            _FAST_ELIGIBLE_MODELS as _oracle_fast_eligible,
        )
        oracle_compute_cost = _oracle_compute_cost
        oracle_fast_eligible = _oracle_fast_eligible
        print("Oracle loaded from:", ORACLE_DIR)
    except Exception as e:
        print(f"WARNING: could not load oracle: {e}")

# ---------------------------------------------------------------------------
# AC-1: unified_llm.compute_cost importable; golden parity with oracle
#        for claude-sonnet-4-5-20250929 across 5 case families.
# ---------------------------------------------------------------------------
print("\n--- AC-1 ---")
try:
    from unified_llm import compute_cost as sut_compute_cost
    print("  compute_cost imported OK")

    if oracle_compute_cost is None:
        print("  UNMET: oracle unavailable — cannot verify parity")
        results["AC-1"] = False
    else:
        MODEL = "claude-sonnet-4-5-20250929"
        ac1_ok = True

        def _parity_check(label, sut_kwargs, oracle_kwargs):
            global ac1_ok
            try:
                sut_result = sut_compute_cost(**sut_kwargs)
                oracle_result = oracle_compute_cost(**oracle_kwargs)
            except Exception as e:
                print(f"  FAIL [{label}]: call raised: {e}")
                ac1_ok = False
                return
            if not isinstance(oracle_result, Decimal):
                print(f"  FAIL [{label}]: oracle result not Decimal: {type(oracle_result)}")
                ac1_ok = False
                return
            if sut_result is None:
                print(f"  FAIL [{label}]: SUT returned None, oracle={oracle_result}")
                ac1_ok = False
                return
            if not isinstance(sut_result, Decimal):
                print(f"  FAIL [{label}]: SUT result not Decimal: {type(sut_result)}")
                ac1_ok = False
                return
            if sut_result != oracle_result:
                print(f"  FAIL [{label}]: SUT={sut_result} != oracle={oracle_result}")
                ac1_ok = False
                return
            print(f"  OK [{label}]: {sut_result}")

        # Case 1: input-only
        inp1 = _rand_tokens(100, 1000)
        _parity_check(
            "input-only",
            dict(model=MODEL, input_tokens=[REDACTED:assignment] output_tokens=0),
            dict(model=MODEL, input_tokens=[REDACTED:assignment] output_tokens=0,
                 cache_read_input_tokens=0, cache_creation_input_tokens=0, speed=None),
        )

        # Case 2: input + output
        inp2 = _rand_tokens(200, 1500)
        out2 = _rand_tokens(50, 800)
        _parity_check(
            "input+output",
            dict(model=MODEL, input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
            dict(model=MODEL, input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
                 cache_read_input_tokens=0, cache_creation_input_tokens=0, speed=None),
        )

        # Case 3: cache_read-heavy
        inp3 = _rand_tokens(50, 300)
        out3 = _rand_tokens(20, 200)
        cr3 = _rand_tokens(500, 2000)
        _parity_check(
            "cache_read-heavy",
            dict(model=MODEL, input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment] cache_read_tokens=[REDACTED:assignment]
            dict(model=MODEL, input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
                 cache_read_input_tokens=[REDACTED:assignment] cache_creation_input_tokens=0, speed=None),
        )

        # Case 4: cache_write-heavy
        inp4 = _rand_tokens(50, 300)
        out4 = _rand_tokens(20, 200)
        cw4 = _rand_tokens(300, 1500)
        _parity_check(
            "cache_write-heavy",
            dict(model=MODEL, input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment] cache_write_tokens=[REDACTED:assignment]
            dict(model=MODEL, input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
                 cache_read_input_tokens=0, cache_creation_input_tokens=[REDACTED:assignment] speed=None),
        )

        # Case 5: fast-mode (speed='fast')
        # claude-sonnet-4-5-20250929 is NOT in _FAST_ELIGIBLE_MODELS,
        # so fast-mode result equals non-fast. We exercise the path anyway.
        inp5 = _rand_tokens(100, 800)
        out5 = _rand_tokens(30, 400)
        _parity_check(
            "fast-mode",
            dict(model=MODEL, input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment] speed="fast"),
            dict(model=MODEL, input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
                 cache_read_input_tokens=0, cache_creation_input_tokens=0, speed="fast"),
        )

        results["AC-1"] = ac1_ok

except ImportError as e:
    print(f"  UNMET: ImportError: {e}")
    results["AC-1"] = False
except Exception as e:
    print(f"  UNMET: {type(e).__name__}: {e}")
    results["AC-1"] = False

# ---------------------------------------------------------------------------
# AC-2: honest None — unknown model and None-rate-dimension model both → None.
# ---------------------------------------------------------------------------
print("\n--- AC-2 ---")
try:
    from unified_llm import compute_cost as sut_compute_cost2

    ac2_ok = True

    # Sub-case 2a: unknown model ID (random name, no domain vocabulary)
    rand_suffix = "".join([chr(_rng.randint(ord("a"), ord("z"))) for _ in range(8)])
    unknown_model = f"xz{rand_suffix}99"
    inp_a = _rand_tokens(100, 500)
    out_a = _rand_tokens(50, 300)
    try:
        result_a = sut_compute_cost2(unknown_model, inp_a, out_a)
        if result_a is not None:
            print(f"  FAIL [unknown-model]: expected None, got {result_a!r} (type={type(result_a).__name__})")
            ac2_ok = False
        else:
            print(f"  OK [unknown-model]: returned None as expected")
    except Exception as e:
        print(f"  FAIL [unknown-model]: raised exception: {e}")
        ac2_ok = False

    # Sub-case 2b: model with None rate dimension via public catalog surface.
    #
    # Strategy: inject a synthetic ModelInfo with input_cost_per_million=None
    # into unified_llm.catalog._CATALOG (resetting the cache so the public
    # surface get_model_info() returns it), then call compute_cost with that
    # model ID and assert result is None unconditionally.
    #
    # The public surface exercised: get_model_info(synthetic_id) confirms the
    # entry is presented; compute_cost(synthetic_id, ...) must return None
    # because the required rate dimension is None.
    try:
        import unified_llm.catalog as _catalog_mod
        from unified_llm.types import ModelInfo
        from datetime import date as _date

        # Generate a semantically-neutral synthetic model ID at runtime
        rand_id_chars = "".join([chr(_rng.randint(ord("a"), ord("z"))) for _ in range(10)])
        synthetic_id = f"probe-{rand_id_chars}"

        synthetic_entry = ModelInfo(
            id=synthetic_id,
            provider="test",
            display_name="Probe model (None cost)",
            context_window=4096,
            supports_tools=False,
            supports_vision=False,
            supports_reasoning=False,
            release_date=_date(2025, 1, 1),
            max_output=None,
            input_cost_per_million=None,   # <-- the required rate dimension is None
            output_cost_per_million=None,
        )

        # Inject into the catalog cache so the public surface returns our entry.
        # Save originals to restore after the probe.
        _orig_catalog = _catalog_mod._CATALOG
        _orig_alias = _catalog_mod._ALIAS_MAP

        # Force a fresh catalog list that includes our synthetic entry
        base_models, base_aliases = _catalog_mod._load_catalog()
        _catalog_mod._CATALOG = list(base_models) + [synthetic_entry]
        _catalog_mod._ALIAS_MAP = dict(base_aliases)

        try:
            # Confirm the entry is presented via the public catalog surface
            from unified_llm import get_model_info
            presented = get_model_info(synthetic_id)
            if presented is None:
                print(f"  FAIL [none-dim-probe]: get_model_info({synthetic_id!r}) returned None — injection failed")
                ac2_ok = False
            elif presented.input_cost_per_million is not None:
                print(f"  FAIL [none-dim-probe]: presented entry has non-None input_cost_per_million={presented.input_cost_per_million!r}")
                ac2_ok = False
            else:
                # Now call compute_cost via the public surface and assert None
                inp_b = _rand_tokens(100, 500)
                out_b = _rand_tokens(50, 300)
                try:
                    result_b = sut_compute_cost2(synthetic_id, inp_b, out_b)
                    if isinstance(result_b, float):
                        print(f"  FAIL [none-dim-probe]: got float — must be None (never float)")
                        ac2_ok = False
                    elif result_b == 0 or result_b == Decimal("0"):
                        print(f"  FAIL [none-dim-probe]: got 0 — must be None (never 0)")
                        ac2_ok = False
                    elif result_b is not None:
                        print(f"  FAIL [none-dim-probe]: expected None for model with None rate dimension, got {result_b!r}")
                        ac2_ok = False
                    else:
                        print(f"  OK [none-dim-probe]: compute_cost({synthetic_id!r}, {inp_b}, {out_b}) -> None as required")
                except Exception as e:
                    print(f"  FAIL [none-dim-probe]: compute_cost raised: {e}")
                    ac2_ok = False
        finally:
            # Restore original catalog state
            _catalog_mod._CATALOG = _orig_catalog
            _catalog_mod._ALIAS_MAP = _orig_alias

    except ImportError as e:
        print(f"  FAIL [none-dim]: catalog surface not importable: {e}")
        ac2_ok = False
    except Exception as e:
        import traceback
        print(f"  FAIL [none-dim]: unexpected error during catalog injection: {e}")
        traceback.print_exc()
        ac2_ok = False

    results["AC-2"] = ac2_ok

except ImportError as e:
    print(f"  UNMET: ImportError: {e}")
    results["AC-2"] = False
except Exception as e:
    print(f"  UNMET: {type(e).__name__}: {e}")
    results["AC-2"] = False

# ---------------------------------------------------------------------------
# AC-3: usage.cost_usd on Client.complete() response and Client.stream() path.
#
# Both paths must route through a real Client with a fake ProviderAdapter
# whose complete()/stream() returns Usage WITHOUT a pre-populated cost_usd.
# The production path in Client must compute cost_usd from token counts.
# ---------------------------------------------------------------------------
print("\n--- AC-3 ---")
try:
    import unified_llm
    from unified_llm import (
        Client, Usage, Response, Message, FinishReason, Role,
        StreamAccumulator, StreamEvent, StreamEventType, Request,
    )

    ac3_ok = True

    # Sub-case 3a: Usage has cost_usd field (existence gate for the other probes)
    try:
        u_test = Usage(input_tokens=10, output_tokens=5, total_tokens=15)
        if not hasattr(u_test, "cost_usd"):
            print("  FAIL [usage-field]: Usage has no cost_usd attribute")
            ac3_ok = False
        else:
            print(f"  OK [usage-field]: Usage.cost_usd exists, default={u_test.cost_usd!r}")
    except Exception as e:
        print(f"  FAIL [usage-field]: {e}")
        ac3_ok = False

    # Sub-case 3b: streaming path via Client.stream() -> StreamAccumulator
    # The fake adapter's stream() yields a FINISH event with Usage that has
    # NO pre-populated cost_usd. The production path must compute it.
    if oracle_compute_cost is not None:
        MODEL3 = "claude-sonnet-4-5-20250929"
        inp3b = _rand_tokens(100, 800)
        out3b = _rand_tokens(30, 400)
        try:
            expected3b = oracle_compute_cost(
                MODEL3,
                input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
                cache_read_input_tokens=0, cache_creation_input_tokens=0, speed=None,
            )
            if not isinstance(expected3b, Decimal):
                print(f"  FAIL [stream-client]: oracle result not Decimal: {type(expected3b)}")
                ac3_ok = False
            else:
                # Build a Usage WITHOUT cost_usd — the production path must compute it
                try:
                    raw_usage_3b = Usage(
                        input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
                        total_tokens=[REDACTED:assignment] + out3b,
                        # Deliberately no cost_usd — the SUT must compute it
                    )
                except TypeError:
                    # Usage doesn't have cost_usd field yet (base SHA) — UNMET
                    print("  FAIL [stream-client]: Usage does not accept cost_usd kwarg (feature not built)")
                    ac3_ok = False
                    raw_usage_3b = None

                if raw_usage_3b is not None:
                    # Verify the Usage we built has no cost_usd pre-populated
                    # (it should be None/absent at construction without the kwarg)
                    pre_cost = getattr(raw_usage_3b, "cost_usd", "MISSING")

                    # Build fake ProviderAdapter for streaming
                    class _FakeStreamAdapter:
                        name = "fake-stream"

                        async def complete(self, request):
                            raise NotImplementedError("streaming only")

                        async def stream(self, request):
                            # Yield a FINISH event with Usage that has no cost_usd pre-set
                            yield StreamEvent(
                                type=StreamEventType.FINISH,
                                finish_reason=FinishReason(reason="stop"),
                                usage=raw_usage_3b,
                                response=Response(
                                    id="stream-probe",
                                    model=MODEL3,
                                    provider="fake-stream",
                                    message=Message(role=Role.ASSISTANT, content=[]),
                                    finish_reason=FinishReason(reason="stop"),
                                    usage=raw_usage_3b,
                                ),
                            )

                        async def close(self):
                            pass

                        async def initialize(self):
                            pass

                        def supports_tool_choice(self, mode):
                            return False

                    fake_stream_adapter = _FakeStreamAdapter()
                    client3b = Client(
                        providers={"fake-stream": fake_stream_adapter},
                        default_provider="fake-stream",
                    )
                    req3b = Request(
                        model=MODEL3,
                        messages=[Message(role=Role.USER, content=[])],
                        provider="fake-stream",
                    )

                    async def _run_stream3b():
                        acc = StreamAccumulator()
                        async for event in client3b.stream(req3b):
                            acc.process(event)
                        return acc.response()

                    try:
                        resp3b = asyncio.run(_run_stream3b())
                        cost3b = getattr(resp3b.usage, "cost_usd", "MISSING")
                        if cost3b == "MISSING":
                            print("  FAIL [stream-client]: response().usage has no cost_usd")
                            ac3_ok = False
                        elif not isinstance(cost3b, (Decimal, type(None))):
                            print(f"  FAIL [stream-client]: cost_usd wrong type: {type(cost3b)}")
                            ac3_ok = False
                        elif cost3b != expected3b:
                            print(f"  FAIL [stream-client]: cost_usd={cost3b} != expected={expected3b}")
                            ac3_ok = False
                        else:
                            print(f"  OK [stream-client]: cost_usd={cost3b} matches oracle (via Client.stream)")
                    except Exception as e:
                        print(f"  FAIL [stream-client]: Client.stream() raised: {e}")
                        ac3_ok = False
        except Exception as e:
            print(f"  FAIL [stream-client]: {e}")
            ac3_ok = False
    else:
        # No oracle: verify field exists and type is correct via Client.stream()
        try:
            raw_usage_no_oracle = Usage(
                input_tokens=[REDACTED:assignment] output_tokens=50, total_tokens=[REDACTED:assignment]
            )

            class _FakeStreamAdapterNoOracle:
                name = "fake-stream-no-oracle"

                async def complete(self, request):
                    raise NotImplementedError

                async def stream(self, request):
                    yield StreamEvent(
                        type=StreamEventType.FINISH,
                        finish_reason=FinishReason(reason="stop"),
                        usage=raw_usage_no_oracle,
                        response=Response(
                            id="stream-probe-no-oracle",
                            model="test-model",
                            provider="fake-stream-no-oracle",
                            message=Message(role=Role.ASSISTANT, content=[]),
                            finish_reason=FinishReason(reason="stop"),
                            usage=raw_usage_no_oracle,
                        ),
                    )

                async def close(self):
                    pass

                async def initialize(self):
                    pass

                def supports_tool_choice(self, mode):
                    return False

            client_no_oracle = Client(
                providers={"fake-stream-no-oracle": _FakeStreamAdapterNoOracle()},
                default_provider="fake-stream-no-oracle",
            )
            req_no_oracle = Request(
                model="test-model",
                messages=[Message(role=Role.USER, content=[])],
                provider="fake-stream-no-oracle",
            )

            async def _run_stream_no_oracle():
                acc = StreamAccumulator()
                async for event in client_no_oracle.stream(req_no_oracle):
                    acc.process(event)
                return acc.response()

            resp_no = asyncio.run(_run_stream_no_oracle())
            cost_no = getattr(resp_no.usage, "cost_usd", "MISSING")
            if cost_no == "MISSING":
                print("  FAIL [stream-client-no-oracle]: cost_usd absent from response")
                ac3_ok = False
            elif not isinstance(cost_no, (Decimal, type(None))):
                print(f"  FAIL [stream-client-no-oracle]: wrong type {type(cost_no)}")
                ac3_ok = False
            else:
                print(f"  OK [stream-client-no-oracle]: cost_usd={cost_no!r}")
        except TypeError as e:
            print(f"  FAIL [stream-client-no-oracle]: Usage construction failed: {e}")
            ac3_ok = False
        except Exception as e:
            print(f"  FAIL [stream-client-no-oracle]: {e}")
            ac3_ok = False

    # Sub-case 3c: non-streaming path via Client.complete()
    # The fake adapter's complete() returns a Response with Usage that has
    # NO pre-populated cost_usd. The production path must compute it.
    if oracle_compute_cost is not None:
        MODEL3c = "claude-sonnet-4-5-20250929"
        inp3c = _rand_tokens(80, 700)
        out3c = _rand_tokens(20, 350)
        try:
            exp3c = oracle_compute_cost(
                MODEL3c,
                input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
                cache_read_input_tokens=0, cache_creation_input_tokens=0, speed=None,
            )
            if not isinstance(exp3c, Decimal):
                print(f"  FAIL [complete-client]: oracle result not Decimal")
                ac3_ok = False
            else:
                # Build a Usage WITHOUT cost_usd — the production path must compute it
                try:
                    raw_usage_3c = Usage(
                        input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
                        total_tokens=[REDACTED:assignment] + out3c,
                        # Deliberately no cost_usd — the SUT must compute it
                    )
                except TypeError:
                    print("  FAIL [complete-client]: Usage does not accept construction (feature not built)")
                    ac3_ok = False
                    raw_usage_3c = None

                if raw_usage_3c is not None:
                    class _FakeCompleteAdapter:
                        name = "fake-complete"

                        async def complete(self, request):
                            # Return Response with Usage that has no cost_usd pre-set
                            return Response(
                                id="complete-probe",
                                model=MODEL3c,
                                provider="fake-complete",
                                message=Message(role=Role.ASSISTANT, content=[]),
                                finish_reason=FinishReason(reason="stop"),
                                usage=raw_usage_3c,
                            )

                        async def stream(self, request):
                            raise NotImplementedError

                        async def close(self):
                            pass

                        async def initialize(self):
                            pass

                        def supports_tool_choice(self, mode):
                            return False

                    client3c = Client(
                        providers={"fake-complete": _FakeCompleteAdapter()},
                        default_provider="fake-complete",
                    )
                    req3c = Request(
                        model=MODEL3c,
                        messages=[Message(role=Role.USER, content=[])],
                        provider="fake-complete",
                    )

                    async def _run_complete3c():
                        return await client3c.complete(req3c)

                    try:
                        resp3c = asyncio.run(_run_complete3c())
                        cost3c = getattr(resp3c.usage, "cost_usd", "MISSING")
                        if cost3c == "MISSING":
                            print("  FAIL [complete-client]: response.usage.cost_usd absent")
                            ac3_ok = False
                        elif not isinstance(cost3c, (Decimal, type(None))):
                            print(f"  FAIL [complete-client]: wrong type {type(cost3c)}")
                            ac3_ok = False
                        elif cost3c != exp3c:
                            print(f"  FAIL [complete-client]: cost_usd={cost3c} != expected={exp3c}")
                            ac3_ok = False
                        else:
                            print(f"  OK [complete-client]: cost_usd={cost3c} (via Client.complete)")
                    except Exception as e:
                        print(f"  FAIL [complete-client]: Client.complete() raised: {e}")
                        ac3_ok = False
        except Exception as e:
            print(f"  FAIL [complete-client]: {e}")
            ac3_ok = False

    results["AC-3"] = ac3_ok

except ImportError as e:
    print(f"  UNMET: ImportError: {e}")
    results["AC-3"] = False
except Exception as e:
    print(f"  UNMET: {type(e).__name__}: {e}")
    results["AC-3"] = False

# ---------------------------------------------------------------------------
# AC-4: DirectProviderBackend provider:response event carries top-level cost_usd.
# ---------------------------------------------------------------------------
print("\n--- AC-4 ---")
try:
    # Minimal amplifier_core stub (pattern from existing tests)
    if "amplifier_core" not in sys.modules:
        @dataclass
        class _StubMessage:
            role: str = "user"
            content: Any = ""
            tool_call_id: str | None = None
            name: str | None = None
            metadata: dict | None = None

        @dataclass
        class _StubChatRequest:
            messages: list = dc_field(default_factory=list)
            tools: list | None = None
            tool_choice: str | None = None
            reasoning_effort: str | None = None

        _stub_core = types.ModuleType("amplifier_core")
        _stub_core.Message = _StubMessage
        _stub_core.ChatRequest = _StubChatRequest
        sys.modules["amplifier_core"] = _stub_core

        @dataclass
        class _StubToolCallBlock:
            id: str = ""
            name: str = ""
            input: dict = dc_field(default_factory=dict)
            type: str = "tool_call"

        _stub_msg = types.ModuleType("amplifier_core.message_models")
        _stub_msg.ToolCallBlock = _StubToolCallBlock
        sys.modules["amplifier_core.message_models"] = _stub_msg

    import unified_llm
    from unified_llm import (
        Usage, Response, Message, FinishReason, Role,
        GenerateResult, StepResult,
    )

    inp4 = _rand_tokens(80, 600)
    out4 = _rand_tokens(20, 300)
    cost4 = None
    if oracle_compute_cost is not None:
        cost4 = oracle_compute_cost(
            "claude-sonnet-4-5-20250929",
            input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
            cache_read_input_tokens=0, cache_creation_input_tokens=0, speed=None,
        )

    try:
        u4 = Usage(
            input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
            total_tokens=[REDACTED:assignment] + out4, cost_usd=cost4,
        )
    except TypeError:
        u4 = Usage(input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment] total_tokens=[REDACTED:assignment] + out4)

    mock_response4 = Response(
        id="r4", model="test-model", provider="test",
        message=Message(role=Role.ASSISTANT, content=[]),
        finish_reason=FinishReason(reason="stop"),
        usage=u4,
    )

    async def _mock_generate(*args, **kwargs):
        step = StepResult(
            text='{"status": "success", "notes": "ok"}',
            tool_calls=[], tool_results=[],
            finish_reason=FinishReason(reason="stop"),
            usage=u4, response=mock_response4, warnings=[],
        )
        return GenerateResult(
            text='{"status": "success", "notes": "ok"}',
            finish_reason=FinishReason(reason="stop"),
            usage=u4, total_usage=u4,
            steps=[step], response=mock_response4,
        )

    class _RecordingHooks4:
        def __init__(self):
            self.events = []
        async def emit(self, event, data):
            self.events.append((event, dict(data)))
            return type("HR", (), {"action": "continue", "data": None, "reason": None})()

    hooks4 = _RecordingHooks4()

    @dataclass
    class _MockNode4:
        id: str = "probe_node"
        prompt: str = "test"
        attrs: dict = dc_field(default_factory=lambda: {
            "llm_model": "test-model",
            "llm_provider": "test",
        })
        response_schema: Any = None
        llm_provider: str = "test"
        llm_model: str = "test-model"

    class _MockSession4:
        config = {}

    class _MockCoordinator4:
        config = {"agents": {}}
        session = _MockSession4()
        def get_capability(self, name): return None

    from amplifier_module_loop_pipeline import DirectProviderBackend
    from amplifier_module_loop_pipeline.context import PipelineContext

    import unified_llm as _ullm
    _orig_gen = getattr(_ullm, "generate", None)
    _ullm.generate = _mock_generate

    try:
        backend4 = DirectProviderBackend(
            provider=object(),
            hooks=hooks4,
            coordinator=_MockCoordinator4(),
        )
        node4 = _MockNode4()

        async def _run4():
            ctx = PipelineContext()
            return await backend4.run(node4, "test prompt", ctx)

        asyncio.run(_run4())
    finally:
        if _orig_gen is not None:
            _ullm.generate = _orig_gen
        elif hasattr(_ullm, "generate"):
            del _ullm.generate

    pr_events = [(e, d) for e, d in hooks4.events if e == "provider:response"]
    if not pr_events:
        print("  FAIL: no provider:response event emitted")
        results["AC-4"] = False
    else:
        _, payload = pr_events[0]
        if "cost_usd" not in payload:
            print(f"  FAIL: 'cost_usd' not a top-level key in provider:response payload")
            print(f"  Payload keys: {list(payload.keys())}")
            if "usage" in payload and "cost_usd" in payload.get("usage", {}):
                print("  NOTE: cost_usd found inside payload['usage'] — not sufficient per AC-4")
            results["AC-4"] = False
        else:
            payload_cost = payload["cost_usd"]
            expected_cost_usd = getattr(u4, "cost_usd", None)
            if payload_cost != expected_cost_usd:
                print(f"  FAIL: payload['cost_usd']={payload_cost!r} != usage.cost_usd={expected_cost_usd!r}")
                results["AC-4"] = False
            else:
                print(f"  OK: payload['cost_usd']={payload_cost!r} (top-level, matches usage.cost_usd)")
                results["AC-4"] = True

except ImportError as e:
    print(f"  UNMET: ImportError: {e}")
    results["AC-4"] = False
except Exception as e:
    import traceback
    print(f"  UNMET: {type(e).__name__}: {e}")
    traceback.print_exc()
    results["AC-4"] = False

# ---------------------------------------------------------------------------
# AC-5: Usage.__add__ propagates cost_usd with None-absorbing rule.
# ---------------------------------------------------------------------------
print("\n--- AC-5 ---")
try:
    from unified_llm import Usage

    ac5_ok = True

    # Sub-case 5a: both cost_usd present → Decimal sum
    d1 = Decimal(str(_rng.randint(1, 999))) / Decimal("1000")
    d2 = Decimal(str(_rng.randint(1, 999))) / Decimal("1000")
    expected_sum = d1 + d2
    try:
        ua5 = Usage(input_tokens=10, output_tokens=5, total_tokens=15, cost_usd=d1)
        ub5 = Usage(input_tokens=20, output_tokens=10, total_tokens=30, cost_usd=d2)
        uc5 = ua5 + ub5
        cost_sum = getattr(uc5, "cost_usd", "MISSING")
        if cost_sum == "MISSING":
            print("  FAIL [both-present]: result has no cost_usd")
            ac5_ok = False
        elif not isinstance(cost_sum, Decimal):
            print(f"  FAIL [both-present]: cost_usd is {type(cost_sum).__name__}, expected Decimal")
            ac5_ok = False
        elif cost_sum != expected_sum:
            print(f"  FAIL [both-present]: {cost_sum} != {expected_sum}")
            ac5_ok = False
        else:
            print(f"  OK [both-present]: {d1} + {d2} = {cost_sum}")
    except TypeError as e:
        print(f"  FAIL [both-present]: Usage does not accept cost_usd: {e}")
        ac5_ok = False
    except Exception as e:
        print(f"  FAIL [both-present]: {e}")
        ac5_ok = False

    # Sub-case 5b: one cost_usd None → result None (left operand has value)
    d3 = Decimal(str(_rng.randint(1, 999))) / Decimal("1000")
    try:
        ud5 = Usage(input_tokens=10, output_tokens=5, total_tokens=15, cost_usd=d3)
        ue5 = Usage(input_tokens=20, output_tokens=10, total_tokens=30, cost_usd=None)
        uf5 = ud5 + ue5
        cost_none = getattr(uf5, "cost_usd", "MISSING")
        if cost_none == "MISSING":
            print("  FAIL [left-has-value-right-none]: result has no cost_usd")
            ac5_ok = False
        elif cost_none is not None:
            print(f"  FAIL [left-has-value-right-none]: expected None, got {cost_none!r}")
            ac5_ok = False
        else:
            print(f"  OK [left-has-value-right-none]: Decimal + None → None")
    except TypeError as e:
        print(f"  FAIL [left-has-value-right-none]: Usage does not accept cost_usd: {e}")
        ac5_ok = False
    except Exception as e:
        print(f"  FAIL [left-has-value-right-none]: {e}")
        ac5_ok = False

    # Sub-case 5c: both None → result None
    try:
        ug5 = Usage(input_tokens=10, output_tokens=5, total_tokens=15, cost_usd=None)
        uh5 = Usage(input_tokens=20, output_tokens=10, total_tokens=30, cost_usd=None)
        ui5 = ug5 + uh5
        cost_both_none = getattr(ui5, "cost_usd", "MISSING")
        if cost_both_none == "MISSING":
            print("  FAIL [both-none]: result has no cost_usd")
            ac5_ok = False
        elif cost_both_none is not None:
            print(f"  FAIL [both-none]: expected None, got {cost_both_none!r}")
            ac5_ok = False
        else:
            print(f"  OK [both-none]: None + None → None")
    except TypeError as e:
        print(f"  FAIL [both-none]: Usage does not accept cost_usd: {e}")
        ac5_ok = False
    except Exception as e:
        print(f"  FAIL [both-none]: {e}")
        ac5_ok = False

    results["AC-5"] = ac5_ok

except ImportError as e:
    print(f"  UNMET: ImportError: {e}")
    results["AC-5"] = False
except Exception as e:
    print(f"  UNMET: {type(e).__name__}: {e}")
    results["AC-5"] = False

# ---------------------------------------------------------------------------
# AC-6 [guard]: Existing token-field Usage arithmetic unchanged;
#               provider:response token fields unchanged.
# ---------------------------------------------------------------------------
print("\n--- AC-6 ---")
try:
    from unified_llm import Usage

    ac6_ok = True

    # Token arithmetic: add two Usage objects (no cost_usd involvement)
    t_inp_a = _rand_tokens(50, 500)
    t_out_a = _rand_tokens(20, 200)
    t_inp_b = _rand_tokens(50, 500)
    t_out_b = _rand_tokens(20, 200)
    t_cr_a = _rand_tokens(10, 100)
    t_cw_b = _rand_tokens(10, 100)

    try:
        ua6 = Usage(
            input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
            total_tokens=[REDACTED:assignment] + t_out_a,
            reasoning_tokens=[REDACTED:assignment]
            cache_read_tokens=[REDACTED:assignment] cache_write_tokens=[REDACTED:assignment]
        )
        ub6 = Usage(
            input_tokens=[REDACTED:assignment] output_tokens=[REDACTED:assignment]
            total_tokens=[REDACTED:assignment] + t_out_b,
            reasoning_tokens=[REDACTED:assignment]
            cache_read_tokens=[REDACTED:assignment] cache_write_tokens=[REDACTED:assignment]
        )
        uc6 = ua6 + ub6

        checks6 = [
            ("input_tokens",       uc6.input_tokens,       t_inp_a + t_inp_b),
            ("output_tokens",      uc6.output_tokens,      t_out_a + t_out_b),
            ("total_tokens",       uc6.total_tokens,       (t_inp_a + t_out_a) + (t_inp_b + t_out_b)),
            ("reasoning_tokens",   uc6.reasoning_tokens,   None),
            ("cache_read_tokens",  uc6.cache_read_tokens,  t_cr_a),
            ("cache_write_tokens", uc6.cache_write_tokens, t_cw_b),
        ]
        for fname, actual, expected in checks6:
            if actual != expected:
                print(f"  FAIL [token-arith]: {fname}={actual!r} != {expected!r}")
                ac6_ok = False
            else:
                print(f"  OK [token-arith]: {fname}={actual!r}")

    except Exception as e:
        print(f"  FAIL [token-arith]: {e}")
        ac6_ok = False

    # Run the existing test that asserts provider:response token fields are present.
    # Use the venv python so all packages are available.
    lp_dir = os.path.join(REPO_ROOT, "modules", "loop-pipeline")
    try:
        result_proc = subprocess.run(
            [
                VENV_PY, "-m", "pytest",
                "tests/test_provider_hooks.py::test_amplifier_backend_emits_provider_response",
                "-x", "-q", "--tb=short",
                "--import-mode=importlib",
            ],
            cwd=lp_dir,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result_proc.returncode == 0:
            print("  OK [token-fields-in-event]: existing provider:response token-fields test passes")
        else:
            print("  FAIL [token-fields-in-event]: existing test failed")
            if result_proc.stdout:
                print(result_proc.stdout[-600:])
            if result_proc.stderr:
                print(result_proc.stderr[-200:])
            ac6_ok = False
    except subprocess.TimeoutExpired:
        print("  FAIL [token-fields-in-event]: test timed out")
        ac6_ok = False
    except Exception as e:
        print(f"  FAIL [token-fields-in-event]: could not run existing test: {e}")
        ac6_ok = False

    results["AC-6"] = ac6_ok

except ImportError as e:
    print(f"  UNMET: ImportError: {e}")
    results["AC-6"] = False
except Exception as e:
    print(f"  UNMET: {type(e).__name__}: {e}")
    results["AC-6"] = False

# ---------------------------------------------------------------------------
# Write census file (one line per AC, nothing else)
# ---------------------------------------------------------------------------
print("\n--- CENSUS ---")
ordered_acs = ["AC-1", "AC-2", "AC-3", "AC-4", "AC-5", "AC-6"]
lines = []
all_met = True
for ac in ordered_acs:
    met = results.get(ac, False)
    row = f"{ac}: {'MET' if met else 'UNMET'}"
    lines.append(row)
    print(f"  {row}")
    if not met:
        all_met = False

with open(CENSUS_PATH, "w") as f:
    f.write("\n".join(lines) + "\n")

print(f"\nAll MET: {all_met}")
sys.exit(0 if all_met else 1)
PYEOF_MARKER

# ---------------------------------------------------------------------------
# Execute the probe script under the isolated venv
# ---------------------------------------------------------------------------
"$VENV_PY" "$PROBE_SCRIPT" "$REPO_ROOT" "$ORACLE_DIR" "$VENV_PY"
PROBE_EXIT=$?

exit $PROBE_EXIT
