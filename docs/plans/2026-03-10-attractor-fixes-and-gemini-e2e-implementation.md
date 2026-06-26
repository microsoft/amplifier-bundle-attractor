# Attractor Fixes & Gemini E2E Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Fix 4 confirmed spec-fidelity defects (D-05, D-07, D-08, NF-03) in amplifier-bundle-attractor and add Gemini E2E test coverage.

**Architecture:** Three phases — Phase 1 fixes are pure config/code changes inside `amplifier-bundle-attractor`. Phase 2 crosses two repos (`amplifier-bundle-execution-environments` and `amplifier-bundle-attractor`) to wire up native `apply_patch` end-to-end. Phase 3 adds Gemini E2E test files and profiles.

**Tech Stack:** Python 3.11+, pytest + pytest-asyncio (strict mode, `loop_scope="session"` for async tests), uv, YAML agent configs, DOT pipeline fixtures.

---

## Context You Need Before Starting

### Workspace layout

```
/home/bkrabach/dev/attractor-dev-machine/
├── amplifier-bundle-attractor/          ← PRIMARY REPO (most changes here)
├── amplifier-bundle-execution-environments/  ← SECONDARY REPO (Phase 2)
├── amplifier-bundle-filesystem/         ← READ-ONLY (source for apply_diff.py)
└── amplifier-module-provider-openai/    ← READ-ONLY (already has native apply_patch support)
```

All repos are git submodules. Work directly in their directories. Commit to `main` branch in each repo. A PR at the end is fine.

### How to run tests

```bash
# amplifier-bundle-attractor (all modules share the root test runner):
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest -x -q

# Specific module:
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest modules/loop-pipeline/tests/ -x -q

# amplifier-bundle-execution-environments:
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-execution-environments
uv run pytest tests/ -x -q
```

### Test conventions
- All async tests use `@pytest.mark.asyncio` (no `loop_scope` needed — file-level `asyncio_mode = "strict"` is set in pyproject.toml)
- **Exception:** `tool-pipeline-run` tests use `@pytest.mark.asyncio(loop_scope="session")` — match the existing style in that file
- Dispatch tool tests in execution-environments use `asyncio.run()` directly (no asyncio decorator needed)
- Tests are never skipped silently; use `pytest.mark.skipif` with a reason for conditional tests

---

## Phase 1: Quick Fixes (amplifier-bundle-attractor only)

---

### Task 1: D-05 — Timeout Alignment

**Files:**
- Modify: `amplifier-bundle-attractor/agents/attractor-agent-openai.yaml`
- Modify: `amplifier-bundle-attractor/agents/attractor-agent-gemini.yaml`

No tests needed — existing tests are mock-based and timeout-agnostic. No new code.

**Step 1: Edit attractor-agent-openai.yaml**

Open `amplifier-bundle-attractor/agents/attractor-agent-openai.yaml`. It currently reads:
```yaml
session:
  orchestrator:
    module: loop-agent
    source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
    config:
      max_tool_rounds_per_input: 50
      default_command_timeout_ms: 120000
```
and:
```yaml
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
    config:
      timeout: 120
```

Make these two changes:
- `default_command_timeout_ms: 120000` → `default_command_timeout_ms: 10000`
- `timeout: 120` → `timeout: 10`

**Step 2: Edit attractor-agent-gemini.yaml**

Open `amplifier-bundle-attractor/agents/attractor-agent-gemini.yaml`. Make the same two changes:
- `default_command_timeout_ms: 120000` → `default_command_timeout_ms: 10000`
- `timeout: 120` → `timeout: 10`

**Step 3: Verify no test breakage**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest -x -q
```

Expected: All tests pass. Timeout values in YAML files are never read by unit tests (they're runtime config).

**Step 4: Commit**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
git add agents/attractor-agent-openai.yaml agents/attractor-agent-gemini.yaml
git commit -m "fix(D-05): align default_command_timeout_ms and bash timeout to 10s spec defaults"
```

---

### Task 2: D-08 — One-Hop Fidelity Degradation

**Files:**
- Modify: `amplifier-bundle-attractor/modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py`
- Modify: `amplifier-bundle-attractor/modules/loop-pipeline/tests/test_batch_d_pipeline_infra.py`

**Background:** When a pipeline resumes from checkpoint, M-23 correctly degrades fidelity from `"full"` to `"summary:high"` for the first node. But it's supposed to be one-hop only — the second node should run at full fidelity again. Currently, the degradation is permanent for the whole run.

**Step 1: Write the failing test first**

Open `amplifier-bundle-attractor/modules/loop-pipeline/tests/test_batch_d_pipeline_infra.py`.

Find the `TestM23CheckpointFidelityDegradation` class (around line 450). After the existing two test methods in that class, add this new test:

```python
    @pytest.mark.asyncio
    async def test_fidelity_restored_after_first_node(self, tmp_path):
        """After the first node post-resume, fidelity is restored to 'full'."""
        fidelities_seen: list[str | None] = []

        class FidelityCapturingBackend:
            async def run(self, node, prompt, context):
                fidelities_seen.append(context.get("graph.default_fidelity"))
                return "done"

        cp = Checkpoint(
            current_node="plan",
            completed_nodes={"start": "success", "plan": "success"},
            context_snapshot={
                "graph.goal": "build auth",
                "outcome": "success",
                "graph.default_fidelity": "full",
            },
            node_outcomes={
                "start": {"status": "success"},
                "plan": {"status": "success"},
            },
            timestamp="2025-01-01T00:00:00Z",
        )
        save_checkpoint(cp, str(tmp_path / "checkpoint.json"))

        engine = _make_engine(
            dot_source="""
            digraph {
                goal = "build auth"
                default_fidelity = "full"
                start [shape=Mdiamond]
                plan [prompt="Plan"]
                implement [prompt="Build"]
                review [prompt="Review"]
                exit [shape=Msquare]
                start -> plan -> implement -> review -> exit
            }
            """,
            backend=FidelityCapturingBackend(),
            logs_root=str(tmp_path),
        )
        await engine.run()

        # implement is the first new node — should see degraded fidelity
        assert fidelities_seen[0] == "summary:high", (
            f"First post-resume node should run at 'summary:high', got {fidelities_seen[0]!r}"
        )
        # review is the second new node — should see restored full fidelity
        assert fidelities_seen[1] == "full", (
            f"Second post-resume node should run at 'full', got {fidelities_seen[1]!r}"
        )
```

**Step 2: Run the new test to confirm it fails**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest modules/loop-pipeline/tests/test_batch_d_pipeline_infra.py::TestM23CheckpointFidelityDegradation::test_fidelity_restored_after_first_node -v
```

Expected: **FAIL** — `fidelities_seen[1]` is `"summary:high"` instead of `"full"`.

**Step 3: Implement the one-hop flag in engine.py**

Open `amplifier-bundle-attractor/modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py`.

**Change 1:** In `__init__` (around line 73-76), add the flag after the existing instance variables:

Find this block:
```python
        self.node_outcomes: dict[str, Outcome] = {}
        self.completed_nodes: list[str] = []
        self.iteration_count: int = 0
        self._checkpoint_path = os.path.join(logs_root, "checkpoint.json")
        self.artifact_store = ArtifactStore(base_dir=logs_root)
```

Add `self._fidelity_degraded_hop: bool = False` after `self.iteration_count`:

```python
        self.node_outcomes: dict[str, Outcome] = {}
        self.completed_nodes: list[str] = []
        self.iteration_count: int = 0
        self._fidelity_degraded_hop: bool = False
        self._checkpoint_path = os.path.join(logs_root, "checkpoint.json")
        self.artifact_store = ArtifactStore(base_dir=logs_root)
```

**Change 2:** In `_try_resume_from_checkpoint()`, after the existing degradation log message (around line 700), set the flag:

Find this block:
```python
        if restored_fidelity == "full":
            self.context.set("graph.default_fidelity", "summary:high")
            logger.info(
                "Checkpoint resume: degraded fidelity from 'full' to "
                "'summary:high' (full session context unavailable)"
            )
