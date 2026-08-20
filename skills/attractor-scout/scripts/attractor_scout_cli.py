#!/usr/bin/env python3
"""attractor-scout CLI — the workhorse thin wrapper over `attractor_scout/`.

Every subcommand is a few lines of argument marshalling over a library call.
No detection logic lives here (SR3: one home for the logic), so the CLI and
the skill's importable path can never disagree about what a signal means.

    enumerate   walk the root, version-check metadata, list sessions
    qualify     E3 prompt-carrying selection (the only shipped selector)
    extract     per-session records + author prior -> extracts.jsonl
    detect      run the deterministic detectors over an extract
    rank        admission gate + score -> ranked JSON
    render      ranked JSON -> self-contained HTML (deterministic, no LLM)
    demo        brief | assemble -- the demonstration/teaching layer
    deck        brief | verify -- OPT-IN deck mode (an authored deck-grade page)
    run         the whole pipeline end to end
    census      event-name / tool-name census (Gap-1 allow-list finalization)

Exit codes: 0 ok · 2 fail-loud (empty corpus, schema mismatch, graph demanded
but unavailable, an invented count in a demo narrative, a red machine gate)
· 3 a deck-mode gate came back red (`deck verify`; its report goes to stdout).
A fail-loud condition NEVER exits 0 with a fabricated count, and neither a demo
nor a deck whose gates came back red is EVER published.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from attractor_scout import (
    author as author_mod,
)
from attractor_scout import (
    clustering,
    discover,
    extract,
    fit_cycle,
    fit_gate,
    fit_recovery,
    graph,
    honest_no,
    pipeline,
    provenance,
    ranking,
    render,
)
from attractor_scout import (
    leverage as leverage_mod,
)
from attractor_scout.errors import AttractorScoutError
from attractor_scout.naming import SKILL_NAME


def _emit(obj, out: str | None) -> None:
    text = json.dumps(obj, indent=1, ensure_ascii=False, default=str)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)


def cmd_enumerate(args) -> int:
    disc = discover.enumerate_sessions(args.root)
    _emit(
        {
            "ci_root": str(disc.root),
            "metadata_files": disc.metadata_files,
            "total_sessions": len(disc.sessions),
            "total_root_sessions": len(disc.roots),
            "total_workspaces": len(disc.workspaces),
            "own_data_scope": disc.scope.as_dict(),
            "workspaces": dict(sorted(disc.workspaces.items(), key=lambda kv: (-kv[1], kv[0]))),
        },
        args.out,
    )
    return 0


def cmd_qualify(args) -> int:
    disc = discover.enumerate_sessions(args.root)
    refs = discover.qualify(disc, selector=args.selector, top_n_workspaces=args.top_n_workspaces)
    _emit(
        {
            "ci_root": str(disc.root),
            "selector": args.selector,
            "scanned_root_sessions": len(disc.roots),
            "qualified": len(refs),
            "sessions": [{"workspace": r.workspace, "session_id": r.session_id} for r in refs],
        },
        args.out,
    )
    return 0


def cmd_extract(args) -> int:
    disc = discover.enumerate_sessions(args.root)
    refs = discover.qualify(disc, selector=args.selector, top_n_workspaces=args.top_n_workspaces)
    records = extract.extract_corpus(disc, refs)
    n = extract.write_extracts(records, args.out)
    mix = Counter(r.get("author") for r in records)
    print(f"extracted={n} -> {args.out}", file=sys.stderr)
    print(f"author prior mix: {dict(mix)}", file=sys.stderr)
    return 0


def cmd_detect(args) -> int:
    records = extract.read_extracts(args.extracts)
    if args.signal == "author":
        author_mod.classify_authors(records)
        _emit({"author_mix": dict(Counter(r["author"] for r in records))}, args.out)
        return 0
    if args.signal == "leverage":
        prof = leverage_mod.compute_leverage(records)
        _emit(prof.as_dict(), args.out)
        return 0
    if args.signal == "fit-gate":
        _emit(fit_gate.terminal_check_prevalence(records), args.out)
        return 0
    if args.signal == "fit-recovery":
        _emit(fit_recovery.detect(records).as_dict(), args.out)
        return 0
    if args.signal == "fit-cycle":
        structural = sum(1 for r in records if fit_cycle.detect(r).cycle)
        explicit = sum(1 for r in records if fit_cycle.detect_explicit_only(r).cycle)
        big = [r for r in records if int(r.get("n_tool_calls", 0) or 0) >= 6]
        big_hits = sum(1 for r in big if fit_cycle.detect(r).cycle)
        _emit(
            {
                "n_sessions": len(records),
                "structural_cycle": structural,
                "structural_rate": structural / max(len(records), 1),
                "explicit_only_cycle": explicit,
                "explicit_only_rate": explicit / max(len(records), 1),
                "implicit_to_explicit_ratio": (structural / explicit) if explicit else None,
                "n_sessions_ge6_tools": len(big),
                "structural_rate_ge6_tools": big_hits / max(len(big), 1),
            },
            args.out,
        )
        return 0
    if args.signal == "frequency":
        from attractor_scout import frequency_signature

        _emit({"clusters": frequency_signature.signature_clusters(records)}, args.out)
        return 0
    if args.signal == "classify-no":
        units = clustering.units_from_signatures(records)
        verdicts = []
        for unit in units:
            members = unit["members"]
            verdicts.append(
                honest_no.classify(
                    cycle=fit_cycle.cluster_cycle(members)["cycle"],
                    gate=fit_gate.cluster_gate(members)["gate"],
                    recovery=fit_recovery.detect(members).verdict,
                )
            )
        _emit(honest_no.summarize(verdicts), args.out)
        return 0
    raise ValueError(f"unknown signal: {args.signal!r}")


def cmd_rank(args) -> int:
    records = provenance.ensure_stamped(extract.read_extracts(args.extracts))
    if args.clusters:
        raw = json.loads(Path(args.clusters).read_text(encoding="utf-8"))
        clusters = raw.get("clusters", raw) if isinstance(raw, dict) else raw
        units, unknown = clustering.units_from_clusters(records, clusters)
        if unknown:
            # Deterministic re-verification (skill step 5): a member id the LLM
            # emitted that does not resolve against the extract is a broken
            # count, not a warning to bury. With --strict it is FATAL.
            preview = ", ".join(unknown[:10])
            print(
                f"RE-VERIFICATION: {len(unknown)} cluster member id(s) did not resolve against the extract: {preview}",
                file=sys.stderr,
            )
            if args.strict:
                raise AttractorScoutError(
                    f"re-verification failed: {len(unknown)} LLM-emitted member id(s) do not resolve "
                    f"against {args.extracts}. The ranking would rest on counts that cannot be verified. "
                    f"First unresolved: {preview}"
                )
    else:
        units = clustering.units_from_signatures(records)

    # THE MINING BOUNDARY (skill step 1's provenance pass, enforced at the
    # last moment before scoring): re-verification above has already checked
    # every member id, so narrowing membership to R4 human-presumed sessions
    # here cannot mask an invented count. Agent-authored and unattributable
    # sessions are counted in the provenance panel instead of ranked.
    gate = provenance.gate_units(units)
    result = ranking.rank(gate.admitted)
    result["provenance"] = provenance.summarize(records, gate=gate)
    _emit(result, args.out)
    return 0


def cmd_render(args) -> int:
    result = json.loads(Path(args.ranked).read_text(encoding="utf-8"))
    demos = None
    if args.demos:
        demos_path = Path(args.demos)
        if not demos_path.is_file():
            raise AttractorScoutError(f"--demos was given but {demos_path} does not exist")
        demos = json.loads(demos_path.read_text(encoding="utf-8"))
    path = render.write_report(result, args.out, generated_at=args.generated_at, demos=demos)
    print(f"wrote {path}", file=sys.stderr)
    return 0


def cmd_demo(args) -> int:
    """The demonstration layer: assemble a brief, or gate + publish a draft."""
    from attractor_scout import demo as demo_mod

    if args.action == "brief":
        slug, brief_path = demo_mod.build_brief(
            ranked_path=args.ranked,
            unit_id=args.unit,
            workdir=args.workdir,
            extracts_path=args.extracts,
        )
        print(f"brief -> {brief_path}", file=sys.stderr)
        # The slug is the ONLY thing on stdout: the skill captures it directly.
        print(slug)
        return 0

    if args.action == "assemble":
        stamp = args.generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry = demo_mod.assemble_demo(
            ranked_path=args.ranked,
            unit_id=args.unit,
            workdir=args.workdir,
            output_dir=args.output_dir,
            lint_cmd=args.lint_cmd,
            generated_at=stamp,
        )
        doc = demo_mod.write_demos(entry, args.out, append=args.append)
        print(
            f"published {entry['dot_relpath']} + {entry['companion_relpath']} "
            f"(verification: {entry['verification']['level']}); "
            f"{len(doc['demos'])} demo(s) in {args.out}",
            file=sys.stderr,
        )
        print(entry["verification"]["level"])
        return 0

    if args.action == "primer-only":
        demo_mod.write_demos(None, args.out, append=False)
        print(f"primer-only demos document -> {args.out}", file=sys.stderr)
        print("primer-only")
        return 0

    raise ValueError(f"unknown demo action: {args.action!r}")


def cmd_deck(args) -> int:
    """Deck mode (OPT-IN): assemble the deck brief, or gate a candidate deck."""
    from attractor_scout import deck as deck_mod

    if args.action == "brief":
        brief_path = deck_mod.build_deck_brief(
            ranked_path=args.ranked,
            demos_path=args.demos,
            workdir=args.workdir,
            run_label=args.run_label,
        )
        print(f"deck brief -> {brief_path}", file=sys.stderr)
        # The path is the ONLY thing on stdout: the skill captures it directly.
        print(brief_path)
        return 0

    if args.action == "verify":
        report = deck_mod.verify_deck(
            deck_path=args.deck,
            ranked_path=args.ranked,
            demos_path=args.demos,
        )
        text = report.render()
        print(text)
        if args.report:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(text + "\n", encoding="utf-8")
        if args.json_out:
            _emit(report.as_dict(), args.json_out)
        if not report.ok:
            failed = ", ".join(g.letter for g in report.gates if not g.passed)
            print(
                f"DECK GATE RED [{failed}]: {args.deck} was NOT published. Re-delegate ONCE with this "
                f"report appended verbatim; if it is still red, say so and do not publish.",
                file=sys.stderr,
            )
            return 3
        print(f"deck verified: {args.deck}", file=sys.stderr)
        return 0

    raise ValueError(f"unknown deck action: {args.action!r}")


def cmd_run(args) -> int:
    result = pipeline.run(
        root=args.root,
        mode=args.mode,
        server_url=args.server_url,
        clusters_path=args.clusters,
        extracts_path=args.extracts,
        selector=args.selector,
        render_to=args.render,
    )
    _emit(result.as_dict(), args.out)
    return 0


def cmd_census(args) -> int:
    """Tool-name census — the input to the Gap-1 `VERIFY_TOOLS` finalization."""
    records = extract.read_extracts(args.extracts)
    tools: Counter[str] = Counter()
    tails: Counter[str] = Counter()
    for rec in records:
        for tool in rec.get("tool_all") or rec.get("tool_seq") or []:
            tools[str(tool)] += 1
        for tool in rec.get("tool_tail") or []:
            tails[str(tool)] += 1
    _emit(
        {
            "n_sessions": len(records),
            "distinct_tools": len(tools),
            "tool_counts": dict(tools.most_common(args.top)),
            "terminal_window_tool_counts": dict(tails.most_common(args.top)),
            "current_verify_tools": sorted(fit_gate.VERIFY_TOOLS),
            "note": "Gap 1 / O4 open: finalize VERIFY_TOOLS from this census; finalization only ADDS recall.",
        },
        args.out,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=SKILL_NAME, description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--root", default=None, help="context-intelligence root (default: env or ~/.amplifier/projects)"
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    def add_selector(p):
        p.add_argument("--selector", default="prompt-carrying", choices=["prompt-carrying", "size-ranked"])
        p.add_argument("--top-n-workspaces", type=int, default=None, help="size-ranked control arm only")

    p = sub.add_parser("enumerate")
    p.add_argument("--out")
    p.set_defaults(func=cmd_enumerate)

    p = sub.add_parser("qualify")
    add_selector(p)
    p.add_argument("--out")
    p.set_defaults(func=cmd_qualify)

    p = sub.add_parser("extract")
    add_selector(p)
    p.add_argument("--out", default="extracts.jsonl")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("detect")
    p.add_argument(
        "signal",
        choices=["frequency", "leverage", "fit-cycle", "fit-gate", "fit-recovery", "classify-no", "author"],
    )
    p.add_argument("--extracts", default="extracts.jsonl")
    p.add_argument("--out")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("rank")
    p.add_argument("--extracts", default="extracts.jsonl")
    p.add_argument("--clusters", default=None, help="semantic clusters JSON from the fast label/cluster pass")
    p.add_argument(
        "--strict",
        action="store_true",
        help="re-verification is FATAL: exit 2 if any LLM-emitted member id does not resolve against the extract",
    )
    p.add_argument("--out")
    p.set_defaults(func=cmd_rank)

    p = sub.add_parser("render")
    p.add_argument("--ranked", default="ranked.json")
    p.add_argument("--out", default=None, help="default: ./<skill>-report.html")
    p.add_argument("--generated-at", default=None, help="pin the timestamp for byte-reproducible output")
    p.add_argument(
        "--demos",
        default=None,
        help="demos.json from `demo assemble`; omitted => byte-identical to the pre-demo artifact",
    )
    p.set_defaults(func=cmd_render)

    p = sub.add_parser(
        "demo",
        help="the demonstration/teaching layer: assemble a brief, or gate+publish a draft",
    )
    p.add_argument("action", choices=["brief", "assemble", "primer-only"])
    p.add_argument("--ranked", default="ranked.json")
    p.add_argument(
        "--unit",
        default=None,
        help="unit_id to demonstrate; default is opportunities[0], the top-ranked one",
    )
    p.add_argument("--workdir", default=None, help="brief: the demo parent dir; assemble: the slug dir")
    p.add_argument(
        "--extracts",
        default=None,
        help="brief: extracts.jsonl, so the gate-tool census is drawn from THEIR terminal windows",
    )
    p.add_argument("--output-dir", default=None, help="assemble: the directory the HTML map lives in")
    p.add_argument("--out", default=None, help="assemble: the demos.json to write")
    p.add_argument(
        "--lint-cmd",
        default=None,
        help="assemble: override the linter argv (rung 2 -- only ever after an explicit user yes)",
    )
    p.add_argument("--append", action="store_true", help="assemble: add to an existing demos.json")
    p.add_argument("--generated-at", default=None, help="pin the demo timestamp for reproducible output")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser(
        "deck",
        help="OPT-IN deck mode: assemble the deck brief, or run the deterministic gates over a candidate deck",
    )
    p.add_argument("action", choices=["brief", "verify"])
    p.add_argument("--ranked", default="ranked.json", help="the re-verified ranking (both actions)")
    p.add_argument("--demos", default=None, help="demos.json from `demo assemble`; carries the real .dot text")
    p.add_argument("--workdir", default=None, help="brief: where deck-brief.md is written")
    p.add_argument("--run-label", default=None, help="brief: a short human label for this run")
    p.add_argument("--deck", default=None, help="verify: the candidate deck HTML to gate")
    p.add_argument("--report", default=None, help="verify: also write the verbatim gate report here")
    p.add_argument("--json-out", default=None, help="verify: also write the machine-readable report here")
    p.set_defaults(func=cmd_deck)

    p = sub.add_parser("run")
    add_selector(p)
    p.add_argument("--mode", default=graph.MODE_AUTO, choices=list(graph.VALID_MODES))
    p.add_argument("--server-url", default=None)
    p.add_argument("--clusters", default=None)
    p.add_argument("--extracts", default=None, help="reuse an existing extracts.jsonl instead of re-mining")
    p.add_argument("--render", default=None, help="also write the HTML artifact here")
    p.add_argument("--out")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("census")
    p.add_argument("--extracts", default="extracts.jsonl")
    p.add_argument("--top", type=int, default=60)
    p.add_argument("--out")
    p.set_defaults(func=cmd_census)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AttractorScoutError as exc:
        print(f"FAIL-LOUD [{type(exc).__name__}]: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
