set -euo pipefail
# DEFINITION.verify.sh — Executable gate for the LLM cost exposure feature
# Feature: unified_llm.compute_cost + usage.cost_usd + provider:response cost_usd
#
# Oracle provenance:
#   Source: https://github.com/microsoft/amplifier-module-provider-anthropic
#   File:   amplifier_module_provider_anthropic/_cost.py
#   Commit: 68434cd3d0b3666d36902aea38bf8592f81c5104
#   Vendored as: oracle.py (plain source, beside this script)
#
# Base SHA: b88964330b707473e308f569db374b30a2608247
# Later commit: e3f57c05b41b63f2a1fa11bef7198e3185b01a6a
# Criteria digest: b19fa073d57bec31af0a0a5707062dba9192a7bfb1dfa746703984d3693934f3
#
# Invocation: bash .ai/capsule/DEFINITION.verify.sh
#   cwd is the repo root (no arguments accepted)
#
# Exit codes:
#   0  — all criteria pass (every census row MET)
#   1  — one or more criteria fail (at least one census row UNMET)
#   >=2 — gate infrastructure failure (oracle missing, python absent, etc.)

# ---------------------------------------------------------------------------
# Repo root is the cwd — the invocation contract guarantees this
# ---------------------------------------------------------------------------
REPO="$(pwd)"

# ---------------------------------------------------------------------------
# Gate directory (where this script and oracle.py live)
# ---------------------------------------------------------------------------
GATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export GATE_DIR
ORACLE_PY="$GATE_DIR/oracle.py"

# Census file path (runner deletes it before each invocation)
CENSUS_FILE="$REPO/.ai/census"

# ---------------------------------------------------------------------------
# Ensure python3 is available
# ---------------------------------------------------------------------------
if ! command -v python3 >/dev/null 2>&1; then
    echo "FATAL: python3 not found on PATH" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Step 1: Verify oracle is present and loadable
# ---------------------------------------------------------------------------
echo "=== GATE INFRASTRUCTURE: verifying oracle ==="
if [[ ! -f "$ORACLE_PY" ]]; then
    echo "FATAL: oracle.py not found at $ORACLE_PY" >&2
    echo "The vendored oracle must ship as a plain source file beside this script." >&2
    echo "Oracle: https://github.com/microsoft/amplifier-module-provider-anthropic @ 68434cd3d0b3666d36902aea38bf8592f81c5104" >&2
    exit 2
fi

python3 - <<'ORACLE_CHECK'
import sys, importlib.util, pathlib, os

gate_dir = pathlib.Path(os.environ.get("GATE_DIR", "."))
oracle_path = gate_dir / "oracle.py"

spec = importlib.util.spec_from_file_location("_oracle", oracle_path)
oracle = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(oracle)
except Exception as e:
    print(f"FATAL: oracle.py failed to load: {e}", file=sys.stderr)
    sys.exit(2)

required = ["compute_cost", "_RATES", "_FAST_ELIGIBLE_MODELS"]
for sym in required:
    if not hasattr(oracle, sym):
        print(f"FATAL: oracle.py missing required symbol: {sym}", file=sys.stderr)
        sys.exit(2)

if "claude-sonnet-4-5-20250929" not in oracle._RATES:
    print("FATAL: oracle._RATES does not contain 'claude-sonnet-4-5-20250929'", file=sys.stderr)
    sys.exit(2)

if not oracle._FAST_ELIGIBLE_MODELS:
    print("FATAL: oracle._FAST_ELIGIBLE_MODELS is empty", file=sys.stderr)
    sys.exit(2)

print(f"Oracle OK: {len(oracle._RATES)} models in _RATES, {len(oracle._FAST_ELIGIBLE_MODELS)} fast-eligible")
ORACLE_CHECK

echo ""

# ---------------------------------------------------------------------------
# Step 2: Verify system under test is present (path-based, no install)
# ---------------------------------------------------------------------------
echo "=== GATE INFRASTRUCTURE: verifying repo structure ==="

UNIFIED_LLM_DIR="$REPO/modules/unified-llm-client"
LOOP_PIPELINE_DIR="$REPO/modules/loop-pipeline"

if [[ ! -d "$UNIFIED_LLM_DIR" ]]; then
    echo "FATAL: unified-llm-client module not found at $UNIFIED_LLM_DIR" >&2
    exit 2
fi
if [[ ! -d "$LOOP_PIPELINE_DIR" ]]; then
    echo "FATAL: loop-pipeline module not found at $LOOP_PIPELINE_DIR" >&2
    exit 2
fi
if [[ ! -f "$UNIFIED_LLM_DIR/unified_llm/__init__.py" ]]; then
    echo "FATAL: unified_llm package not found under $UNIFIED_LLM_DIR" >&2
    exit 2
fi
if [[ ! -f "$LOOP_PIPELINE_DIR/amplifier_module_loop_pipeline/__init__.py" ]]; then
    echo "FATAL: amplifier_module_loop_pipeline package not found under $LOOP_PIPELINE_DIR" >&2
    exit 2
fi

export UNIFIED_LLM_DIR LOOP_PIPELINE_DIR

echo "Repo structure OK."
echo ""

# ---------------------------------------------------------------------------
# Probe infrastructure: write probe to a temp file and run it
# This avoids heredoc variable-expansion issues with complex probe scripts.
# ---------------------------------------------------------------------------
PASS_COUNT=0
FAIL_COUNT=0
INFRA_FAIL_COUNT=0

# Per-AC results: "MET" or "UNMET"
declare -A AC_RESULT
AC_RESULT["AC-1"]="UNMET"
AC_RESULT["AC-2"]="UNMET"
AC_RESULT["AC-3"]="UNMET"
AC_RESULT["AC-4"]="UNMET"
AC_RESULT["AC-5"]="UNMET"
AC_RESULT["AC-6"]="UNMET"

TMPDIR_GATE="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_GATE"' EXIT

run_probe() {
    local ac_id="$1"
    local description="$2"
    local probe_file="$3"

    echo "--- $ac_id: $description ---"
    local exit_code=0
    python3 "$probe_file" || exit_code=$?
    if [[ $exit_code -eq 0 ]]; then
        echo "  PASS: $ac_id"
        PASS_COUNT=$((PASS_COUNT + 1))
        AC_RESULT["$ac_id"]="MET"
    elif [[ $exit_code -ge 2 ]]; then
        echo "  INFRA-FAIL: $ac_id (gate infrastructure failure, exit $exit_code)"
        INFRA_FAIL_COUNT=$((INFRA_FAIL_COUNT + 1))
        AC_RESULT["$ac_id"]="UNMET"
    else
        echo "  FAIL: $ac_id"
        FAIL_COUNT=$((FAIL_COUNT + 1))
        AC_RESULT["$ac_id"]="UNMET"
    fi
    echo ""
}

