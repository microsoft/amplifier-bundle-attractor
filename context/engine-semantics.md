# Attractor Engine Runtime Semantics (SHIPPED engine)

Runtime semantics of the **SHIPPED** engine (`AmplifierBackend`), above DOT syntax.
Where this diverges from the nlspec prose, the **SHIPPED behavior wins**. Each fact is
cited (`file:line` in `modules/loop-pipeline/amplifier_module_loop_pipeline/`, or nlspec
`§`). Re-validate any fact whose cite breaks — a broken cite means the engine moved and
this file is stale.

Cites are relative to `modules/loop-pipeline/amplifier_module_loop_pipeline/`.
nlspec = `attractor/attractor-spec.md`.

---

## 1. Node-type → handler capability table

Source: nlspec §2.8; `validation.py:24-34` (`SHAPE_TO_HANDLER`).

| shape | handler | LLM? | runs code? | set context / route? | tag |
|---|---|---|---|---|---|
| `Mdiamond` | `start` | no | no | no (no-op SUCCESS) `handlers/start.py` | [NLSPEC] |
| `Msquare` | `exit` | no | no | no (engine checks goal gates) `handlers/exit.py` | [NLSPEC] |
| `box` | `codergen` | **yes** | no | **YES via backend** (JSON / `report_outcome`) `backend.py:604-637` | [NLSPEC] |
| `diamond` | `conditional` | no | no | no-op SUCCESS; engine routes `handlers/conditional.py` | [NLSPEC] |
| `hexagon` | `wait.human` | no | no | yes — `suggested_next_ids` + `human.gate.*` `handlers/human.py` | [NLSPEC] |
| `component` | `parallel` | no (orchestrates) | no | emits `branch.{i}.outcome` `handlers/parallel.py` | [NLSPEC] |
| `tripleoctagon` | `parallel.fan_in` | **yes if `prompt` set** | no | yes `handlers/fan_in.py` (§4.9) | [NLSPEC] |
| `parallelogram` | `tool` | no | **yes (shell)** | yes — `tool.output` + `tool.last_line` `handlers/tool.py` | [NLSPEC] |
| `house` | `stack.manager_loop` | no | orchestrates child | experimental ("future form TBD" `validation.py:33`) | [EXTENSION] |
| `folder` | `pipeline` | no (runs child graph) | no | yes — merges child `outputs=` back `handlers/pipeline.py` | [EXTENSION] |

Handler resolution: explicit `type` attr → shape mapping → default `codergen` (nlspec §4.2;
`node_outputs.py:83-89`).

---

## 2. ★ THE DELTA LIST — engine does X, NOT Y (spec says Y) ★

**HIGHEST-VALUE SECTION.** The nlspec prose describes the *pure* handlers; the *shipped*
`AmplifierBackend` behaves differently. Reasoning from the spec here makes you confidently
wrong about the running engine.

1. **`box`/codergen nodes CAN route and set context.** Spec §4.5 shows `CodergenHandler`
   returning hard-coded SUCCESS. SHIPPED: the backend maps the LLM result to a full
   `Outcome` (status, `context_updates`, `preferred_label`, `suggested_next_ids`) via
   (a) a response that is entirely JSON → `_parse_outcome` (`backend.py:903`), or
   (b) the child calling the **`report_outcome` tool** → `_find_report_outcome_call`
   (`backend.py:621,827-890`). LLM nodes are NOT routing-inert.

2. **FAIL is fail-fast — it does NOT traverse plain edges.** Spec §3.2 pseudocode advances
   on any selected edge. SHIPPED (`edge_selection.py:79-101`): on `status==FAIL`, plain
   unconditional edges are skipped. FAIL routes ONLY via `condition="outcome=fail"`, a
   downstream node with `runs_on=always|failure`, or `retry_target`/`fallback_retry_target`
   (§3.7); else the branch halts FAIL. (This is the §3.7 fix merged this session.)

3. **Dotted context keys DO expand** in `tool_command` / `tool_env` / `description`
   (`substitution.py:90-103`, M5) — `${tool.output}`, `$tool.output` both resolve. The
   old "dotted keys not expanded" belief is stale. **CAVEAT:** they do NOT expand inside a
   codergen `prompt` — prompts only expand `$goal`, `$context`, and *plain* (non-dotted)
   keys (`codergen.py:144-173`).

4. **Tool CWD = `context.target_dir` → `graph.source_dir` → process default** — NOT the
   engine dir (`tool.py:116-123`). Set `context.target_dir` for the job dir; no `${JOB_DIR}`
   injection needed.

5. **Verdict fences are tolerated.** Spec implies strict bare JSON. SHIPPED strips
   ` ```json … ``` ` fences before parsing (`backend.py:614-618,925-927`). (The real
   foot-gun is prose-before-JSON — see §6.)

6. **No backend / no `llm_model` now RAISE (fail-loud), not silently degrade.**
   `CodergenHandler` with no backend raises (`codergen.py:88-92`); `_resolve_model` raises
   with no `llm_model` (`backend.py:772`). The old "silent DirectProviderBackend / silent
   default model" modes are closed.

