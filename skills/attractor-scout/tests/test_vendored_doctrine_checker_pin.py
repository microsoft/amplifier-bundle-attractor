"""The drift tripwire on the vendored doctrine checker.

`scripts/attractor_scout/authoring_contract.py` is a BYTE-IDENTICAL copy of
`examples/authoring/check_authored_pipeline.py`. Vendoring — rather than
importing the repo file by relative path — is what lets this skill's suite
exercise the gate hermetically (its conftest forbids cross-repo imports) and
what keeps the skill working when it is installed without the full repo tree.

The cost of that choice is drift, and this file is the price paid for it: a
sha256 equality check that turns "someone improved the upstream checker and
the vendored second opinion silently kept its old mind" from a quiet wrong
answer into a red test. Upstream change now costs exactly one `cp`.

The repo already sanctions this pattern twice: the upstream checker is itself
an adapted copy of `examples/objective/check_child_contract.py` carrying its
own agreement test, and `test_quality_protocol_guard.py` Q-307 pins two copies
of the decision matrix to each other.

Both checks SKIP when the repo file is absent — that is the standalone-install
case, not a failure.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from attractor_scout import demo_templates as T

SKILL_DIR = Path(__file__).resolve().parent.parent
BUNDLE_ROOT = SKILL_DIR.parent.parent

VENDORED = SKILL_DIR / "scripts" / "attractor_scout" / "authoring_contract.py"
UPSTREAM = BUNDLE_ROOT / "examples" / "authoring" / "check_authored_pipeline.py"
DOT_REFERENCE = BUNDLE_ROOT / "context" / "dot-reference.md"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_vendored_copy_exists_and_is_importable():
    assert VENDORED.is_file(), "the doctrine checker must ship INSIDE the skill (bundle-only installs)"
    from attractor_scout.authoring_contract import parse_dot_min, run_checks

    assert callable(parse_dot_min)
    assert callable(run_checks)


def test_vendored_checker_is_byte_identical_to_upstream():
    if not UPSTREAM.is_file():
        pytest.skip("standalone install: the repo's examples/authoring tree is not present")
    vendored_sha = _sha256(VENDORED)
    upstream_sha = _sha256(UPSTREAM)
    assert vendored_sha == upstream_sha, (
        "the vendored doctrine checker has DRIFTED from "
        f"{UPSTREAM.relative_to(BUNDLE_ROOT)}.\n"
        f"  vendored: {vendored_sha}\n"
        f"  upstream: {upstream_sha}\n"
        "A stale second opinion returns a confidently wrong answer. Re-copy it:\n"
        f"  cp {UPSTREAM.relative_to(BUNDLE_ROOT)} "
        f"{VENDORED.relative_to(BUNDLE_ROOT)}"
    )


def test_every_vocabulary_attribute_in_the_brief_is_real():
    """The brief teaches a fresh-context delegate the engine's vocabulary.

    An invented attribute is not an error at runtime — it is SILENTLY DROPPED,
    and the graph runs unconfigured. So the excerpt may never drift into a
    spelling the engine does not read.
    """
    if not DOT_REFERENCE.is_file():
        pytest.skip("standalone install: the repo's context/dot-reference.md is not present")
    reference = DOT_REFERENCE.read_text(encoding="utf-8")
    missing = [attr for attr in T.VOCAB_ATTRIBUTES if f"{attr}=" not in reference]
    assert not missing, (
        f"the brief's vocabulary excerpt names attribute(s) absent from "
        f"{DOT_REFERENCE.relative_to(BUNDLE_ROOT)}: {missing}. An attribute that is not on that "
        f"page is not read by anything."
    )


def test_every_vocabulary_attribute_appears_in_the_excerpt_itself():
    for attr in T.VOCAB_ATTRIBUTES:
        assert f"{attr}=" in T.VOCAB_EXCERPT, f"{attr} is listed but not actually taught"


def test_the_vendored_checker_and_the_demo_layer_share_one_parser():
    """One parser, one truth: the node-name check must not invent a second."""
    import inspect

    from attractor_scout import demo

    source = inspect.getsource(demo.dot_node_ids)
    assert "authoring_contract" in source
    assert "parse_dot_min" in source
