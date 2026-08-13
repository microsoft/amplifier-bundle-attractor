#!/usr/bin/env bash
# Execute the SHIPPED capsule gate -- the exact bytes about to be pushed --
# in a pristine scratch worktree at the pinned base SHA, and assert the
# contract the capsule claims to satisfy.
#
# WHY THIS EXISTS (incident, 2026-08-13): PR #205 shipped a verify.sh
# containing 54 `[REDACTED:assignment]` markers across 31 lines. The
# in-run judge SHIPped a gate that ran; the run-evidence secret scrubber
# then rewrote it before the branch push. Every CI check on that PR was
# green, because NOTHING anywhere executes a shipped capsule gate -- the
# pipeline proves the gate in-run against the pre-push copy, and CI only
# tests the modules. A capsule PR is a proposal that a human is asked to
# approve on the strength of proofs about a script; if the script that
# arrives is not the script that was proven, the proofs are worthless and
# nothing notices.
#
# This is the five-line check that closes it: run what is actually being
# shipped, from a clean tree at the pinned SHA, and refuse to open the PR
# unless it still behaves like the capsule it claims to be.
#
# THE ASSERTED CONTRACT, per lane, is the vendored .dot's own -- not a new
# opinion invented here:
#
#   feature  (feature-capsule.dot, `redgate`): the gate is invoked as
#            `bash .ai/capsule/DEFINITION.verify.sh` with the repo root as
#            cwd, and on EVERY run writes .ai/census with exactly one
#            `AC-<n>: MET` / `AC-<n>: UNMET` row per ingested criterion and
#            nothing else. At the pinned base it must exit 1 (articulate
#            RED) with a complete, well-formed census. When the capsule
#            ships its recorded base census (`<id>.census-red`), the fresh
#            census must carry the SAME AC-ID set. When DEFINITION.md
#            declares a red_signal, it is one whole census row and must
#            appear in the fresh census verbatim.
#
#   defect   (capsule.dot, `redgate`): same invocation; there is NO census
#            in this lane. At the pinned base the gate must exit exactly 1
#            (>=2 is an infrastructure problem, 0 is green-on-main) and
#            must print a line containing the literal red_signal declared
#            in DEFINITION.md's frontmatter -- that is precisely what makes
#            the red "red for the stated reason" rather than an unrelated
#            crash.
#
# Both lanes additionally require non-empty diagnostic output: a gate that
# exits 1 in silence tells a reviewer nothing.
#
# NATIVE to this repository (not one of the vendored pipeline files in this
# directory -- see README.md's provenance section).
#
# usage:
#   verify_shipped_gate.sh --lane feature|defect \
#                          --capsule-dir DIR --id ID \
#                          --repo REPO_ROOT --base-sha SHA \
#                          [--log-dir DIR] [--timeout SECONDS]

set -euo pipefail

LANE=""
CAPSULE_DIR=""
ID=""
REPO=""
BASE_SHA=""
LOG_DIR=""
# The gates run in seconds; feature-capsule.dot's own gate_time_ceiling
# defaults to 180s and capsule.dot's redgate allows 900s in-run. 300s is a
# deliberate middle: generous for any honest gate, short enough that a
# hung/networked gate fails the run instead of burning the job's budget.
TIMEOUT=300

while [ $# -gt 0 ]; do
    case "$1" in
        --lane)        LANE="$2"; shift 2 ;;
        --capsule-dir) CAPSULE_DIR="$2"; shift 2 ;;
        --id)          ID="$2"; shift 2 ;;
        --repo)        REPO="$2"; shift 2 ;;
        --base-sha)    BASE_SHA="$2"; shift 2 ;;
        --log-dir)     LOG_DIR="$2"; shift 2 ;;
        --timeout)     TIMEOUT="$2"; shift 2 ;;
        *) echo "verify_shipped_gate: unknown argument: $1" >&2; exit 2 ;;
    esac
done

for req in LANE CAPSULE_DIR ID REPO BASE_SHA; do
    if [ -z "${!req}" ]; then
        echo "verify_shipped_gate: missing required argument --${req,,}" >&2
        exit 2
    fi
done
case "$LANE" in
    feature|defect) ;;
    *) echo "verify_shipped_gate: --lane must be 'feature' or 'defect', got '$LANE'" >&2; exit 2 ;;
esac

GATE="$CAPSULE_DIR/$ID.verify.sh"
DEFINITION="$CAPSULE_DIR/$ID.md"

fail() {
    echo "::error::verify_shipped_gate: $*" >&2
    exit 1
}

