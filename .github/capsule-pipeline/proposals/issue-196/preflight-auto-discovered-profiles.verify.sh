set -euo pipefail

# ---------------------------------------------------------------------------
# Gate: provider preflight false refusal on auto-discovered profiles
#
# Verifies that PipelineOrchestrator.execute() does NOT raise
# ProviderPreflightError when the only profiles available are auto-discovered
# from coordinator.config["agents"] (no explicit "profiles" in config).
#
# Exit codes:
#   0  = defect NOT present (fixed, or gate does not capture it)
#   1  = defect IS present (assertion failure; diagnostics printed)
#   2  = infrastructure problem (missing tooling, etc.)
#
# RED ATTRIBUTION -- THIS GATE NEVER PRINTS THE DECLARED red_signal ITSELF.
# The substring that attributes the red belongs to the code under test: it
# reaches the log only because Part 1 echoes the preflight's own
# ProviderPreflightError text VERBATIM.  A red raised by anything else (an
# over-broad patch, a broken regression suite, an unrelated failure) therefore
# CANNOT carry the signal -- which is the only thing that makes checking for
# the signal mean anything at all.  Do NOT re-introduce a hardcoded echo of
# it at any exit-1 site; each site says what actually failed instead.
# ---------------------------------------------------------------------------

REPO_ROOT="$(pwd)"
MODULE_DIR="$REPO_ROOT/modules/loop-pipeline"

# --- Infrastructure guards --------------------------------------------------

if ! command -v python3 >/dev/null 2>&1; then
    echo "INFRA: python3 not found" >&2
    exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "INFRA: uv not found" >&2
    exit 2
fi

if [ ! -d "$MODULE_DIR" ]; then
    echo "INFRA: module directory not found: $MODULE_DIR" >&2
    exit 2
fi

if [ ! -f "$MODULE_DIR/pyproject.toml" ]; then
    echo "INFRA: pyproject.toml not found in $MODULE_DIR" >&2
    exit 2
fi

# ---------------------------------------------------------------------------
# Probe identities: ONE generator, indistinguishable shapes, DUAL ROLES
#
# Every provider name this gate uses -- in the must-ACCEPT scenarios and in
# the must-REFUSE scenarios alike -- comes from the single generator below:
# nine uniformly random lowercase letters (the FIRST letter included) plus
# the run's shared numeric suffix.  No fixed prefix, no per-part alphabet, no
# shape difference of any kind between an accept-case name and a refuse-case
# name.
#
# WHY (post-review hardening): the earlier revision drew must-ACCEPT names
# from a fixed "p" prefix and must-REFUSE names from fixed "q"/"r"/"s"
# prefixes.  Those disjoint prefixes leaked the expected verdict through the
# NAME, so a patch that merely accepted any provider matching p* greened
# every part of this gate without implementing the auto-discovery rule at
# all.  Randomizing the first letter alone would only make that dodge
# improbable, so the fix goes further:
#
#   EACH GENERATED NAME MUST BE ACCEPTED IN ONE SCENARIO AND REFUSED IN
#   ANOTHER.
#
#     NAME_1 -- ACCEPTED in Part 1 (an agent of that name exists)
#            -- REFUSED  in Part 2 (no agent of that name exists)
#     NAME_2 -- ACCEPTED in Part 2 (an agent of that name exists)
#            -- REFUSED  in Part 3 (no agent of that name exists)
#
# The name string therefore carries ZERO information about the required
# verdict: any decision that is a function of the name alone -- a prefix
# rule, a suffix rule, a length rule, a hardcoded literal, a regex, an
# allow-list -- is forced to get one of the two scenarios wrong.  The ONLY
# feature distinguishing an accept-case from a refuse-case here is the real
# rule under test: does a matching auto-discovered agent exist in
# coordinator.config["agents"]?
#
# This is a property of the probes' INPUTS, not an inspection of how any fix
# decides: the gate still only ever observes whether the run was refused.
# ---------------------------------------------------------------------------

RAND_SUFFIX="${RANDOM}${RANDOM}"

_PROBE_ALPHABET="abcdefghijklmnopqrstuvwxyz"

