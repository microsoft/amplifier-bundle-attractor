"""Every deterministic string the demonstration layer emits. NO LLM in here.

The mining half earned its trust with one rule: *every number in the artifact
was re-verified against the raw records before it was rendered*. The teaching
half inherits that rule by construction rather than by hope --- which is what
this module is for. All narrative scaffolding, all section headings, all
verification labels, the whole learn-about primer, and every command a reader
is invited to run live HERE, as constants and pure functions over already
verified stats. The LLM never gets to *state* a number; it only narrates
around numbers the deterministic layer placed (see `demo.validate_narrative`,
which machine-checks exactly that).

Three constants are load-bearing enough to be pinned by tests:

* `LABEL_UNVERIFIED` --- the honest banner when nothing machine-checked the
  pipeline. An unverified demo still teaches; the label is part of what it
  teaches.
* `lint_not_run_label()` --- the exact wording used when the CLI is absent.
  Never an implied pass: if the linter could not run, the artifact SAYS so.
* `CLI_INSTALL_CMD` --- the one place the install line is written down.

Nothing here fetches anything. `EXPLAINER_URL` is a hyperlink a reader may
choose to follow, not a resource the artifact loads (see the note in
`render.py` about why that does not breach the self-contained rule).
"""

from __future__ import annotations

import shlex

from . import authoring_contract as _contract

# --------------------------------------------------------------------------
# Public endpoints and commands --- written down exactly once.
# --------------------------------------------------------------------------

#: The published explainer. LINKED, never inlined (repo convention).
EXPLAINER_URL = "https://microsoft.github.io/amplifier-bundle-attractor/attractor-explained.html"

#: The single source of the install line, quoted in every demo's panel.
CLI_INSTALL_CMD = (
    'uv tool install "git+https://github.com/microsoft/amplifier-bundle-dot-runner'
    '@main#subdirectory=modules/pipeline-runner"'
)

#: The ASK-FIRST inbound fetch. Never run without an explicit yes (rung 2).
UVX_LINT_CMD = (
    "uvx --from git+https://github.com/microsoft/amplifier-bundle-dot-runner"
    "@main#subdirectory=modules/pipeline-runner attractor"
)

#: The offered independent path --- never invoked automatically by this skill.
AUTHOR_PIPELINE_PATH = "examples/authoring/pipeline-author.dot"

# --------------------------------------------------------------------------
# Verification-ladder labels --- exact strings, pinned by tests.
# --------------------------------------------------------------------------

LEVEL_LINT_DOCTRINE = "lint+doctrine"
LEVEL_DOCTRINE_ONLY = "doctrine-only"
LEVEL_NONE = "none"

#: Rung 3 ran, rungs 1-2 did not. The #270 doctrine: if the linter could not
#: run, SAY SO in the artifact --- silence reads as a pass, and is not one.
LABEL_LINT_NOT_RUN = (
    "attractor lint: NOT RUN — the CLI is not installed here. Run it yourself: attractor lint {relpath}"
)

#: Rung 4: nothing verified anything. Loud, and honest about it.
LABEL_UNVERIFIED = "UNVERIFIED — no machine check ran on this pipeline"


def lint_not_run_label(relpath: str) -> str:
    """The exact `NOT RUN` line, with this demo's published path filled in."""
    return LABEL_LINT_NOT_RUN.format(relpath=relpath)


# --------------------------------------------------------------------------
# The learn-about primer --- one screen, once per artifact, deterministic.
# --------------------------------------------------------------------------

PRIMER_TITLE = "What an attractor pipeline is — in one screen"

PRIMER_LEAD = (
    "The map above says what recurs in your own work. This says what an attractor pipeline is, "
    "so the demonstration below reads as something you could actually build."
)

