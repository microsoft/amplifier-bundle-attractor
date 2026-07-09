"""attractor: run an arbitrary attractor DOT pipeline standalone.

Thin argv front door over ``run_pipeline`` / ``runner.py``. Fails loud: a
missing provider API key, missing DOT source, or a pipeline error all print a
clear message and exit non-zero. No fallbacks, no synthetic success.

Subcommands:
    run       <dot_file>   run a DOT pipeline
    doctor                 environment diagnostics
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from . import runner
from .params import parse_params


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="attractor", description="Run an attractor DOT pipeline standalone."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a DOT pipeline")
    run.add_argument("dot_file", nargs="?", help="path to a .dot file")
    run.add_argument(
        "--dot-source",
        help="inline DOT digraph string (alternative to dot_file)",
    )
    run.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="k=v",
        help=(
            "key=value param for $param expansion in node prompts (repeatable). "
            "Value may be @path/to/file to read the param's value from a file's "
            "full contents (curl-style; handy for multi-line content like a "
            "checkbox worklist or spec). Use @@literal for a literal value "
            "starting with '@' (e.g. --param handle=@@jdoe -> '@jdoe')."
        ),
    )
    run.add_argument(
        "--provider",
        default="anthropic",
        help="provider whose API key to preflight-check (default: anthropic)",
    )
    run.add_argument(
        "--logs-root",
        default=None,
        help="directory for run logs (default: a fresh tempdir)",
    )
    run.add_argument(
        "--cwd",
        default=None,
        help=(
            "working directory for the pipeline -- where box-node agents and "
            "tool-node commands write files (default: current directory; "
            "created if it doesn't exist)"
        ),
    )

    sub.add_parser("doctor", help="environment diagnostics")

    return parser


def cmd_run(args: argparse.Namespace) -> int:
    # --- Resolve DOT source: --dot-source wins, else read dot_file ---
    if args.dot_source:
        dot_source = args.dot_source
    elif args.dot_file:
        dot_path = Path(args.dot_file).expanduser()
        if not dot_path.is_file():
            print(f"attractor: DOT file not found: {dot_path}", file=sys.stderr)
            return 1
        dot_source = dot_path.read_text(encoding="utf-8")
    else:
        print(
            "attractor: either a dot_file argument or --dot-source is required",
            file=sys.stderr,
        )
        return 1

    # --- Parse params (fail loud on malformed entries) ---
    try:
        params = parse_params(args.param)
    except ValueError as e:
        print(f"attractor: {e}", file=sys.stderr)
        return 1

    # --- Fail loud: unknown --provider is a CLI-argument error ---
    if args.provider not in runner.PROVIDER_KEY_ENV:
        print(
            f"attractor: unknown provider {args.provider!r}. Known providers: "
            f"{', '.join(sorted(runner.PROVIDER_KEY_ENV))}",
            file=sys.stderr,
        )
        return 1

    # --- Fail loud: provider API key must be present BEFORE we run anything ---
    key_env = runner.PROVIDER_KEY_ENV[args.provider]
    if not os.environ.get(key_env):
        print(
            f"attractor: missing API key -- set {key_env} for provider {args.provider!r}",
            file=sys.stderr,
        )
        return 1

    # --- Resolve logs root ---
    if args.logs_root:
        logs_root = Path(args.logs_root).expanduser()
    else:
        logs_root = Path(tempfile.mkdtemp(prefix="attractor-run-"))

    # --- Resolve pipeline working directory ---
    if args.cwd:
        cwd = Path(args.cwd).expanduser().resolve()
    else:
        cwd = Path.cwd()

    print(f"attractor: running pipeline (cwd: {cwd}, logs: {logs_root})")

    try:
        result = asyncio.run(
            runner.run_pipeline(
                dot_source,
                params=params or None,
                provider=args.provider,
                logs_root=logs_root,
                cwd=cwd,
            )
        )
    except Exception as e:  # noqa: BLE001 -- fail loud with the real error, no fallback
        print(
            f"attractor: pipeline execution failed: {type(e).__name__}: {e}",
            file=sys.stderr,
        )
        return 1

    print(f"attractor: status={result.status}")
    print(f"attractor: logs={result.logs_dir}")
    if result.notes:
        print("attractor: notes:")
        print(result.notes)
    print(
        json.dumps(
            {
                "status": result.status,
                "notes": result.notes,
                "logs_dir": str(result.logs_dir),
            }
        )
    )

    return 0 if result.status == "success" else 1


def cmd_doctor(args: argparse.Namespace) -> int:
    del args  # doctor takes no options today

    ok = True

    try:
        import amplifier_module_loop_pipeline  # noqa: F401
    except ImportError as e:
        print(
            f"attractor doctor: FAIL -- amplifier_module_loop_pipeline not importable: {e}"
        )
        ok = False
    else:
        print("attractor doctor: OK -- amplifier_module_loop_pipeline importable")

    for provider, env_name in sorted(runner.PROVIDER_KEY_ENV.items()):
        present = bool(os.environ.get(env_name))
        status = "present" if present else "absent"
        print(f"attractor doctor: {provider} ({env_name}): {status}")

    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    dispatch = {
        "run": cmd_run,
        "doctor": cmd_doctor,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
