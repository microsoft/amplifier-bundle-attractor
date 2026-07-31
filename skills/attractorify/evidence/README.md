# Evidence directory — SIMULATIONS, not session records

Every `session-*.md` file in this directory is an **authored design simulation**:
a constructed illustration of intended skill behavior for one acceptance
scenario. None is a live Amplifier session transcript — there are no session
IDs, no timestamps, and no external verifiability.

What IS real here: `session-b-generated.dot` is a real artifact that passes
`attractor lint` clean (independently re-verified), and its gate command has
been tested against fixture files.

Live-session evidence is being collected separately and will replace or
supplement these files. Until real transcripts land, treat the skill as
experimental and these files as acceptance illustrations only.

Note: these simulations predate the executed diagnosis-gate mechanic (the
required `.attractorify/diagnosis.md` artifact + verbatim bash gate now in
`SKILL.md` Step 1) and do not depict it.

| File | Scenario it illustrates |
|---|---|
| `session-a-thin-context.md` | Thin context → targeted clarifying questions (ask-first) |
| `session-b-sufficient-context.md` | Sufficient context → design + lint-clean `.dot` handback |
| `session-b-generated.dot` | The (real) artifact produced in scenario B |
| `session-c-diagnosis-honesty.md` | Work that does NOT warrant an attractor → honest refusal |
