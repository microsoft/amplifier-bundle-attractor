# Real-session check: both skills still load by name, with the flag set

The acceptance criterion for `model_performance-lswd` is behavioural, not
textual: *"Given a real session, when `load_skill(skill_name="attractor-scout")`
and `load_skill(skill_name="attractorify")` are each called directly, then both
still load successfully."*

## How it was checked, and why this way

This lane's own agent session is the real session. It does not mount this
bundle, so the two skills were not in its catalog. `tool-skills` exposes a
`source` parameter for exactly this — `SkillsTool.execute` resolves a local
path, runs the production `discover_skills()` over it, and merges the result
into the live catalog **in memory only** (no config write, no disk state; see
`amplifier_module_tool_skills/__init__.py`, the `source_str` branch of
`execute`). So the check below is the real tool, the real discovery, the real
load path, in a live session, at **$0**.

Two paths were deliberately NOT taken, both for reasons recorded in the
program's standing rules:

- **A fresh `amplifier` run against this worktree's bundle.** The bundle mounts
  `modules/tool-report-outcome` by relative source, and a first run
  editable-installs local module sources into the SHARED uv tool venv
  (`~/.local/share/uv/tools/amplifier`), which `AMPLIFIER_HOME` does not
  isolate. Pointing that at a lane worktree writes `.pth` files at a path that
  vanishes on teardown — the same failure shape as the 2026-09-07 incident that
  rewrote 61 of 63 `.pth` files. See HIGHWAY.md Constraints.
- **A container/DTU run.** Correct, and explicitly endorsed by the census-safety
  rule — but it needs API keys and an LLM turn, and this lane's spend authority
  is **$0**.

## The check, verbatim

Registering the source (the worktree's `skills/`, post-change):

    load_skill(source=".../amplifier-bundle-attractor/skills")
    -> "Source '...' resolved to .../skills. Found 2 skill(s), 2 new:
        attractor-scout, attractorify."

Metadata as the live discovery parses it (`load_skill(info="attractorify")`),
confirming the file with the new frontmatter is the one being read:

    name: attractorify   version: 1.0.0
    path: .../skills/attractorify/SKILL.md
    allowed_tools: [read_file, write_file, bash, delegate]

Then each skill loaded directly by name:

| call | result |
|---|---|
| `load_skill(skill_name="attractorify")` | `success: true` — full body returned, `skill_directory` `.../skills/attractorify`, `loaded_from` `.../skills` |
| `load_skill(skill_name="attractor-scout")` | `success: true` — full body returned, `skill_directory` `.../skills/attractor-scout`, `loaded_from` `.../skills` |

Both returned the **complete SKILL.md body inline** — not a fork handle, not a
stub. Neither declares `context: fork`, and `SkillsTool._load_skill` gates fork
execution on that field alone, so `disable-model-invocation` changes nothing on
the load path. That is the whole point of the change: unreachable by the model,
unchanged for the human.

The bodies were loaded to verify loadability. Neither skill was executed.

## Cross-check, independent of the session

`measure_catalog_delta.py` (in this directory) runs the same production
`discover_skills()` over the worktree in a plain interpreter and reports:

    attractor-scout  disable_model_invocation=True user_invocable=True context=None
    attractorify     disable_model_invocation=True user_invocable=True context=None

and renders the live `SkillsVisibilityHook._format_skills_list` with the flag
forced off and on, confirming both skills move from the `Available skills`
section to the `User-invoked skills (available via /command)` section.
