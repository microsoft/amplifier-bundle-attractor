#!/usr/bin/env bash
# DoD script for the task-runner sample fixture (planted-red drill).
# Run from the target directory (wherever the task runner was pointed).
#
# INTENTIONAL: this script fails at the runner's FIRST gate visit no matter
# what the worker does — it keys on .ai/iter, which only the runner's verify
# node increments. The worker cannot absorb the red during the work phase.
#
# First visit: creates .ai-demo/nonce, prints the required answer, exits 1.
# Second+ visit: checks .ai-demo/answer.txt contains sha256(.ai-demo/nonce).
set -u -o pipefail

mkdir -p .ai-demo

# Create the nonce on first call (idempotent — won't overwrite on retry).
if [ ! -f .ai-demo/nonce ]; then
    date +%s%N > .ai-demo/nonce
fi

want=$(sha256sum .ai-demo/nonce | cut -d' ' -f1)
gate_visits=$(cat .ai/iter 2>/dev/null || echo 0)

if [ "$gate_visits" -lt 2 ]; then
    echo "PLANTED RED (by design): this is runner gate visit $gate_visits; the drill requires >=2." >&2
    echo "" >&2
    echo "Nothing is broken. The corrective path is exercising." >&2
    echo "Ensure .ai-demo/answer.txt contains:" >&2
    echo "  sha256(.ai-demo/nonce) = $want" >&2
    echo "" >&2
    echo "Compute it with: sha256sum .ai-demo/nonce" >&2
    echo "Write the hex digest (first field only) to .ai-demo/answer.txt." >&2
    echo "The NEXT gate visit will pass if the answer is correct." >&2
    echo "Do not modify .ai/ or this script." >&2
    exit 1
fi

if ! grep -q "$want" .ai-demo/answer.txt 2>/dev/null; then
    echo "WRONG ANSWER: .ai-demo/answer.txt must contain sha256(.ai-demo/nonce) = $want" >&2
    echo "Current contents:" >&2
    cat .ai-demo/answer.txt 2>/dev/null || echo "(file missing)" >&2
    exit 1
fi

echo "sample-task mechanical DoD: PASS (planted-red corrective path demonstrated)"
echo "  gate_visits=$gate_visits, nonce=$(cat .ai-demo/nonce), answer=$(cat .ai-demo/answer.txt)"
exit 0