```

Replace with:
```python
        if restored_fidelity == "full":
            self.context.set("graph.default_fidelity", "summary:high")
            self._fidelity_degraded_hop = True
            logger.info(
                "Checkpoint resume: degraded fidelity from 'full' to "
                "'summary:high' (full session context unavailable); "
                "will restore after first node"
            )
```

**Change 3:** In the main `run()` loop, after Step 3 records completion (around line 325-327), add the one-hop restoration.

Find this block:
```python
            # Step 3: Record completion
            self.completed_nodes.append(current_node.id)
            self.node_outcomes[current_node.id] = outcome
            logger.debug("Node %s completed: %s", current_node.id, outcome.status.value)
```

Replace with:
```python
            # Step 3: Record completion
            self.completed_nodes.append(current_node.id)
            self.node_outcomes[current_node.id] = outcome
            logger.debug("Node %s completed: %s", current_node.id, outcome.status.value)

            # M-23: One-hop fidelity restoration — after the first new node
            # post-checkpoint executes, restore to full fidelity.
            if self._fidelity_degraded_hop:
                self.context.set("graph.default_fidelity", "full")
                self._fidelity_degraded_hop = False
                logger.info(
                    "Checkpoint resume: restored fidelity to 'full' "
                    "after one-hop degradation (node '%s')",
                    current_node.id,
                )
```

**Step 4: Run the new test to confirm it passes**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest modules/loop-pipeline/tests/test_batch_d_pipeline_infra.py::TestM23CheckpointFidelityDegradation::test_fidelity_restored_after_first_node -v
```

Expected: **PASS**.

**Step 5: Update the existing test that now has wrong assertion**

The existing test `test_full_fidelity_degraded_on_resume` currently asserts `fidelity == "summary:high"`. After the fix, the engine restores fidelity to `"full"` after the `implement` node runs. Update the assertion.

Find in `TestM23CheckpointFidelityDegradation`:
```python
    async def test_full_fidelity_degraded_on_resume(self, tmp_path):
        """When checkpoint has full fidelity, it's degraded to summary:high."""
```

The existing test runs a pipeline: `start -> plan -> implement -> exit`, where `start` and `plan` are already completed in the checkpoint. So `implement` is the first new node. After implement runs, fidelity is restored. Then `exit` terminates the pipeline.

Find the assertion at the bottom of that test:
```python
        fidelity = engine.context.get("graph.default_fidelity")
        assert fidelity == "summary:high"
```

Replace with:
```python
        # After the fix: fidelity is degraded for the first new node, then
        # restored to "full". The context ends at "full".
        fidelity = engine.context.get("graph.default_fidelity")
        assert fidelity == "full", (
            f"Expected fidelity restored to 'full' after one-hop, got {fidelity!r}"
        )
```

Also update the docstring of that test:
```python
    async def test_full_fidelity_degraded_on_resume(self, tmp_path):
        """When checkpoint has full fidelity, it degrades to summary:high for one hop then restores."""
```

**Step 6: Run the full TestM23 class**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest modules/loop-pipeline/tests/test_batch_d_pipeline_infra.py::TestM23CheckpointFidelityDegradation -v
```

Expected: All 3 tests **PASS**:
- `test_full_fidelity_degraded_on_resume` — PASS (assertion updated)
- `test_non_full_fidelity_not_degraded_on_resume` — PASS (unchanged, should still pass)
- `test_fidelity_restored_after_first_node` — PASS (new test)

**Step 7: Run full test suite**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest -x -q
```

Expected: All tests pass.

**Step 8: Commit**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
git add modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py \
        modules/loop-pipeline/tests/test_batch_d_pipeline_infra.py
git commit -m "fix(D-08): one-hop fidelity degradation on checkpoint resume"
```

---

### Task 3: NF-03 — Provider Extraction

**Files:**
- Examine: `amplifier-bundle-attractor/modules/tool-pipeline-run/amplifier_module_tool_pipeline_run/__init__.py`
- Examine: `amplifier-bundle-attractor/modules/tool-pipeline-run/tests/test_pipeline_run.py`

**Background:** `_extract_required_providers()` is supposed to parse a DOT source and return the set of required LLM providers (from `model_stylesheet` rules and per-node `llm_provider` attributes). The design investigation found 3 tests that verify this behavior are failing.

**Step 1: Run the 3 failing tests to see their current state**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest modules/tool-pipeline-run/tests/test_pipeline_run.py::test_extract_required_providers_from_stylesheet \
              modules/tool-pipeline-run/tests/test_pipeline_run.py::test_extract_required_providers_from_node_attrs \
              modules/tool-pipeline-run/tests/test_pipeline_run.py::test_extract_required_providers_empty_when_none \
              -v
```

**Case A: If all 3 tests pass** — NF-03 is already fixed. Skip to Step 4 (commit note).

**Case B: If tests fail** — Continue to Step 2.

**Step 2: Diagnose the failure**

Run this to see what's happening:
```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run python -c "
from amplifier_module_loop_pipeline.stylesheet import parse_stylesheet
css = '''* { llm_provider: anthropic; llm_model: claude-3; }
.planning { llm_provider: openai; llm_model: o3; }'''
rules = parse_stylesheet(css)
for rule in rules:
    print('Rule:', rule)
    print('Properties:', getattr(rule, 'properties', None))
    print('Attrs:', vars(rule) if hasattr(rule, '__dict__') else dir(rule))
"
```

If `rule.properties` is `{}` or missing `llm_provider`, the stylesheet parser doesn't handle non-visual CSS properties. In that case, proceed to Step 3.

**Step 3: Fix `_extract_required_providers()` in `__init__.py`**

Open `amplifier-bundle-attractor/modules/tool-pipeline-run/amplifier_module_tool_pipeline_run/__init__.py`.

Find the `_extract_required_providers` method (around line 160). The current implementation calls `parse_stylesheet` and looks for `rule.properties.get("llm_provider")`. If `parse_stylesheet` doesn't populate `properties` with model attributes, we need to use `rule.attrs` or a different accessor, OR parse the stylesheet ourselves.

**Option A: If `parse_stylesheet` returns rules with an `attrs` dict instead of `properties`:**

Replace the stylesheet parsing block:
```python
        # Source 1: model_stylesheet rules
        if graph.model_stylesheet:
            rules = parse_stylesheet(graph.model_stylesheet)
            for rule in rules:
                provider = rule.properties.get("llm_provider")
                if provider:
                    providers.add(provider)
```

With (using `attrs` instead):
```python
        # Source 1: model_stylesheet rules
        if graph.model_stylesheet:
            rules = parse_stylesheet(graph.model_stylesheet)
            for rule in rules:
                # Try both .properties and .attrs (stylesheet rule dataclass varies)
                props = getattr(rule, "properties", None) or getattr(rule, "attrs", {})
                provider = props.get("llm_provider")
                if provider:
                    providers.add(provider)
```

**Option B: If `parse_stylesheet` doesn't work at all for provider extraction:**

Replace the entire `_extract_required_providers` method with a regex-based fallback for stylesheets, keeping the node-attribute logic intact:

