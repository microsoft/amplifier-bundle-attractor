from dataclasses import FrozenInstanceError

import pytest

from amplifier_module_remote_source.limits import FetchLimits


def test_defaults():
    lim = FetchLimits()
    assert lim.max_depth == 10
    assert lim.max_files == 100
    assert lim.max_total_bytes == 10 * 1024 * 1024
    assert lim.max_concurrency == 8


def test_override():
    lim = FetchLimits(max_depth=2, max_files=3)
    assert (lim.max_depth, lim.max_files) == (2, 3)


def test_frozen():
    lim = FetchLimits()
    with pytest.raises(FrozenInstanceError):
        lim.max_depth = 99  # type: ignore[misc]