[ -f "$GATE" ] || fail "the shipped gate does not exist: $GATE"
[ -f "$DEFINITION" ] || fail "the shipped definition does not exist: $DEFINITION"

: "${LOG_DIR:=$(mktemp -d)}"
mkdir -p "$LOG_DIR"
GATE_LOG="$LOG_DIR/shipped-gate.log"
CENSUS_COPY="$LOG_DIR/shipped-gate.census"

# --- pristine scratch worktree at the pinned base SHA -------------------
# Pristine matters: the gate's verdict must track the tree it runs in, and
# the workspace this job ran the pipeline in has .ai/ state, caches, and
# whatever the run left behind. A detached worktree at $BASE_SHA is the
# same clean-room the pipeline's own nonvacuity_gate uses.
SCRATCH="$(mktemp -d)"
WORKTREE="$SCRATCH/tree"
cleanup() {
    git -C "$REPO" worktree remove --force "$WORKTREE" >/dev/null 2>&1 || true
    git -C "$REPO" worktree prune >/dev/null 2>&1 || true
    rm -rf "$SCRATCH"
}
trap cleanup EXIT

echo "verify_shipped_gate: lane=$LANE id=$ID"
echo "verify_shipped_gate: creating a pristine worktree at $BASE_SHA"
git -C "$REPO" worktree add --detach --quiet "$WORKTREE" "$BASE_SHA" \
    || fail "could not create a scratch worktree at $BASE_SHA (is the checkout too shallow to reach it?)"

# --- install the EXACT shipped bytes -----------------------------------
# The gate contract fixes the invocation path, so the shipped file is
# installed at .ai/capsule/DEFINITION.verify.sh -- byte-identical content,
# re-verified below, at the only path the gate was ever proven under.
mkdir -p "$WORKTREE/.ai/capsule"
cp "$GATE" "$WORKTREE/.ai/capsule/DEFINITION.verify.sh"
shipped_sum="$(sha256sum < "$GATE" | awk '{print $1}')"
installed_sum="$(sha256sum < "$WORKTREE/.ai/capsule/DEFINITION.verify.sh" | awk '{print $1}')"
[ "$shipped_sum" = "$installed_sum" ] \
    || fail "internal: the installed gate is not byte-identical to the shipped gate ($shipped_sum != $installed_sum)"

# --- stage the capsule's VENDORED GATE FIXTURES beside the gate --------
# A capsule may carry plain sibling files its gate loads by path. That is
# the shape acceptance criteria demand when they require an oracle
# "VENDORED beside the gate inside the capsule": the specify stage's
# `package` step copies every plain regular file from .ai/capsule/ out as
# <id>.<original-name>, so re-staging is exactly the inverse -- strip the
# `<id>.` prefix and put the file back where the gate expects to find it.
#
# WHY THIS EXISTS (incident, 2026-08-13, run 31689374533): before plain
# files could travel with the pair, the only shape that satisfied
# "vendored inside the capsule" was embedding the pinned oracle in
# verify.sh as a base64-ish blob -- which the capsule-artifacts scan then
# blocked, correctly, as shape=high-entropy-token (an encoded blob is
# secret-shaped AND unreviewable). Now the readable file ships. If this
# script did not re-stage it, a gate that loads its oracle from
# .ai/capsule/ would fail HERE, for a reason that is this script's fault
# rather than the gate's -- and the diagnostic above would blame the
# capsule.
#
# Scope is deliberately narrow: `<id>.*.py` and `<id>.*.json` only. Every
# artifact `package` ships alongside the pair (<id>.criteria.md,
# <id>.criteria-digest, <id>.base-sha, <id>.census-red,
# <id>.hypothesis.patch, <id>.critique.md, the findings, the questions)
# carries a different suffix, so the two sets cannot collide -- and an
# extension not named here is left alone rather than guessed at.
fixture_count=0
for fixture in "$CAPSULE_DIR/$ID."*.py "$CAPSULE_DIR/$ID."*.json; do
    [ -f "$fixture" ] || continue
    fixture_base="$(basename "$fixture")"
    staged_name="${fixture_base#"$ID."}"
    cp "$fixture" "$WORKTREE/.ai/capsule/$staged_name"
    fixture_sum="$(sha256sum < "$fixture" | awk '{print $1}')"
    staged_sum="$(sha256sum < "$WORKTREE/.ai/capsule/$staged_name" | awk '{print $1}')"
    [ "$fixture_sum" = "$staged_sum" ] \
        || fail "internal: the staged fixture .ai/capsule/$staged_name is not byte-identical to the shipped $fixture_base ($staged_sum != $fixture_sum)"
    echo "verify_shipped_gate: staged vendored fixture $fixture_base -> .ai/capsule/$staged_name (sha256 $fixture_sum)"
    fixture_count=$((fixture_count + 1))
