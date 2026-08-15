#!/usr/bin/env bash
# Guidance eval -- wrapper.
#
# Preflight, Gitea instance discovery, PINNED mirror refresh, then dispatch run_guidance_eval.py.
#
# The mirror is refreshed from a DETACHED clone at a resolved SHA, never from a working tree. A
# working-tree push would install whatever happens to be uncommitted on the machine running the
# eval, and the results would silently describe a tree that exists nowhere else.
#
# Usage:
#   ./run.sh --smoke                       # one scenario, full plumbing
#   ./run.sh                               # all six scenarios
#   ./run.sh --scenarios qa-02-never-converges
#   ./run.sh --list                        # what would run, and against which criteria
#
# Extra arguments pass through to run_guidance_eval.py (see `--help` there).
#
# Environment:
#   BUNDLE_REF          ref of amplifier-bundle-attractor under test (default: origin/main)
#   BUNDLE_SRC          local checkout to push from (default: <workspace>/amplifier-bundle-attractor)
#   AMPLIFIER_GITEA_ID  pick a specific gitea instance (default: first running one)
#   GUIDANCE_EVAL_RESULTS_ROOT  where results land (default: outside the repo -- see README)
#   ANTHROPIC_API_KEY   required; falls back to ~/.amplifier/keys.env

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../../.." && pwd)"
BUNDLE_REPO="amplifier-bundle-attractor"
BUNDLE_REF="${BUNDLE_REF:-origin/main}"
CACHE="$HERE/.cache"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" >&2; }
die() { log "ERROR: $*"; exit 1; }

# The workspace this checkout lives in. Normally REPO_ROOT's parent -- but when the harness runs
# from a git worktree parked under <workspace>/.amplifier/worktrees/<name>, one level up lands
# inside .amplifier. Climb out of any .amplifier segment so both layouts resolve the same, which
# is what lets the sibling-venv search below work from either.
WORKSPACE_ROOT="$(cd "$REPO_ROOT/.." && pwd)"
while [ "$(basename "$WORKSPACE_ROOT")" != "/" ]; do
    case "$WORKSPACE_ROOT" in
        */.amplifier/*|*/.amplifier) WORKSPACE_ROOT="$(dirname "$WORKSPACE_ROOT")" ;;
        *) break ;;
    esac
done

# ---- 0. python environment ---------------------------------------------------
# amplifier_evaluation supplies the AI user, extractor and grader. Resolved BEFORE the --list
# short-circuit so inspecting the instrument uses the same interpreter a real run would.
if ! python3 -c "import amplifier_evaluation" 2>/dev/null; then
    for venv in "${AMPLIFIER_EVALUATION_ROOT:-/nonexistent}/.venv" \
                "$WORKSPACE_ROOT/amplifier-bundle-evaluation/.venv" \
                "$REPO_ROOT/../amplifier-bundle-evaluation/.venv" \
                "$HOME"/.amplifier/cache/amplifier-bundle-evaluation-*/.venv; do
        if [ -f "$venv/bin/activate" ]; then
            log "activating $venv"
            # shellcheck disable=SC1091
            . "$venv/bin/activate"
            break
        fi
    done
fi

# `--list` needs none of the infrastructure below; short-circuit so a contributor can inspect the
# instrument without Docker running. run_guidance_eval.py tolerates a missing amplifier_evaluation
# for this path and fails loudly only when a real run is attempted.
for a in "$@"; do
    if [ "$a" = "--list" ]; then
        exec python3 "$HERE/run_guidance_eval.py" --gitea-url none "$@"
    fi
done

# ---- 1. preflight -----------------------------------------------------------
log "preflight"
command -v amplifier-digital-twin >/dev/null || die "amplifier-digital-twin not on PATH"
command -v amplifier-gitea >/dev/null || die "amplifier-gitea not on PATH"
command -v docker >/dev/null || die "docker not on PATH"
docker info >/dev/null 2>&1 || die "Docker is not running"
command -v git >/dev/null || die "git not on PATH"
command -v python3 >/dev/null || die "python3 not on PATH"

python3 -c "import amplifier_evaluation" 2>/dev/null || die \
    "amplifier_evaluation not importable. Clone microsoft/amplifier-bundle-evaluation, run
    'uv sync' there, and activate its .venv (or set AMPLIFIER_EVALUATION_ROOT)."
python3 -c "import yaml" 2>/dev/null || die "pyyaml missing in the active python env"

if [ -z "${ANTHROPIC_API_KEY:-}" ] && [ -f "$HOME/.amplifier/keys.env" ]; then
    set -a
    # shellcheck disable=SC1091
    . "$HOME/.amplifier/keys.env"
    set +a
fi
[ -n "${ANTHROPIC_API_KEY:-}" ] || die "ANTHROPIC_API_KEY not set and not in ~/.amplifier/keys.env"

# ---- 1. gitea ---------------------------------------------------------------
log "resolving a Gitea instance"
GITEA_ID="${AMPLIFIER_GITEA_ID:-}"
if [ -z "$GITEA_ID" ]; then
    GITEA_ID="$(amplifier-gitea list | python3 -c '
import json, sys
running = [i for i in json.load(sys.stdin) if i.get("container_running")]
print(running[0]["id"] if running else "")')"
fi
[ -n "$GITEA_ID" ] || die "no running gitea instance; create one with 'amplifier-gitea create'"