# The one and only source of provider names in this gate.
gen_provider_name() {
    local _name=""
    local _i
    for _i in 1 2 3 4 5 6 7 8 9; do
        _name="${_name}${_PROBE_ALPHABET:$((RANDOM % 26)):1}"
    done
    printf '%s%s' "$_name" "$RAND_SUFFIX"
}

NAME_1="$(gen_provider_name)"
NAME_2="$(gen_provider_name)"
while [ "$NAME_2" = "$NAME_1" ]; do
    NAME_2="$(gen_provider_name)"
done

# Self-check: both names must be shape-identical.  A failure here means this
# script has started leaking expectations through the name again -- an
# authoring defect in the gate, not a verdict about the code under test, so
# it exits 2 rather than 1.
for _probe_name in "$NAME_1" "$NAME_2"; do
    if ! [[ "$_probe_name" =~ ^[a-z][a-z][a-z][a-z][a-z][a-z][a-z][a-z][a-z][0-9]+$ ]]; then
        echo "INFRA: generated probe name '$_probe_name' does not match the single expected shape" >&2
        exit 2
    fi
done
if [ "${#NAME_1}" -ne "${#NAME_2}" ]; then
    echo "INFRA: generated probe names differ in length (${#NAME_1} vs ${#NAME_2})" >&2
    exit 2
fi

echo "--- probe identities (one generator; roles assigned by SCENARIO, never by name) ---"
echo "NAME_1=$NAME_1  -> must be ACCEPTED in Part 1, REFUSED in Part 2"
echo "NAME_2=$NAME_2  -> must be ACCEPTED in Part 2, REFUSED in Part 3"
echo ""

# ---------------------------------------------------------------------------
# Part 1: Behavioral probe -- does the bug reproduce?
#
# Construct the exact trigger scenario from the issue:
#   - No "profiles" key in orchestrator config
#   - Coordinator with session.spawn and coordinator.config["agents"] containing
#     an agent whose name matches the declared llm_provider
#   - At least one provider mounted (so simulation mode is NOT triggered)
#   - A graph node declares llm_provider matching the agent name
#
# The agent/provider name is NAME_1: runtime-generated, semantically neutral,
# and -- per the header above -- required to be REFUSED in Part 2, so no patch
# can green this part by recognizing the name.  Because the name is unknown to
# preflight's PROVIDER_KEY_ENV map, no credential env var is required: the
# credential check passes automatically for unknown providers.  The ONLY
# possible cause of a ProviderPreflightError in this scenario is the bug --
# the preflight not seeing the auto-discovered profile.
#
# At the buggy SHA: ProviderPreflightError raised (run refused at startup)
# After a correct fix: no ProviderPreflightError -- run proceeds past startup
# ---------------------------------------------------------------------------

PROBE_SCRIPT="$(mktemp /tmp/gate_probe_XXXXXX.py)"
trap 'rm -f "$PROBE_SCRIPT"' EXIT

cat > "$PROBE_SCRIPT" << PYEOF
import sys
import os
import asyncio

# Load from the repo tree, not from any ambient install
sys.path.insert(0, "$MODULE_DIR")

# Produced by the gate's single provider-name generator (see the script
# header): shape-identical to every refuse-case name, and required to be
# refused itself in Part 2.
PROV_NAME = "${NAME_1}"

GRAPH_NAME = "g${RAND_SUFFIX}"
NODE_NAME = "n${RAND_SUFFIX}"

DOT_SOURCE = f"""
digraph {GRAPH_NAME} {{
    graph [goal="preflight probe"]
    start [shape=Mdiamond]
    {NODE_NAME} [shape=box, llm_provider="{PROV_NAME}", prompt="probe task"]
    done [shape=Msquare]
    start -> {NODE_NAME} -> done
}}
"""

# Coordinator with session.spawn and an agent whose name exactly matches
# PROV_NAME -- the auto-discovery path maps this as profiles[PROV_NAME] = PROV_NAME.
class FakeCoordinator:
    def __init__(self, agent_name):
        self.config = {
            "agents": {
                agent_name: {"session": {"orchestrator": {"module": "loop-agent"}}},
            }
        }

    def get_capability(self, name):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs):
        return {"output": '{"status": "success", "notes": "probe spawn"}', "session_id": "s-probe"}

class FakeProvider:
    pass