```python
    def _extract_required_providers(self, dot_source: str) -> set[str]:
        """Parse a DOT source and extract all required LLM providers.

        Checks two sources:
        1. model_stylesheet rules — each rule with an llm_provider declaration
        2. Node-level llm_provider attributes — explicit per-node settings

        Structural nodes (Mdiamond/start, Msquare/exit) are excluded since
        they don't invoke an LLM.

        Args:
            dot_source: The DOT digraph source string.

        Returns:
            Set of provider names (e.g. {"anthropic", "openai"}).
        """
        import re

        if not HAS_PIPELINE:
            logger.debug("loop-pipeline not available; skipping provider extraction")
            return set()

        providers: set[str] = set()

        try:
            graph = parse_dot(dot_source)
        except Exception as exc:
            logger.warning("Failed to parse DOT source for provider extraction: %s", exc)
            return providers

        # Source 1: model_stylesheet rules — use regex as a reliable fallback
        # parse_stylesheet may not expose llm_provider in its rule properties.
        if graph.model_stylesheet:
            try:
                rules = parse_stylesheet(graph.model_stylesheet)
                for rule in rules:
                    props = getattr(rule, "properties", None) or getattr(rule, "attrs", {})
                    provider = props.get("llm_provider")
                    if provider:
                        providers.add(provider)
            except Exception:
                pass  # Fall through to regex below

            # Regex fallback: find all llm_provider: <value> declarations in the stylesheet
            for match in re.finditer(r"llm_provider\s*:\s*([a-zA-Z0-9_-]+)", graph.model_stylesheet):
                providers.add(match.group(1))

        # Source 2: explicit node attributes
        structural_shapes = {"Mdiamond", "Msquare", "point"}
        for node in graph.nodes.values():
            if node.shape in structural_shapes:
                continue
            provider = node.attrs.get("llm_provider")
            if provider:
                providers.add(provider)

        return providers
```

**Step 4: Run the 3 tests to confirm they pass**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest modules/tool-pipeline-run/tests/test_pipeline_run.py::test_extract_required_providers_from_stylesheet \
              modules/tool-pipeline-run/tests/test_pipeline_run.py::test_extract_required_providers_from_node_attrs \
              modules/tool-pipeline-run/tests/test_pipeline_run.py::test_extract_required_providers_empty_when_none \
              -v
```

Expected: All 3 **PASS**.

**Step 5: Run full test suite**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest -x -q
```

Expected: All tests pass.

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
git add modules/tool-pipeline-run/amplifier_module_tool_pipeline_run/__init__.py
git commit -m "fix(NF-03): extract required providers from stylesheet and node attrs"
```

---

### Task 4: Phase 1 Regression Check

**Step 1: Run the full test suite one final time**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest -q
```

Expected: All 2,239+ tests pass. Note the final count.

If any tests fail, stop and debug before continuing to Phase 2.

---

## Phase 2: Native `apply_patch` End-to-End (D-07)

This phase touches two repos. Do the execution-environments work first (Tasks 5-7), then update the attractor agent configs (Tasks 8-10).

---

### Task 5: Vendor `apply_diff.py` into execution-environments

**Files:**
- Read: `amplifier-bundle-filesystem/modules/tool-apply-patch/amplifier_module_tool_apply_patch/apply_diff.py`
- Create: `amplifier-bundle-execution-environments/modules/tools-env-all/amplifier_module_tools_env_all/apply_diff.py`

**Background:** `apply_diff.py` from the filesystem bundle is a pure string-in/string-out V4A diff applier with zero I/O dependencies. It's Apache-2.0 licensed (ported from OpenAI Agents SDK). We vendor it rather than import it to avoid a cross-bundle runtime dependency.

**Step 1: Copy the file**

```bash
cp /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-filesystem/modules/tool-apply-patch/amplifier_module_tool_apply_patch/apply_diff.py \
   /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-execution-environments/modules/tools-env-all/amplifier_module_tools_env_all/apply_diff.py
```

**Step 2: Verify the copy exists and has the correct content**

```bash
head -15 /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-execution-environments/modules/tools-env-all/amplifier_module_tools_env_all/apply_diff.py
```

Expected: Shows the Apache-2.0 copyright header and the `apply_diff` function.

**Step 3: Commit**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-execution-environments
git add modules/tools-env-all/amplifier_module_tools_env_all/apply_diff.py
git commit -m "vendor: copy apply_diff.py from amplifier-bundle-filesystem (Apache-2.0)"
```

---

### Task 6: Implement `env_apply_patch` Dispatch Tool

**Files:**
- Modify: `amplifier-bundle-execution-environments/modules/tools-env-all/amplifier_module_tools_env_all/dispatch.py`
- Modify: `amplifier-bundle-execution-environments/modules/tools-env-all/amplifier_module_tools_env_all/__init__.py`

**Step 1: Write the failing test first**

(See Task 7 — write the tests before implementing. Come back here after Step 1 of Task 7.)

**Step 2: Add `EnvApplyPatchTool` to `dispatch.py`**

Open `amplifier-bundle-execution-environments/modules/tools-env-all/amplifier_module_tools_env_all/dispatch.py`.

At the very end of the file (after `EnvFileExistsTool`), add:

```python
# ---------------------------------------------------------------------------
# EnvApplyPatchTool
# ---------------------------------------------------------------------------


