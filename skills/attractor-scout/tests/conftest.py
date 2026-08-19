"""Standalone pytest wiring for the attractor-scout engine layer.

These tests are runnable on their own (`pytest skills/attractor-scout/tests`)
with nothing but the standard library. They deliberately do NOT import
anything from the rest of this repo: the engine layer is additive and must
not couple itself to the pipeline modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

SKILL_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SKILL_DIR / "scripts"))
sys.path.insert(0, str(SKILL_DIR))


@pytest.fixture
def corpus_root(tmp_path: Path) -> Path:
    root = tmp_path / "projects"
    root.mkdir(parents=True, exist_ok=True)
    return root