STATUS_JSON="$(amplifier-gitea status "$GITEA_ID")"
GITEA_PORT="$(echo "$STATUS_JSON" | python3 -c 'import json,sys; print(json.load(sys.stdin)["port"])')"
GITEA_TOKEN="$(amplifier-gitea token "$GITEA_ID" | python3 -c 'import json,sys; print(json.load(sys.stdin)["token"])')"
GITEA_URL="http://localhost:$GITEA_PORT"
log "gitea: $GITEA_URL (id=$GITEA_ID)"

# ---- 2. pinned mirror refresh ------------------------------------------------
BUNDLE_SRC="${BUNDLE_SRC:-$REPO_ROOT/../$BUNDLE_REPO}"
[ -e "$BUNDLE_SRC/.git" ] || BUNDLE_SRC="$REPO_ROOT"
log "pinning $BUNDLE_REF from $BUNDLE_SRC"

# A --mirror clone, deliberately, and this is not incidental. A plain `git clone <local-path>`
# rewrites `origin` to point at that local path, so `origin/main` inside the clone resolves to the
# LOCAL checkout's `main` -- which in a workspace whose checkout lags upstream is a different,
# older commit than the `origin/main` the operator meant. That mistake is silent: the mirror gets
# pushed, the DTU installs, and the eval grades a tree nobody asked for.
# --mirror copies refs/* verbatim, including refs/remotes/origin/*, so `origin/main` here means
# what it means in the source checkout. Nothing is checked out; a SHA is all a push needs.
rm -rf "$CACHE/src.git"
mkdir -p "$CACHE"
git clone --quiet --mirror "$BUNDLE_SRC" "$CACHE/src.git" || die "clone of $BUNDLE_SRC failed"
BUNDLE_SHA="$(git -C "$CACHE/src.git" rev-parse --verify --quiet "${BUNDLE_REF}^{commit}" \
    || git -C "$CACHE/src.git" rev-parse --verify --quiet "origin/${BUNDLE_REF}^{commit}")" \
    || die "cannot resolve BUNDLE_REF '$BUNDLE_REF' in $BUNDLE_SRC"

# Guard the exact failure above: whatever ref was asked for, the SHA that gets pushed must carry a
# recognisable bundle. A ref that resolves to a commit without bundle.md is not this repo.
git -C "$CACHE/src.git" cat-file -e "$BUNDLE_SHA:bundle.md" 2>/dev/null \
    || die "resolved SHA $BUNDLE_SHA has no bundle.md -- '$BUNDLE_REF' did not resolve to what you meant"

HEAD_SHA="$(git -C "$BUNDLE_SRC" rev-parse HEAD)"
if [ "$HEAD_SHA" != "$BUNDLE_SHA" ]; then
    log "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    log "NOTE: the DTU will install $BUNDLE_REF @ $BUNDLE_SHA"
    log "NOTE: your checkout HEAD is $HEAD_SHA"
    log "NOTE: uncommitted or unpushed work is NOT what this run grades."
    log "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
fi

# The eval always installs from a mirror BRANCH, so a SHA ref gets a stable branch name.
if echo "$BUNDLE_REF" | grep -Eq '^[0-9a-f]{7,40}$'; then
    BUNDLE_BRANCH="guideval-under-test"
else
    BUNDLE_BRANCH="${BUNDLE_REF#origin/}"
    BUNDLE_BRANCH="${BUNDLE_BRANCH#refs/heads/}"
fi

code="$(curl -sS -H "Authorization: token $GITEA_TOKEN" \
    "$GITEA_URL/api/v1/repos/admin/$BUNDLE_REPO" -o /dev/null -w '%{http_code}')"
if [ "$code" != "200" ]; then
    log "creating mirror repo admin/$BUNDLE_REPO"
    curl -sS -X POST "$GITEA_URL/api/v1/admin/users/admin/repos" \
        -H "Authorization: token $GITEA_TOKEN" -H "Content-Type: application/json" \
        -d "{\"name\":\"$BUNDLE_REPO\",\"default_branch\":\"main\",\"auto_init\":false,\"private\":false}" \
        -o /dev/null
fi

log "pushing $BUNDLE_SHA -> admin/$BUNDLE_REPO@$BUNDLE_BRANCH"
git -C "$CACHE/src.git" -c credential.helper= push --force \
    "http://admin:$GITEA_TOKEN@localhost:$GITEA_PORT/admin/$BUNDLE_REPO.git" \
    "$BUNDLE_SHA:refs/heads/$BUNDLE_BRANCH" >/dev/null 2>&1 \
    || die "mirror push failed"

# ---- 3. dispatch -------------------------------------------------------------
log "dispatching run_guidance_eval.py (bundle=$BUNDLE_BRANCH @ ${BUNDLE_SHA:0:12})"
exec python3 "$HERE/run_guidance_eval.py" \
    --gitea-url "$GITEA_URL" \
    --gitea-token "$GITEA_TOKEN" \
    --bundle-repo "$BUNDLE_REPO" \
    --bundle-branch "$BUNDLE_BRANCH" \
    --bundle-sha "$BUNDLE_SHA" \
    "$@"