done
if [ "$fixture_count" -eq 0 ]; then
    echo "verify_shipped_gate: no vendored gate fixtures shipped with this capsule (<id>.*.py / <id>.*.json) -- the gate runs with the pair alone."
fi

echo "verify_shipped_gate: running the shipped gate (sha256 $shipped_sum, ${TIMEOUT}s cap)"
chmod +x "$WORKTREE/.ai/capsule/DEFINITION.verify.sh" 2>/dev/null || true
rm -f "$WORKTREE/.ai/census"

# --- run it ------------------------------------------------------------
rc=0
( cd "$WORKTREE" && timeout "$TIMEOUT" bash .ai/capsule/DEFINITION.verify.sh ) \
    > "$GATE_LOG" 2>&1 || rc=$?
[ -f "$WORKTREE/.ai/census" ] && cp "$WORKTREE/.ai/census" "$CENSUS_COPY"

echo "verify_shipped_gate: rc=$rc; gate output ($(wc -l < "$GATE_LOG") line(s)) at $GATE_LOG"
echo "--- shipped gate output (last 40 lines) ---"
tail -40 "$GATE_LOG" || true
echo "--- end shipped gate output ---"

# --- assert the contract -----------------------------------------------
if [ "$rc" -eq 124 ]; then
    fail "the SHIPPED gate did not finish within ${TIMEOUT}s. A capsule gate is a check a human is asked to trust and a later stage runs repeatedly; one that hangs is not shippable. See the output above."
fi
if [ "$rc" -eq 0 ]; then
    fail "the SHIPPED gate exited 0 (GREEN) at the pinned base SHA $BASE_SHA, but the capsule claims it is RED there. The bytes being pushed do not behave like the gate this run proved. Refusing to open a capsule PR."
fi
if [ "$rc" -ne 1 ]; then
    fail "the SHIPPED gate exited $rc at the pinned base SHA $BASE_SHA. Only rc=1 is a valid RED verdict; rc>=2 is an infrastructure/prerequisite failure -- which is exactly what a corrupted or non-executable script produces (the 2026-08-13 class: a scrubbed verify.sh whose embedded program no longer parses). The bytes being pushed do not run. Refusing to open a capsule PR."
fi
if [ ! -s "$GATE_LOG" ]; then
    fail "the SHIPPED gate exited 1 but printed NOTHING. A silent red tells a reviewer nothing about why. Refusing to open a capsule PR."
fi

# The declared red_signal, read from DEFINITION.md's YAML frontmatter the
# same way both .dot pipelines read it.
red_signal="$(awk '/^---$/{c++;next} c==1 && /^red_signal:/{sub(/^red_signal:[[:space:]]*/,""); print; exit}' "$DEFINITION" | sed 's/[[:space:]]*$//')"