async def run_probe():
    try:
        from amplifier_module_loop_pipeline import PipelineOrchestrator
        from amplifier_module_loop_pipeline.preflight import ProviderPreflightError
    except ImportError as e:
        print(f"INFRA: import failed: {e}", file=sys.stderr)
        sys.exit(2)

    orchestrator = PipelineOrchestrator(
        config={"dot_source": DOT_SOURCE}
        # Deliberately NO "profiles" key -- auto-discovery path only
    )
    # Coordinator has exactly one agent named PROV_NAME -- the auto-discovery
    # path would map profiles[PROV_NAME] = PROV_NAME if the preflight sees it.
    coordinator = FakeCoordinator(PROV_NAME)

    try:
        # providers={"anthropic": FakeProvider()} ensures mounted_providers is
        # non-empty, so simulation mode is NOT triggered and the preflight runs.
        await orchestrator.execute(
            prompt="probe goal",
            context=None,
            providers={"anthropic": FakeProvider()},
            tools={},
            hooks=None,
            coordinator=coordinator,
        )
        # Reached here: preflight did NOT refuse -- defect is NOT present
        print("PROBE_RESULT: no_false_refusal")
        sys.exit(0)
    except ProviderPreflightError as e:
        # ANY ProviderPreflightError in this scenario is the reported defect:
        # the run was refused when it should not have been.  PROV_NAME is not
        # in PROVIDER_KEY_ENV so no credential check applies -- the ONLY reason
        # for refusal is the bug (preflight does not see the auto-discovered profile).
        msg = str(e)
        print("PROBE_RESULT: false_refusal detected -- run refused when it should not be")
        print(f"ASSERTION FAILED (Part 1): an agent named '{PROV_NAME}' exists in")
        print('  coordinator.config["agents"], so _build_backend() would auto-discover a')
        print("  profile for it -- yet execute()'s preflight refused the run at startup.")
        print("  The preflight's own refusal text follows VERBATIM; this gate does not")
        print("  compose it, and prints no diagnostic substring of its own here:")
        print(f"ERROR: {msg}")
        sys.exit(1)
    except Exception as e:
        # An exception from the code under test that is not ProviderPreflightError
        # means the preflight passed (the bug is fixed) but the pipeline failed
        # for another reason (e.g. fake provider doesn't implement the full
        # interface).  This is not the reported defect -- exit 0.
        print(f"PROBE_RESULT: no_false_refusal (preflight passed; pipeline raised {type(e).__name__})")
        sys.exit(0)

asyncio.run(run_probe())
PYEOF

echo "--- Part 1: behavioral probe (false refusal on auto-discovered profile) ---"
PROBE_RC=0

set +e
PROBE_OUTPUT=$(cd "$MODULE_DIR" && uv run --project "$MODULE_DIR" python3 "$PROBE_SCRIPT" 2>&1)
PROBE_RC=$?
set -e

echo "$PROBE_OUTPUT"

if [ "$PROBE_RC" -eq 2 ]; then
    echo "INFRA: probe script reported infrastructure failure" >&2
    exit 2
fi

if [ "$PROBE_RC" -eq 1 ]; then
    # Defect is present.  The attributing signal is already in the output
    # above, quoted from the preflight's own exception -- deliberately NOT
    # re-printed here (see RED ATTRIBUTION in the header).
    echo "Part 1 FAILED: the run was refused although a matching auto-discovered agent exists."
    exit 1
fi

# ---------------------------------------------------------------------------
# Part 2: Mixed-scope probe -- per-item selectivity
#
# When the coordinator has an agent matching one declared provider but the
# graph also has a node declaring a DIFFERENT provider with no matching agent
# and no mounted provider, the preflight must still refuse for that unmatched
# provider.  This confirms the fix is not a whole-scope suppression.
#
# Graph: two nodes -- one declares NAME_2 (has a matching agent, should pass),
# one declares NAME_1 (no agent, no mount, should be refused).  After a
# correct fix a ProviderPreflightError is raised for NAME_1, not for NAME_2.
#
# NOTE THE ROLE SWAP: NAME_1 was the must-ACCEPT name in Part 1 and is the
# must-REFUSE name here, with only the presence of a matching agent changed.
# A patch keyed on the provider NAME rather than on the auto-discovery rule
# cannot satisfy both parts.
#
# CRITICAL: The caught ProviderPreflightError's message must NOT mention
# NAME_2 -- if it does, the matched item was wrongly refused, which means the
# fix did not implement per-item selectivity (e.g. it suppresses all preflight
# checks when the graph has more than one provider).
# ---------------------------------------------------------------------------