class EnvApplyPatchTool:
    """Apply pre-parsed file operations in a named environment instance.

    Accepts a list of operations in native apply_patch format:
      {"type": "create_file"|"update_file"|"delete_file", "path": "...", "diff": "..."}

    For create_file: applies the diff to an empty string (pure insertion).
    For update_file: reads the existing file, applies the V4A diff, writes back.
    For delete_file: removes the file via exec_command.
    """

    def __init__(self, registry: EnvironmentRegistry) -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "env_apply_patch"

    @property
    def description(self) -> str:
        return (
            "Apply pre-parsed file operations (create, update, or delete files) "
            "in a named environment instance. Each operation specifies a 'type' "
            "(create_file, update_file, or delete_file), a 'path', and a 'diff' "
            "(V4A patch content). Accepts the native apply_patch format from the "
            "OpenAI Responses API."
        )

    @property
    def input_schema(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "instance": _INSTANCE_SCHEMA,
                "operations": {
                    "type": "array",
                    "description": "List of file operations to apply",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": ["create_file", "update_file", "delete_file"],
                                "description": "Operation type",
                            },
                            "path": {
                                "type": "string",
                                "description": "File path (relative or absolute)",
                            },
                            "diff": {
                                "type": "string",
                                "description": "V4A diff content (empty string for delete_file)",
                            },
                        },
                        "required": ["type", "path"],
                    },
                },
            },
            "required": ["operations"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        backend, error = _get_backend(self._registry, input)
        if error:
            return error

        operations = input.get("operations")
        if not operations:
            return _missing("operations")

        from .apply_diff import apply_diff

        results = []
        try:
            for op in operations:
                op_type = op.get("type")
                path = op.get("path")
                diff = op.get("diff", "")

                if not path:
                    return ToolResult(
                        success=False,
                        error={"message": "Each operation must have a 'path' field"},
                    )

                if op_type == "create_file":
                    content = apply_diff("", diff, mode="create")
                    await backend.write_file(path, content)
                    results.append({"type": "create_file", "path": path, "status": "ok"})

                elif op_type == "update_file":
                    existing = await backend.read_file(path)
                    new_content = apply_diff(existing, diff)
                    await backend.write_file(path, new_content)
                    results.append({"type": "update_file", "path": path, "status": "ok"})

                elif op_type == "delete_file":
                    await backend.exec_command(f"rm -f -- {path}")
                    results.append({"type": "delete_file", "path": path, "status": "ok"})

                else:
                    return ToolResult(
                        success=False,
                        error={"message": f"Unknown operation type: '{op_type}'"},
                    )

        except Exception as e:
            return ToolResult(success=False, error={"message": str(e)})

        return ToolResult(success=True, output={"results": results})
```

**Step 3: Register `EnvApplyPatchTool` in `__init__.py`**

Open `amplifier-bundle-execution-environments/modules/tools-env-all/amplifier_module_tools_env_all/__init__.py`.

Update the docstring to show 12 tools (was 11):

Find:
```python
"""Instance-based execution environment tools for Amplifier.

Provides 11 tools:
- env_create: Factory for creating environment instances
- env_destroy: Tear down instances
- env_list: Show all active instances
- env_exec, env_read_file, env_write_file, env_edit_file,
  env_grep, env_glob, env_list_dir, env_file_exists: Common-shape dispatch tools
"""
```

Replace with:
```python
"""Instance-based execution environment tools for Amplifier.

Provides 12 tools:
- env_create: Factory for creating environment instances
- env_destroy: Tear down instances
- env_list: Show all active instances
- env_exec, env_read_file, env_write_file, env_edit_file,
  env_grep, env_glob, env_list_dir, env_file_exists: Common-shape dispatch tools
- env_apply_patch: Apply V4A file patches (create, update, delete)
"""
```

Update the import block:

Find:
```python
    from .dispatch import (
        EnvEditFileTool,
        EnvExecTool,
        EnvFileExistsTool,
        EnvGlobTool,
        EnvGrepTool,
        EnvListDirTool,
        EnvReadFileTool,
        EnvWriteFileTool,
    )
```

Replace with:
```python
    from .dispatch import (
        EnvApplyPatchTool,
        EnvEditFileTool,
        EnvExecTool,
        EnvFileExistsTool,
        EnvGlobTool,
        EnvGrepTool,
        EnvListDirTool,
        EnvReadFileTool,
        EnvWriteFileTool,
    )
```

Add `EnvApplyPatchTool` to the `all_tools` list:

Find:
```python
    # Create all 11 tools
    all_tools = [
        EnvCreateTool(
            registry=registry,
            coordinator=coordinator,
            backends=backends,
            enable_security=enable_security,
        ),
        EnvDestroyTool(registry=registry),
        EnvListTool(registry=registry),
        EnvExecTool(registry=registry),
        EnvReadFileTool(registry=registry),
        EnvWriteFileTool(registry=registry),
        EnvEditFileTool(registry=registry),
        EnvGrepTool(registry=registry),
        EnvGlobTool(registry=registry),
        EnvListDirTool(registry=registry),
        EnvFileExistsTool(registry=registry),
    ]
```

Replace with:
```python
    # Create all 12 tools
    all_tools = [
        EnvCreateTool(
            registry=registry,
            coordinator=coordinator,
            backends=backends,
            enable_security=enable_security,
        ),
        EnvDestroyTool(registry=registry),
        EnvListTool(registry=registry),
        EnvExecTool(registry=registry),
        EnvReadFileTool(registry=registry),
        EnvWriteFileTool(registry=registry),
        EnvEditFileTool(registry=registry),
        EnvGrepTool(registry=registry),
        EnvGlobTool(registry=registry),
        EnvListDirTool(registry=registry),
        EnvFileExistsTool(registry=registry),
        EnvApplyPatchTool(registry=registry),
    ]
```

Also update the log message:
```python
    logger.info("tools-env-all: registered %d tools", len(all_tools))
```
(This already uses `len(all_tools)` so no change needed.)

Update the description in the return dict:
```python
    return {
        "name": "tools-env-all",
        "version": "0.2.0",
        "description": "Instance-based execution environment tools (12 tools)",
        "tools": [t.name for t in all_tools],
    }
```

---

### Task 7: Unit Tests for `env_apply_patch`

**Files:**
- Modify: `amplifier-bundle-execution-environments/tests/test_dispatch.py`

**Step 1: Write failing tests**

Open `amplifier-bundle-execution-environments/tests/test_dispatch.py`.

The `FakeBackend` class already has `read_file`, `write_file`, and `exec_command` methods. We'll extend `FakeBackend` with scripted `read_file` responses for update_file tests.

At the end of the file, add this entire test class:

```python
# ---------------------------------------------------------------------------
# EnvApplyPatchTool
# ---------------------------------------------------------------------------


class TestEnvApplyPatchToolName:
    def test_name(self, registry: EnvironmentRegistry) -> None:
        from amplifier_module_tools_env_all.dispatch import EnvApplyPatchTool

        tool = EnvApplyPatchTool(registry)
        assert tool.name == "env_apply_patch"


class TestEnvApplyPatchToolSchema:
    def test_schema_has_operations(self, registry: EnvironmentRegistry) -> None:
        from amplifier_module_tools_env_all.dispatch import EnvApplyPatchTool

        tool = EnvApplyPatchTool(registry)
        schema = tool.input_schema
        assert "operations" in schema["properties"]
        assert schema["required"] == ["operations"]

    def test_schema_has_instance(self, registry: EnvironmentRegistry) -> None:
        from amplifier_module_tools_env_all.dispatch import EnvApplyPatchTool

        tool = EnvApplyPatchTool(registry)
        assert "instance" in tool.input_schema["properties"]


class TestEnvApplyPatchCreateFile:
    def test_create_file_calls_write_file(
        self, registry: EnvironmentRegistry, fake_backend: FakeBackend
    ) -> None:
        from amplifier_module_tools_env_all.dispatch import EnvApplyPatchTool

        tool = EnvApplyPatchTool(registry)
        # A V4A create diff — lines prefixed with "+" are added to a new file
        diff = "+hello world\n+second line\n"
        result = asyncio.run(
            tool.execute({
                "operations": [
                    {"type": "create_file", "path": "/workspace/hello.py", "diff": diff}
                ]
            })
        )
        assert result.success is True
        assert result.output["results"][0]["type"] == "create_file"
        assert result.output["results"][0]["status"] == "ok"
        # write_file should have been called
        write_calls = [c for c in fake_backend.calls if c[0] == "write_file"]
        assert len(write_calls) == 1
        assert write_calls[0][1] == "/workspace/hello.py"

    def test_create_file_does_not_read_first(
        self, registry: EnvironmentRegistry, fake_backend: FakeBackend
    ) -> None:
        from amplifier_module_tools_env_all.dispatch import EnvApplyPatchTool

        tool = EnvApplyPatchTool(registry)
        asyncio.run(
            tool.execute({
                "operations": [
                    {"type": "create_file", "path": "/workspace/new.py", "diff": "+content\n"}
                ]
            })
        )
        # Must NOT call read_file for create operations
        read_calls = [c for c in fake_backend.calls if c[0] == "read_file"]
        assert len(read_calls) == 0


class TestEnvApplyPatchUpdateFile:
    def test_update_file_reads_then_writes(
        self, registry: EnvironmentRegistry, fake_backend: FakeBackend
    ) -> None:
        from amplifier_module_tools_env_all.dispatch import EnvApplyPatchTool

        # Prime the FakeBackend to return specific content for read_file
        # FakeBackend.read_file always returns "file content\n"
        # We craft a diff that matches this content
        tool = EnvApplyPatchTool(registry)
        # Replace "file content" with "new content" using a V4A diff
        diff = " file content\n"  # context line — no change diff, just context
        # Actually let's use a real update diff:
        diff = "-file content\n+new content\n"
        result = asyncio.run(
            tool.execute({
                "operations": [
                    {"type": "update_file", "path": "/workspace/existing.py", "diff": diff}
                ]
            })
        )
        assert result.success is True
        # Both read_file and write_file must have been called
        read_calls = [c for c in fake_backend.calls if c[0] == "read_file"]
        write_calls = [c for c in fake_backend.calls if c[0] == "write_file"]
        assert len(read_calls) == 1
        assert read_calls[0][1] == "/workspace/existing.py"
        assert len(write_calls) == 1
        assert write_calls[0][1] == "/workspace/existing.py"

    def test_update_file_applies_diff_correctly(
        self, registry: EnvironmentRegistry
    ) -> None:
        from amplifier_module_tools_env_all.dispatch import EnvApplyPatchTool

        # Use a backend with controlled read_file return value
        class ControlledBackend:
            def __init__(self):
                self.written_content = None

            @property
            def env_type(self):
                return "fake"

            def working_directory(self):
                return "/workspace"

            def platform(self):
                return "linux"

            def os_version(self):
                return "FakeOS"

            async def read_file(self, path, offset=None, limit=None):
                return "original line\nsecond line\n"

            async def write_file(self, path, content):
                self.written_content = content

            async def exec_command(self, cmd, timeout=None, workdir=None, env_vars=None):
                from amplifier_env_common.models import EnvExecResult
                return EnvExecResult(stdout="", stderr="", exit_code=0, timed_out=False, duration_ms=1)

            def info(self):
                return {"type": "fake"}

        from amplifier_env_common.registry import EnvironmentRegistry

        controlled = ControlledBackend()
        reg = EnvironmentRegistry()
        reg.register("local", controlled, "fake")

        tool = EnvApplyPatchTool(reg)
        diff = "-original line\n+replaced line\n"
        result = asyncio.run(
            tool.execute({
                "operations": [
                    {"type": "update_file", "path": "/workspace/file.py", "diff": diff}
                ]
            })
        )
        assert result.success is True
        assert "replaced line" in controlled.written_content
        assert "original line" not in controlled.written_content


class TestEnvApplyPatchDeleteFile:
    def test_delete_file_calls_exec_command(
        self, registry: EnvironmentRegistry, fake_backend: FakeBackend
    ) -> None:
        from amplifier_module_tools_env_all.dispatch import EnvApplyPatchTool

        tool = EnvApplyPatchTool(registry)
        result = asyncio.run(
            tool.execute({
                "operations": [
                    {"type": "delete_file", "path": "/workspace/old.py"}
                ]
            })
        )
        assert result.success is True
        exec_calls = [c for c in fake_backend.calls if c[0] == "exec_command"]
        assert len(exec_calls) == 1
        assert "/workspace/old.py" in exec_calls[0][1]


class TestEnvApplyPatchValidation:
    def test_missing_operations_returns_error(
        self, registry: EnvironmentRegistry
    ) -> None:
        from amplifier_module_tools_env_all.dispatch import EnvApplyPatchTool

        tool = EnvApplyPatchTool(registry)
        result = asyncio.run(tool.execute({}))
        assert result.success is False
        assert "operations" in result.error["message"]

    def test_unknown_op_type_returns_error(
        self, registry: EnvironmentRegistry
    ) -> None:
        from amplifier_module_tools_env_all.dispatch import EnvApplyPatchTool

        tool = EnvApplyPatchTool(registry)
        result = asyncio.run(
            tool.execute({
                "operations": [{"type": "explode_file", "path": "/workspace/f.py"}]
            })
        )
        assert result.success is False
        assert "explode_file" in result.error["message"]

    def test_unknown_instance_returns_error(
        self, registry: EnvironmentRegistry
    ) -> None:
        from amplifier_module_tools_env_all.dispatch import EnvApplyPatchTool

        tool = EnvApplyPatchTool(registry)
        result = asyncio.run(
            tool.execute({
                "instance": "ghost-env",
                "operations": [{"type": "create_file", "path": "/f.py", "diff": "+x\n"}],
            })
        )
        assert result.success is False
        assert "ghost-env" in result.error["message"]


class TestEnvApplyPatchMultipleOps:
    def test_multiple_operations_processed_in_order(
        self, registry: EnvironmentRegistry, fake_backend: FakeBackend
    ) -> None:
        from amplifier_module_tools_env_all.dispatch import EnvApplyPatchTool

        tool = EnvApplyPatchTool(registry)
        result = asyncio.run(
            tool.execute({
                "operations": [
                    {"type": "create_file", "path": "/a.py", "diff": "+a\n"},
                    {"type": "create_file", "path": "/b.py", "diff": "+b\n"},
                ]
            })
        )
        assert result.success is True
        assert len(result.output["results"]) == 2
        assert result.output["results"][0]["path"] == "/a.py"
        assert result.output["results"][1]["path"] == "/b.py"
```

**Step 2: Run the failing tests**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-execution-environments
uv run pytest tests/test_dispatch.py -k "ApplyPatch" -v
```

Expected: **ImportError** or **AttributeError** — `EnvApplyPatchTool` doesn't exist yet.

**Step 3: Go back and implement** (Task 6 Steps 2-3 above)

**Step 4: Run tests again**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-execution-environments
uv run pytest tests/test_dispatch.py -k "ApplyPatch" -v
```

Expected: All tests **PASS**.

**Step 5: Run the full test suite**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-execution-environments
uv run pytest tests/ -x -q
```

Expected: All tests pass (existing tests must not be broken).

**Step 6: Commit**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-execution-environments
git add modules/tools-env-all/amplifier_module_tools_env_all/dispatch.py \
        modules/tools-env-all/amplifier_module_tools_env_all/__init__.py \
        tests/test_dispatch.py
git commit -m "feat(D-07): add env_apply_patch tool with V4A diff support"
```

---

### Task 8: Update Attractor OpenAI Host Agent to Use Native `apply_patch`

**Files:**
- Modify: `amplifier-bundle-attractor/agents/attractor-agent-openai.yaml`

**Background:** The current agent uses Attractor's own custom `tool-apply-patch` module (function engine, raw V4A strings). We replace it with the filesystem bundle's `tool-apply-patch` configured for `engine: native`. This activates the `apply_patch.engine: native` coordinator capability, which the OpenAI provider already detects to send `{"type": "apply_patch"}` as a built-in tool type to the Responses API.

There are no tests for this YAML change (it's runtime config). We verify visually that the change is correct.

**Step 1: Edit attractor-agent-openai.yaml**

Open `amplifier-bundle-attractor/agents/attractor-agent-openai.yaml`.

Find:
```yaml
tools:
  - module: tool-apply-patch
    source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/tool-apply-patch
```

Replace with:
```yaml
tools:
  - module: tool-apply-patch
    source: git+https://github.com/microsoft/amplifier-bundle-filesystem@main#subdirectory=modules/tool-apply-patch
    config:
      engine: native
      allowed_write_paths: ["."]
      denied_write_paths: []
```

**Step 2: Also update the description comment at the top of the file**

Find the comment at the top:
```yaml
# OpenAI coding agent (codex-rs aligned)
# Uses apply_patch for edits, restricted filesystem, 120s shell timeout.
```

Replace with (reflecting D-05 timeout fix too):
```yaml
# OpenAI coding agent (codex-rs aligned)
# Uses native apply_patch (Responses API built-in), restricted filesystem, 10s shell timeout.
```

Also update the bundle description:
```yaml
  description: >
    OpenAI coding agent (codex-rs aligned).
    Uses apply_patch for edits, restricted filesystem, 120s shell timeout.
```
→
```yaml
  description: >
    OpenAI coding agent (codex-rs aligned).
    Uses native apply_patch (Responses API built-in), restricted filesystem, 10s shell timeout.
```

**Step 3: Verify the YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('amplifier-bundle-attractor/agents/attractor-agent-openai.yaml'))" && echo "YAML valid"
```

Expected: `YAML valid`

**Step 4: Commit**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
git add agents/attractor-agent-openai.yaml
git commit -m "fix(D-07): switch OpenAI host agent to filesystem bundle native apply_patch"
```

---

### Task 9: Update Isolated Environment Guidance

**Files:**
- Modify: `amplifier-bundle-attractor/context/isolated-environment-guidance.md`

**Background:** The isolated-environment-guidance.md tells the model which `env_*` tools to use instead of host-local tools. After Task 6, `env_apply_patch` is available. The model needs to know it replaces `apply_patch`.

**Step 1: Edit isolated-environment-guidance.md**

Open `amplifier-bundle-attractor/context/isolated-environment-guidance.md`.

Current content:
```markdown
# Isolated Environment Execution

You are executing inside an isolated environment (Docker container or remote host).
All file and command operations MUST use the environment tools:

- `env_exec` -- execute shell commands (replaces `bash`)
- `env_read_file` -- read file contents (replaces `read_file`)
- `env_write_file` -- write file contents (replaces `write_file`)
- `env_edit_file` -- edit file with string replacement (replaces `edit_file`)
- `env_grep` -- search file contents (replaces `grep`)
- `env_glob` -- find files by pattern (replaces `glob`)
- `env_list_dir` -- list directory contents
- `env_file_exists` -- check if a file exists

Do NOT use `bash`, `read_file`, `write_file`, `edit_file`, `grep`, or `glob` directly.
Those tools operate on the host filesystem and would bypass the isolated environment.

All environment tools accept an optional `instance` parameter (default: "local").
If an environment instance was created for this session, use the instance name provided.
```

Replace entirely with:
```markdown
# Isolated Environment Execution

You are executing inside an isolated environment (Docker container or remote host).
All file and command operations MUST use the environment tools:

- `env_exec` -- execute shell commands (replaces `bash`)
- `env_read_file` -- read file contents (replaces `read_file`)
- `env_write_file` -- write file contents (replaces `write_file`)
- `env_edit_file` -- edit file with string replacement (replaces `edit_file`)
- `env_apply_patch` -- apply V4A file patches (replaces `apply_patch`)
- `env_grep` -- search file contents (replaces `grep`)
- `env_glob` -- find files by pattern (replaces `glob`)
- `env_list_dir` -- list directory contents
- `env_file_exists` -- check if a file exists

Do NOT use `bash`, `read_file`, `write_file`, `edit_file`, `apply_patch`, `grep`, or `glob` directly.
Those tools operate on the host filesystem and would bypass the isolated environment.

All environment tools accept an optional `instance` parameter (default: "local").
If an environment instance was created for this session, use the instance name provided.
```

**Step 2: Commit**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
git add context/isolated-environment-guidance.md
git commit -m "docs(D-07): add env_apply_patch to isolated environment guidance"
```

---

### Task 10: Phase 2 Regression Check

**Step 1: Run attractor tests**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest -q
```

Expected: All tests pass.

**Step 2: Run execution-environments tests**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-execution-environments
uv run pytest tests/ -q
```

Expected: All tests pass.

---

## Phase 3: Gemini E2E Tests

---

### Task 11: Create Gemini Agent E2E Profile

**Files:**
- Create: `amplifier-bundle-attractor/profiles/attractor-e2e-gemini.yaml`

**Step 1: Create the profile**

Create `amplifier-bundle-attractor/profiles/attractor-e2e-gemini.yaml` with exactly this content:

```yaml
bundle:
  name: attractor-e2e-gemini
  version: 0.1.0
  description: E2E test profile - Gemini agent (no pipeline)

providers:
  - module: provider-gemini
    source: git+https://github.com/microsoft/amplifier-module-provider-gemini@main
    config:
      default_model: gemini-2.5-pro

session:
  orchestrator:
    module: loop-agent
    source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
    config:
      max_tool_rounds_per_input: 20
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main

tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
    config:
      timeout: 10
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main
  - module: tool-web
    source: git+https://github.com/microsoft/amplifier-module-tool-web@main
  - module: tool-report-outcome
    source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/tool-report-outcome

context:
  include:
    - context/system-gemini.md

hooks:
  - module: hooks-tool-truncation
    source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/hooks-tool-truncation
```

**Step 2: Verify the YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('amplifier-bundle-attractor/profiles/attractor-e2e-gemini.yaml'))" && echo "YAML valid"
```

Expected: `YAML valid`

---

### Task 12: Create Gemini Pipeline E2E Profile

**Files:**
- Create: `amplifier-bundle-attractor/profiles/attractor-e2e-pipeline-gemini.yaml`

**Step 1: Create the profile**

Create `amplifier-bundle-attractor/profiles/attractor-e2e-pipeline-gemini.yaml` with exactly this content:

```yaml
bundle:
  name: attractor-e2e-pipeline-gemini
  version: 0.1.0
  description: E2E test profile - Gemini pipeline (spawns agent sessions)

