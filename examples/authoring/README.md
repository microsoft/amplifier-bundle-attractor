# The Authoring Attractor

A pipeline that authors pipelines, under executed machine gates.

You state a **design brief** for work you want a reusable pipeline to do.
`pipeline-author.dot` diagnoses the brief, drafts a new pipeline, hardens it
against `dot-runner lint` and a structural authoring contract, submits it to an
independent doctrine critique that inherits nothing from the author, and
publishes it with provenance — or tells you honestly that the work described
does not want an attractor at all.

The basin: **a new pipeline exists that lints clean, satisfies the authoring
contract, and survives an independent critique — or the brief is honestly
redirected.** Both are green exits, told apart by a disposition artifact. A run
that can do neither escalates loudly with a nonzero exit, never through the
success door.

---

## Why this exists

Writing a good attractor is convergence-shaped work. You draft, you find out the
draft is wrong, you fix it, you find out again. That is a corrective loop with a
machine-checkable gate at the end of it — which is exactly the shape this repo
says deserves a pipeline.

It also has the repo's own measured evidence behind it. `/attractorify` learned
the hard way that prompt text alone does not deliver authoring discipline: its
diagnosis rules only became reliable once they were backed by an **executed**
form gate and an independent verifier. The same lesson is the whole subject of
`docs/OPERATIONS.md` §1 — *"the test passes" is not "it works"* — and of
`examples/objective/check_child_contract.py`'s C9, where a rule that lived only
in a composer's prompt was being ignored until something ran it.

So the doctrine here is not stated at the author and hoped for. It is executed
at the draft.

---

## Three front doors, one doctrine

This is the third of three surfaces that all apply the same three-question test
and reach the same conclusions. They differ in **who is present** and **what
comes out**.

| Surface | Who is in the loop | What it produces | Reach for it when |
|---|---|---|---|
| [`/attractorify`](../../skills/attractorify/SKILL.md) | a **human**, conversationally | a validated *intent*: a diagnosis artifact, a design, an invocation | you are in a session, the shape is unclear, and you want to think it through with someone |
| **`examples/authoring/`** (here) | **nobody** — it runs unattended | a *reusable pipeline*, converged under executed gates, published with provenance | you know what the pipeline must converge on, and you want the artifact hardened rather than hand-reviewed |
| [`examples/objective/`](../objective/README.md) | nobody | a *satisfied objective* — via a shipped lane, or a single-purpose child composed at run time and thrown away | you have work to get done and no interest in which pipeline does it |

The relationship in one line each:

- **attractorify DESIGNS** — conversationally, with a person, ending at an
  artifact and a recommendation. It never runs anything.
- **this CONVERGES the artifact** — under `dot-runner lint`, a structural
  contract, and an independent critique, ending at a published `.dot` + `.md`
  someone else can run next month.
- **objective-runner's compose path AUTHORS RUN-TIME CHILDREN** — a
  single-purpose graph for one objective, admitted by
  `check_child_contract.py`, executed immediately, and not intended to be
  reused.

They compose. `/attractorify`'s conversational design produces a validated
intent; handing that intent here as a brief is how it becomes a hardened
artifact instead of a hand-off document. The skill offers exactly that at the
end of its design step.

**When NOT to use this.** If you want *one* thing done once, you do not want a
reusable pipeline — use the objective runner, or just do the work. If the brief
describes a staged sequence with human approval and no machine check, this run
will tell you so and exit green with a redirect; that is a correct outcome, but
you can save the round trip by reading
[`docs/PIPELINE_DESIGN_PRINCIPLES.md`](../../docs/PIPELINE_DESIGN_PRINCIPLES.md)
§0 first.

---

## Run it

```bash
AUTH="$PWD/examples/authoring"        # capture the absolute path BEFORE cd
cd /path/to/your/workspace
dot-runner run "$AUTH/pipeline-author.dot" \
    --worker coding-agent \
    --param brief="Objective: every factual claim in README.md that names a
                   command must actually run. Evidence: the commands are in
                   fenced bash blocks; a script can extract and execute them.
                   Budget: 4 iterations, 30 minutes." \
    --param authoring_dir="$AUTH" \
    --param target_dir="$PWD" \
    --param pipeline_name="doc-claims-verified" \
    --cwd .
```

Process cwd must equal `--cwd` for box-node (agent) pipelines — see
[`../../modules/pipeline-runner/KNOWN_ISSUES.md`](../../modules/pipeline-runner/KNOWN_ISSUES.md).

| Param | Required | What it is |
|---|---|---|
| `brief` | yes | **The design brief.** The objective the pipeline should converge on, the evidence sources available to it, and the budget it should carry. Prose is fine; the more concretely you can name the machine check, the better the draft. |
| `authoring_dir` | yes | Absolute path to *this* directory. The doctrine gate runs `check_authored_pipeline.py` from here, and the author reads the repo's doctrine docs relative to it. |
| `target_dir` | yes | Absolute path to the workspace. Prompt text for box nodes. |
| `pipeline_name` | no (`authored-pipeline`) | Published basename under `out/`. Validated at preflight against `[A-Za-z0-9._-]+` — it becomes a filename. |
| `max_iterations` | no (4) | Author-attempt budget. Every corrective leg spends one. |
| `max_frames` | no (2) | How many unreadable triage records to tolerate before giving up on the diagnosis. |

