"""Tool node handler — executes shell commands.

Reads the tool_command from node attributes, executes it via subprocess,
captures stdout, and returns SUCCESS or FAIL based on exit code.

Supported attributes:
    tool_command   — Shell command to execute (required)
    parse_json     — If "true", json.loads() the stdout; if result is a dict,
                     inject each key-value into context and context_updates;
                     on JSONDecodeError logs WARNING and continues (SUCCESS)
    tool_env       — Comma-separated list of context variable names to expose
                     as uppercase environment variables to the subprocess

Routing via tool.last_line:
    The last non-empty line of stdout is extracted and stored in context as ``tool.last_line``.
    This enables condition-based edge routing using condition="context.tool.last_line=<label>":

        RunTests [shape=parallelogram, tool_command="... && echo tests_pass || echo tests_fail"];
        RunTests -> Pass  [condition="context.tool.last_line=tests_pass"];
        RunTests -> Retry [condition="context.tool.last_line=tests_fail"];

    Unlike setting outcome.preferred_label, storing in context preserves the standard
    condition="outcome=success" routing behaviour for tool nodes whose output is not a
    routing label (e.g. the "routing" echo in existing tests).

Spec coverage: TOOL-001–004, Section 4.10.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from ..context import PipelineContext
from ..graph import Graph, Node
from ..outcome import Outcome, StageStatus
from ..substitution import substitute_context

logger = logging.getLogger(__name__)

_LOG_TRUNCATE_CHARS = 200

# Issue 10 / analog of WS-4 Sub-fix C: bounded tail sizes for failed_step payload.
_STDERR_TAIL_BYTES = 2048  # last 2 KiB kept per-stream
_STDOUT_TAIL_BYTES = 2048  # last 2 KiB kept per-stream
_TOTAL_CAP_BYTES = 8192  # 8 KiB total JSON-serialised cap
_CMD_INITIAL_CAP = 500  # command captured at up to 500 chars
_CMD_TRUNCATE_CAP = 200  # step-3 truncation drops to 200 chars
_STDERR_TRUNCATE_BYTES = 1024  # step-2 truncation drops stderr to 1 KiB


class ToolHandler:
    """Handler for tool nodes (shape=parallelogram).

    Executes an external command configured via the node's
    ``tool_command`` attribute.
    """

    async def execute(
        self,
        node: Node,
        context: PipelineContext,
        graph: Graph,
        logs_root: str,
    ) -> Outcome:
        """Execute a tool command and return the outcome.

        Writes command.txt and output.txt to the stage log directory.
        Stores stdout in context as ``tool.output``.
        """
        command = node.attrs.get("tool_command", "")
        if not command:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason="No tool_command specified on node",
            )

        # M5 (R12): Expand $variable and ${variable} tokens from pipeline context.
        # Drops the old "." guard — dotted keys (e.g. ${tool.output}) now
        # resolve correctly when their producer succeeded.  Missing keys are
        # left as literal tokens; failed-key detection was already handled
        # by the engine's eager pre-execution scan (M2) before this handler ran.
        snapshot = context.snapshot()
        command = substitute_context(command, snapshot)

        # Write command to logs
        stage_dir = os.path.join(logs_root, node.id)
        os.makedirs(stage_dir, exist_ok=True)
        _write_file(os.path.join(stage_dir, "command.txt"), command)

        # M-16: Read timeout from node attribute (seconds)
        timeout_s: float | None = None
        if node.timeout is not None:
            timeout_s = float(node.timeout)

        # Build environment for subprocess: handle tool_env attribute
        env: dict[str, str] | None = None
        tool_env_attr = node.attrs.get("tool_env", "")
        if tool_env_attr:
            env = dict(os.environ)
            var_names = [v.strip() for v in tool_env_attr.split(",") if v.strip()]
            for var_name in var_names:
                value = context.get(var_name)
                if value is not None:
                    env[var_name.upper()] = str(value)

        # Resolve working directory: prefer context.target_dir (the session's
        # project directory where pipeline output files are created), then fall
        # back to graph.source_dir (the DOT file's directory), then None.
        # Without this, tool_command scripts that grep/read files created in
        # the project directory fail when graph.source_dir is None (built-in
        # pipelines loaded via the resolver) because the subprocess defaults
        # to /workspace/ instead of /workspace/project/.
        cwd: str | None = context.get("context.target_dir") or graph.source_dir or None

        try:
            t0 = time.monotonic()
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_s
                )
            except asyncio.TimeoutError:
                # Kill the process on timeout
                proc.kill()
                await proc.wait()
                return Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=f"Timeout after {timeout_s}s: {command}",
                )
            duration_s = round(time.monotonic() - t0, 3)
            stdout_text = stdout_bytes.decode(errors="replace") if stdout_bytes else ""
            stderr_text = stderr_bytes.decode(errors="replace") if stderr_bytes else ""

            # Write output to logs
            _write_file(
                os.path.join(stage_dir, "output.txt"),
                stdout_text + stderr_text,
            )

            if proc.returncode != 0:
                # proc.communicate() guarantees returncode is set; the or-0 is
                # a type-narrowing hint for static checkers (never actually 0
                # since we're inside the returncode != 0 branch).
                exit_code: int = proc.returncode or -1
                return Outcome(
                    status=StageStatus.FAIL,
                    failure_reason=(
                        f"Command exited with code {exit_code}: "
                        f"{stderr_text.strip() or stdout_text.strip()}"
                    ),
                    failed_step=_build_failed_step(
                        command=command,
                        exit_code=exit_code,
                        duration_s=duration_s,
                        stdout_text=stdout_text,
                        stderr_text=stderr_text,
                    ),
                )

            # Store stdout in context
            context.set("tool.output", stdout_text)
            context_updates: dict[str, Any] = {"tool.output": stdout_text}

            # Handle parse_json attribute
            if node.attrs.get("parse_json", "") == "true":
                try:
                    parsed = json.loads(stdout_text)
                    if isinstance(parsed, dict):
                        for key, value in parsed.items():
                            context.set(key, value)
                            context_updates[key] = value
                except json.JSONDecodeError:
                    logger.warning(
                        "parse_json: failed to parse JSON output from node %r: %r",
                        node.id,
                        stdout_text[:_LOG_TRUNCATE_CHARS],
                    )

            # Extract the last non-empty stdout line into tool.last_line.
            # Tool commands that echo a routing label as their final output
            # (e.g. "echo tests_pass") can then be routed via
            # condition="context.tool.last_line=tests_pass" on outgoing edges.
            # This avoids polluting outcome.preferred_label, which would break
            # condition="outcome=success" routing on tool nodes whose output is
            # not a routing label.
            # Always emit tool.last_line so the inferred output contract in
            # HANDLER_INFERRED_OUTPUTS["tool"] holds even when stdout is empty.
            # Empty stdout → tool.last_line = "". Downstream edge conditions
            # that gate on a non-empty value will simply not match, which is
            # the correct behaviour (no routing label was produced).
            # Rationale: option (a) from the zen-architect review — emit "" on
            # empty stdout rather than silently omitting the key and triggering
            # a false-positive PIPELINE_NODE_CONTRACT_VIOLATION.
            last_line = next(
                (
                    line.strip()
                    for line in reversed(stdout_text.splitlines())
                    if line.strip()
                ),
                "",
            )
            context.set("tool.last_line", last_line)
            context_updates["tool.last_line"] = last_line

            return Outcome(
                status=StageStatus.SUCCESS,
                context_updates=context_updates,
                notes=f"Tool completed: {command}",
            )
        except Exception as e:
            return Outcome(
                status=StageStatus.FAIL,
                failure_reason=str(e),
            )


def _write_file(path: str, content: str) -> None:
    """Write content to a file."""
    with open(path, "w") as f:
        f.write(content)


def _build_failed_step(
    *,
    command: str,
    exit_code: int,
    duration_s: float,
    stdout_text: str,
    stderr_text: str,
) -> dict[str, Any]:
    """Build the ``failed_step`` payload for a failed tool invocation.

    Issue 10 / analog of WS-4 Sub-fix C for in-pipeline tool nodes.

    Captures command, exit_code, duration_s, stdout_tail, and stderr_tail.
    The subprocess stdout/stderr are already in memory at call time so there
    is no extra allocation; the helper wraps them and applies an 8 KiB
    JSON-serialised total cap with the following truncation order (mirrors
    WS-4 Sub-fix C):

        1. Drop ``stdout_tail`` first (least useful for failure diagnosis)
        2. Truncate ``stderr_tail`` to last ``_STDERR_TAIL_BYTES`` (2 KiB)
        3. Truncate ``command`` to ``_CMD_TRUNCATE_CAP`` chars (200)

    When step 1 fires, ``verification_gap.log_filtered`` is set to ``True``
    so consumers know information was dropped.

    Empty stdout / stderr produce ``""`` (never ``None``).
    """
    # stdout/stderr are used in full — the 8 KiB cap below decides what to keep.
    # Empty inputs → empty string, never None (mirrors WS-6 R12.5 Issue 4).
    failed_step: dict[str, Any] = {
        "command": command[:_CMD_INITIAL_CAP],
        "exit_code": exit_code,
        "duration_s": duration_s,
        "stdout_tail": stdout_text or "",
        "stderr_tail": stderr_text or "",
    }

    # 8 KiB cap with documented truncation order.
    # When step 1 fires, set verification_gap.log_filtered=True so
    # consumers know information was dropped.
    _truncated = False
    if len(json.dumps(failed_step)) > _TOTAL_CAP_BYTES:
        # Step 1: drop stdout_tail first (success output, least useful for diagnosis)
        failed_step = dict(failed_step)
        del failed_step["stdout_tail"]
        _truncated = True
    if len(json.dumps(failed_step)) > _TOTAL_CAP_BYTES:
        # Step 2: truncate stderr_tail to last 2 KiB
        failed_step = dict(failed_step)
        failed_step["stderr_tail"] = failed_step["stderr_tail"][-_STDERR_TAIL_BYTES:]
    if len(json.dumps(failed_step)) > _TOTAL_CAP_BYTES:
        # Step 3: truncate command to 200 chars
        failed_step = dict(failed_step)
        failed_step["command"] = failed_step["command"][:_CMD_TRUNCATE_CAP]
    if _truncated:
        failed_step["verification_gap"] = {
            "log_filtered": True,
            "where": "tool_handler:8KiB-cap",
        }

    return failed_step