includes:
  - bundle: attractor:behaviors/attractor-core

agents:
  attractor-gemini:
    description: Gemini coding agent for pipeline nodes
    session:
      orchestrator:
        module: loop-agent
        source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-agent
        config: {max_tool_rounds_per_input: 50, default_command_timeout_ms: 120000}

providers:
  - module: provider-gemini
    source: git+https://github.com/microsoft/amplifier-module-provider-gemini@main
    config:
      default_model: gemini-2.5-pro
  - module: provider-mock
    source: git+https://github.com/microsoft/amplifier-module-provider-mock@main

session:
  orchestrator:
    module: loop-pipeline
    source: git+https://github.com/microsoft/amplifier-bundle-attractor@main#subdirectory=modules/loop-pipeline
    config:
      dot_file: ./tests/e2e/fixtures/simple_file_creation.dot
      profiles:
        gemini: attractor-gemini
  context:
    module: context-simple
    source: git+https://github.com/microsoft/amplifier-module-context-simple@main
    config:
      max_tokens: 300000
      compact_threshold: 0.8
      auto_compact: true

tools:
  - module: tool-filesystem
    source: git+https://github.com/microsoft/amplifier-module-tool-filesystem@main
  - module: tool-bash
    source: git+https://github.com/microsoft/amplifier-module-tool-bash@main
    config:
      timeout: 10
  - module: tool-search
    source: git+https://github.com/microsoft/amplifier-module-tool-search@main