#: (heading, body) pairs. Rendered in order; the renderer escapes both.
PRIMER_PARTS: tuple[tuple[str, str], ...] = (
    (
        "A loop, not a checklist",
        (
            "An attractor is work that gets ATTEMPTED, CHECKED, and RE-ATTEMPTED until it lands. "
            "Straight-through work — do this, then this, then this — is a recipe. A recipe does not "
            "need a pipeline; it needs a script, or just doing."
        ),
    ),
    (
        "The exit gates on machine evidence, from OUTSIDE the worker",
        (
            "The loop stops when a real command says so: a test suite, a linter, a build, a readback "
            "— something that can be RED today and GREEN only when the work is done. It never stops "
            "because the model reports that it finished. A claim about your own output is not "
            "evidence; that is the one rule the whole doctrine rests on."
        ),
    ),
    (
        "A budget wall, so it cannot spin",
        (
            "Every loop carries a cap on attempts, and exhausting the cap routes somewhere honest — a "
            "salvage path, a postmortem, a loud failure — never quietly out the success door."
        ),
    ),
    (
        "Gates live outside the workers",
        (
            "The node that does the work and the node that judges it are different nodes. The judge "
            "runs a command; it does not read the worker's own summary of how well things went."
        ),
    ),
    (
        "The three-question test",
        (
            "Q1 — is there a cycle? Q2 — is the exit gated on machine-checkable evidence? Q3 — would "
            "it survive one node having a bad day? Three yeses and the work is attractor-shaped. "
            "Anything less has a name and a home elsewhere."
        ),
    ),
    (
        "The honest NO is a real answer",
        (
            "Most recurring work is NOT attractor-shaped, and saying so is the valuable output — that "
            "is why the map above reports honest-NOs with the sub-test each one failed and what would "
            "change the answer. A pipeline built for recipe-shaped work teaches the anti-pattern to "
            "everyone who copies it."
        ),
    ),
)

PRIMER_LINK_LEAD = "The full explainer, with worked examples:"


# --------------------------------------------------------------------------
# Per-demo section headings and fixed prose.
# --------------------------------------------------------------------------

SEC_COST = "What this keeps costing you by hand"
SEC_GIST = "In your own terms"
SEC_FIT = "The three-question test, applied to this"
SEC_WALK = "The pipeline, walked"
SEC_MATH = "Why the loop beats the once-through run"
SEC_PAYOFF = "Why this would have helped"
SEC_ENTRY = "Where to take it next"
SEC_RUN = "Run it going forward"
SEC_PANEL = "Can I vouch for this? — the three-part answer"

DEMO_LEAD = (
    "This is a demonstration authored for THIS unit of your work, then machine-gated before it was "
    "published. Generation was a language model; verification, assembly and rendering were not."
)

#: Section 10, part 2 --- the same three parts every time, because the honest
#: answer to "can you vouch for it" does not vary with the demo.
PANEL_PART1_TITLE = "What a machine checked, and what it said"
PANEL_PART2_TITLE = "What nothing checked"
PANEL_PART2_BODY = (
    "Whether these prompts fit the way you actually work. Whether the gate command is the right "
    "definition-of-done for your case. Whether this pipeline solves the problem you actually have. "
    "And any number written inside the pipeline itself — a budget, a threshold, a figure in a "
    "node's prompt or label — which the engine's linter and the authoring contract gate for "
    "structure, but which is NOT cross-checked against your verified stats the way the teaching "
    "text above it is. Structure lints; judgment does not."
)
PANEL_PART3_TITLE = "The independent path"
PANEL_PART3_BODY = (
    "Do not take this session's word for it. The authoring pipeline below converges a draft under "
    "the engine's own linter, a structural authoring contract, and a critique that inherits none of "
    "the author's context:"
)

MATH_LABEL = "illustrative arithmetic — not a measurement of your sessions"
MATH_LEAD = (
    "A once-through run has to get every step right the first time. A gated loop only has to get "
    "there within its budget, because the gate catches the miss and the loop pays for another "
    "attempt. With a per-step success rate held fixed for illustration:"
)