7. **Invalid `fidelity=` warns, not silently defaults.** `fidelity.py:78,94,109,192` (M-22)
   logs a warning then falls back to `compact`.

---

## 3. Routing contract

Source: `edge_selection.py`; `handlers/tool.py`; nlspec §3.3, §3.7, §10.

- **Token channel:** route a tool node via `condition="context.tool.last_line=<token>"`.
  `tool.last_line` = last non-empty stripped stdout line (`tool.py:210-219`).
  `tool.output` = **full stdout** (`tool.py:177`) — conditioning on it silently never matches.
- **Bare-token condition** = truthy lookup: `condition="context.flag"` is true iff the value
  is non-empty (nlspec §10.5; `conditions.py`).
- **5-step selection** (§3.3; `edge_selection.py:39-101`): condition-match → `preferred_label`
  (unconditional edges only) → `suggested_next_ids` (unconditional only) → highest `weight`
  → **lexical tiebreak on target id**. The lexical tiebreak is silent but specified —
  >1 unconditional edge from one node picks lexically-first.
- **Tool non-zero exit → FAIL** (`tool.py:156-174`); needs an explicit FAIL route per #2 above.
- **No edge selected & outcome≠FAIL → branch terminates SUCCESS** (nlspec §3.2 step 6). It
  does NOT hard-fail `no_matching_edge` and does NOT loop. "Every LLM node needs an
  unconditional fallback" is an authoring/lint discipline, not a runtime hard-fail. [MEDIUM]

---

## 4. Substitution + CWD

Source: `substitution.py`; `node_outputs.py:68-75`; `handlers/tool.py:116-123`.

- Both `$key` and `${key}` resolve, including dotted keys. `$$` → literal `$`.
- **Substitutable attrs only:** `tool_command`, `prompt`, `description`, `tool_env`
  (`SUBSTITUTABLE_ATTRS`, `node_outputs.py:68`). Other attrs are not scanned.
- **Prompt caveat (repeat of delta #3):** dotted keys do NOT expand in `prompt`; only
  `$goal`, `$context`, plain keys do (`codergen.py:144-173`).
- **Absent key → literal token survives** (`substitution.py:11`, intentional pass-through).
  Under `set -eu` bash this dies "unbound variable". **Defense:** shell default
  `${var:-fallback}` in the `tool_command`.
- **CWD:** `context.target_dir` → `graph.source_dir` (the `.dot`'s dir) → process default.

---

## 5. Verdict contract

Source: `backend.py:604-637, 903-951`.

- A verdict status is taken from the response **only if the entire stripped response is a
  JSON object** (`stripped.startswith("{")`) or a ` ```json ``` ` fenced block
  (`backend.py:614-618`). Not "JSON on the last line" — the **whole** message.
- **KNOWN OPEN BUG (fix planned — design Track 3A):** prose-then-JSON → `startswith("{")` is false → falls
  through to `report_outcome` args → else **plain prose → silently coerced to SUCCESS**
  (`backend.py:632-637, 947-951`). A model that "explains, then emits JSON" has its verdict
  silently dropped. Empty response → FAIL.
- **Robust path:** have the node call the **`report_outcome` tool** rather than emit
  free-text JSON.

---

## 6. Remaining real foot-guns

- **`last_response` inter-node carry is ~200 chars** under every fidelity mode except `full`
  — the truncation is in the handler writing the key (`codergen.py:137`, `response_text[:200]`),
  not in `compact` specifically. `compact`/`truncate` preambles surface that short key;
  **`full` bypasses it** via stored transcripts (`backend.py:643-704`). Need the full prior
  text downstream? Use `fidelity=full`.
- **`folder`/subgraph checkpoint reuse across loop iterations** `[UNVERIFIED]` — child logs
  use a node-id-keyed path `subgraph_<node.id>` (`pipeline.py:167`); a folder re-entered in a
  loop reuses the same child log/checkpoint dir and may restore stale completed-state (the
  "skips all but the 1st source" symptom). Child-engine resume gate not yet traced; repro
  `prove_folder_failure.py` pending. Treat as open until confirmed.

---

## 7. Golden Rules

1. **Every inference is an `llm`/`box` node.** Never call `unified_llm` directly, never
   drop to Python for model calls.
2. **Code nodes (`parallelogram`/tool) are glue only** — shell/IO/orchestration, not inference.
3. **Copy the nearest proven pipeline before inventing.** Simplicity applies to the proven
   pattern, not to a minimal node count built on a wrong engine model.
4. **Route verdicts via the `report_outcome` tool, not free-text JSON** (§5 bug).
5. **Run `dot_graph validate` after every edit** — catches isolated nodes, stray quotes,
   missing fallback edges before an expensive rebuild.
6. **Author for fail-loud:** explicit FAIL edges (§2 #2), explicit `llm_model`, explicit
   `${var:-default}` in shell.