hooks:
  - module: hooks-logging
    config:
      additional_events:
        - "pipeline:start"
        - "pipeline:complete"
        - "pipeline:node_start"
        - "pipeline:node_complete"
        - "pipeline:edge_selected"
        - "pipeline:checkpoint"
        - "pipeline:goal_gate_check"
        - "pipeline:error"
        - "pipeline:stage_retrying"
        - "pipeline:stage_failed"

context:
  include:
    - context/system-gemini.md
```

**Step 2: Verify the YAML is valid**

```bash
python3 -c "import yaml; yaml.safe_load(open('amplifier-bundle-attractor/profiles/attractor-e2e-pipeline-gemini.yaml'))" && echo "YAML valid"
```

Expected: `YAML valid`

---

### Task 13: Create `test_gemini_agent.py`

**Files:**
- Create: `amplifier-bundle-attractor/tests/e2e/test_gemini_agent.py`

**Step 1: Create the test file**

Create `amplifier-bundle-attractor/tests/e2e/test_gemini_agent.py` with exactly this content:

```python
"""Gemini agent E2E tests.

These tests invoke the Gemini agent via the amplifier CLI and verify
real behavior against the Google AI API. They are skipped automatically
when GOOGLE_API_KEY is not set.

Run manually:
    cd /tmp/gemini-e2e-test && GOOGLE_API_KEY=<key> \
    pytest <BUNDLE_ROOT>/tests/e2e/test_gemini_agent.py -v -s

Prerequisites:
    - amplifier CLI installed and on PATH
    - GOOGLE_API_KEY set in environment
    - Working directory with write access (tests create files)
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# Gate all tests in this file behind GOOGLE_API_KEY
pytestmark = pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY not set — skipping Gemini E2E tests",
)

# Resolve bundle root relative to this file (tests/e2e/ -> bundle root)
BUNDLE_ROOT = str(Path(__file__).parent.parent.parent.resolve())
PROFILE_PATH = str(Path(BUNDLE_ROOT) / "profiles" / "attractor-e2e-gemini.yaml")

# Generous timeout — LLM calls can be slow
E2E_TIMEOUT_SECONDS = 180


def _run_agent(workdir: str, instruction: str, timeout: int = E2E_TIMEOUT_SECONDS) -> subprocess.CompletedProcess:
    """Run the Gemini agent via amplifier CLI and return the result."""
    return subprocess.run(
        [
            "amplifier",
            "run",
            "-B", f"file://{PROFILE_PATH}",
            "--mode", "single",
            instruction,
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=workdir,
        env={**os.environ},
    )


# ---------------------------------------------------------------------------
# E2E Test 1: Basic agent invocation
# ---------------------------------------------------------------------------


def test_gemini_agent_basic_invocation(tmp_path):
    """Gemini agent starts, completes a task, and exits without error."""
    result = _run_agent(
        workdir=str(tmp_path),
        instruction="Write a Python function called add(a, b) that returns a + b. Respond with just the code.",
    )
    # Agent should exit 0 (success)
    assert result.returncode == 0, (
        f"Agent exited with code {result.returncode}.\n"
        f"STDOUT: {result.stdout[:1000]}\n"
        f"STDERR: {result.stderr[:500]}"
    )
    # Output should mention Python or the function
    combined = (result.stdout + result.stderr).lower()
    assert "def add" in combined or "return a + b" in combined or "python" in combined, (
        f"Expected Python function in output, got:\n{result.stdout[:500]}"
    )


# ---------------------------------------------------------------------------
# E2E Test 2: File creation with edit_file tool
# ---------------------------------------------------------------------------


def test_gemini_agent_creates_file(tmp_path):
    """Gemini agent can create a file using the filesystem tool."""
    result = _run_agent(
        workdir=str(tmp_path),
        instruction=(
            "Create a file called hello.py that prints 'Hello from Gemini'. "
            "Use the write_file tool."
        ),
    )
    assert result.returncode == 0, (
        f"Agent failed (exit {result.returncode}):\n"
        f"STDOUT: {result.stdout[:500]}\nSTDERR: {result.stderr[:500]}"
    )
    hello_py = tmp_path / "hello.py"
    assert hello_py.exists(), (
        f"hello.py was not created.\nAGENT OUTPUT: {result.stdout[:500]}"
    )
    content = hello_py.read_text()
    assert "Gemini" in content or "Hello" in content, (
        f"hello.py content unexpected: {content[:200]}"
    )


# ---------------------------------------------------------------------------
# E2E Test 3: Web tools (Gemini-unique capability)
# ---------------------------------------------------------------------------


def test_gemini_agent_can_use_web_search(tmp_path):
    """Gemini agent has access to web_search tool and can use it."""
    result = _run_agent(
        workdir=str(tmp_path),
        instruction=(
            "Use the web_search tool to search for 'Python asyncio documentation'. "
            "Tell me the first result title you find."
        ),
    )
    assert result.returncode == 0, (
        f"Agent failed (exit {result.returncode}):\n"
        f"STDOUT: {result.stdout[:500]}\nSTDERR: {result.stderr[:500]}"
    )
    combined = (result.stdout + result.stderr).lower()
    assert "python" in combined or "asyncio" in combined or "search" in combined, (
        f"Expected web search result mention, got:\n{result.stdout[:500]}"
    )


# ---------------------------------------------------------------------------
# E2E Test 4: Multi-turn conversation (read then edit)
# ---------------------------------------------------------------------------


def test_gemini_agent_read_then_edit(tmp_path):
    """Gemini agent can read an existing file and edit it."""
    # Set up a pre-existing file
    existing_file = tmp_path / "existing.py"
    existing_file.write_text("print('original content')\n")

    result = _run_agent(
        workdir=str(tmp_path),
        instruction=(
            "Read the file existing.py, then edit it to also print 'added line'. "
            "Use read_file then edit_file tools."
        ),
    )
    assert result.returncode == 0, (
        f"Agent failed (exit {result.returncode}):\n"
        f"STDOUT: {result.stdout[:500]}\nSTDERR: {result.stderr[:500]}"
    )
    content = existing_file.read_text()
    assert "original" in content, f"Original content lost: {content}"
    assert "added" in content or "added line" in content, (
        f"Expected 'added line' in file, got: {content}"
    )
```

**Step 2: Verify the test file is syntactically valid**

```bash
python3 -m py_compile amplifier-bundle-attractor/tests/e2e/test_gemini_agent.py && echo "Syntax OK"
```

Expected: `Syntax OK`

**Step 3: Run with `--collect-only` to verify pytest can discover tests**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest tests/e2e/test_gemini_agent.py --collect-only 2>&1 | head -30
```

Expected: Pytest discovers 4 tests but shows them as SKIPPED (because GOOGLE_API_KEY is not set in this context).

---

### Task 14: Create `test_gemini_pipeline.py`

**Files:**
- Create: `amplifier-bundle-attractor/tests/e2e/test_gemini_pipeline.py`

**Step 1: Create the test file**

Create `amplifier-bundle-attractor/tests/e2e/test_gemini_pipeline.py` with exactly this content:

```python
"""Gemini pipeline E2E test.