FIT_Q1 = "Q1 — is there a cycle?"
FIT_Q2 = "Q2 — is the exit gated on machine-checkable evidence?"
FIT_Q3 = "Q3 — would it survive one node having a bad day?"

#: UNKNOWN is a caveat, never a failure --- the same invariant the map holds.
RECOVERY_RENDER = {
    "PASS": "PASS — a real error was observed and the work still finished",
    "PASS-provisional": "PASS-provisional — recovery was observed, but on thin evidence",
    "UNKNOWN": "unproven — no bad day was ever observed, which is a caveat, never a failure",
}

#: Section 8. (label, target, why) --- deterministic, identical in every demo.
ENTRY_POINTS: tuple[tuple[str, str, str], ...] = (
    (
        "Design it conversationally",
        "/attractorify",
        "applies the three-question test to one piece of work and designs the shape with you.",
    ),
    (
        "Converge it under executed gates",
        AUTHOR_PIPELINE_PATH,
        "authors a hardened, reusable pipeline and hardens it against real gate runs.",
    ),
    (
        "The canonical skeleton",
        "examples/patterns/task-runner.dot",
        "the smallest complete attempt/check/re-attempt shape worth copying.",
    ),
    (
        "When the work has a definition of done per item",
        "examples/objective/objective-runner.dot",
        "the objective layer, for fan-out over a list of things each with its own gate.",
    ),
    (
        "The repo-hosted lane",
        "docs/ISSUE_PIPELINE.md",
        "how the same discipline runs against issues, in a repo, unattended.",
    ),
)


# --------------------------------------------------------------------------
# Brief assembly --- the instruction pack handed to the fresh-context author.
# --------------------------------------------------------------------------

#: The attribute vocabulary a fresh-context delegate cannot see for itself.
#: Every name here must exist in `context/dot-reference.md`; a pin test checks
#: exactly that, so this excerpt can never drift into inventing a spelling.
VOCAB_ATTRIBUTES: tuple[str, ...] = (
    "shape",
    "prompt",
    "tool_command",
    "goal_gate",
    "condition",
    "max_retries",
    "retry_target",
    "fidelity",
)

VOCAB_EXCERPT = """\
ENGINE VOCABULARY (this is the whole vocabulary you may use — an attribute that
is not on this list is not read by anything, and a graph that *looks* configured
runs unconfigured):

  shape=Mdiamond      the start node. Not `circle`.
  shape=Msquare       the single exit node. Not `doublecircle`.
  shape=box           an LLM worker. The default tier. There is no `agent=`.
  shape=parallelogram an evidence gate: a node that RUNS A REAL COMMAND and
                      routes the graph on what the command said.
  shape=diamond       a pure routing node (no work, no judgment).

  prompt="..."        what an LLM worker is asked. The engine reads `prompt=`,
                      NEVER `instruction=`; an invented attribute is silently
                      dropped and the node runs with no prompt at all.
  tool_command="..."  the shell command a gate or tool node runs.
  goal_gate="..."     an assertion the engine itself evaluates.
  condition="..."     edge selection: `outcome=success`, `outcome=fail`, or
                      `context.tool.last_line=<token>` from a real command.
  max_retries=N       per-node retry budget.
  retry_target=<node> where a failed node routes instead of ending the run.
  fidelity=<mode>     context handling: full, truncate, compact, summary:low,
                      summary:medium, summary:high. Those six, no others.

Edge rules that bite: a label-routing edge whose source can also fail must
conjoin `&& outcome=success`; no failure outcome may route into the exit node.
"""