MIXED_PROBE_SCRIPT="$(mktemp /tmp/gate_mixed_XXXXXX.py)"
trap 'rm -f "$PROBE_SCRIPT" "$MIXED_PROBE_SCRIPT"' EXIT

cat > "$MIXED_PROBE_SCRIPT" << PYEOF2
import sys
import os
import asyncio

sys.path.insert(0, "$MODULE_DIR")

# Both names come from the gate's single generator.  NAME_2 plays the accept
# role here and the refuse role in Part 3; NAME_1 played the accept role in
# Part 1 and plays the refuse role here.
PROV_MATCHED = "${NAME_2}"
PROV_UNMATCHED = "${NAME_1}"

GRAPH_NAME = "mg${RAND_SUFFIX}"
NODE_A = "na${RAND_SUFFIX}"
NODE_B = "nb${RAND_SUFFIX}"

DOT_SOURCE = f"""
digraph {GRAPH_NAME} {{
    graph [goal="mixed probe"]
    start [shape=Mdiamond]
    {NODE_A} [shape=box, llm_provider="{PROV_MATCHED}", prompt="task a"]
    {NODE_B} [shape=box, llm_provider="{PROV_UNMATCHED}", prompt="task b"]
    done [shape=Msquare]
    start -> {NODE_A} -> {NODE_B} -> done
}}
"""

class FakeCoordinator:
    def __init__(self, matched_name):
        self.config = {
            "agents": {
                matched_name: {"session": {"orchestrator": {"module": "loop-agent"}}},
                # No agent named PROV_UNMATCHED
            }
        }

    def get_capability(self, name):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs):
        return {"output": '{"status": "success"}', "session_id": "s-mixed"}

class FakeProvider:
    pass