Runs a DOT graph pipeline with Gemini as the provider via the attractor
pipeline profile. Verifies that the loop-pipeline orchestrator can execute
a multi-node graph using the Gemini agent.

Run manually:
    cd /tmp/gemini-pipeline-test && GOOGLE_API_KEY=<key> \
    pytest <BUNDLE_ROOT>/tests/e2e/test_gemini_pipeline.py -v -s

Prerequisites:
    - amplifier CLI installed and on PATH
    - GOOGLE_API_KEY set in environment
    - Working directory with write access
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

# Gate all tests in this file behind GOOGLE_API_KEY
pytestmark = pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY not set — skipping Gemini pipeline E2E tests",
)

# Resolve bundle root
BUNDLE_ROOT = str(Path(__file__).parent.parent.parent.resolve())
PIPELINE_PROFILE_PATH = str(
    Path(BUNDLE_ROOT) / "profiles" / "attractor-e2e-pipeline-gemini.yaml"
)
FIXTURES_DIR = str(Path(BUNDLE_ROOT) / "tests" / "e2e" / "fixtures")

# Pipeline tests are slow (multiple LLM calls)
PIPELINE_TIMEOUT_SECONDS = 600


def _run_pipeline(workdir: str, instruction: str = "Run the pipeline") -> subprocess.CompletedProcess:
    """Run the Gemini pipeline via amplifier CLI."""
    return subprocess.run(
        [
            "amplifier",
            "run",
            "-B", f"file://{PIPELINE_PROFILE_PATH}",
            "--mode", "single",
            instruction,
        ],
        capture_output=True,
        text=True,
        timeout=PIPELINE_TIMEOUT_SECONDS,
        cwd=workdir,
        env={**os.environ},
    )


# ---------------------------------------------------------------------------
# E2E Test 1: Simple single-node pipeline
# ---------------------------------------------------------------------------


def test_gemini_pipeline_simple_file_creation(tmp_path):
    """Pipeline executes simple_file_creation.dot with Gemini: start -> implement -> done.

    DOT fixture: tests/e2e/fixtures/simple_file_creation.dot
    Expected: Agent creates hello.py in the working directory.
    """
    start_time = time.time()
    result = _run_pipeline(
        workdir=str(tmp_path),
        instruction="Run the pipeline",
    )
    elapsed = time.time() - start_time

    print(f"\nPipeline completed in {elapsed:.1f}s")
    print(f"Exit code: {result.returncode}")
    print(f"STDOUT (last 500): {result.stdout[-500:]}")
    if result.stderr:
        print(f"STDERR (last 200): {result.stderr[-200:]}")

    assert result.returncode == 0, (
        f"Pipeline exited with code {result.returncode}.\n"
        f"STDOUT: {result.stdout[-1000:]}\n"
        f"STDERR: {result.stderr[-500:]}"
    )

    # The implement node's prompt asks it to create hello.py
    hello_py = tmp_path / "hello.py"
    assert hello_py.exists(), (
        f"hello.py was not created by the pipeline.\n"
        f"Files in workdir: {list(tmp_path.iterdir())}\n"
        f"AGENT OUTPUT: {result.stdout[-500:]}"
    )

    # Verify the file contains something Python-like
    content = hello_py.read_text()
    assert len(content) > 0, "hello.py is empty"
    print(f"hello.py content ({len(content)} bytes): {content[:200]}")
```

**Step 2: Verify the test file is syntactically valid**

```bash
python3 -m py_compile amplifier-bundle-attractor/tests/e2e/test_gemini_pipeline.py && echo "Syntax OK"
```

Expected: `Syntax OK`

**Step 3: Run with `--collect-only` to verify pytest discovers the test**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest tests/e2e/test_gemini_pipeline.py --collect-only 2>&1 | head -20
```

Expected: Discovers 1 test, shows it as SKIPPED (no GOOGLE_API_KEY).

---

### Task 15: Update MANUAL_E2E.md with Gemini Test Docs

**Files:**
- Modify: `amplifier-bundle-attractor/tests/e2e/MANUAL_E2E.md`

**Step 1: Add Gemini section to the bottom of MANUAL_E2E.md**

