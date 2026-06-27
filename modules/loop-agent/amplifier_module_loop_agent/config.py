"""Session configuration for the coding agent loop.

Spec coverage: CFG-001 through CFG-009.

Provides SessionConfig with all spec defaults and from_dict()
construction for mount-plan integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields


@dataclass
class SessionConfig:
    """Configuration for a coding agent session.

    All fields have spec-defined defaults. Use from_dict() to construct
    from a mount plan configuration dictionary.

    Notes on omitted fields:
    - Command execution timeouts (default/max): owned by the external
      shell/bash tool modules, not by loop-agent (relates to CAL-3/CAL-4
      ExecutionEnvironment gap).
    - Per-tool output char limits and line limits: configured on the
      hooks-tool-truncation module via its own hook config block
      (char_limits / line_limits keys). loop-agent has no channel into
      the truncation hook's config — do not add those fields here.
    """

    max_turns: int = 0  # 0 = unlimited
    max_tool_rounds_per_input: int = 0  # 0 = unlimited (spec CFG default)
    reasoning_effort: str | None = None
    enable_loop_detection: bool = True
    loop_detection_window: int = 10
    max_subagent_depth: int = 1
    current_depth: int = 0  # Current subagent depth (set by parent for child sessions)
    context_window_size: int = 0  # 0 = unknown/unlimited
    system_prompt: str = ""  # Base system prompt (layer 1)
    system_prompt_file: str = (
        ""  # Path to base prompt file (layer 1, relative to bundle root)
    )
    user_instructions: str = ""  # User instruction override (layer 5, highest priority)
    working_dir: str = ""  # Working directory for environment context and project docs
    max_tool_rounds_per_provider: dict[str, int] = field(default_factory=dict)
    supports_parallel_tool_calls: bool = True  # False = sequential tool execution

    @classmethod
    def from_dict(cls, config: dict) -> SessionConfig:
        """Construct from a config dictionary, ignoring unknown keys."""
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in config.items() if k in known})
