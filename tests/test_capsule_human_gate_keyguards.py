from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_PIPELINE_DIR = _REPO_ROOT / ".github" / "capsule-pipeline"
_CONDITION = "outcome=success && context.human.gate.selected={key}"

_CASES = (
    (
        "capsule.dot",
        (
            ("abandon", "[A] Abandon -- keep the postmortem", "A"),
            ("bump_budget", "[C] Continue -- raise the budget", "C"),
            (
                "package",
                "[K] Keep -- accept the capsule as-is, findings and postmortem attached",
                "K",
            ),
        ),
    ),
    (
        "feature-capsule.dot",
        (
            ("abandon", "[A] Abandon -- keep the postmortem", "A"),
            ("bump_budget", "[C] Continue -- raise the budget", "C"),
            (
                "package",
                "[K] Keep -- accept the capsule as-is, findings, questions, and postmortem attached",
                "K",
            ),
        ),
    ),
    (
        "task-runner.dot",
        (
            ("abandon", "[A] Abandon — keep the postmortem", "A"),
            ("bump_budget", "[C] Continue — raise the budget", "C"),
        ),
    ),
)


@pytest.mark.parametrize(("filename", "expected"), _CASES)
def test_escalate_choices_use_canonical_selected_keys(filename, expected):
    lines = [
        line.strip()
        for line in (_PIPELINE_DIR / filename).read_text(encoding="utf-8").splitlines()
        if line.lstrip().startswith("escalate ->")
    ]

    actual = []
    for line in lines:
        target, attributes = line.removeprefix("escalate ->").split("[", maxsplit=1)
        target = target.strip()
        attributes = attributes.removesuffix("]").strip()
        condition, label = attributes.split(", label=", maxsplit=1)
        actual.append((target, label.removeprefix('"').removesuffix('"'), condition))

    assert [(target, label) for target, label, _ in actual] == [
        (target, label) for target, label, _ in expected
    ]
    assert [condition for _, _, condition in actual] == [
        f'condition="{_CONDITION.format(key=key)}"' for _, _, key in expected
    ]