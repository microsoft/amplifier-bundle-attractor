#!/usr/bin/env python3
"""Run validate-agents' DETERMINISTIC phases (0-3) against a checkout, at $0.

The recipe's phases `environment-check`, `agent-discovery`,
`structural-validation` and `quality-classification` are pure bash/python
heredocs -- no LLM. This runner executes those steps VERBATIM out of the
recipe YAML (no re-implementation, so it cannot diverge from the recipe) and
prints the resulting `quality_level`, error/warning counts and per-agent
classification.

Used to establish the pre-change (stock) baseline verdict without paying for a
second full recipe run. The full recipe -- including its LLM phases -- is run
once, on the branch, and its verdict is quoted separately.

    usage: run_validate_phases.py <recipe.yaml> <repo-path>
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

DETERMINISTIC = [
    "environment-check",
    "agent-discovery",
    "structural-validation",
    "quality-classification",
]


def substitute(command: str, ctx: dict) -> str:
    def repl(m: re.Match) -> str:
        key = m.group(1).strip()
        if key not in ctx:
            return m.group(0)
        val = ctx[key]
        return val if isinstance(val, str) else json.dumps(val)

    return re.sub(r"\{\{([^}]+)\}\}", repl, command)


def main(recipe_path: str, repo_path: str) -> int:
    recipe = yaml.safe_load(Path(recipe_path).read_text())
    steps = {s["id"]: s for s in recipe["steps"]}
    ctx: dict = {"repo_path": repo_path}

    for step_id in DETERMINISTIC:
        step = steps[step_id]
        cmd = substitute(step["command"], ctx)
        proc = subprocess.run(
            ["bash", "-c", cmd], capture_output=True, text=True, timeout=120
        )
        if proc.returncode != 0:
            print(f"STEP {step_id} FAILED rc={proc.returncode}", file=sys.stderr)
            print(proc.stderr[-2000:], file=sys.stderr)
            return proc.returncode
        out = proc.stdout.strip()
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError:
            print(f"STEP {step_id}: non-JSON output", file=sys.stderr)
            print(out[-2000:], file=sys.stderr)
            return 1
        ctx[step["output"]] = parsed

    struct = ctx["structural_results"]
    qual = ctx["quality_classification"]
    print(f"repo: {repo_path}")
    print(f"agents discovered: {ctx['discovery_results']['total_count']}")
    print(f"structural summary: {struct['summary']}")
    print(f"quality_level: {qual['quality_level']}")
    print(f"quality summary: {qual['summary']}")
    for a in struct["agents"]:
        codes = [e.get("code") for e in a.get("errors", [])]
        wcodes = [w.get("code") for w in a.get("warnings", [])]
        print(
            f"  {a['name']:24s} chars={a['description_length']:5d} "
            f"examples={a['example_count']} commentary={a['commentary_count']} "
            f"errors={codes} warnings={wcodes}"
        )
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(main(sys.argv[1], sys.argv[2]))