**Environment.** `attractor`, `python3` and `sha256sum` must be on `PATH`;
preflight refuses loudly before paying for an LLM if any is missing.

### Reading the result

```bash
cat .authoring/disposition        # authored | redirected | escalated
```

| Disposition | Exit | What you got | Where to look |
|---|---|---|---|
| `authored` | 0 | A new pipeline that lints clean, satisfies the contract, and a fresh-context critic said SHIP to | `out/<name>.dot`, `out/<name>.md`, `.authoring/PROVENANCE.md` |
| `redirected` | 0 | The honest no: this work does not want an attractor, here is why and where it belongs | `.authoring/redirect.md` |
| `escalated` | **1** | Could not converge within budget; the value salvaged is the analysis | `.authoring/postmortem/report.md`, `.authoring/convergence.jsonl` |

Nothing reaches `out/` until every gate is green. The author works in
`.authoring/draft/`; `package` publishes only what the critique accepted. Draft
and published are different words on purpose.

---

## What it does, in order

1. **`preflight`** (tool) — makes the run's preconditions true or refuses before
   an LLM is ever paid: state directories, `attractor`/`python3`/`sha256sum` on
   `PATH`, the checker present, and a `pipeline_name` that is safe as a filename.
2. **`triage`** (LLM) — applies the repo's own
   [three-question test](../../docs/PIPELINE_DESIGN_PRINCIPLES.md) to the
   **brief**, restates the brief durably into `.authoring/triage.md`, names the
   sink, and ends with an anchored `VERDICT:` line. It **proposes**; it does not
   route.
3. **`triage_gate`** (tool) — reads the last line and decides. This is the node
   that routes, and it is outside the worker's context.
4. Either the honest no — **`redirect_report`** (LLM) writes
   `.authoring/redirect.md` and the run exits green — or the authoring loop:
5. **`author`** (LLM) — writes `.authoring/draft/pipeline.dot` and its `.md`
   companion, having read the doctrine docs, the shipped exemplars, and any gate
   reports left by a previous attempt.
6. **`lint_gate`** (tool) — `dot-runner lint`. ERRORs block; warnings are recorded.
   **This node also owns the one budget counter**, checked on entry.
7. **`doctrine_gate`** (tool) — `check_authored_pipeline.py`, the structural
   contract below. Failures route back to the author with the report.
8. **`critique`** (LLM, `fidelity="truncate"`) — an independent doctrine critique
   that inherits nothing from the author's context. Ends with an anchored
   `VERDICT: SHIP` or `VERDICT: ITERATE`.
9. **`verdict_gate`** (tool) — last-line anchored, case-insensitive exact match.
   Fails closed: a missing, empty or unparseable critique is `noverdict`, which
   routes to another iteration, never to the exit.
10. **`package`** (tool) — publishes `out/<name>.dot` + `out/<name>.md` and
    assembles `.authoring/PROVENANCE.md` from the real artifacts, with sha256
    digests. Assembled by a shell rather than a model on purpose: a provenance
    record a model wrote is a summary; one a shell assembled is a record.
11. **`finalize`** (tool) — refuses to open the exit without a disposition
    artifact. **`postmortem` → `escalated`** is the loud path when it cannot
    converge.

Full node-by-node contracts are in
[`pipeline-author.md`](pipeline-author.md).

---

## The authoring contract

`check_authored_pipeline.py` is the second gate. It owns the design checks
`dot-runner lint` deliberately does not, or owns only as advice. It is
stdlib-only for the same reason the objective layer's checker is: it has to run
under whatever `python3` is on `PATH` in the target workspace, not the
`dot-runner` CLI's own virtualenv.

It is a **stated contract, not a guessing game** — the author node is pointed at
this section by name, so every check below is something it was told before it
wrote a line.

| Check | What it requires | Why |
|---|---|---|
| **A0** | the `.dot` exists, is readable, and parses | a second opinion is only useful if it read the same graph; a disagreement with the engine's parser fails closed |
| **A1** | exactly one exit node | the engine refuses anything else. A second honest terminal is a LOUD nonzero tool node, never a second `Msquare` |
| **A2** | at least one corrective cycle | *"If your pipeline graph has no cycle, it should probably have been a recipe."* |
| **A3** | at least one evidence-bearing gate: a tool node running a **real** command whose result routes the graph | a node that only `printf`s a constant cannot fail, so nothing behind it is gated. This is the code-tier form of *"is the model here for judgment, or just to type?"* |
| **A4** | **the exit is structurally unreachable without passing such a gate** | the load-bearing check. A1–A3 all pass on a graph with a corrective loop off to one side and `done` hanging straight off a worker. A4 deletes every evidence gate and asks whether the exit is still reachable |
| **A5** | a tool node walls an iteration budget and routes exhaustion | a loop with no wall spends until the engine's step cap kills it with a bare FAIL, bypassing every salvage path |
| **A6** | every reachable LLM worker has an `outcome=fail` route or a `retry_target` | a FAIL does not traverse plain edges. One transient provider error at an unrouted node ends the run, however much work already landed |
| **A7** | label-routing edges conjoin `&& outcome=success` where the source can also fail | a failing tool node does not refresh `context.tool.last_line`, so a **stale** label can match alongside the failure edge on a later visit ([`docs/ROUTING-REFERENCE.md`](../../docs/ROUTING-REFERENCE.md) §3) |
| **A8** | no failure outcome routed into the terminal success node | this converts a failure into a successful run. The engine's own lint calls this `TOPO-006` and **warns**; here it **blocks** |
| **A9** | a companion `.md` exists and names every reachable LLM worker | every exemplar in this repo ships with a paired guide, because a graph shows what it does and not why it is shaped that way. Coverage is the machine-checkable core of the node contract |
| **A10** | no evidence gate routes **two different answers** into the exit | A4 asks whether the exit is reached *through* a gate; A10 asks the question left over — whether the gate's answer decided anything. `gate -> done [last_line=green]` **plus** `gate -> done [last_line=red]` satisfies A3, A4 and A8 while the run ends green whether the tests passed or not |

