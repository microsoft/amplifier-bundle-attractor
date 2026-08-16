"""Guards for the assistant-answer extractor that `assistant_answer_lacks_all` reads.

`assistant_answer_text()` decides how much of a transcript a `lacks_all` mechanical check is
allowed to search. Every failure mode of that function has a direction:

* **Keeping too much** turns a PASS into a FAIL. Loud, visible, and safe -- somebody reads the
  failed check and looks at the transcript.
* **Dropping too much** turns a FAIL into a PASS. Silent, and it is the failure the check
  itself exists to prevent (issue #262).

So these tests are written around that asymmetry: the truncation regression from #262 is the
RED->GREEN case, and the two pre-existing fail-closed behaviours are pinned alongside it so a
later "cleanup" cannot quietly trade them away.

Run:  python3 -m pytest evals/guidance/harness/tests -q
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

HARNESS = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load_driver() -> Any:
    """Import run_guidance_eval.py by path -- it is a script, not a package member."""
    spec = importlib.util.spec_from_file_location(
        "guidance_eval_driver", HARNESS / "run_guidance_eval.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


driver = _load_driver()
assistant_answer_text = driver.assistant_answer_text


INNER_HEADING_TRANSCRIPT = (FIXTURES / "assistant-answer-inner-heading.md").read_text(
    encoding="utf-8"
)


# --------------------------------------------------------------------------- #262, the regression


def test_inner_markdown_heading_does_not_truncate_the_answer() -> None:
    """#262: an assistant answer that structures itself with `## ` must extract IN FULL.

    The fixture's assistant turn carries two markdown headings of its own. Before the fix the
    splitter treated them as role boundaries, so everything after `## What is happening` was
    attributed to a section whose "role" was the heading text -- never `assistant` -- and
    silently dropped.
    """
    answer = assistant_answer_text(INNER_HEADING_TRANSCRIPT)

    assert "BEFORE-INNER-HEADING-SENTINEL" in answer, "prose before the inner heading was lost"
    assert "AFTER-INNER-HEADING-SENTINEL" in answer, (
        "prose AFTER the assistant's own `## ` heading was dropped -- the #262 fail-open sliver"
    )
    # The heading itself is the assistant's prose too, not a delimiter to be eaten.
    assert "## What would fix it" in answer


def test_role_headings_still_bound_the_turns() -> None:
    """Widening the answer must not widen it to the whole transcript.

    `assistant_answer_lacks_all` exists to scope banned literals to what the USER SAW. If the
    fix for #262 simply stopped splitting, the check would silently become
    `transcript_lacks_all` and `qa-02`'s persona -- which is instructed to propose the
    anti-pattern out loud -- would fail it every time.
    """
    answer = assistant_answer_text(INNER_HEADING_TRANSCRIPT)

    assert "USERONLY-SENTINEL" not in answer, "a user turn leaked into the assistant answer"
    assert "TOOLRESULT-SENTINEL" not in answer, "a tool turn leaked into the assistant answer"
    # Both assistant turns are kept -- the second one opens with its own `## ` heading.
    assert "TRAILING-ASSISTANT-SENTINEL" in answer


def test_thinking_and_tool_blocks_are_still_excised() -> None:
    """Private reasoning and raw tool traffic are not things the session said to the user."""
    answer = assistant_answer_text(INNER_HEADING_TRANSCRIPT)

    assert "THINKING-SENTINEL" not in answer
    assert "TOOLUSE-SENTINEL" not in answer


def test_the_check_kind_itself_sees_the_truncated_region() -> None:
    """End-to-end over the real check: a banned literal after an inner heading must FAIL.

    This is the sliver in its live form. `MechanicalChecker` derives the answer text once in
    `__init__`; the check below never touches the DTU, so a `None` stands in for it.
    """
    transcript = INNER_HEADING_TRANSCRIPT.replace(
        "AFTER-INNER-HEADING-SENTINEL wire the exit to",
        "just let the model decide when it's done and wire the exit to",
    )
    checker = driver.MechanicalChecker(None, transcript)  # type: ignore[arg-type]
    spec = {
        "id": "MC-TEST",
        "kind": "assistant_answer_lacks_all",
        "none_of": ["let the model decide when it's done"],
    }

    [result] = asyncio.run(checker.run([spec]))

    assert not result.passed, (
        "the banned literal sat AFTER the assistant's own `## ` heading and the check passed "
        "anyway -- fail-open"
    )


# --------------------------------------------------------------- the pre-existing fail-closed pair


def test_unterminated_thinking_block_is_kept_verbatim() -> None:
    """Fail-closed: a block with no close marker is NOT excised.

    A truncated render must never be a licence to stop searching text.
    """
    transcript = (
        "# Session transcript\n\n"
        "## assistant\n\n"
        "[thinking] UNCLOSED-SENTINEL the render was cut off here\n"
    )

    answer = assistant_answer_text(transcript)

    assert "UNCLOSED-SENTINEL" in answer


def test_headingless_transcript_falls_back_to_the_whole_text() -> None:
    """Fail-closed: no assistant turns found means search everything, not nothing.

    An unrecovered session falls back to a reconstruction with no role headings at all. Returning
    "" there would make every `lacks_all` check pass vacuously.
    """
    transcript = "The assistant said HEADINGLESS-SENTINEL and then stopped.\n"

    assert assistant_answer_text(transcript) == transcript
    assert "HEADINGLESS-SENTINEL" in assistant_answer_text(transcript)


def test_prose_only_headings_fall_back_to_the_whole_text() -> None:
    """The exemplar-mode artifact has `## ` headings but no roles: still fail-closed."""
    transcript = (
        "# Session transcript\n\n"
        "## The objective, as stated by the user\n\n"
        "EXEMPLAR-SENTINEL\n\n"
        "## Runner exit status\n\n`0`\n"
    )

    assert assistant_answer_text(transcript) == transcript


@pytest.mark.parametrize("heading", ["### assistant", "#assistant", "  ## assistant"])
def test_only_a_top_level_role_heading_opens_a_turn(heading: str) -> None:
    """A near-miss heading is not a role boundary -- so its text stays where it was."""
    transcript = f"# Session transcript\n\n## assistant\n\nkept\n\n{heading}\n\nNEARMISS-SENTINEL\n"

    assert "NEARMISS-SENTINEL" in assistant_answer_text(transcript)
