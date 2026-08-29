"""Live end-to-end DOT pipeline test against real APIs."""

import asyncio
import sys
import tempfile
import time

# Modules installed via: uv pip install -e ./amplifier-module-loop-pipeline -e ./unified-llm-client
# into the amplifier tool env at ~/.local/share/uv/tools/amplifier/

DOT_SIMPLE = r"""
digraph SimplePipeline {
    graph [goal="Write a Python one-liner that prints the first 10 Fibonacci numbers"]

    start     [shape=Mdiamond]
    implement [shape=box, prompt="Write a Python script that does: $goal\n\nRespond with ONLY the code, no explanation.", llm_provider="anthropic", llm_model="claude-sonnet-4-20250514"]
    done      [shape=Msquare]

    start -> implement -> done
}
"""

DOT_PLAN_IMPLEMENT_REVIEW = r"""
digraph plan_implement_review {
    graph [goal="Create and validate a Python script"]

    start     [shape=Mdiamond]
    plan      [shape=box, prompt="List the steps needed to create a Python script that defines an add(a,b) function and tests it. Be brief - just list 2-3 steps.", llm_provider="anthropic", llm_model="claude-sonnet-4-20250514"]
    implement [shape=box, prompt="Create test_math.py with a function add(a,b) that returns a+b, and a main block that asserts add(1,2)==3 and prints 'Tests passed'. Respond with ONLY the code.", llm_provider="anthropic", llm_model="claude-sonnet-4-20250514", goal_gate=true]
    validate  [shape=box, prompt="Review the code from the previous stage. Does it define add(a,b) and test it? Report success or failure.", llm_provider="anthropic", llm_model="claude-sonnet-4-20250514"]
    done      [shape=Msquare]

    start -> plan -> implement -> validate -> done
}
"""

DOT_CONDITIONAL = r"""
digraph conditional_test {
    graph [goal="Create and validate a Python function"]

    start     [shape=Mdiamond]
    implement [shape=box, prompt="Create a Python function multiply(a,b) that returns a*b. Show that multiply(3,4)==12. Respond with ONLY the code and result.", llm_provider="anthropic", llm_model="claude-sonnet-4-20250514"]
    test      [shape=box, prompt="Review the previous output. Did it correctly show multiply(3,4)==12? Report success or failure.", llm_provider="anthropic", llm_model="claude-sonnet-4-20250514"]
    gate      [shape=diamond, label="Tests pass?"]
    done      [shape=Msquare]

    start -> implement -> test -> gate
    gate -> done      [condition="outcome=success", weight=1]
    gate -> implement [condition="outcome!=success", label="Retry", weight=0]
}
"""

DOT_MULTI_PROVIDER = r"""
digraph MultiProvider {
    graph [goal="Explain what a monad is"]

    start   [shape=Mdiamond]
    draft   [shape=box, prompt="Write a brief 2-sentence explanation of: $goal", llm_provider="anthropic", llm_model="claude-sonnet-4-20250514"]
    review  [shape=box, prompt="Review and improve this explanation, keeping it under 3 sentences. The draft:\n\n$context", llm_provider="openai", llm_model="gpt-4.1-mini"]
    done    [shape=Msquare]

    start -> draft -> review -> done
}
"""


async def run_pipeline(name, dot_source):
    import unified_llm
    from amplifier_module_loop_pipeline.backend import AmplifierBackend
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.engine import PipelineEngine
    from amplifier_module_loop_pipeline.handlers import HandlerRegistry
    from amplifier_module_loop_pipeline.transforms import apply_transforms
    from amplifier_module_loop_pipeline.validation import validate_or_raise

    print(f"\n{'=' * 60}")
    print(f"  PIPELINE: {name}")
    print(f"{'=' * 60}")

    # 1. Parse and validate
    graph = parse_dot(dot_source)
    context = PipelineContext()
    apply_transforms(graph, context)
    validate_or_raise(graph)
    print(f"✓ Parsed {len(graph.nodes)} nodes, {len(graph.edges)} edges")

    # 2. Create backend -- unified_llm.Client.from_env() auto-creates the client
    #    from env vars; no coordinator means the `direct` worker handles every
    #    node (EXTENSIONS.md Sec40).
    client = unified_llm.Client.from_env()
    backend = AmplifierBackend(
        provider=client,
        tools={},
        hooks=None,
        unified_client=client,
        default_worker="direct",
    )

    # 3. Build engine
    logs_root = tempfile.mkdtemp(prefix=f"pipeline-{name}-")
    registry = HandlerRegistry(backend=backend)
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
    )

    # 4. Run with timing
    start_time = time.time()
    try:
        outcome = await engine.run()
        elapsed = time.time() - start_time
        print(f"✓ Outcome: {outcome.status.value} ({elapsed:.1f}s)")
        if outcome.notes:
            preview = str(outcome.notes)[:500]
            print(f"  Notes: {preview}")
        if outcome.context_updates:
            print(f"  Context updates: {outcome.context_updates}")
        return True
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"✗ FAILED after {elapsed:.1f}s: {type(e).__name__}: {e}")
        import traceback

        traceback.print_exc()
        return False


async def main():
    results = {}

    # Test 1: Simple single-node pipeline (Anthropic)
    results["simple"] = await run_pipeline("simple", DOT_SIMPLE)

    # Test 2: Multi-provider pipeline (Anthropic + OpenAI)
    results["multi_provider"] = await run_pipeline("multi_provider", DOT_MULTI_PROVIDER)

    # Test 3: Multi-stage pipeline (plan -> implement -> validate)
    results["plan_implement_review"] = await run_pipeline(
        "plan_implement_review", DOT_PLAN_IMPLEMENT_REVIEW
    )

    # Test 4: Conditional routing with retry loop
    results["conditional"] = await run_pipeline("conditional", DOT_CONDITIONAL)

    # Summary
    print(f"\n{'=' * 60}")
    print("  RESULTS")
    print(f"{'=' * 60}")
    for name, passed in results.items():
        status = "✓ PASS" if passed else "✗ FAIL"
        print(f"  {status}: {name}")

    all_passed = all(results.values())
    print(f"\n  {'ALL PASSED' if all_passed else 'SOME FAILED'}")
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
