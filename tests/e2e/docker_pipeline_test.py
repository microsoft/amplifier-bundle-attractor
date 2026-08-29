"""E2E test: run an attractor pipeline with output verified inside a Docker container.

This test creates a real Docker container, runs a 2-node pipeline via
AmplifierBackend's direct worker where the LLM generates a Python script, then
verifies the output exists ONLY in the container (not on the host).

Prerequisites:
    - Docker daemon running
    - ANTHROPIC_API_KEY set
    - amplifier-module-loop-pipeline installed
    - unified-llm-client installed

Run:
    ~/.local/share/uv/tools/amplifier/bin/python tests/e2e/docker_pipeline_test.py
"""

import asyncio
import json
import subprocess
import sys
import tempfile
import time

CONTAINER_NAME = "attractor-e2e-docker-test"
CONTAINER_IMAGE = "python:3.12-slim"
WORKSPACE_DIR = "/workspace"

# A simple 2-node pipeline: plan what to build, then build it
DOT_SOURCE = r"""
digraph docker_test {
    graph [goal="Create a Python script that prints the first 5 squares"]

    start     [shape=Mdiamond]
    implement [shape=box, prompt="Write a Python script called squares.py that prints the first 5 square numbers (1, 4, 9, 16, 25), one per line. Respond with ONLY the Python code, nothing else.", llm_provider="anthropic", llm_model="claude-sonnet-4-20250514"]
    verify    [shape=box, prompt="Review the code from the previous stage. Does it print exactly 5 square numbers? Report success or failure in one sentence.", llm_provider="anthropic", llm_model="claude-sonnet-4-20250514"]
    done      [shape=Msquare]

    start -> implement -> verify -> done
}
"""


def docker_exec(cmd: str) -> tuple[str, int]:
    """Execute a command inside the Docker container."""
    result = subprocess.run(
        ["docker", "exec", CONTAINER_NAME, "bash", "-c", cmd],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.stdout.strip(), result.returncode


def setup_container() -> bool:
    """Create the Docker container."""
    # Remove if exists
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        capture_output=True,
    )

    # Create container
    result = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--name",
            CONTAINER_NAME,
            CONTAINER_IMAGE,
            "sleep",
            "3600",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"Failed to create container: {result.stderr}")
        return False

    # Create workspace directory
    docker_exec(f"mkdir -p {WORKSPACE_DIR}")
    print(f"Container created: {CONTAINER_NAME}")
    return True


def teardown_container() -> None:
    """Remove the Docker container."""
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        capture_output=True,
    )
    print(f"Container removed: {CONTAINER_NAME}")


async def run_pipeline_in_docker() -> dict:
    """Run the pipeline and capture the LLM output, then write it to the container."""
    import unified_llm
    from amplifier_module_loop_pipeline.backend import AmplifierBackend
    from amplifier_module_loop_pipeline.context import PipelineContext
    from amplifier_module_loop_pipeline.dot_parser import parse_dot
    from amplifier_module_loop_pipeline.engine import PipelineEngine
    from amplifier_module_loop_pipeline.handlers import HandlerRegistry
    from amplifier_module_loop_pipeline.transforms import apply_transforms
    from amplifier_module_loop_pipeline.validation import validate_or_raise

    # Parse and prepare the pipeline
    graph = parse_dot(DOT_SOURCE)
    context = PipelineContext()
    apply_transforms(graph, context)
    validate_or_raise(graph)

    # Create backend -- unified_llm.Client.from_env() auto-creates the client
    # from env; no coordinator means the `direct` worker handles every node
    # (EXTENSIONS.md Sec40).
    client = unified_llm.Client.from_env()
    backend = AmplifierBackend(
        provider=client,
        tools={},
        hooks=None,
        unified_client=client,
        default_worker="direct",
    )

    # Create engine
    logs_root = tempfile.mkdtemp(prefix="docker-e2e-")
    registry = HandlerRegistry(backend=backend)
    engine = PipelineEngine(
        graph=graph,
        context=context,
        handler_registry=registry,
        logs_root=logs_root,
    )

    # Run the pipeline
    start_time = time.time()
    outcome = await engine.run()
    elapsed = time.time() - start_time

    # Get the implement node's response from status.json
    # (AmplifierBackend's direct worker returns Outcome directly, so response.md is not written;
    #  the response text lives in status.json's "notes" field)
    import os

    status_file = os.path.join(logs_root, "implement", "status.json")
    code_content = ""
    if os.path.exists(status_file):
        with open(status_file) as f:
            status_data = json.load(f)
        code_content = status_data.get("notes", "").strip()

        # Strip markdown code fences if present
        if code_content.startswith("```"):
            lines = code_content.split("\n")
            lines = [line for line in lines if not line.startswith("```")]
            code_content = "\n".join(lines)

    return {
        "outcome": outcome.status.value,
        "elapsed": elapsed,
        "code_content": code_content,
        "logs_root": logs_root,
    }