# ---------------------------------------------------------------------------
# AC-1: unified_llm.compute_cost importable, multi-model golden parity
# ---------------------------------------------------------------------------
cat > "$TMPDIR_GATE/probe_ac1.py" << 'PROBE_AC1'
import sys, os, importlib.util, pathlib, random
from decimal import Decimal

gate_dir = pathlib.Path(os.environ["GATE_DIR"])
unified_llm_dir = os.environ["UNIFIED_LLM_DIR"]
loop_pipeline_dir = os.environ["LOOP_PIPELINE_DIR"]

# Load oracle from sibling path (never from ambient cache or installed package)
oracle_path = gate_dir / "oracle.py"
spec = importlib.util.spec_from_file_location("_oracle", oracle_path)
oracle = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(oracle)
except Exception as e:
    print(f"INFRA-FAIL: oracle.py failed to load: {e}", file=sys.stderr)
    sys.exit(2)

if not all(hasattr(oracle, s) for s in ("compute_cost", "_RATES", "_FAST_ELIGIBLE_MODELS")):
    print("INFRA-FAIL: oracle missing required symbols", file=sys.stderr)
    sys.exit(2)

# Import SUT from repo tree (hermetic: sys.path.insert, never pip install)
sys.path.insert(0, unified_llm_dir)
try:
    import unified_llm
    compute_cost = getattr(unified_llm, "compute_cost", None)
    if compute_cost is None:
        print("FAIL AC-1: unified_llm.compute_cost not present (AttributeError)", file=sys.stderr)
        sys.exit(1)
except ImportError as e:
    print(f"FAIL AC-1: cannot import unified_llm: {e}", file=sys.stderr)
    sys.exit(1)

# Select models from oracle (at least 3, must include required + fast-eligible)
all_model_ids = list(oracle._RATES.keys())
fast_eligible = list(oracle._FAST_ELIGIBLE_MODELS)

required_model = "claude-sonnet-4-5-20250929"
if required_model not in oracle._RATES:
    print(f"INFRA-FAIL: oracle does not contain required model {required_model!r}", file=sys.stderr)
    sys.exit(2)

if not fast_eligible:
    print("INFRA-FAIL: oracle has no fast-eligible models", file=sys.stderr)
    sys.exit(2)

fast_model = fast_eligible[0]

# Pick a third model distinct from the required and fast models
third_candidates = [m for m in all_model_ids if m != required_model and m != fast_model]
if not third_candidates:
    print("INFRA-FAIL: oracle has fewer than 3 distinct models for sampling", file=sys.stderr)
    sys.exit(2)
third_model = third_candidates[0]

sampled_models = [required_model, third_model]  # non-fast models tested with all cases
# fast_model tested with fast-mode + non-fast cases below

print(f"Sampled models: {sampled_models} + fast-eligible: {fast_model!r}")

# Generate token counts at runtime (not fixed literals — criteria requirement)
rng = random.Random()  # seeded from OS entropy; different each gate run
def gen_tokens():
    return rng.randint(100, 50000)

failures = []

def check_parity(model, sut_kwargs, oracle_kwargs, case_label):
    """Assert SUT result == oracle result, both as Decimal."""
    try:
        sut_result = compute_cost(model, **sut_kwargs)
    except Exception as e:
        failures.append(f"{model}/{case_label}: SUT raised {type(e).__name__}: {e}")
        return
    try:
        oracle_result = oracle.compute_cost(model, **oracle_kwargs)
    except Exception as e:
        failures.append(f"{model}/{case_label}: oracle raised {type(e).__name__}: {e}")
        return

    # Oracle-derived expected value must be asserted Decimal before comparison
    if oracle_result is not None and not isinstance(oracle_result, Decimal):
        failures.append(f"{model}/{case_label}: oracle returned non-Decimal {type(oracle_result)}")
        return

    if sut_result is None and oracle_result is None:
        return  # both None: OK

    if sut_result is None and oracle_result is not None:
        failures.append(f"{model}/{case_label}: SUT returned None, oracle returned {oracle_result!r}")
        return

    if sut_result is not None and oracle_result is None:
        failures.append(f"{model}/{case_label}: SUT returned {sut_result!r}, oracle returned None")
        return

    if not isinstance(sut_result, Decimal):
        failures.append(f"{model}/{case_label}: SUT returned non-Decimal {type(sut_result).__name__}: {sut_result!r}")
        return

    if sut_result != oracle_result:
        failures.append(f"{model}/{case_label}: SUT={sut_result!r} != oracle={oracle_result!r}")

# Cases for each non-fast model
for model in sampled_models:
    # Case A: input-only
    inp = gen_tokens()
    check_parity(model,
        {"input_tokens": inp, "output_tokens": 0},
        {"input_tokens": inp, "output_tokens": 0},
        "input-only")

    # Case B: input+output
    inp, out = gen_tokens(), gen_tokens()
    check_parity(model,
        {"input_tokens": inp, "output_tokens": out},
        {"input_tokens": inp, "output_tokens": out},
        "input+output")

    # Case C: cache_read-heavy (SUT uses cache_read_tokens; oracle uses cache_read_input_tokens)
    inp, out, cr = gen_tokens(), gen_tokens(), gen_tokens()
    check_parity(model,
        {"input_tokens": inp, "output_tokens": out, "cache_read_tokens": cr},
        {"input_tokens": inp, "output_tokens": out, "cache_read_input_tokens": cr},
        "cache_read-heavy")

    # Case D: cache_write-heavy (SUT uses cache_write_tokens; oracle uses cache_creation_input_tokens)
    inp, out, cw = gen_tokens(), gen_tokens(), gen_tokens()
    check_parity(model,
        {"input_tokens": inp, "output_tokens": out, "cache_write_tokens": cw},
        {"input_tokens": inp, "output_tokens": out, "cache_creation_input_tokens": cw},
        "cache_write-heavy")

# Fast-eligible model: run ALL required cases (criteria: golden parity applies to every sampled model)
# Case A: input-only
inp_fa = gen_tokens()
check_parity(fast_model,
    {"input_tokens": inp_fa, "output_tokens": 0},
    {"input_tokens": inp_fa, "output_tokens": 0},
    "input-only")

