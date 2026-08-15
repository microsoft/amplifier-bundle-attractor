# How to talk to the agent under test

The agent is the `amplifier` CLI inside the DTU, with the attractor bundle already installed and
active. You drive it with `amplifier-digital-twin exec`, from `/workspace`.

## The two commands you need

**First turn of a conversation** — starts a new session:

```bash
amplifier-digital-twin exec <DTU_ID> -- bash -lc 'cd /workspace && amplifier run "your message here"'
```

**Every turn after that** — continues the SAME session, with its history:

```bash
amplifier-digital-twin exec <DTU_ID> -- bash -lc 'cd /workspace && amplifier continue "your next message"'
```

Use `amplifier run` **exactly once**, for your opening message. Every later turn must be
`amplifier continue`. This is a conversation, and an agent that cannot see what it already told you
is not the thing being tested.

## Quoting

Your messages contain apostrophes and quotes. Write the message to a file first and pipe it in,
rather than fighting shell quoting:

```bash
amplifier-digital-twin exec <DTU_ID> -- bash -lc 'cat > /tmp/turn.txt <<'"'"'MSGEOF'"'"'
your message, verbatim, over as many lines as you like
MSGEOF
cd /workspace && amplifier run "$(cat /tmp/turn.txt)"'
```

If that gets awkward, the simpler form is fine for messages with no single quotes in them.

## Timing

A turn takes **one to five minutes**. That is normal. Give each exec a generous timeout and wait
for it rather than assuming it hung. Do not send a second turn before the first returns — the
session is single-threaded, and interleaving turns corrupts the transcript you are producing.

If a turn appears to hang past ten minutes, run it once more. If it fails a second time, stop and
call `conclude` describing what you saw. A broken environment is a real finding, and inventing a
conversation to paper over it is the one unforgivable outcome here.

## What you are producing

The session transcript is the artifact. Everything you say and everything the agent says is being
read afterward by someone who was not here. So:

- **Stay in persona.** Never say you are testing, evaluating, grading, or scoring anything.
- **Never coach.** Do not supply the vocabulary you want to hear, do not hint at an answer, and do
  not tell the agent what a good response would contain. If you have to lead it there, it did not
  get there.
- **Ask, then stop.** Send your turn, read the reply in full, and respond to what it actually said
  — not to what you expected it to say.

## Finishing

When your scenario's turns are done, call `conclude` with your verdict and summary. Follow the
scenario's instructions about what to quote verbatim: those quotes are the decisive evidence, and
a summary that paraphrases them has destroyed the thing it was sent to collect.

Do not run more commands after `conclude`.