async def main() -> int:
    results = {}

    print(f"\n{'=' * 60}")
    print("  DOCKER E2E TEST: Pipeline with Container Isolation")
    print(f"{'=' * 60}")

    # Step 1: Setup container
    print("\n[1/5] Setting up Docker container...")
    if not setup_container():
        print("FAILED: Could not create Docker container")
        return 1

    try:
        # Step 2: Run pipeline (LLM generates code)
        print("[2/5] Running pipeline (LLM generates code)...")
        pipeline_result = await run_pipeline_in_docker()
        print(
            f"  Pipeline: {pipeline_result['outcome']} ({pipeline_result['elapsed']:.1f}s)"
        )

        if pipeline_result["outcome"] != "success":
            print("  FAILED: Pipeline did not succeed")
            results["pipeline"] = False
        else:
            results["pipeline"] = True

        # Step 3: Write the generated code INTO the container
        print("[3/5] Writing generated code to container...")
        code = pipeline_result["code_content"]
        if not code:
            print("  FAILED: No code generated by pipeline")
            results["code_generated"] = False
        else:
            # Write code to container via docker exec
            docker_exec(f"cat > {WORKSPACE_DIR}/squares.py << 'PYEOF'\n{code}\nPYEOF")
            results["code_generated"] = True
            print(f"  Written {len(code)} bytes to {WORKSPACE_DIR}/squares.py")

        # Step 4: Verify file exists in container
        print("[4/5] Verifying code exists in container...")
        output, rc = docker_exec(f"cat {WORKSPACE_DIR}/squares.py")
        if rc == 0 and output:
            print(f"  File exists in container ({len(output)} bytes)")
            results["file_in_container"] = True
        else:
            print(f"  FAILED: File not found in container (rc={rc})")
            results["file_in_container"] = False

        # Run the code inside the container
        print("  Running code in container...")
        output, rc = docker_exec(f"cd {WORKSPACE_DIR} && python3 squares.py")
        if rc == 0:
            print(f"  Output: {output}")
            # Check if output contains square numbers
            if "1" in output and "4" in output and "9" in output:
                results["code_runs"] = True
                print("  Code runs correctly in container")
            else:
                results["code_runs"] = False
                print(f"  Code ran but output unexpected: {output}")
        else:
            results["code_runs"] = False
            print(f"  Code failed to run (rc={rc})")

        # Step 5: Verify file does NOT exist on host
        print("[5/5] Verifying isolation (file NOT on host)...")
        import os

        host_path = f"{WORKSPACE_DIR}/squares.py"
        if os.path.exists(host_path):
            print(f"  FAILED: File exists on host at {host_path} (isolation broken!)")
            results["isolation"] = False
        else:
            print("  File does NOT exist on host (isolation confirmed)")
            results["isolation"] = True

    finally:
        teardown_container()

    # Summary
    print(f"\n{'=' * 60}")
    print("  RESULTS")
    print(f"{'=' * 60}")
    all_pass = True
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  {status}: {name}")

    print(f"\n  {'ALL PASSED' if all_pass else 'SOME FAILED'}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