Open `amplifier-bundle-attractor/tests/e2e/MANUAL_E2E.md` and append the following at the end (after the Troubleshooting section):

```markdown

---

## Gemini E2E Tests

These tests require a real Google AI API key.

### Prerequisites
- `GOOGLE_API_KEY` set in environment
- `amplifier` CLI installed and on PATH
- Working directory with write access

### Running via pytest

```bash
# Set up a temp dir and run all Gemini E2E tests
mkdir -p /tmp/gemini-e2e && cd /tmp/gemini-e2e
GOOGLE_API_KEY=<your-key> pytest <BUNDLE_ROOT>/tests/e2e/test_gemini_agent.py -v -s
GOOGLE_API_KEY=<your-key> pytest <BUNDLE_ROOT>/tests/e2e/test_gemini_pipeline.py -v -s
```

### Gemini Agent Tests (E2E G1-G4)

Use profile: `profiles/attractor-e2e-gemini.yaml`

#### E2E G1: Basic invocation

```bash
mkdir -p /tmp/gemini-e2e-g1 && cd /tmp/gemini-e2e-g1
amplifier run -B "file://<BUNDLE_ROOT>/profiles/attractor-e2e-gemini.yaml" \
  --mode single \
  "Write a Python function called add(a, b) that returns a + b. Respond with just the code."
```

**Expected:** Output contains a Python `def add` function.

#### E2E G2: File creation

```bash
mkdir -p /tmp/gemini-e2e-g2 && cd /tmp/gemini-e2e-g2
amplifier run -B "file://<BUNDLE_ROOT>/profiles/attractor-e2e-gemini.yaml" \
  --mode single \
  "Create a file called hello.py that prints 'Hello from Gemini'. Use the write_file tool."
```

**Expected:** `hello.py` exists. Verify: `test -f hello.py && cat hello.py`

#### E2E G3: Web search (Gemini-unique)

```bash
mkdir -p /tmp/gemini-e2e-g3 && cd /tmp/gemini-e2e-g3
amplifier run -B "file://<BUNDLE_ROOT>/profiles/attractor-e2e-gemini.yaml" \
  --mode single \
  "Use the web_search tool to search for 'Python asyncio documentation'. Tell me the first result title."
```

**Expected:** Output mentions asyncio and a URL or title.

#### E2E G4: Read and edit

```bash
mkdir -p /tmp/gemini-e2e-g4 && cd /tmp/gemini-e2e-g4
echo "print('original content')" > existing.py
amplifier run -B "file://<BUNDLE_ROOT>/profiles/attractor-e2e-gemini.yaml" \
  --mode single \
  "Read the file existing.py, then edit it to also print 'added line'. Use read_file then edit_file."
```

**Expected:** `existing.py` contains both "original" and "added". Verify: `grep -q original existing.py && grep -q added existing.py`

### Gemini Pipeline Test (E2E P1)

Use profile: `profiles/attractor-e2e-pipeline-gemini.yaml`

#### E2E P1: Simple file creation pipeline

DOT fixture: `tests/e2e/fixtures/simple_file_creation.dot`

```bash
mkdir -p /tmp/gemini-pipeline-e2e && cd /tmp/gemini-pipeline-e2e
amplifier run -B "file://<BUNDLE_ROOT>/profiles/attractor-e2e-pipeline-gemini.yaml" \
  --mode single \
  "Run the pipeline"
```

**Expected:**
- Pipeline executes: start → implement → done
- `hello.py` is created in the working directory
- Verify: `test -f hello.py && python3 hello.py`

### Timeout Guidance (Gemini)

| Test Type | Typical Duration | Suggested Timeout |
|-----------|-----------------|-------------------|
| Agent (G1-G4) | 30-120s | 180s |
| Pipeline single-node (P1) | 90-300s | 600s |

### Troubleshooting

- **"No provider configured"**: Check `GOOGLE_API_KEY` is set
- **Rate limit errors**: Add a delay between tests or use a project with higher quota
- **web_search fails**: Verify `tool-web` module is available in the profile
- **Pipeline hangs**: Check that `dot_file` path resolves correctly relative to the profile
```

**Step 2: Commit all Phase 3 files**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
git add profiles/attractor-e2e-gemini.yaml \
        profiles/attractor-e2e-pipeline-gemini.yaml \
        tests/e2e/test_gemini_agent.py \
        tests/e2e/test_gemini_pipeline.py \
        tests/e2e/MANUAL_E2E.md
git commit -m "feat: add Gemini E2E tests and profiles (agent + pipeline)"
```

---

### Task 16: Final Verification

**Step 1: Run the full attractor test suite**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
uv run pytest -q
```

Expected: All tests pass. The Gemini E2E tests are collected but SKIPPED (no `GOOGLE_API_KEY` in CI environment).

**Step 2: Run the full execution-environments test suite**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-execution-environments
uv run pytest tests/ -q
```

Expected: All tests pass.

**Step 3: Verify all YAML files are valid**

```bash
python3 -c "
import yaml, pathlib
for f in pathlib.Path('amplifier-bundle-attractor').rglob('*.yaml'):
    try:
        yaml.safe_load(f.read_text())
    except yaml.YAMLError as e:
        print(f'INVALID YAML: {f}: {e}')
        exit(1)
print('All YAML files valid')
"
```

Expected: `All YAML files valid`

**Step 4: Verify git log is clean in both repos**

```bash
cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor
git log --oneline -10

cd /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-execution-environments
git log --oneline -5
```

Expected: Commits are present for each phase/task. No uncommitted changes.

**Step 5: If running Gemini E2E tests manually (optional, requires GOOGLE_API_KEY)**

```bash
cd /tmp && mkdir -p gemini-e2e-run && cd gemini-e2e-run
GOOGLE_API_KEY="<your-key>" pytest \
  /home/bkrabach/dev/attractor-dev-machine/amplifier-bundle-attractor/tests/e2e/test_gemini_agent.py \
  -v -s --timeout=180
```

Expected: All 4 agent tests pass.

---

## Summary of All Changed Files

### `amplifier-bundle-attractor`

| File | Task | Change |
|------|------|--------|
| `agents/attractor-agent-openai.yaml` | D-05, D-07 | Timeout fix + switch to native apply_patch |
| `agents/attractor-agent-gemini.yaml` | D-05 | Timeout fix |
| `modules/loop-pipeline/amplifier_module_loop_pipeline/engine.py` | D-08 | One-hop fidelity flag |
| `modules/loop-pipeline/tests/test_batch_d_pipeline_infra.py` | D-08 | Update + new test |
| `modules/tool-pipeline-run/amplifier_module_tool_pipeline_run/__init__.py` | NF-03 | Fix provider extraction |
| `context/isolated-environment-guidance.md` | D-07 | Add env_apply_patch |
| `profiles/attractor-e2e-gemini.yaml` | Gemini E2E | New profile |
| `profiles/attractor-e2e-pipeline-gemini.yaml` | Gemini E2E | New profile |
| `tests/e2e/test_gemini_agent.py` | Gemini E2E | New test file |
| `tests/e2e/test_gemini_pipeline.py` | Gemini E2E | New test file |
| `tests/e2e/MANUAL_E2E.md` | Gemini E2E | Gemini section added |

### `amplifier-bundle-execution-environments`

| File | Task | Change |
|------|------|--------|
| `modules/tools-env-all/amplifier_module_tools_env_all/apply_diff.py` | D-07 | Vendored (copied from filesystem bundle) |
| `modules/tools-env-all/amplifier_module_tools_env_all/dispatch.py` | D-07 | Add EnvApplyPatchTool class |
| `modules/tools-env-all/amplifier_module_tools_env_all/__init__.py` | D-07 | Register EnvApplyPatchTool |
| `tests/test_dispatch.py` | D-07 | Tests for EnvApplyPatchTool |

### No changes in:
- `amplifier-bundle-filesystem` (read-only source)
- `amplifier-module-provider-openai` (already has native apply_patch support)