if [ "$LANE" = "feature" ]; then
    census="$WORKTREE/.ai/census"
    [ -f "$census" ] || fail "the SHIPPED gate exited 1 but wrote NO census at .ai/census. feature-capsule.dot's contract is that the gate writes one \`AC-<n>: MET|UNMET\` row per ingested criterion on EVERY run -- the census is the verdict channel, and a capsule without one cannot be adjudicated. Refusing to open a capsule PR."
    [ -s "$census" ] || fail "the SHIPPED gate wrote an EMPTY census at .ai/census. Refusing to open a capsule PR."

    echo "--- shipped gate census ---"
    cat "$census"
    echo "--- end census ---"

    bad="$(grep -vE '^AC-[0-9]+: (MET|UNMET)$' "$census" || true)"
    if [ -n "$bad" ]; then
        { echo "::error::verify_shipped_gate: the SHIPPED gate wrote a MALFORMED census -- every line must be exactly 'AC-<n>: MET' or 'AC-<n>: UNMET' and nothing else. Offending line(s):"; echo "$bad" | sed 's/^/  /'; } >&2
        exit 1
    fi

    ids="$(awk -F: '{print $1}' "$census")"
    dupes="$(echo "$ids" | LC_ALL=C sort | uniq -d)"
    if [ -n "$dupes" ]; then
        { echo "::error::verify_shipped_gate: the SHIPPED gate wrote DUPLICATE census rows -- exactly one row per criterion is required. Duplicated AC-ID(s):"; echo "$dupes" | sed 's/^/  /'; } >&2
        exit 1
    fi

    if ! grep -qE '^AC-[0-9]+: UNMET$' "$census"; then
        fail "the SHIPPED gate exited 1 (RED) but every census row reads MET. That is the rc/census incoherence feature-capsule.dot fails a round on -- a self-contradicting verdict. Refusing to open a capsule PR."
    fi

    # The capsule's own recorded base census, when it ships one, is the
    # verdict the pipeline's redgate MEASURED at this exact SHA. Compare
    # the WHOLE census -- every AC-ID *and* its MET/UNMET verdict -- not
    # merely the ID set.
    #
    # The ID set alone is provably too weak, and this is measured, not
    # theoretical: running the OLD (contains-based) scrubber over a real,
    # good feature capsule corrupted 33 assignments across 15 lines of its
    # gate, and the corrupted gate still exited 1 and still wrote a census
    # with the same six AC-IDs -- because its own error handling caught
    # the syntax failure and dutifully recorded it as UNMET. What gave it
    # away was a VERDICT flip: the `[guard]` criterion that reads MET at
    # base (asserting existing behavior that must still hold) had turned
    # UNMET. A corrupted gate cannot observe anything, so it reports
    # everything absent -- which is exactly the shape this comparison
    # catches and an ID-set check cannot.
    #
    # Rows are compared sorted: order is not part of the contract, the
    # per-criterion verdict is. A gate whose base verdict is not
    # reproducible at the SHA it was proven at is not shippable either --
    # the pipeline re-runs it ~10x per round and every downstream leg
    # (discrimination, non-vacuity) assumes that stability.
    recorded="$CAPSULE_DIR/$ID.census-red"
    if [ -f "$recorded" ]; then
        fresh_rows="$(LC_ALL=C sort "$census")"
        recorded_rows="$(grep -E '^AC-[0-9]+: (MET|UNMET)$' "$recorded" | LC_ALL=C sort)"
        if [ "$fresh_rows" != "$recorded_rows" ]; then
            {
                echo "::error::verify_shipped_gate: the SHIPPED gate's census does not match the census this run RECORDED at the same base SHA ($ID.census-red). The bytes being pushed do not reproduce the verdict they were proven to produce -- either the gate was mutated after it was proven, or it is not reproducible at the SHA it pins. Refusing to open a capsule PR."
                echo "--- recorded ($ID.census-red) vs fresh (this run of the shipped gate) ---"
                diff -u <(echo "$recorded_rows") <(echo "$fresh_rows") \
                    --label "recorded" --label "fresh" || true
            } >&2
            exit 1
        fi
        echo "verify_shipped_gate: census matches the recorded $ID.census-red row-for-row ($(echo "$recorded_rows" | wc -l) criterion/criteria, verdicts included)."
    else
        echo "verify_shipped_gate: no $ID.census-red shipped -- the census could not be cross-checked against a recorded verdict (well-formedness, uniqueness and rc/census coherence were)."
    fi

    if [ -n "$red_signal" ]; then
        grep -qxF "$red_signal" "$census" \
            || fail "the declared red_signal '$red_signal' is not a whole line in the SHIPPED gate's census. feature-capsule.dot checks the red_signal whole-line-equal against the census FILE; a capsule whose declared signal does not appear there is not the capsule that was proven. Refusing to open a capsule PR."
        echo "verify_shipped_gate: declared red_signal '$red_signal' found as a whole census row."
    else
        echo "::warning::verify_shipped_gate: DEFINITION.md declares no red_signal -- census shape and rc were asserted, the signal was not."
    fi
else
    if [ -n "$red_signal" ]; then
        grep -qF -- "$red_signal" "$GATE_LOG" \
            || fail "the SHIPPED gate exited 1, but its output does NOT contain the declared red_signal '$red_signal'. capsule.dot's redgate requires exactly this: the red must be red for the STATED reason, not an unrelated crash. The bytes being pushed do not reproduce the proof. Refusing to open a capsule PR."
        echo "verify_shipped_gate: declared red_signal '$red_signal' found in the shipped gate's output."
    else
        fail "DEFINITION.md declares no red_signal. capsule.dot requires one (it is what proves the red is red for the stated reason), and without it the shipped gate's rc=1 is unattributable. Refusing to open a capsule PR."
    fi
fi

echo "verify_shipped_gate: PASS -- the SHIPPED gate (sha256 $shipped_sum) runs at $BASE_SHA and satisfies the $LANE-lane contract."