# Case B: input+output (non-fast)
inp_fb, out_fb = gen_tokens(), gen_tokens()
check_parity(fast_model,
    {"input_tokens": inp_fb, "output_tokens": out_fb},
    {"input_tokens": inp_fb, "output_tokens": out_fb},
    "input+output-non-fast")

# Case C: cache_read-heavy
inp_fc, out_fc, cr_fc = gen_tokens(), gen_tokens(), gen_tokens()
check_parity(fast_model,
    {"input_tokens": inp_fc, "output_tokens": out_fc, "cache_read_tokens": cr_fc},
    {"input_tokens": inp_fc, "output_tokens": out_fc, "cache_read_input_tokens": cr_fc},
    "cache_read-heavy")

# Case D: cache_write-heavy
inp_fd, out_fd, cw_fd = gen_tokens(), gen_tokens(), gen_tokens()
check_parity(fast_model,
    {"input_tokens": inp_fd, "output_tokens": out_fd, "cache_write_tokens": cw_fd},
    {"input_tokens": inp_fd, "output_tokens": out_fd, "cache_creation_input_tokens": cw_fd},
    "cache_write-heavy")

# Case E: fast-mode (criteria requirement — only on fast-eligible model)
inp_fast, out_fast = gen_tokens(), gen_tokens()
check_parity(fast_model,
    {"input_tokens": inp_fast, "output_tokens": out_fast, "speed": "fast"},
    {"input_tokens": inp_fast, "output_tokens": out_fast, "speed": "fast"},
    "fast-mode")

if failures:
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    sys.exit(1)

print(f"AC-1 PASS: {len(sampled_models)} non-fast models + fast-eligible {fast_model!r} all passed parity")
PROBE_AC1

run_probe "AC-1" "unified_llm.compute_cost multi-model golden parity" "$TMPDIR_GATE/probe_ac1.py"

# ---------------------------------------------------------------------------
# AC-2: Honest None — unknown model and None-rate-dimension model
# ---------------------------------------------------------------------------
cat > "$TMPDIR_GATE/probe_ac2.py" << 'PROBE_AC2'
import sys, os, importlib.util, pathlib
from decimal import Decimal

gate_dir = pathlib.Path(os.environ["GATE_DIR"])
unified_llm_dir = os.environ["UNIFIED_LLM_DIR"]

oracle_path = gate_dir / "oracle.py"
spec = importlib.util.spec_from_file_location("_oracle", oracle_path)
oracle = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(oracle)
except Exception as e:
    print(f"INFRA-FAIL: oracle.py failed to load: {e}", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, unified_llm_dir)
try:
    import unified_llm
    compute_cost = getattr(unified_llm, "compute_cost", None)
    if compute_cost is None:
        print("FAIL AC-2: unified_llm.compute_cost not importable", file=sys.stderr)
        sys.exit(1)
except ImportError as e:
    print(f"FAIL AC-2: cannot import unified_llm: {e}", file=sys.stderr)
    sys.exit(1)

failures = []

# Sub-case A: Unknown model id (not in oracle _RATES)
# Gate confirms absence via oracle introspection (permitted by criteria)
unknown_model = "model-id-not-in-any-rate-table-xyz-20260101"
if unknown_model in oracle._RATES:
    print(f"INFRA-FAIL: test model {unknown_model!r} unexpectedly found in oracle._RATES", file=sys.stderr)
    sys.exit(2)

try:
    result_unknown = compute_cost(unknown_model, input_tokens=1000, output_tokens=200)
except Exception as e:
    failures.append(f"Unknown model: SUT raised {type(e).__name__}: {e} (expected None)")
    result_unknown = None

if result_unknown is not None:
    failures.append(f"Unknown model: expected None, got {result_unknown!r} (type={type(result_unknown).__name__})")
if result_unknown == 0:
    failures.append("Unknown model: returned 0 (must be None, not 0)")
if isinstance(result_unknown, float):
    failures.append("Unknown model: returned float (must be Decimal or None)")

print(f"  Unknown model -> {result_unknown!r} OK" if result_unknown is None else "")

# Sub-case B: Model with None rate dimension via public catalog surface only
# The criteria require reaching this case through unified_llm.list_models() or
# unified_llm.get_model_info() — never through private imports or monkey-patching.
list_models = getattr(unified_llm, "list_models", None)
if list_models is None:
    print("INFRA-FAIL: unified_llm.list_models not available (public catalog surface unavailable)", file=sys.stderr)
    sys.exit(2)

try:
    all_models = list_models()