**A10 is the hollow-gate check**, and it exists because the shape it catches is
the cheapest available way to comply with the letter of "do not weaken a gate"
while defeating it: the gate is not weakened, deleted or relaxed — it is left
fully intact and simply **unwired**. Nothing else in the repo flags it, the
engine's own linter included.

Its boundary is drawn narrowly on purpose, and both edges are worth stating:

- **Only the exit fires it.** Two distinct tokens landing on the same *ordinary*
  node is inert for routing too, but it is frequently deliberate — three of this
  repo's own shipped graphs do it on purpose, sending several distinct diagnoses
  to one node that writes them up (`criteria_gate -> write_unspecced_finding` on
  four separate malformed-criteria tokens). There the token is *recorded* rather
  than routed on, which is legitimate. Two tokens into the **exit** has no such
  reading.
- **Only relay no-ops are seen through.** A `diamond` that merely forwards is
  chased through, because putting one in the middle is pure laundering of the
  same defect. The chase stops at any node that *does* something: if the two
  answers ran different workers before converging, the gate's answer
  demonstrably changed what happened, and whether that path should still end
  green is a judgement A10 does not have. That one is left to the critique tier.

**A5's vocabulary is deliberately explicit**, because a check nobody can satisfy
on purpose is a trap. The budget node's `tool_command` must name one of
`max_iteration`, `max_round`, `max_attempt`, `max_retries`, `budget`, `iter`;
one of its outgoing edges must carry a `condition` or `label` naming one of
`exhaust`, `budget`, `stall`, `over_budget`, `give_up`, `spent`.

**Token contract** (Idiom A — always exit 0, so `tool.last_line` is always
fresh):

```
doctrine_ok     every check passed
doctrine_bad    at least one check failed; route back to the author
```

A nonzero exit means the script could not run at all, which routes to the
postmortem path — deliberately distinct from `doctrine_bad`, a judgement about
the graph. A missing or unparseable `.dot` is a judgement, not a crash: the
author node is the one that was supposed to write it.

### What it deliberately does not check

**Whether the pipeline is any good for the brief it came from.** Structure is
checkable; fitness for purpose is not. A gate can be structurally perfect and
assert the wrong thing, or assert something that was already true. That is the
critique node's job, and it is why the authoring pipeline still pays for an LLM
after both machine gates are already green.

That line is *semantic vs structural*, and it is worth saying where A10 sits
relative to it. "Does this gate assert the right thing?" needs judgment and
stays on the critique side. "Can this gate's result change where the graph
goes?" is pure topology — a set comparison over outgoing edges, no different in
kind from A4's reachability computation — so it belongs in the machine tier,
where the deterministic failures are supposed to die before an LLM is paid to
find them.

### Calibration: it admits the repo's own exemplars

The checker is pinned against shipped, battle-tested graphs — if it rejected
`task-runner.dot` it would be the checker that was wrong, not the exemplar:

```bash
python3 examples/authoring/check_authored_pipeline.py \
    --pipeline examples/patterns/task-runner.dot \
    --companion examples/patterns/task-runner.md \
    --report /tmp/report.txt
```

`examples/pipelines/00-convergence-loop.dot` **fails** A5 and A6, correctly: it
is a deliberately minimal teaching graph, not a production attractor, and its
own guide says so. The checker is calibrated for pipelines meant to be run on
real work.

And it admits `pipeline-author.dot` itself. The exemplar obeys the contract it
hands out; a test in
`modules/loop-pipeline/tests/test_authoring_layer_gates.py` keeps that true.

---

## Files

| File | What it is |
|---|---|
| [`pipeline-author.dot`](pipeline-author.dot) | the authoring attractor |
| [`pipeline-author.md`](pipeline-author.md) | its companion guide — node-by-node contracts and the design record |
| [`check_authored_pipeline.py`](check_authored_pipeline.py) | the structural authoring contract (A0–A9), stdlib-only |

Run artifacts (`.authoring/`, `out/`) are written into the **workspace** you
point the run at, never into this directory.
