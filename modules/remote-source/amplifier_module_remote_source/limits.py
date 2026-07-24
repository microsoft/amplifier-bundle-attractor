"""Fetch-walk safety envelope (Layer A)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FetchLimits:
    """Safety envelope for a recursive fetch walk. v1 uses these constructor
    defaults; external override config keys are deferred."""

    max_depth: int = 10
    max_files: int = 100
    max_total_bytes: int = 10 * 1024 * 1024  # 10 MB
    per_request_timeout: float = 30.0
    max_concurrency: int = 8
