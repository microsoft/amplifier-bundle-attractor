"""Pipeline context store for Attractor pipelines.

A thread-safe key-value store shared across all stages during a pipeline
run. Supports namespaced keys, clone for parallel branch isolation,
snapshot for checkpointing, and an append-only run log.

Spec coverage: CTX-001–005, Section 5.1 (Context)

Namespace conventions (spec Section 5.1):
    context.*   — Semantic state shared between nodes
    graph.*     — Graph attributes mirrored at initialization
    internal.*  — Engine bookkeeping (retry counters, timing)
    parallel.*  — Parallel handler state (results, counts)
    stack.*     — Supervisor/worker state
    human.gate.*— Human interaction state
    work.*      — Per-item context for parallel work items
"""

from __future__ import annotations

import threading
from typing import Any


class PipelineContext:
    """Thread-safe key-value store for pipeline execution state.

    All reads and writes are protected by a read-write lock (implemented
    as a simple mutex for correctness; a RWLock optimization can be added
    later if profiling shows contention).
    """

    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self._logs: list[str] = []
        self._lock = threading.Lock()

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value by key, returning default if missing."""
        with self._lock:
            return self._values.get(key, default)

    def get_string(self, key: str, default: str = "") -> str:
        """Get a value as a string, returning default if missing."""
        value = self.get(key)
        if value is None:
            return default
        return str(value)

    def set(self, key: str, value: Any) -> None:
        """Set a value by key."""
        with self._lock:
            self._values[key] = value

    def pop(self, key: str, default: Any = None) -> Any:
        """Remove a key entirely and return its value (default if absent).

        Deliberately distinct from ``set(key, None)``: a null write leaves the
        key PRESENT, so it surfaces in :meth:`snapshot` and therefore in every
        subsequent ``checkpoint.json`` context. Reserved one-hop keys (e.g.
        ``resume.fidelity_cap``, spec 5.3 rule 6) must leave no trace at all,
        so their clear is a removal, not a null write.
        """
        with self._lock:
            return self._values.pop(key, default)

    def update(self, updates: dict[str, Any]) -> None:
        """Merge multiple key-value pairs into the context."""
        with self._lock:
            self._values.update(updates)

    def snapshot(self) -> dict[str, Any]:
        """Return a shallow copy of all values for checkpointing."""
        with self._lock:
            return dict(self._values)

    def clone(self) -> PipelineContext:
        """Create an independent copy for parallel branch isolation."""
        with self._lock:
            new_ctx = PipelineContext()
            new_ctx._values = dict(self._values)
            new_ctx._logs = list(self._logs)
            return new_ctx

    def append_log(self, entry: str) -> None:
        """Append an entry to the append-only run log."""
        with self._lock:
            self._logs.append(entry)

    def get_logs(self) -> list[str]:
        """Return a copy of the run log."""
        with self._lock:
            return list(self._logs)
