"""amplifier_module_pipeline_runner: reusable engine-driving library + CLI.

Public API:
    drive_engine    -- drive the attractor engine directly given an already-built
                       coordinator (low-level; caller owns session/spawn wiring).
    run_pipeline     -- high-level convenience: builds the prepared bundle,
                       session, and spawn wiring, then calls drive_engine.
    resume_pipeline  -- explicit, opt-in resume of an interrupted run from the
                       checkpoint in its run directory (attractor-spec §5.3).
                       run_pipeline never reads a checkpoint back; resume
                       happens through this entry point or not at all.
    PipelineResult   -- result dataclass returned by run_pipeline.
    parse_param      -- parse a single ``key=value`` (or ``@file`` / ``@@literal``)
                       CLI-style param string.
    DEFAULT_PROFILES -- default llm_provider -> agent-name routing map.
"""

from __future__ import annotations

from .params import parse_param
from .runner import (
    DEFAULT_PROFILES,
    PipelineResult,
    drive_engine,
    resume_pipeline,
    run_pipeline,
)

__all__ = [
    "drive_engine",
    "run_pipeline",
    "resume_pipeline",
    "PipelineResult",
    "parse_param",
    "DEFAULT_PROFILES",
]