except Exception as e:
    print(f"INFRA-FAIL: unified_llm.list_models() raised {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(2)

none_rate_model = None
for m in all_models:
    if m.input_cost_per_million is None or m.output_cost_per_million is None:
        none_rate_model = m.id
        print(f"  Found None-rate-dimension model in catalog: {none_rate_model!r}")
        break

if none_rate_model is None:
    # No model with None cost in catalog — gate-infrastructure failure per AC-2
    print("INFRA-FAIL: no model with a None cost field found via unified_llm.list_models().", file=sys.stderr)
    print("  The implementation must add a model entry with null cost fields to models.json,", file=sys.stderr)
    print("  or expose another public surface for this case.", file=sys.stderr)
    print("  This is a gate-infrastructure failure per AC-2 public-surface binding.", file=sys.stderr)
    sys.exit(2)

try:
    result_none_rate = compute_cost(none_rate_model, input_tokens=500, output_tokens=100)
except Exception as e:
    failures.append(f"None-rate-dimension model {none_rate_model!r}: SUT raised {type(e).__name__}: {e} (expected None)")
    result_none_rate = None

if result_none_rate is not None:
    failures.append(
        f"None-rate-dimension model {none_rate_model!r}: expected None (catalog has None cost field), "
        f"got {result_none_rate!r} (type={type(result_none_rate).__name__})"
    )
if result_none_rate == 0:
    failures.append("None-rate-dimension model: returned 0 (must be None)")
if isinstance(result_none_rate, float):
    failures.append("None-rate-dimension model: returned float (must be Decimal or None)")

if failures:
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    sys.exit(1)

print(f"AC-2 PASS: unknown model -> None, none-rate-dimension model ({none_rate_model!r}) -> None")
PROBE_AC2

run_probe "AC-2" "compute_cost returns None for unknown model and None-rate-dimension model" "$TMPDIR_GATE/probe_ac2.py"

# ---------------------------------------------------------------------------
# AC-3: Client.complete() response carries usage.cost_usd; streaming accumulator
# ---------------------------------------------------------------------------
cat > "$TMPDIR_GATE/probe_ac3.py" << 'PROBE_AC3'
import sys, os, importlib.util, pathlib, random, asyncio
from decimal import Decimal

gate_dir = pathlib.Path(os.environ["GATE_DIR"])
unified_llm_dir = os.environ["UNIFIED_LLM_DIR"]

oracle_path = gate_dir / "oracle.py"
spec = importlib.util.spec_from_file_location("_oracle", oracle_path)
oracle = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(oracle)
except Exception as e:
    print(f"INFRA-FAIL: oracle.py failed to load: {e}", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, unified_llm_dir)
try:
    import unified_llm
except ImportError as e:
    print(f"FAIL AC-3: cannot import unified_llm: {e}", file=sys.stderr)
    sys.exit(1)

compute_cost = getattr(unified_llm, "compute_cost", None)
if compute_cost is None:
    print("FAIL AC-3: unified_llm.compute_cost not importable (AC-1 prerequisite)", file=sys.stderr)
    sys.exit(1)

# Select a model known to oracle
test_model = "claude-sonnet-4-5-20250929"
if test_model not in oracle._RATES:
    print(f"INFRA-FAIL: test model {test_model!r} not in oracle._RATES", file=sys.stderr)
    sys.exit(2)

# Runtime-generated token counts (criteria: never fixed literals)
rng = random.Random()
inp_tokens = rng.randint(500, 20000)
out_tokens = rng.randint(100, 5000)

# Oracle-derived expected cost (asserted Decimal before comparison)
try:
    oracle_expected = oracle.compute_cost(test_model, input_tokens=inp_tokens, output_tokens=out_tokens)
except Exception as e:
    print(f"INFRA-FAIL: oracle.compute_cost raised {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(2)

if oracle_expected is None:
    print(f"INFRA-FAIL: oracle returned None for {test_model!r} with real tokens — rate table may be wrong", file=sys.stderr)
    sys.exit(2)
if not isinstance(oracle_expected, Decimal):
    print(f"INFRA-FAIL: oracle returned non-Decimal {type(oracle_expected)}", file=sys.stderr)
    sys.exit(2)

print(f"  oracle_expected for {test_model!r} inp={inp_tokens} out={out_tokens}: {oracle_expected!r}")

failures = []

# ---- Sub-case A: Client.complete() path ----
# Mock adapter returns Response with model id + token counts, NO cost_usd set
class MockAdapter:
    name = "mock"

    async def complete(self, request):
        return unified_llm.Response(
            id="mock-resp-ac3-complete",
            model=test_model,
            provider="mock",
            message=unified_llm.Message.assistant("hello"),
            finish_reason=unified_llm.FinishReason(reason="stop"),
            usage=unified_llm.Usage(
                input_tokens=inp_tokens,
                output_tokens=out_tokens,
                total_tokens=inp_tokens + out_tokens,
            ),
            # NOTE: no cost_usd — the SUT must compute it from model+tokens
        )

    async def stream(self, request):
        yield unified_llm.StreamEvent(
            type=unified_llm.StreamEventType.FINISH,
            finish_reason=unified_llm.FinishReason(reason="stop"),
            usage=unified_llm.Usage(
                input_tokens=inp_tokens,
                output_tokens=out_tokens,
                total_tokens=inp_tokens + out_tokens,
            ),
            response=unified_llm.Response(
                id="mock-stream-ac3",
                model=test_model,
                provider="mock",
                message=unified_llm.Message.assistant("hello"),
                finish_reason=unified_llm.FinishReason(reason="stop"),
                usage=unified_llm.Usage(
                    input_tokens=inp_tokens,
                    output_tokens=out_tokens,
                    total_tokens=inp_tokens + out_tokens,
                ),
            ),
        )

    async def close(self): pass
    async def initialize(self): pass
    def supports_tool_choice(self, mode): return False

async def run_complete_test():
    adapter = MockAdapter()
    client = unified_llm.Client(providers={"mock": adapter}, default_provider="mock")
    request = unified_llm.Request(
        model=test_model,
        provider="mock",
        messages=[unified_llm.Message.user("hello")],
    )
    return await client.complete(request)

try:
    response = asyncio.run(run_complete_test())
except Exception as e:
    failures.append(f"Client.complete() raised {type(e).__name__}: {e}")
    response = None

if response is not None:
    if not hasattr(response.usage, "cost_usd"):
        failures.append("Client.complete(): response.usage has no cost_usd attribute")
    else:
        sut_cost = response.usage.cost_usd
        if sut_cost is None:
            failures.append(f"Client.complete(): cost_usd is None for known model {test_model!r} (oracle expected {oracle_expected!r})")
        elif not isinstance(sut_cost, Decimal):
            failures.append(f"Client.complete(): cost_usd is not Decimal: {type(sut_cost).__name__}: {sut_cost!r}")
        elif sut_cost != oracle_expected:
            failures.append(f"Client.complete(): cost_usd={sut_cost!r} != oracle={oracle_expected!r}")
        else:
            print(f"  complete() path: cost_usd={sut_cost!r} == oracle={oracle_expected!r} OK")

# ---- Sub-case B: Streaming accumulator path ----
async def run_stream_test():
    adapter = MockAdapter()
    client = unified_llm.Client(providers={"mock": adapter}, default_provider="mock")
    request = unified_llm.Request(
        model=test_model,
        provider="mock",
        messages=[unified_llm.Message.user("hello")],
    )
    acc = unified_llm.StreamAccumulator()
    async for event in client.stream(request):
        acc.process(event)
    return acc.response()

try:
    stream_response = asyncio.run(run_stream_test())
except Exception as e:
    failures.append(f"Streaming accumulator raised {type(e).__name__}: {e}")
    stream_response = None

if stream_response is not None:
    if not hasattr(stream_response.usage, "cost_usd"):
        failures.append("Streaming accumulator: response.usage has no cost_usd attribute")
    else:
        sut_stream_cost = stream_response.usage.cost_usd
        if sut_stream_cost is None:
            failures.append(f"Streaming accumulator: cost_usd is None for known model {test_model!r} (oracle expected {oracle_expected!r})")
        elif not isinstance(sut_stream_cost, Decimal):
            failures.append(f"Streaming accumulator: cost_usd is not Decimal: {type(sut_stream_cost).__name__}: {sut_stream_cost!r}")
        elif sut_stream_cost != oracle_expected:
            failures.append(f"Streaming accumulator: cost_usd={sut_stream_cost!r} != oracle={oracle_expected!r}")
        else:
            print(f"  stream() path: cost_usd={sut_stream_cost!r} == oracle={oracle_expected!r} OK")

if failures:
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    sys.exit(1)

print("AC-3 PASS: Client.complete() and streaming accumulator both carry oracle-consistent cost_usd")
PROBE_AC3

run_probe "AC-3" "Client.complete() response.usage.cost_usd oracle-consistent; streaming accumulator" "$TMPDIR_GATE/probe_ac3.py"

# ---------------------------------------------------------------------------
# AC-4: DirectProviderBackend provider:response carries top-level cost_usd key
#        equal to response.usage.cost_usd (propagation, not recomputation)
# ---------------------------------------------------------------------------
cat > "$TMPDIR_GATE/probe_ac4.py" << 'PROBE_AC4'
import sys, os, importlib.util, pathlib, random, asyncio, types, dataclasses, typing
from decimal import Decimal

gate_dir = pathlib.Path(os.environ["GATE_DIR"])
unified_llm_dir = os.environ["UNIFIED_LLM_DIR"]
loop_pipeline_dir = os.environ["LOOP_PIPELINE_DIR"]

# Oracle is loaded only to verify the oracle infrastructure is sound.
# AC-4 does NOT require the backend to recompute cost from oracle rates;
# it only requires that payload['cost_usd'] == response.usage.cost_usd.
oracle_path = gate_dir / "oracle.py"
spec = importlib.util.spec_from_file_location("_oracle", oracle_path)
oracle = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(oracle)
except Exception as e:
    print(f"INFRA-FAIL: oracle.py failed to load: {e}", file=sys.stderr)
    sys.exit(2)

sys.path.insert(0, unified_llm_dir)
sys.path.insert(0, loop_pipeline_dir)

# Stub amplifier_core (required by loop-pipeline imports) if not already present
if "amplifier_core" not in sys.modules:
    @dataclasses.dataclass
    class _StubMessage:
        role: str = "user"
        content: typing.Any = ""
        tool_call_id: typing.Optional[str] = None
        name: typing.Optional[str] = None
        metadata: typing.Optional[dict] = None

    @dataclasses.dataclass
    class _StubChatRequest:
        messages: list = dataclasses.field(default_factory=list)
        tools: typing.Optional[list] = None
        tool_choice: typing.Optional[str] = None
        reasoning_effort: typing.Optional[str] = None

    _stub_core = types.ModuleType("amplifier_core")
    _stub_core.Message = _StubMessage
    _stub_core.ChatRequest = _StubChatRequest
    sys.modules["amplifier_core"] = _stub_core

    @dataclasses.dataclass
    class _StubToolCallBlock:
        id: str = ""
        name: str = ""
        input: dict = dataclasses.field(default_factory=dict)
        type: str = "tool_call"

    _stub_msg = types.ModuleType("amplifier_core.message_models")
    _stub_msg.ToolCallBlock = _StubToolCallBlock
    sys.modules["amplifier_core.message_models"] = _stub_msg

try:
    import unified_llm
    from amplifier_module_loop_pipeline import DirectProviderBackend
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.graph import Node
    from amplifier_module_loop_pipeline.pipeline_events import PROVIDER_RESPONSE
except ImportError as e:
    print(f"FAIL AC-4: import failed: {e}", file=sys.stderr)
    sys.exit(1)

# Verify Usage has cost_usd field (gates the probe; failure -> UNMET, not infra)
try:
    _u_test = unified_llm.Usage(input_tokens=1, output_tokens=1, total_tokens=2)
except Exception as e:
    print(f"INFRA-FAIL: Usage() construction raised {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(2)

if not hasattr(_u_test, "cost_usd"):
    print("FAIL AC-4: Usage has no cost_usd attribute — feature not built", file=sys.stderr)
    sys.exit(1)

# Generate a runtime random Decimal cost to place on the mock response's usage.
# The probe asserts propagation: payload['cost_usd'] must equal this value.
# This is NOT an oracle-computed value — it is the response's own cost_usd.
rng = random.Random()
_cost_cents = rng.randint(1, 99999)
known_cost_usd = Decimal(_cost_cents) / Decimal("1000000")  # e.g. Decimal("0.000042")

print(f"  Sub-case A: response.usage.cost_usd = {known_cost_usd!r} (Decimal, non-None)")

class RecordingHooks:
    def __init__(self):
        self.events = []

    async def emit(self, event_name, data):
        self.events.append((event_name, dict(data)))
        return type("R", (), {"action": "continue", "data": None})()

# ---- Sub-case A: response.usage.cost_usd is a known Decimal ----
# The mock client returns a response with cost_usd already set.
# AC-4 requires payload['cost_usd'] == response.usage.cost_usd.
# The backend must propagate the value, not recompute it.

class MockUnifiedClientDecimal:
    async def complete(self, request):
        return unified_llm.Response(
            id="mock-ac4-decimal",
            model="test-model-ac4",
            provider="mock",
            message=unified_llm.Message.assistant('{"status": "success"}'),
            finish_reason=unified_llm.FinishReason(reason="stop"),
            usage=unified_llm.Usage(
                input_tokens=rng.randint(100, 5000),
                output_tokens=rng.randint(10, 500),
                total_tokens=rng.randint(110, 5500),
                cost_usd=known_cost_usd,
            ),
        )
    async def stream(self, request):
        return
        yield

node_a = Node(
    id="test_node_ac4_" + str(rng.randint(10000, 99999)),
    shape="box",
    attrs={"llm_model": "test-model-ac4", "llm_provider": "mock"},
)

hooks_a = RecordingHooks()
backend_a = DirectProviderBackend(
    provider=object(),
    unified_client=MockUnifiedClientDecimal(),
    hooks=hooks_a,
)

async def run_a():
    try:
        await backend_a.run(node_a, "test prompt ac4-a", PipelineContext())
    except Exception:
        pass

try:
    asyncio.run(run_a())
except Exception as e:
    print(f"FAIL AC-4: backend.run() (sub-case A) raised {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)

pr_events_a = [(e, d) for e, d in hooks_a.events if e == PROVIDER_RESPONSE]
if not pr_events_a:
    print("FAIL AC-4: no provider:response event emitted (sub-case A)", file=sys.stderr)
    sys.exit(1)

_, payload_a = pr_events_a[0]

failures = []

# Assert top-level cost_usd key exists (not only nested in usage)
if "cost_usd" not in payload_a:
    failures.append(
        f"Sub-case A: provider:response payload missing top-level 'cost_usd' key. "
        f"Keys present: {list(payload_a.keys())}"
    )
else:
    sut_cost_a = payload_a["cost_usd"]
    if sut_cost_a != known_cost_usd:
        failures.append(
            f"Sub-case A: payload['cost_usd']={sut_cost_a!r} != "
            f"response.usage.cost_usd={known_cost_usd!r} (propagation failed)"
        )
    elif not isinstance(sut_cost_a, Decimal):
        failures.append(
            f"Sub-case A: payload['cost_usd'] is not Decimal: "
            f"{type(sut_cost_a).__name__}: {sut_cost_a!r}"
        )
    else:
        print(f"  Sub-case A PASS: payload['cost_usd']={sut_cost_a!r} == response.usage.cost_usd OK")

# Belt-and-suspenders: assert cost_usd is not ONLY nested in usage
usage_dict_a = payload_a.get("usage", {})
if "cost_usd" in usage_dict_a and "cost_usd" not in payload_a:
    failures.append("Sub-case A: cost_usd is nested inside usage only — must be at top level of payload")

# ---- Sub-case B: response.usage.cost_usd is None ----
# The mock client returns a response with cost_usd=None.
# AC-4: None is a legal value; an absent key is not.
# The backend must propagate None, not omit the key.

print(f"  Sub-case B: response.usage.cost_usd = None")

class MockUnifiedClientNone:
    async def complete(self, request):
        return unified_llm.Response(
            id="mock-ac4-none",
            model="test-model-ac4-none",
            provider="mock",
            message=unified_llm.Message.assistant('{"status": "success"}'),
            finish_reason=unified_llm.FinishReason(reason="stop"),
            usage=unified_llm.Usage(
                input_tokens=rng.randint(100, 5000),
                output_tokens=rng.randint(10, 500),
                total_tokens=rng.randint(110, 5500),
                cost_usd=None,
            ),
        )
    async def stream(self, request):
        return
        yield

node_b = Node(
    id="test_node_ac4_" + str(rng.randint(10000, 99999)),
    shape="box",
    attrs={"llm_model": "test-model-ac4-none", "llm_provider": "mock"},
)

hooks_b = RecordingHooks()
backend_b = DirectProviderBackend(
    provider=object(),
    unified_client=MockUnifiedClientNone(),
    hooks=hooks_b,
)

async def run_b():
    try:
        await backend_b.run(node_b, "test prompt ac4-b", PipelineContext())
    except Exception:
        pass

try:
    asyncio.run(run_b())
except Exception as e:
    print(f"FAIL AC-4: backend.run() (sub-case B) raised {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)

pr_events_b = [(e, d) for e, d in hooks_b.events if e == PROVIDER_RESPONSE]
if not pr_events_b:
    print("FAIL AC-4: no provider:response event emitted (sub-case B)", file=sys.stderr)
    sys.exit(1)

_, payload_b = pr_events_b[0]

if "cost_usd" not in payload_b:
    failures.append(
        f"Sub-case B: provider:response payload missing top-level 'cost_usd' key even when "
        f"response.usage.cost_usd=None. An absent key is not legal. "
        f"Keys present: {list(payload_b.keys())}"
    )
else:
    sut_cost_b = payload_b["cost_usd"]
    if sut_cost_b is not None:
        failures.append(
            f"Sub-case B: payload['cost_usd']={sut_cost_b!r}, expected None "
            f"(response.usage.cost_usd was None; propagation failed)"
        )
    else:
        print(f"  Sub-case B PASS: payload['cost_usd']=None when response.usage.cost_usd=None OK")

if failures:
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    sys.exit(1)

print("AC-4 PASS: provider:response carries top-level cost_usd key propagated from response.usage.cost_usd")
PROBE_AC4

run_probe "AC-4" "DirectProviderBackend provider:response carries top-level cost_usd key" "$TMPDIR_GATE/probe_ac4.py"

# ---------------------------------------------------------------------------
# AC-5: Usage addition — any None operand yields None cost_usd
# ---------------------------------------------------------------------------
cat > "$TMPDIR_GATE/probe_ac5.py" << 'PROBE_AC5'
import sys, os
from decimal import Decimal

unified_llm_dir = os.environ["UNIFIED_LLM_DIR"]
sys.path.insert(0, unified_llm_dir)

try:
    from unified_llm import Usage
except ImportError as e:
    print(f"FAIL AC-5: cannot import Usage from unified_llm: {e}", file=sys.stderr)
    sys.exit(1)

failures = []

# Verify cost_usd field exists on Usage (existence gates the probe, not the verdict)
try:
    u_test = Usage(input_tokens=1, output_tokens=1, total_tokens=2)
except Exception as e:
    print(f"INFRA-FAIL: Usage() construction raised {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(2)

if not hasattr(u_test, "cost_usd"):
    failures.append("Usage has no cost_usd attribute")
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    sys.exit(1)

# Sub-case A: Decimal + None = None
try:
    a = Usage(input_tokens=100, output_tokens=50, total_tokens=150, cost_usd=Decimal("0.01"))
    b = Usage(input_tokens=200, output_tokens=80, total_tokens=280, cost_usd=None)
    result_ab = a + b
except TypeError as e:
    failures.append(f"Decimal + None raised TypeError: {e}")
    result_ab = None
except Exception as e:
    failures.append(f"Decimal + None raised {type(e).__name__}: {e}")
    result_ab = None

if result_ab is not None:
    if result_ab.cost_usd is not None:
        failures.append(f"Decimal + None: cost_usd={result_ab.cost_usd!r}, expected None")
    else:
        print("  Decimal + None -> None OK")

# Sub-case B: None + Decimal = None
try:
    c = Usage(input_tokens=10, output_tokens=5, total_tokens=15, cost_usd=None)
    d = Usage(input_tokens=20, output_tokens=10, total_tokens=30, cost_usd=Decimal("0.005"))
    result_cd = c + d
except TypeError as e:
    failures.append(f"None + Decimal raised TypeError: {e}")
    result_cd = None
except Exception as e:
    failures.append(f"None + Decimal raised {type(e).__name__}: {e}")
    result_cd = None

if result_cd is not None:
    if result_cd.cost_usd is not None:
        failures.append(f"None + Decimal: cost_usd={result_cd.cost_usd!r}, expected None")
    else:
        print("  None + Decimal -> None OK")

# Sub-case C: None + None = None
try:
    e = Usage(input_tokens=1, output_tokens=1, total_tokens=2, cost_usd=None)
    f = Usage(input_tokens=1, output_tokens=1, total_tokens=2, cost_usd=None)
    result_ef = e + f
except TypeError as err:
    failures.append(f"None + None raised TypeError: {err}")
    result_ef = None
except Exception as err:
    failures.append(f"None + None raised {type(err).__name__}: {err}")
    result_ef = None

if result_ef is not None:
    if result_ef.cost_usd is not None:
        failures.append(f"None + None: cost_usd={result_ef.cost_usd!r}, expected None")
    else:
        print("  None + None -> None OK")

# Sub-case D: Decimal + Decimal = sum (Decimal)
try:
    g = Usage(input_tokens=100, output_tokens=50, total_tokens=150, cost_usd=Decimal("0.01"))
    h = Usage(input_tokens=200, output_tokens=80, total_tokens=280, cost_usd=Decimal("0.02"))
    result_gh = g + h
except TypeError as err:
    failures.append(f"Decimal + Decimal raised TypeError: {err}")
    result_gh = None
except Exception as err:
    failures.append(f"Decimal + Decimal raised {type(err).__name__}: {err}")
    result_gh = None

if result_gh is not None:
    expected_sum = Decimal("0.03")
    if result_gh.cost_usd is None:
        failures.append(f"Decimal + Decimal: cost_usd=None, expected {expected_sum!r}")
    elif not isinstance(result_gh.cost_usd, Decimal):
        failures.append(f"Decimal + Decimal: cost_usd is not Decimal: {type(result_gh.cost_usd).__name__}")
    elif result_gh.cost_usd != expected_sum:
        failures.append(f"Decimal + Decimal: cost_usd={result_gh.cost_usd!r}, expected {expected_sum!r}")
    else:
        print(f"  Decimal + Decimal -> {result_gh.cost_usd!r} OK")

if failures:
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    sys.exit(1)

print("AC-5 PASS: Usage addition with None operand yields None cost_usd; Decimal+Decimal sums correctly")
PROBE_AC5

run_probe "AC-5" "Usage addition: any None operand yields None cost_usd total" "$TMPDIR_GATE/probe_ac5.py"

# ---------------------------------------------------------------------------
# AC-6 [guard]: Existing token-field Usage arithmetic and provider:response fields unchanged
# ---------------------------------------------------------------------------
cat > "$TMPDIR_GATE/probe_ac6.py" << 'PROBE_AC6'
import sys, os, types, asyncio, dataclasses, typing
from decimal import Decimal

unified_llm_dir = os.environ["UNIFIED_LLM_DIR"]
loop_pipeline_dir = os.environ["LOOP_PIPELINE_DIR"]

sys.path.insert(0, unified_llm_dir)
sys.path.insert(0, loop_pipeline_dir)

# Stub amplifier_core
if "amplifier_core" not in sys.modules:
    @dataclasses.dataclass
    class _StubMessage:
        role: str = "user"
        content: typing.Any = ""
        tool_call_id: typing.Optional[str] = None
        name: typing.Optional[str] = None
        metadata: typing.Optional[dict] = None

    @dataclasses.dataclass
    class _StubChatRequest:
        messages: list = dataclasses.field(default_factory=list)
        tools: typing.Optional[list] = None
        tool_choice: typing.Optional[str] = None
        reasoning_effort: typing.Optional[str] = None

    _stub_core = types.ModuleType("amplifier_core")
    _stub_core.Message = _StubMessage
    _stub_core.ChatRequest = _StubChatRequest
    sys.modules["amplifier_core"] = _stub_core

    @dataclasses.dataclass
    class _StubToolCallBlock:
        id: str = ""
        name: str = ""
        input: dict = dataclasses.field(default_factory=dict)
        type: str = "tool_call"

    _stub_msg = types.ModuleType("amplifier_core.message_models")
    _stub_msg.ToolCallBlock = _StubToolCallBlock
    sys.modules["amplifier_core.message_models"] = _stub_msg

try:
    from unified_llm import Usage
    import unified_llm
    from amplifier_module_loop_pipeline import DirectProviderBackend
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.graph import Node
    from amplifier_module_loop_pipeline.pipeline_events import PROVIDER_RESPONSE
except ImportError as e:
    print(f"FAIL AC-6: import failed: {e}", file=sys.stderr)
    sys.exit(1)

failures = []

# ---- Guard A: Usage.__add__ for existing token fields ----
try:
    a = Usage(input_tokens=100, output_tokens=50, total_tokens=150)
    b = Usage(input_tokens=200, output_tokens=80, total_tokens=280)
    result = a + b
except Exception as e:
    failures.append(f"Usage add (basic) raised {type(e).__name__}: {e}")
    result = None

if result is not None:
    if result.input_tokens != 300:
        failures.append(f"Usage add: input_tokens={result.input_tokens}, expected 300")
    if result.output_tokens != 130:
        failures.append(f"Usage add: output_tokens={result.output_tokens}, expected 130")
    if result.total_tokens != 430:
        failures.append(f"Usage add: total_tokens={result.total_tokens}, expected 430")

# Optional fields: both None -> None (existing _add_optional semantics)
try:
    a2 = Usage(input_tokens=10, output_tokens=5, total_tokens=15)
    b2 = Usage(input_tokens=20, output_tokens=10, total_tokens=30)
    r2 = a2 + b2
except Exception as e:
    failures.append(f"Usage add (optional None+None) raised {type(e).__name__}: {e}")
    r2 = None

if r2 is not None:
    if r2.reasoning_tokens is not None:
        failures.append(f"Usage add: reasoning_tokens={r2.reasoning_tokens!r}, expected None")
    if r2.cache_read_tokens is not None:
        failures.append(f"Usage add: cache_read_tokens={r2.cache_read_tokens!r}, expected None")

# Optional fields: one non-None -> sum (treating None as 0) — existing _add_optional semantics
try:
    a3 = Usage(input_tokens=10, output_tokens=5, total_tokens=15, reasoning_tokens=100)
    b3 = Usage(input_tokens=20, output_tokens=10, total_tokens=30, reasoning_tokens=None)
    r3 = a3 + b3
except Exception as e:
    failures.append(f"Usage add (optional one-non-None) raised {type(e).__name__}: {e}")
    r3 = None

if r3 is not None:
    if r3.reasoning_tokens != 100:
        failures.append(f"Usage add: reasoning_tokens={r3.reasoning_tokens!r}, expected 100")

# cache_read_tokens: non-None + non-None -> sum (guard against regressing to always-None)
# Prescription (iteration 2 critique): 7 + 9 == 16
try:
    a4 = Usage(input_tokens=10, output_tokens=5, total_tokens=15, cache_read_tokens=7)
    b4 = Usage(input_tokens=20, output_tokens=10, total_tokens=30, cache_read_tokens=9)
    r4 = a4 + b4
except Exception as e:
    failures.append(f"Usage add (cache_read non-None+non-None) raised {type(e).__name__}: {e}")
    r4 = None

if r4 is not None:
    if r4.cache_read_tokens != 16:
        failures.append(f"Usage add: cache_read_tokens={r4.cache_read_tokens!r}, expected 16 (7+9)")
    else:
        print("  cache_read_tokens 7+9=16: OK")

# cache_write_tokens: non-None + non-None -> sum (guard against regressing to always-None)
# Prescription (iteration 2 critique): 8 + 10 == 18
try:
    a5 = Usage(input_tokens=10, output_tokens=5, total_tokens=15, cache_write_tokens=8)
    b5 = Usage(input_tokens=20, output_tokens=10, total_tokens=30, cache_write_tokens=10)
    r5 = a5 + b5
except Exception as e:
    failures.append(f"Usage add (cache_write non-None+non-None) raised {type(e).__name__}: {e}")
    r5 = None

if r5 is not None:
    if r5.cache_write_tokens != 18:
        failures.append(f"Usage add: cache_write_tokens={r5.cache_write_tokens!r}, expected 18 (8+10)")
    else:
        print("  cache_write_tokens 8+10=18: OK")

if not failures:
    print("  Usage token-field arithmetic: OK")

# ---- Guard B: provider:response payload still carries expected token fields ----
class RecordingHooksGuard:
    def __init__(self):
        self.events = []
    async def emit(self, event_name, data):
        self.events.append((event_name, dict(data)))
        return type("R", (), {"action": "continue", "data": None})()

class MockUnifiedClientGuard:
    async def complete(self, request):
        return unified_llm.Response(
            id="guard-resp-ac6",
            model="test-model-guard",
            provider="test",
            message=unified_llm.Message.assistant('{"status": "success"}'),
            finish_reason=unified_llm.FinishReason(reason="stop"),
            usage=unified_llm.Usage(
                input_tokens=10,
                output_tokens=20,
                total_tokens=30,
                cache_read_tokens=13,
                cache_write_tokens=17,
            ),
        )
    async def stream(self, request):
        return
        yield

node = Node(
    id="guard_node_ac6",
    shape="box",
    attrs={"llm_model": "test-model-guard", "llm_provider": "test"},
)

hooks = RecordingHooksGuard()
backend = DirectProviderBackend(
    provider=object(),
    unified_client=MockUnifiedClientGuard(),
    hooks=hooks,
)

async def run_guard():
    try:
        await backend.run(node, "guard prompt", PipelineContext())
    except Exception:
        pass

try:
    asyncio.run(run_guard())
except Exception as e:
    failures.append(f"backend.run() raised {type(e).__name__}: {e}")

pr_events = [(e, d) for e, d in hooks.events if e == PROVIDER_RESPONSE]
if not pr_events:
    failures.append("AC-6 guard: no provider:response event emitted by DirectProviderBackend")
else:
    _, payload = pr_events[0]
    usage = payload.get("usage", {})
    # Core token fields must be present and carry the correct values
    for key in ["input_tokens", "output_tokens", "total_tokens"]:
        if key not in usage:
            failures.append(f"AC-6 guard: provider:response usage missing key: {key!r}")
    if usage.get("input_tokens") != 10:
        failures.append(f"AC-6 guard: input_tokens={usage.get('input_tokens')!r}, expected 10")
    if usage.get("output_tokens") != 20:
        failures.append(f"AC-6 guard: output_tokens={usage.get('output_tokens')!r}, expected 20")
    if usage.get("total_tokens") != 30:
        failures.append(f"AC-6 guard: total_tokens={usage.get('total_tokens')!r}, expected 30")
    # Cache token fields must be present and carry the values set on the mock Usage
    # (AC-6 protects these existing fields from being silently dropped by the feature build)
    if "cache_read_tokens" not in usage:
        failures.append("AC-6 guard: provider:response usage missing key: 'cache_read_tokens'")
    elif usage.get("cache_read_tokens") != 13:
        failures.append(f"AC-6 guard: cache_read_tokens={usage.get('cache_read_tokens')!r}, expected 13")
    if "cache_write_tokens" not in usage:
        failures.append("AC-6 guard: provider:response usage missing key: 'cache_write_tokens'")
    elif usage.get("cache_write_tokens") != 17:
        failures.append(f"AC-6 guard: cache_write_tokens={usage.get('cache_write_tokens')!r}, expected 17")
    if "finish_reason" not in payload:
        failures.append("AC-6 guard: provider:response missing finish_reason key")
    if "step_count" not in payload:
        failures.append("AC-6 guard: provider:response missing step_count key")
    if not failures:
        print(f"  provider:response token fields: OK (keys={list(payload.keys())})")

if failures:
    for f in failures:
        print(f"FAIL: {f}", file=sys.stderr)
    sys.exit(1)

print("AC-6 PASS [guard]: existing Usage arithmetic and provider:response token fields unchanged")
PROBE_AC6

run_probe "AC-6" "Existing Usage arithmetic and provider:response token fields unchanged" "$TMPDIR_GATE/probe_ac6.py"

# ---------------------------------------------------------------------------
# Write census file (TYPED VERDICT CHANNEL — must be written on every run)
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$CENSUS_FILE")"
{
    echo "AC-1: ${AC_RESULT[AC-1]}"
    echo "AC-2: ${AC_RESULT[AC-2]}"
    echo "AC-3: ${AC_RESULT[AC-3]}"
    echo "AC-4: ${AC_RESULT[AC-4]}"
    echo "AC-5: ${AC_RESULT[AC-5]}"
    echo "AC-6: ${AC_RESULT[AC-6]}"
} > "$CENSUS_FILE"

echo ""
echo "========================================"
echo "GATE SUMMARY"
echo "========================================"
echo "  PASS:       $PASS_COUNT"
echo "  FAIL:       $FAIL_COUNT"
echo "  INFRA-FAIL: $INFRA_FAIL_COUNT"
echo "========================================"
echo "Census:"
cat "$CENSUS_FILE"
echo "========================================"

# ---------------------------------------------------------------------------
# Exit code must be coherent with census:
#   exit 0 <=> every row MET
#   exit 1 <=> at least one row UNMET (including infra failures, which are UNMET)
# ---------------------------------------------------------------------------
ALL_MET=true
while IFS= read -r line; do
    if [[ "$line" == *": UNMET" ]]; then
        ALL_MET=false
        break
    fi
done < "$CENSUS_FILE"

if [[ "$ALL_MET" == "true" ]]; then
    echo "GATE RESULT: ALL PASS (exit 0)"
    exit 0
else
    echo "GATE RESULT: FAIL (exit 1)"
    exit 1
fi
