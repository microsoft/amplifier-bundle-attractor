# Design — Closing the Attractor Pipeline-Authoring Friction

**Status:** PROPOSED (design only — not implemented)
**Source:** cross-session friction analysis (`analysis/session-friction/SYNTHESIS.md`, 4 sessions × 2 lenses) → 3 expert consults (bundle-design-expert, foundation-expert, attractor-expert) → reviewed through restless-old-brian / cranky-old-sam / crusty-old-engineer.

---

## 0. The critical correction (validate-before-build paid off)

The friction reports were snapshots; **the engine has since moved — much of it from the fail-loud conformance work merged THIS session.** The attractor-expert re-validated every "engine fact" against the live code. Result:

| Synthesis "foot-gun" | Reality (attractor-expert, cited) |
|---|---|
| box/LLM nodes have no `report_outcome`, can't set context/labels | **STALE** — true of pure nlspec §4.5, false of the shipped `AmplifierBackend`: box nodes route via bare-JSON response **or** the `report_outcome` tool (`backend.py:621,827`) |
| silent alphabetical edge fallback → infinite loop on FAIL | **FIXED** — FAIL is now fail-fast (`edge_selection.py:79-101`, our §3.7 work) |
| dotted keys not expanded; `tool_command` CWD = engine dir | **STALE** — `$key`/`${key}` incl. dotted now resolve in tool_command/tool_env/description; CWD = `target_dir → source_dir → default` (`tool.py:116-123`) |
| silent DirectProviderBackend fallback (no-tools false-positive) | **MOSTLY CLOSED** — no backend now raises (`codergen.py:88`); no `llm_model` raises (`backend.py:772`); FAILs if neither path available |
| invalid `fidelity=` silently defaults | **FIXED** — now warns (`fidelity.py:192`) |

**Net: of the ~12 foot-guns, 4 are already fixed (this session), several "facts" were stale, 2 are genuine open bugs.** Had we documented the reports verbatim we'd have shipped a reference describing already-fixed bugs as live. This is the whole reason for the persona+expert pass.

---

## 1. The cut (COSam)

~15 synthesis recommendations + 3 expert proposals → **5 changes in 3 tracks.** What we explicitly did NOT do:

| Cut | Why |
|---|---|
| New "attractor authoring" **skill** | Skills are on-demand; the agent can write a foot-gun before deciding to load it. Engine semantics must fire **unconditionally at spawn** → use @-mention, not a skill. (We already have `dot-syntax`/`dot-patterns`/`dot-quality`/`dot-graph-intelligence` — enrich, don't add.) |
| New **mode** | A mode enforces *tool policy*; "every LLM call is an `llm` node" is a *semantic design rule*. Modes also carry adoption friction. The context file + lint cover it. |
| Separate `attractor-author` **agent** | **DEFER (YAGNI).** attractor-expert already holds the knowledge; give it the authoring role too. Split into a `coding`-role worker only if authoring volume/model-role mismatch proves it. (Open decision #1.) |
| Edit `foundation:behaviors/agents` delegation table | Taxes **every** ecosystem session for attractor-only work. Use the attractor bundle's own already-loaded awareness file instead. |
| Routing-matrix "demote modular-builder on .dot keywords" | **Folklore.** routing-matrix maps `model_role → model`; it has **no agent-selection / keyword mechanism.** Do not build on it. |
| Durable-design-memory mechanism (synthesis D3) | Higher ceremony; defer until week-long sessions are common. |
| Enrich general `dot-graph:dot-author` | Different domain (general Graphviz vs attractor engine). It was used 0× in the friction sessions anyway. |
| Standalone authority-demarcation artifact (Theme 5) | Folded into provenance tags inside the one context file. |

---

## 2. The design — 3 tracks, 5 changes

### TRACK 1 — KNOWLEDGE (kills Themes 1, 4, 5 — highest leverage)

**1A. New `amplifier-bundle-attractor/context/engine-semantics.md`** (tight, ~2–4k tokens, every fact source-cited `file:line`/`§`). Sections:
- **Node-type → handler capability table** with `[NLSPEC]` / `[EXTENSION]` provenance tags (e.g. `folder`/`pipeline` and `tool.last_line` are extensions).
- **The "engine does X, NOT Y (spec says Y)" delta list** — the ~7 points where the shipped `AmplifierBackend` diverges from nlspec prose. *attractor-expert: this is the single highest-value piece — it's exactly where a spec-reading agent goes confidently wrong.*
- Routing contract (`tool.last_line` token channel vs `tool.output` full stdout; bare-token truthy; FAIL fail-fast per §3.7; lexical tiebreak).
- Substitution + CWD (which attrs substitute; dotted-in-prompt caveat; `${var:-default}` defense; `target_dir` CWD).
- Verdict contract (response must be **entirely** JSON or use the `report_outcome` tool; prose-then-JSON is silently coerced — see 3A).
- Remaining real foot-guns (fidelity 200-char `last_response` carry → use `fidelity=full`; folder checkpoint reuse — see 3B).
- **Golden Rules** (Theme 2): every inference is an `llm`/`box` node; never direct `unified_llm` / drop-to-python; code nodes are glue; copy the nearest proven pipeline before inventing; route verdicts via `report_outcome`, not free-text JSON.

**1B. @-mention `engine-semantics.md` into `agents/attractor-expert.md`** and give attractor-expert the explicit **design + authoring** remit for attractor `.dot` work (it already carries the knowledge; the context-sink loads it fresh on every spawn — compaction-immune, the Theme-4 fix).

### TRACK 2 — ROUTING (kills Theme 3 — near-zero cost)

**2A. Strengthen `context/pipeline-awareness.md`** (already in `context.include` via `attractor-core` → ~0 added tokens). Add an explicit "Before delegating pipeline implementation" block: attractor/`.dot`/pipeline/LLM-workflow work → **consult attractor-expert as Step 0** (design + author) *before* any generic builder; state plainly "modular-builder has no attractor engine semantics and will re-discover foot-guns." (foundation-expert: this is the highest-reliability lever because it's already loaded and fires before the generic implementation path.)

**2B. Strengthen `attractor-expert` meta.description** — MUST be consulted at **design → mid-build → review** (not once); prefer over modular-builder when attractor/`.dot` keywords present. (Secondary reinforcement; real but fires late on its own.)

### TRACK 3 — ENGINE FAIL-LOUD + LINT (kills the silent residue of Themes 1, 6 — REAL, provable, continues merged work)

**3A. FIX-LOUD: verdict prose+JSON silently coerced to SUCCESS** (`backend.py:632-637,947`). A node *asked* for a verdict whose response yields no parseable status should emit FAIL / `PIPELINE_NODE_CONTRACT_VIOLATION`, not SUCCESS. Same family as the `auto_status` fix already merged. Cheap — the parser already knows it found neither JSON nor `report_outcome`.

**3B. FIX-LOUD (verify first): folder/subgraph checkpoint reuse across loop iterations** (`pipeline.py:167`, child logs keyed only on node id). Iteration 2+ can restore iteration 1's checkpoint → silently skips real work. **Repro already exists** in the workspace (`prove_folder_failure.py`). Confirm, then namespace child logs by iteration (or detect a re-entered folder checkpoint and clear/warn). Mark OPEN until the repro is run.

**3C. Promote `test_pipeline_lint.py` to a shipped gate** with: `condition=context.tool.output=` mis-use (vs `last_line`); isolated nodes; >1 unconditional edge from one node (warn); prose-before-JSON verdict. (attractor-expert: most remaining foot-guns are DOCUMENT + warn-level lint, not engine changes — keeps the engine-change surface small.)

---

## 3. Sequencing (ROB — provable first, real over speculative)

1. **3B verify** — run `prove_folder_failure.py` (cheap, confirms the one genuinely-open bug before any code change).
2. **1A `engine-semantics.md` + 1B @-mention** — the highest-leverage knowledge change; turns S4's "one good upfront briefing pre-empts every foot-gun" into a default.
3. **2A + 2B routing** — near-zero cost; do alongside Track 1.
4. **3A + 3B fix + 3C lint** — the engine fail-loud batch; same track as the §3.7 / auto_status / goal_gate fixes already merged. Each independently shippable + provable, with a test.

Each track is independently shippable. Track 1+2 are doc/agent-only (low risk). Track 3 is code with tests (the fail-loud line we've already been holding).

---

## 4. Open decisions (need a call)

1. **Separate `attractor-author` (coding-role worker) vs attractor-expert (reasoning) doing both.** Recommend: **defer** — start with attractor-expert dual-role; split only if authoring volume or a reasoning-vs-coding model mismatch shows up.
2. **Home repo for `engine-semantics.md`** — recommend `amplifier-bundle-attractor/context/` (where the engine + most changes live). Confirm vs resolver-dot-graph.
3. **Stale facts in `SYNTHESIS.md` / friction reports** — recommend a one-line "corrected against live engine — see engine-semantics.md" pointer rather than rewriting the reports.

---

## 5. Traceability — change → friction killed

| Change | Theme(s) | Evidence |
|---|---|---|
| 1A engine-semantics.md (esp. X-not-Y delta) | 1, 5 | every session; the stale-fact corrections above |
| 1B @-mention into attractor-expert (context sink) | 4 | S2 "didn't survive as a live belief"; S1 per-subsession re-reads |
| 2A pipeline-awareness Step-0 routing | 3 | S2 3.7% specialist use; S1/S3 0 specialist calls |
| 2B meta.description cadence | 3 | reactive consultation clusters |
| 3A verdict fail-loud | 1 | S3 prose-not-JSON verdict; same class as auto_status |
| 3B folder checkpoint | 1 | S3 F13 (stale subgraph restore); `prove_folder_failure.py` |
| 3C lint gate | 1, 6 | S1 routing-condition class (3× hand-fixed); S4 isolated node / stray quote |
