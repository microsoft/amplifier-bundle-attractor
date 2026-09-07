#!/usr/bin/env bash
# Re-runs every gate this repo's CI runs (.github/workflows/ci.yml), plus the
# skill-local suite CI does not cover but this change touches.
#
#   bash docs/lanes/lswd-attractor-catalog-reduction/evidence/run_suites.sh
#
# Zero spend: pytest, uv, graphviz. No LLM call, no `amplifier` invocation.
set -uo pipefail

# evidence/ -> lswd-.../ -> lanes/ -> docs/ -> repo root
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)"
cd "$REPO" || exit 1

echo "repo: $REPO"
echo "HEAD: $(git rev-parse HEAD)"
echo

echo "=== CI job: opinionated-guards ==="
echo '$ python -m pytest tests/ -q --ignore=tests/e2e'
python -m pytest tests/ -q --ignore=tests/e2e 2>&1 | tail -5
echo

echo "=== CI job: unit-tests (module tool-report-outcome) ==="
echo '$ cd modules/tool-report-outcome && uv run pytest -q'
( cd modules/tool-report-outcome && uv run pytest -q 2>&1 | tail -5 )
echo

echo "=== CI job: dot-render-gate ==="
echo '$ dot -Tsvg on every git-tracked .dot (excluding .ai/ .amplifier/ evals/)'
failed=0
checked=0
while IFS= read -r f; do
  case "$f" in
    .ai/*|.amplifier/*|evals/*) continue ;;
  esac
  checked=$((checked + 1))
  if err="$(dot -Tsvg "$f" -o /dev/null 2>&1)"; then
    continue
  fi
  failed=$((failed + 1))
  echo "FAILED TO RENDER: ${f}: ${err}"
done < <(git ls-files '*.dot')
echo "Checked ${checked} git-tracked .dot file(s); ${failed} failed to render."
echo

echo "=== NOT in CI, run because SKILL.md changed: skills/attractor-scout own suite ==="
echo '$ cd skills/attractor-scout && python -m pytest tests -q'
( cd skills/attractor-scout && python -m pytest tests -q 2>&1 | tail -5 )
