"""Unit tests for amplifier_module_pipeline_runner.runner.seed_context.

Uses a tiny fake context object (not the real PipelineContext) -- these tests
assert only the seeding contract (flat params + reserved key + collision
guard), not engine internals. No DOT/graph/call-order assertions.
"""

from __future__ import annotations

import pytest

from amplifier_module_pipeline_runner.runner import seed_context


class FakeContext:
    """Minimal stand-in for PipelineContext -- captures .set() calls."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def set(self, key: str, value: str) -> None:
        self.values[key] = value


def test_user_params_seeded_flat():
    ctx = FakeContext()
    seed_context(ctx, {"outfile": "proof.txt", "content": "ok"}, "/tmp/work")

    assert ctx.values["outfile"] == "proof.txt"
    assert ctx.values["content"] == "ok"


def test_target_dir_set_to_str_cwd():
    ctx = FakeContext()
    seed_context(ctx, {}, "/tmp/work")

    assert ctx.values["context.target_dir"] == "/tmp/work"


def test_target_dir_set_with_path_object(tmp_path):
    ctx = FakeContext()
    seed_context(ctx, None, tmp_path)

    assert ctx.values["context.target_dir"] == str(tmp_path)


def test_reserved_key_collision_raises():
    ctx = FakeContext()
    with pytest.raises(ValueError):
        seed_context(ctx, {"context.target_dir": "/sneaky/path"}, "/tmp/work")

    # Nothing should have been seeded -- guard fires before any context.set call.
    assert ctx.values == {}