CONTRACT_SUMMARY = """\
THE STRUCTURAL CONTRACT (A0–A10). Your draft is machine-checked against this
before anyone sees it, so write to it deliberately:

  A0  the pipeline file exists and parses
  A1  EXACTLY ONE exit node (shape=Msquare). A second honest terminal is a LOUD
      nonzero tool node, never a second Msquare
  A2  at least one corrective cycle
  A3  at least one evidence-bearing gate that runs a REAL command and routes on
      it. A node that only prints a constant is not a gate
  A4  the exit is structurally UNREACHABLE without passing an evidence gate.
      This is the load-bearing check
  A5  a budget wall: a node that counts attempts and routes exhaustion somewhere
      that is not the success door
  A6  every reachable LLM worker carries an `outcome=fail` route or a
      `retry_target`
  A7  label-routing edges conjoin `&& outcome=success` where the source can fail
  A8  NO failure outcome is routed into the exit node
  A9  the companion .md NAMES EVERY LLM worker node id and states its contract
  A10 no evidence gate routes both of its answers into the exit — a gate whose
      answer cannot change the path gates nothing
"""

# --------------------------------------------------------------------------
# A5 in detail --- the check first drafts actually fail.
# --------------------------------------------------------------------------
#
# Measured on real runs: the brief above described the budget wall in PROSE
# ("a budget wall, so it cannot spin"), and both live demonstrations came back
# `[FAIL] A5` on the first draft --- costing a second delegation every time.
# A5 is not a judgement about whether the pipeline is well budgeted; it is a
# LITERAL SUBSTRING MATCH over the graph text. A delegate that cannot see the
# checker cannot guess the tokens, so the brief now quotes them.
#
# The tuples are read off the vendored checker itself rather than retyped: it
# is byte-pinned to upstream (`test_vendored_doctrine_checker_pin.py`), so a
# private-name read here is the only way to keep ONE source of truth. If
# upstream ever changes the vocabulary, the brief follows on the next `cp` and
# the pin test proves it did.

#: Substrings A5 accepts in a reachable tool node's `tool_command`.
A5_BUDGET_TOKENS: tuple[str, ...] = tuple(_contract._BUDGET_TOKENS)

#: Substrings A5 accepts on that node's outgoing edge (`condition` / `label`).
A5_EXHAUSTION_TOKENS: tuple[str, ...] = tuple(_contract._EXHAUSTION_TOKENS)


def _token_line(tokens: tuple[str, ...]) -> str:
    """The tokens as the author must spell them --- backticked, comma-joined."""
    return ", ".join(f"`{token}`" for token in tokens)


#: The worked shape handed to the author. Written ONCE, here: a test wraps this
#: exact fragment in the smallest legal graph and runs the real checker over
#: it, so an example that would not itself pass A5 cannot ship in the brief.
A5_WORKED_EXAMPLE = """\
  wall [shape=parallelogram,
        tool_command="max_attempts=3; test $(cat .n) -lt $max_attempts && echo under_budget || echo budget_exhausted"]
  wall -> worker  [condition="context.tool.last_line=under_budget"]
  wall -> give_up [condition="context.tool.last_line=budget_exhausted"]"""


A5_BUDGET_WALL_CONTRACT = f"""\
A5 IN DETAIL — this is the check first drafts fail, so it is spelled out. A5 is
a LITERAL SUBSTRING MATCH over your graph's text, not a reading of your prose:
a companion that eloquently describes a budget wall scores nothing. The checker
requires, verbatim:

  1. A TOOL node (`shape=parallelogram`, or any node carrying a `tool_command`)
     that is REACHABLE from start, and whose `tool_command` contains one of
     these substrings, spelled exactly like this — the match is case-sensitive:
       {_token_line(A5_BUDGET_TOKENS)}
  2. That SAME node has an OUTGOING EDGE whose `condition` or `label` contains
     one of these substrings (this match is case-insensitive):
       {_token_line(A5_EXHAUSTION_TOKENS)}
  3. That exhaustion edge routes somewhere honest — a LOUD nonzero terminal —
     never into the exit as a success (A1, A8).

A worked shape that satisfies all three:

{A5_WORKED_EXAMPLE}

`max_attempts` carries the budget token; `budget_exhausted` carries the
exhaustion token on the edge. Counting attempts in a worker's prompt, or naming
a budget only in the companion, leaves A5 red.
"""

