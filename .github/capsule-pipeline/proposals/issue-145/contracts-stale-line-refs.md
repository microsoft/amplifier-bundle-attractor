---
id: contracts-stale-line-refs
title: "docs/CONTRACTS.md line references for engine.py / edge_selection.py are stale"
red_signal: FAIL: engine.py lines 597-600 do not contain 'continue_on_fail'
base_sha: 71e6c4ca2dc98f4f876a20e707e60cb309fe3da1
target_repo: microsoft/amplifier-bundle-attractor
verify: DEFINITION.verify.sh
---

## Goal

`docs/CONTRACTS.md` accurately points readers to the correct line numbers in
`engine.py` and `edge_selection.py` for the `continue_on_fail`, `_get_runs_on`,
and `_check_node_skip` contracts. A reader following any reference in the
"Fail-Fast Policy" section (Section 2) or the "Code Reference Index" table
(Section 6) lands in the described code, not in an unrelated block.

## Why this matters

`docs/CONTRACTS.md` is the primary reference for pipeline authors and resolver
implementers trying to understand the engine's fail-fast and failure-routing
contracts. When its line-number pointers are wrong, readers spend time chasing
the wrong code and may draw incorrect conclusions about how `continue_on_fail`,
`runs_on`, and the outcome-status guard in `edge_selection.py` interact. The
mismatch is large enough (85–264 lines off) that readers reliably land in
completely unrelated functions.

At the pinned SHA the stale references are:

**Section 2 prose (lines 82–88 of CONTRACTS.md):**

| What the doc says | What the file actually contains at that line |
|---|---|
| `engine.py` lines 582–596: canonical explanation of `continue_on_fail` / `runs_on` | Lines 578–596 are the `asyncio.TimeoutError` block building `_timeout_status`, constructing the `Outcome`, and starting `_emit`. No `continue_on_fail` explanation. |
| `engine.py` lines 597–600: "`continue_on_fail` check" | Lines 597–600 are the closing lines of the timeout event dict (`"execution_index": execution_index,` and `}`). No `continue_on_fail` in sight. |
| `engine.py` lines 1332–1357: "`_get_runs_on`" | Line 1332 is `json.dump(manifest, f, indent=2)` — inside `_write_manifest()`. `_get_runs_on` starts at line 1563. |
| `engine.py` lines 1383–1481: "`_check_node_skip`" | Lines 1383–1408 are inside `_write_node_status()`. `_check_node_skip` starts at line 1614. |
| `edge_selection.py` line 79: "outcome-status guard" | Line 79 is a blank line. The `if outcome.status != StageStatus.FAIL:` guard is at line 94. |

**Section 6 Code Reference Index table (lines 163–165 of CONTRACTS.md):**

| What the doc says | What the file actually contains at that line |
|---|---|
| `engine.py` lines 593–596 and comment block 578–592: "`continue_on_fail` override" | Lines 578–596 are the timeout-handling block; the actual `continue_on_fail` conditional is at lines 682–686 with its comment block at lines 667–681. |
| `engine.py` lines 1299–1323: "`_get_runs_on`" | Lines 1299–1323 are inside `_write_manifest()`. `_get_runs_on` is at line 1563. |
| `engine.py` lines 1350–1447: "`_check_node_skip`" | Lines 1350–1408 are inside `_write_node_status()`. `_check_node_skip` is at line 1614. |

The correct target values at the pinned SHA:

- `engine.py` `continue_on_fail` comment block: lines **667–681**
- `engine.py` `continue_on_fail` conditional: lines **682–686**
- `engine.py` `_get_runs_on`: line **1563**
- `engine.py` `_check_node_skip`: line **1614**
- `edge_selection.py` outcome-status guard: line **94**

## Definition of done

**What the verification script checks (mechanically):**

1. `engine.py` lines 597–600 contain `continue_on_fail` — i.e., the doc's
   claimed location now actually contains the described code (or the reference
   has been updated to a range that does).
2. `edge_selection.py` line 79 (or the line the doc now claims for the
   outcome-status guard) contains `outcome.status != StageStatus.FAIL`.
3. `engine.py` line 1332 (or the line the doc now claims for `_get_runs_on`)
   is inside that function — i.e., `_get_runs_on` appears at or before that
   line and `_check_node_skip` appears after it.

The script binds to the observable end state — the doc's claimed lines match
the described code — rather than to any particular new line number, so it
remains valid regardless of how the fix is structured (updating numbers,
switching to symbol anchors, or any other approach that makes the references
accurate).

**Judgment criteria a human reviewer should apply:**

- Both Section 2 prose and the Section 6 table are updated — they currently
  cite different (both wrong) line numbers for the same targets.
- The inline cross-reference on CONTRACTS.md line 82 ("See engine.py lines
  582–596") is also corrected.
- If the fix switches from line-number references to function/symbol anchors,
  the script goes green by design (the stale numbers are gone); a reviewer
  should confirm the symbol names are accurate.

## Non-goals

- Changes to `engine.py`, `edge_selection.py`, or any other source file. This
  is a documentation-only fix.
- Adding CI automation to keep line references fresh. That would be a
  worthwhile follow-up but is out of scope for this fix.
- Updating any other documentation files beyond `docs/CONTRACTS.md`.
