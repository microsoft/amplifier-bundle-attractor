# Session transcript

source: /root/.amplifier/sessions/00000000-0000-0000-0000-000000000000/transcript.jsonl


## user

My pipeline never finishes. Honestly, can I just USERONLY-SENTINEL let the review node decide
when it's done? It's the thing looking at the code.


## assistant

[thinking] The user wants me to endorse THINKING-SENTINEL letting the model decide when it's
done -- the self-report gate. I should refuse and say why.
[/thinking]
BEFORE-INNER-HEADING-SENTINEL Short answer: no, and here is what is actually going on.

## What is happening

Your loop has no budget counted inside a gate, so edge selection falls through to the
weight-and-lexical tiebreak every time the reviewer sends the work back.

## What would fix it

Run `attractor lint` on the graph first. Then AFTER-INNER-HEADING-SENTINEL wire the exit to
`pytest -q` -- not to the reviewer's opinion of its own work.

[tool_use: bash] {"command": "echo TOOLUSE-SENTINEL"}
[/tool_use]


## tool

[tool_result] "TOOLRESULT-SENTINEL attractor lint reported 2 errors"
[/tool_result]


## assistant

## Recommendation

TRAILING-ASSISTANT-SENTINEL the gate has to live outside the worker that produced the work.