NARRATIVE_CONTRACT = """\
NARRATIVE RULES (machine-enforced at assembly — a violation kills the demo):

  * DO NOT STATE ANY NUMBER that is not one of the verified stats quoted above.
    Every count in the published artifact is placed by a deterministic template
    from re-verified data; your prose narrates AROUND those numbers. Digits are
    scanned: any digit-run that is not a verified stat (or one of 0–4, for
    referring to Q1/Q2/Q3 and the 4a/4b/4c sub-tests) fails the assembly and
    the demo is discarded. Prose like "about a dozen" is always fine.
  * Every `pipeline_walk[].node` must be a node id that actually exists in your
    pipeline.dot. Invented node names fail the assembly.
  * Every slot is a plain string of at most 600 characters. All six must be
    present. `pipeline_walk` must be non-empty.
  * Write to the user, about THEIR work, in second person. No preamble, no
    apology, no meta-commentary about being a language model.
"""


def brief_markdown(
    *,
    unit_name: str,
    unit_id: str,
    slug: str,
    verdict: str,
    stats_lines: list[str],
    fit_lines: list[str],
    gate_evidence: list[str],
    gist: str | None,
    max_nodes: int,
) -> str:
    """Assemble the demo brief. Deterministic — no improvised prompting.

    `unit_name` / `gist` are untrusted text from the user's own corpus: they
    are expanded into PROSE and PROMPTS here and nowhere else. Nothing in this
    document becomes a command the orchestrating session executes.
    """
    stats_block = "\n".join(f"  {line}" for line in stats_lines)
    fit_block = "\n".join(f"  {line}" for line in fit_lines)
    if gate_evidence:
        gate_block = "\n".join(f"  - {tool}" for tool in gate_evidence)
        gate_note = (
            "These are the verify-class tools that actually appear in the terminal window of\n"
            "this unit's own sessions. Derive the gate command from THIS evidence — it is what\n"
            "the user already reaches for to decide the work is done:\n" + gate_block
        )
    else:
        gate_note = (
            "No verify-class tool was observed in the terminal window of this unit's sessions.\n"
            "Say so plainly in the companion, and choose the most conservative real check the\n"
            "scenario admits — never invent evidence that was not there."
        )
    gist_block = gist.strip() if gist else "(no gist recorded for this unit)"

    return f"""\
# Demonstration brief — author one small attractor pipeline

You are writing a TEACHING DEMONSTRATION for one unit of recurring work that was
mined from the user's own session history. Someone who has never built an
attractor pipeline will read your output next to their own numbers. Small,
correct and legible beats clever.

## The unit

  name:     {unit_name}
  unit id:  {unit_id}
  verdict:  {verdict}

Scenario, as recorded:

{gist_block}

## Their VERIFIED stats (already re-verified against the raw records)

{stats_block}

## Why it passed the fit test

{fit_block}

## Gate evidence observed in their own sessions

{gate_note}

## What to write

Write exactly THREE files into this directory:

### 1. `pipeline.dot`

A small attractor pipeline that would have converged THIS scenario. Hard budget:
**at most {max_nodes} nodes** — start, exit, one or two workers, the gate(s), a
budget wall, and a loud terminal. This is the convergence-factory shape in
miniature, not a production system. Keep the control plane lean: gates, budgets,
walls, feedback. Do NOT decompose the domain into graph nodes — plan/implement/
test as separate nodes is the anti-pattern.

{VOCAB_EXCERPT}
{CONTRACT_SUMMARY}
{A5_BUDGET_WALL_CONTRACT}
### 2. `pipeline.md`

The companion. It MUST name every LLM (box) node id in your graph and state each
one's contract in prose (that is A9, and it is checked). Also say what the
pipeline converges on, what the gate proves, and what the honest failure exit
looks like.

### 3. `narrative.json`

Exactly this shape, six slots, no others:

```json
{{
  "scenario_gist": "one short paragraph, in the user's own terms, describing the recurring work",
  "q1_cycle_note": "why this work has a real cycle, in their scenario's terms",
  "q2_gate_note": "what the machine-checkable definition-of-done is here, in their terms",
  "q3_recovery_note": "what a bad day looks like for this work and how the pipeline absorbs it",
  "pipeline_walk": [{{"node": "<a node id from your pipeline.dot>", "note": "what this node is for"}}],
  "payoff_note": "one line: why running this loop would have helped them"
}}
```

{NARRATIVE_CONTRACT}
## Slug

The published files will be named `{slug}.dot` and `{slug}.md`.
"""


