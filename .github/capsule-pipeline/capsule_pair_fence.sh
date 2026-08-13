#!/usr/bin/env bash
# Pair-integrity fence for the capsule artifacts destined for a capsule PR.
#
# WHY THIS EXISTS (incident, 2026-08-13): the run-evidence secret scrubber
# ran over capsule_out along with the logs and rewrote 54 token-accounting
# assignments across 31 lines of a judge-approved verify.sh -- and the
# CORRUPTED file is what got pushed (PR #205). Nothing between the pipeline
# run and the branch push had any opinion about whether the bytes changed,
# and no CI check executes a shipped capsule gate, so every check stayed
# green over a gate that no longer parses.
#
# The fence closes that window mechanically, and it is deliberately dumb:
#
#   record <capsule_out> <manifest>   -- sha256 every file in capsule_out,
#                                        immediately after the pipeline run,
#                                        before ANY later step touches it.
#   verify <capsule_out> <manifest>   -- re-compute after all scrub/scan/
#                                        classification processing and
#                                        immediately before the bytes are
#                                        staged for the branch push. ANY
#                                        difference -- changed, added, or
#                                        removed file -- is a loud failure
#                                        that NAMES the files.
#
# It makes no judgment about WHY bytes changed and offers no repair path.
# The capsule pair (DEFINITION.md + DEFINITION.verify.sh and the evidence
# shipped beside them) is the run's output under review; silently mutating
# it invalidates every proof the run just spent hours establishing. A run
# that would have mutated it must fail, not publish.
#
# NATIVE to this repository (not one of the vendored pipeline files in this
# directory -- see README.md's provenance section).
#
# Stdlib/coreutils only: it must run on a bare GitHub Actions runner.

set -euo pipefail

usage() {
    cat >&2 <<'EOF'
usage: capsule_pair_fence.sh record <capsule_out_dir> <manifest_path>
       capsule_pair_fence.sh verify <capsule_out_dir> <manifest_path>
EOF
    exit 2
}

# sha256 of every regular file under $1, sorted by path, paths relative to
# $1 so the manifest is location-independent.
manifest_of() {
    local dir="$1"
    ( cd "$dir" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 -r sha256sum )
}

cmd_record() {
    local dir="$1" manifest="$2"
    if [ ! -d "$dir" ]; then
        echo "::error::capsule_pair_fence: cannot record -- capsule_out directory does not exist: $dir" >&2
        exit 1
    fi
    mkdir -p "$(dirname "$manifest")"
    manifest_of "$dir" > "$manifest"
    echo "capsule_pair_fence: recorded $(wc -l < "$manifest") file digest(s) from $dir"
    echo "--- capsule pair manifest (sha256) ---"
    cat "$manifest"
}

cmd_verify() {
    local dir="$1" manifest="$2"
    if [ ! -f "$manifest" ]; then
        echo "::error::capsule_pair_fence: INTEGRITY UNPROVABLE -- no manifest at $manifest. The fence is recorded immediately after the pipeline run; a missing manifest means that step did not run, so byte-identity of the capsule pair cannot be attested. Refusing to push an unattested capsule." >&2
        exit 1
    fi
    if [ ! -d "$dir" ]; then
        echo "::error::capsule_pair_fence: INTEGRITY VIOLATION -- capsule_out directory disappeared between the pipeline run and the push: $dir" >&2
        exit 1
    fi

    local now
    now="$(mktemp)"
    manifest_of "$dir" > "$now"

    if diff -u "$manifest" "$now" > /dev/null 2>&1; then
        echo "capsule_pair_fence: OK -- all $(wc -l < "$manifest") capsule_out file(s) are byte-identical to what the pipeline produced."
        rm -f "$now"
        return 0
    fi

    # Name the files. `-` lines are the recorded state, `+` the current one.
    local changed added removed
    removed="$(comm -23 <(awk '{print $2}' "$manifest" | LC_ALL=C sort) <(awk '{print $2}' "$now" | LC_ALL=C sort) || true)"
    added="$(comm -13 <(awk '{print $2}' "$manifest" | LC_ALL=C sort) <(awk '{print $2}' "$now" | LC_ALL=C sort) || true)"
    changed="$(join <(LC_ALL=C sort -k2 "$manifest" | awk '{print $2, $1}') \
                    <(LC_ALL=C sort -k2 "$now" | awk '{print $2, $1}') \
               | awk '$2 != $3 {print $1}' || true)"

    {
        echo "::error::capsule_pair_fence: INTEGRITY VIOLATION -- the capsule artifacts in $dir are NOT byte-identical to what the pipeline produced. Something between the pipeline run and the branch push MUTATED the capsule pair. This is exactly the 2026-08-13 corruption class (the secret scrubber rewriting a shipped verify.sh) and it is never acceptable: the capsule is the run's reviewed output, and its proofs (articulate-RED at base, non-vacuity, the judge's SHIP) attach to THESE bytes and no others. Refusing to open a capsule PR."
        [ -n "$changed" ] && { echo "MUTATED file(s):"; echo "$changed" | sed 's/^/  /'; }
        [ -n "$added" ]   && { echo "ADDED file(s) (not present when the pipeline finished):"; echo "$added" | sed 's/^/  /'; }
        [ -n "$removed" ] && { echo "REMOVED file(s) (present when the pipeline finished, gone now):"; echo "$removed" | sed 's/^/  /'; }
        echo "--- recorded vs current manifest ---"
        diff -u "$manifest" "$now" || true
    } >&2

    rm -f "$now"
    exit 1
}

[ $# -eq 3 ] || usage
case "$1" in
    record) cmd_record "$2" "$3" ;;
    verify) cmd_verify "$2" "$3" ;;
    *) usage ;;
esac
