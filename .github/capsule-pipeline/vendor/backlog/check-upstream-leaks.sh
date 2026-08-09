#!/usr/bin/env bash
# ============================================================================
# VENDORED COPY -- see .github/capsule-pipeline/README.md for provenance,
# scope, and re-sync instructions. Source: a private working repository
# (not publicly reachable), pinned at its commit
# b3bcedb5da8d60ce4490ad9ad9e2d547235891f5 (backlog/check-upstream-leaks.sh,
# 2026-08-09 -- subtraction sweep: the \bcapsules?\b deny-seed RETIRED as
# public upstream vocabulary, retirement recorded in the SEED NOTES below),
# fixtures from the same commit. Otherwise byte-identical to the source
# below this header -- do not hand-edit the body; re-sync from source and
# re-apply this header instead.
# ============================================================================
# check-upstream-leaks.sh — structural-leak tripwire for upstream-bound text.
#
# LADDER STEP 2 (pre-committed at the T4-9 checkpoint, fired by the T1-7
# close — structural-leak instance #2, "the evals loop" in EXTENSIONS §28;
# instance #1 was T4-9's §27 retirement inventory citing noverdict/clean_b):
# a grep-able deny-list of internal uplift-mechanism vocabulary, run against
# upstream-bound artifacts (PR bodies, EXTENSIONS entries, commit messages,
# doc diffs) — INCLUDING tracked files, not just diffs.
#
# CONTRACT:
#   - HIT  = exit 1 (BLOCK — a human adjudicates; a hit is a question, not
#     automatically a leak).
#   - SILENCE PROVES NOTHING: exit 0 means only "no seeded pattern matched."
#     The membership test (backlog/TEMPLATE.md "Upstream citation boundary")
#     + the close-stage human leak scan remain the CONTROL OF RECORD; this
#     script is a mechanical tripwire beneath them, never their replacement.
#   - Seed list per the T1-7 council; EXTENDABLE — add a line to DENY below
#     whenever a new internal mechanism name is coined.
#   - CLOSE-RITUAL OWNERSHIP (T2-7 council, sam): a leak adjudicated TRUE
#     at a close EXTENDS the seed list AND commits a RED fixture
#     reconstructing the missed text, in the SAME close. The scanner only
#     ever knows the vocabulary its misses have taught it; the ritual is
#     what feeds it.
#   - DE-ESCALATION PRE-COMMITMENT (T2-7 council): after N=5 consecutive
#     leak-free closes — counting ONLY closes that shipped upstream-bound
#     text; the clock starts at the T2-7 close, AFTER the T2-7 seed
#     extension — a retirement review of this tripwire is warranted
#     (a review, not an automatic removal; the membership test + human
#     scan remain the control of record either way).
#
# SEED NOTES (morphology-tolerant, with two membership-test carve-outs):
#   - no[_-]?verdicts? matches the COMPOUND forms only (noverdict,
#     no-verdict). The spaced phrase "no verdict" is legitimate upstream
#     spec prose (EXTENSIONS §25 goal-gate text: "no verdict was produced")
#     — the compound is ours, the phrase is theirs.
#   - pm[_-]?gate is DUAL-STATUS: upstream's own exemplar
#     examples/patterns/task-runner.dot ships a pm_gate node, so shipped
#     upstream text may cite it legitimately. A pm_gate hit is ALLOWED only
#     when the scanned artifact itself anchors the term to that exemplar
#     (contains "examples/patterns/task-runner"); unanchored uses block.
#   - TASK IDs (T2-7 close — the maiden run caught 1 of 3 leaked
#     vocabulary classes; "T2-3 COORDINATION" scanned clean): the patterns
#     \bT<digits>-<digits>\b and \bEX-<digits>\b are our internal task-ID
#     vocabulary, EXCEPT the IDs upstream's own shipped tree already
#     carries — earlier merged waves baked T0-1/T0-4/T0-5/T1-1 into
#     upstream's AGENTS.md, PRINCIPLES.md, SPEC_CONFORMANCE.md,
#     specs/EXTENSIONS.md ("Conformance Restoration Note (T0-4)") and
#     docs/ (census: amplifier-bundle-attractor @ ef382c4; re-run
#     `git grep -ohE '\bT[0-9]+-[0-9]+\b' | sort -u` there when extending
#     TID_ALLOW). Citing an ID upstream already carries is a question
#     already adjudicated ALLOWED; any OTHER ID blocks. Case-sensitive:
#     canonical IDs are uppercase (lowercase t11-attempt-1 style run
#     names are not task-ID citations).
#   - \bprimer\b (T2-7 close — "primer §6.11" scanned clean on the maiden
#     run): the doctrine primer is context/primer.md, internal-only;
#     upstream's shipped tree contains zero uses of the word (same census
#     ref). Section pointers into it are exactly the leak shape that
#     slipped.
#   - \bcapsules?\b RETIRED (subtraction sweep 2026-08-09): the word became
#     PUBLIC vocabulary when the pipeline shipped upstream —
#     .github/capsule-pipeline/ plus workflows/README/docs carry 301 uses of
#     capsule/capsules in the shipped tree. Measured cost of keeping it:
#     burned iteration 1 in 5/6 eval runs + live CI iteration burns (authors
#     legitimately echo the word). No fixture existed solely for this
#     pattern, so none was removed with it.
#
# Usage:
#   backlog/check-upstream-leaks.sh <file>...     # scan files ('-' = stdin)
#   git diff upstream-base..HEAD | backlog/check-upstream-leaks.sh -
#   backlog/check-upstream-leaks.sh --self-test   # RED/GREEN proof (both
#       known leak instances reconstructed as fixtures must exit 1; the
#       current shipped EXTENSIONS §27/§28 text must exit 0)
# Exit: 0 clean, 1 blocking hit(s), 2 usage/self-test-failure.
# ============================================================================
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"