async def run_mixed_probe():
    try:
        from amplifier_module_loop_pipeline import PipelineOrchestrator
        from amplifier_module_loop_pipeline.preflight import ProviderPreflightError
    except ImportError as e:
        print(f"INFRA: import failed: {e}", file=sys.stderr)
        sys.exit(2)

    orchestrator = PipelineOrchestrator(
        config={"dot_source": DOT_SOURCE}
        # No "profiles" -- auto-discovery path
    )
    coordinator = FakeCoordinator(PROV_MATCHED)

    try:
        await orchestrator.execute(
            prompt="mixed probe goal",
            context=None,
            providers={"anthropic": FakeProvider()},
            tools={},
            hooks=None,
            coordinator=coordinator,
        )
        # No exception: the unmatched provider was NOT refused.
        # A correct fix should still refuse the unmatched provider.
        # Reaching here means the fix suppressed the entire preflight.
        print("MIXED_PROBE_RESULT: unmatched provider not refused (fix over-suppressed preflight)")
        print(f"ASSERTION FAILED (Part 2): no agent named '{PROV_UNMATCHED}' exists and no")
        print("  provider module is mounted under that name, yet the run started anyway --")
        print("  the preflight was suppressed for the whole graph instead of per item.")
        sys.exit(1)
    except ProviderPreflightError as e:
        msg = str(e)
        # A ProviderPreflightError was raised -- check WHICH provider it names.
        # A correct fix refuses ONLY PROV_UNMATCHED; PROV_MATCHED has a matching
        # agent and must NOT appear in the refusal.
        # If PROV_MATCHED appears in the message, the matched item was wrongly
        # refused (per-item selectivity is broken -- e.g. the fix suppresses
        # auto-discovery for multi-provider graphs).
        print("MIXED_PROBE_RESULT: ProviderPreflightError raised")
        print(f"Refusal message: {msg}")
        if PROV_MATCHED in msg:
            # The matched provider was refused -- this is the defect still present
            print(f"ASSERTION FAILED (Part 2): the refusal names '{PROV_MATCHED}', which HAS a")
            print('  matching agent in coordinator.config["agents"] -- the matched')
            print("  auto-discovered profile was NOT accepted, so per-item selectivity is broken.")
            sys.exit(1)
        if PROV_UNMATCHED not in msg:
            # Sanity: the unmatched provider must appear in the refusal
            print(f"ASSERTION FAILED (Part 2): the refusal does not name '{PROV_UNMATCHED}', the")
            print("  one provider in this graph with no agent and no mounted module.")
            sys.exit(1)
        # Correct: PROV_UNMATCHED is refused, PROV_MATCHED is not mentioned
        print("MIXED_PROBE_RESULT: correct -- only unmatched provider refused, matched accepted")
        sys.exit(0)
    except Exception as e:
        print(f"INFRA: unexpected exception: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)

asyncio.run(run_mixed_probe())
PYEOF2

echo ""
echo "--- Part 2: mixed-scope probe (per-item selectivity) ---"
MIXED_RC=0

set +e
MIXED_OUTPUT=$(cd "$MODULE_DIR" && uv run --project "$MODULE_DIR" python3 "$MIXED_PROBE_SCRIPT" 2>&1)
MIXED_RC=$?
set -e

echo "$MIXED_OUTPUT"

if [ "$MIXED_RC" -eq 2 ]; then
    echo "INFRA: mixed-scope probe reported infrastructure failure" >&2
    exit 2
fi

if [ "$MIXED_RC" -eq 1 ]; then
    echo "Part 2 FAILED: per-item selectivity is not preserved (see the assertion above)."
    exit 1
fi

# ---------------------------------------------------------------------------
# Part 3: Negative probe -- provider with NO matching agent must refuse
#
# Confirms that profile binding is preserved: when a node declares a provider
# that has no matching agent in the coordinator (and is not mounted), the
# preflight must still refuse.  A correct fix makes the preflight aware of
# auto-discovered profiles -- it does NOT disable profile binding entirely.
#
#   - Node declares llm_provider=NAME_2 -- the very name Part 2 required the
#     gate to ACCEPT, with the presence of a matching agent the only change
#   - Coordinator has session.spawn but only an agent named NAME_1
#     (no agent for NAME_2)
#   - At least one provider mounted (so simulation mode is NOT triggered)
#
# A correct fix: ProviderPreflightError raised (no agent/profile for NAME_2)
# A void/over-broad patch: no refusal (greens incorrectly)
# ---------------------------------------------------------------------------

NEG_PROBE_SCRIPT="$(mktemp /tmp/gate_neg_XXXXXX.py)"
trap 'rm -f "$PROBE_SCRIPT" "$MIXED_PROBE_SCRIPT" "$NEG_PROBE_SCRIPT"' EXIT

cat > "$NEG_PROBE_SCRIPT" << PYEOF3
import sys
import os
import asyncio

sys.path.insert(0, "$MODULE_DIR")

# The same two generated names, roles swapped once more: NAME_2 was the
# must-ACCEPT name in Part 2 and is the must-REFUSE name here; NAME_1 is the
# coordinator's unrelated agent.
PROV_DECL = "${NAME_2}"
PROV_OTHER = "${NAME_1}"

GRAPH_NAME = "ng${RAND_SUFFIX}"
NODE_NAME = "nn${RAND_SUFFIX}"

DOT_SOURCE = f"""
digraph {GRAPH_NAME} {{
    graph [goal="negative probe"]
    start [shape=Mdiamond]
    {NODE_NAME} [shape=box, llm_provider="{PROV_DECL}", prompt="probe task"]
    done [shape=Msquare]
    start -> {NODE_NAME} -> done
}}
"""

# Coordinator with session.spawn but only an agent named PROV_OTHER.
# The node declares PROV_DECL, which has no matching agent -- must be refused.
class FakeCoordinatorNoMatch:
    def __init__(self, other_name):
        self.config = {
            "agents": {
                other_name: {"session": {"orchestrator": {"module": "loop-agent"}}},
                # Deliberately NO agent named PROV_DECL
            }
        }

    def get_capability(self, name):
        if name == "session.spawn":
            return self._spawn_fn
        return None

    async def _spawn_fn(self, **kwargs):
        return {"output": '{"status": "success"}', "session_id": "s-neg"}

class FakeProvider:
    pass

async def run_neg_probe():
    try:
        from amplifier_module_loop_pipeline import PipelineOrchestrator
        from amplifier_module_loop_pipeline.preflight import ProviderPreflightError
    except ImportError as e:
        print(f"INFRA: import failed: {e}", file=sys.stderr)
        sys.exit(2)

    orchestrator = PipelineOrchestrator(
        config={"dot_source": DOT_SOURCE}
        # No "profiles" -- auto-discovery path only
    )
    coordinator = FakeCoordinatorNoMatch(PROV_OTHER)

    try:
        # providers={"anthropic": FakeProvider()} keeps preflight active.
        # The coordinator has an agent for PROV_OTHER but NOT for PROV_DECL.
        # The node declares llm_provider=PROV_DECL, which has no agent/profile.
        await orchestrator.execute(
            prompt="negative probe goal",
            context=None,
            providers={"anthropic": FakeProvider()},
            tools={},
            hooks=None,
            coordinator=coordinator,
        )
        # No exception: the unmatched provider was NOT refused.
        # A correct fix must refuse here because there is no agent/profile for PROV_DECL.
        # An over-broad fix that disables all preflight checks would reach here.
        print("NEG_PROBE_RESULT: provider not refused despite no matching agent (over-broad fix)")
        print(f"ASSERTION FAILED (Part 3): the only agent is '{PROV_OTHER}', so nothing can")
        print(f"  serve '{PROV_DECL}' -- yet the run started. Profile binding was disabled")
        print("  rather than extended to auto-discovered profiles.")
        sys.exit(1)
    except ProviderPreflightError as e:
        msg = str(e)
        # Correct outcome: preflight refuses because there is no agent/profile for PROV_DECL.
        print("NEG_PROBE_RESULT: correct -- unmatched provider refused as expected")
        print(f"Refusal message: {msg}")
        sys.exit(0)
    except Exception as e:
        print(f"INFRA: unexpected exception: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)

asyncio.run(run_neg_probe())
PYEOF3

echo ""
echo "--- Part 3: negative probe (provider with no matching agent must refuse) ---"
NEG_RC=0

set +e
NEG_OUTPUT=$(cd "$MODULE_DIR" && uv run --project "$MODULE_DIR" python3 "$NEG_PROBE_SCRIPT" 2>&1)
NEG_RC=$?
set -e

echo "$NEG_OUTPUT"

if [ "$NEG_RC" -eq 2 ]; then
    echo "INFRA: negative probe reported infrastructure failure" >&2
    exit 2
fi

if [ "$NEG_RC" -eq 1 ]; then
    # Over-broad fix detected: provider passed without a matching agent.
    # This is the reported defect's required profile-binding guarantee.
    echo "Part 3 FAILED: profile binding was not preserved (see the assertion above)."
    exit 1
fi

# ---------------------------------------------------------------------------
# Part 4: Regression test suite
#
# A correct fix includes a regression test in the repo's test suite.  We run
# the full loop-pipeline test suite (excluding test_remote_dot.py, which
# requires an optional module not in this module's declared dependencies) and
# check that it passes.
#
# This check is purely behavioral: any regression test added anywhere in the
# suite that passes is sufficient.  We do not require the test to be in a
# specific file or to have a specific source shape.
#
# test_remote_dot.py is excluded because it imports amplifier_module_remote_source,
# which is not a declared dependency of loop-pipeline; its collection fails on
# a fresh clone and is unrelated to this defect.
# ---------------------------------------------------------------------------

echo ""
echo "--- Part 4: regression test suite (loop-pipeline tests must pass) ---"
SUITE_RC=0

set +e
SUITE_OUTPUT=$(cd "$MODULE_DIR" && uv run --project "$MODULE_DIR" pytest tests/ --ignore=tests/test_remote_dot.py -q 2>&1)
SUITE_RC=$?
set -e

echo "$SUITE_OUTPUT"

if [ "$SUITE_RC" -ne 0 ]; then
    echo "Part 4 FAILED: the loop-pipeline regression test suite reported failures (see the"
    echo "  pytest output above for the failing tests). This is a SUITE failure, not the"
    echo "  reported preflight refusal -- and it deliberately carries none of that refusal's"
    echo "  wording, so this red cannot be mistaken for the reported defect."
    exit 1
fi

echo ""
echo "All checks passed: defect is NOT present."
exit 0