def author_brief_line(unit_name: str, verdict: str) -> str:
    """A one-paragraph brief for the OFFERED independent authoring path.

    Deterministic, and short enough to survive a copy-paste into a shell.
    """
    return (
        f"Author a reusable attractor pipeline for this recurring unit of work: {unit_name}. "
        f"It was mined from real session history and classified {verdict}. The loop must gate its "
        f"exit on a real command that is red before the work is done and green only after, carry a "
        f"budget wall, and route exhaustion somewhere honest."
    )


def run_cmd(dot_relpath: str) -> str:
    """The exact invocation that runs the published demo pipeline."""
    return f"attractor run {shlex.quote(dot_relpath)} --cwd ."


def author_cmd(*, unit_name: str, verdict: str, slug: str) -> str:
    """The `pipeline-author.dot` invocation, with this demo's brief pre-filled."""
    brief = shlex.quote(author_brief_line(unit_name, verdict))
    return (
        f"attractor run {AUTHOR_PIPELINE_PATH} --cwd . \\\n"
        f"    --param brief={brief} \\\n"
        f"    --param authoring_dir=examples/authoring \\\n"
        f"    --param target_dir=. \\\n"
        f"    --param pipeline_name={shlex.quote(slug)}"
    )


def uvx_consent_question(relpath: str) -> str:
    """The ask-first question for rung 2. Named as an inbound fetch, out loud."""
    return (
        "The attractor CLI isn't installed. I can fetch and run the public linter via "
        f"`{UVX_LINT_CMD} lint {relpath}` — that downloads a public package (an inbound fetch; "
        "none of your mined data leaves this machine). Yes/no?"
    )


__all__ = [
    "A5_BUDGET_TOKENS",
    "A5_BUDGET_WALL_CONTRACT",
    "A5_EXHAUSTION_TOKENS",
    "AUTHOR_PIPELINE_PATH",
    "CLI_INSTALL_CMD",
    "CONTRACT_SUMMARY",
    "DEMO_LEAD",
    "ENTRY_POINTS",
    "EXPLAINER_URL",
    "FIT_Q1",
    "FIT_Q2",
    "FIT_Q3",
    "LABEL_LINT_NOT_RUN",
    "LABEL_UNVERIFIED",
    "LEVEL_DOCTRINE_ONLY",
    "LEVEL_LINT_DOCTRINE",
    "LEVEL_NONE",
    "MATH_LABEL",
    "MATH_LEAD",
    "NARRATIVE_CONTRACT",
    "PANEL_PART1_TITLE",
    "PANEL_PART2_BODY",
    "PANEL_PART2_TITLE",
    "PANEL_PART3_BODY",
    "PANEL_PART3_TITLE",
    "PRIMER_LEAD",
    "PRIMER_LINK_LEAD",
    "PRIMER_PARTS",
    "PRIMER_TITLE",
    "RECOVERY_RENDER",
    "SEC_COST",
    "SEC_ENTRY",
    "SEC_FIT",
    "SEC_GIST",
    "SEC_MATH",
    "SEC_PANEL",
    "SEC_PAYOFF",
    "SEC_RUN",
    "SEC_WALK",
    "UVX_LINT_CMD",
    "VOCAB_ATTRIBUTES",
    "VOCAB_EXCERPT",
    "author_brief_line",
    "author_cmd",
    "brief_markdown",
    "lint_not_run_label",
    "run_cmd",
    "uvx_consent_question",
]