DENY='clean[_-]?b\b
no[_-]?verdicts?\b
evals?[-_ ](loop|harness|batter(y|ies))
test[_-]verdict[_-]classification|verdict[-_ ]classification
attractor[-_ ]uplift|\buplift\b
\bbacklog\b
\bprimer\b'
DENY_PM='pm[_-]?gate\b'
PM_ANCHOR='examples/patterns/task-runner'
# Task-ID census (T2-7 close; see SEED NOTES): case-SENSITIVE extraction,
# then subtract the IDs upstream's shipped tree already carries.
DENY_TID='\b(T[0-9]+-[0-9]+|EX-[0-9]+)\b'
TID_ALLOW='T0-1|T0-4|T0-5|T1-1'

scan_file() {  # scan_file <path-or-dash> -> sets HITS (global counter)
    local f="$1" body tmp=""
    if [ "$f" = "-" ]; then
        tmp=$(mktemp); cat > "$tmp"; body="$tmp"; f="(stdin)"
    else
        [ -r "$1" ] || { echo "unreadable: $1" >&2; HITS=$((HITS+1)); return; }
        body="$1"
    fi
    while IFS= read -r pat; do
        [ -n "$pat" ] || continue
        if grep -nEiq "$pat" "$body"; then
            echo "LEAK-HIT [$f] pattern '$pat':" >&2
            grep -nEi "$pat" "$body" | head -5 >&2
            HITS=$((HITS+1))
        fi
    done <<EOF
$DENY
EOF
    tids=$(grep -oE "$DENY_TID" "$body" 2>/dev/null | sort -u | grep -vE "^($TID_ALLOW)$")
    if [ -n "$tids" ]; then
        echo "LEAK-HIT [$f] internal task id(s) not carried by upstream's shipped tree (TID_ALLOW census): $(printf '%s' "$tids" | tr '\n' ' ')" >&2
        grep -nE "$DENY_TID" "$body" | head -5 >&2
        HITS=$((HITS+1))
    fi
    if grep -nEiq "$DENY_PM" "$body"; then
        if grep -q "$PM_ANCHOR" "$body"; then
            echo "note [$f]: pm_gate hit ALLOWED (anchored to upstream exemplar $PM_ANCHOR — membership-test carve-out)" >&2
        else
            echo "LEAK-HIT [$f] pattern '$DENY_PM' (UNANCHORED — no $PM_ANCHOR citation in artifact):" >&2
            grep -nEi "$DENY_PM" "$body" | head -5 >&2
            HITS=$((HITS+1))
        fi
    fi
    [ -n "$tmp" ] && rm -f "$tmp"
}

if [ "${1:-}" = "--self-test" ]; then
    FX="$HERE/fixtures/leak-scan"
    st_fail=0
    for red in "$FX/t4-9-prefix.RED.txt" "$FX/t1-7-prefix.RED.txt" \
               "$FX/t2-7-coordination.RED.txt" "$FX/t2-7-primer-ref.RED.txt"; do
        if "$0" "$red" >/dev/null 2>&1; then
            echo "SELF-TEST FAIL: $red did NOT trip the scanner (expected exit 1)" >&2
            st_fail=1
        else
            echo "self-test ok: RED fixture trips the scanner: $(basename "$red")"
        fi
    done
    if "$0" "$FX/extensions-27-28.GREEN.md" >/dev/null 2>&1; then
        echo "self-test ok: GREEN fixture (shipped EXTENSIONS §27/§28 text) passes clean"
    else
        echo "SELF-TEST FAIL: shipped EXTENSIONS §27/§28 text tripped the scanner (expected exit 0)" >&2
        st_fail=1
    fi
    # False-positive control (T2-7 close): task IDs upstream's own shipped
    # tree already carries must NOT block (TID_ALLOW carve-out).
    if printf 'the fan-out retirement (T0-4) restored spec single-best-edge selection; see also T0-1, T0-5 and T1-1.\n' | "$0" - >/dev/null 2>&1; then
        echo "self-test ok: GREEN control — upstream-carried task ids (T0-1/T0-4/T0-5/T1-1) pass the TID_ALLOW carve-out"
    else
        echo "SELF-TEST FAIL: upstream-carried task ids tripped the TID scan (carve-out broken)" >&2
        st_fail=1
    fi
    [ $st_fail -eq 0 ] && echo "check-upstream-leaks self-test: PASS (RED x4, GREEN x2)"
    exit $((st_fail == 0 ? 0 : 2))
fi

[ $# -ge 1 ] || { echo "usage: $0 <file>... | -  (or --self-test)" >&2; exit 2; }

HITS=0
for f in "$@"; do scan_file "$f"; done
if [ "$HITS" -gt 0 ]; then
    echo "BLOCKED: $HITS deny-list hit group(s) — adjudicate before shipping upstream (a hit is a question, not a verdict; the membership test is the control of record)." >&2
    exit 1
fi
echo "clean (no seeded pattern matched — silence proves nothing; run the membership test + human scan)."
exit 0
