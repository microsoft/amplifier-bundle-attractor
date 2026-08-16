#!/usr/bin/env python3
"""Driver for the attractor guidance eval.

One scenario per trial. Each trial:

    launch DTU -> install the bundle the way a user does -> readiness + negative controls
      -> seed fixture (exemplar scenarios)
      -> drive the scenario
           session mode:  AIUser holds a real conversation with the installed session
           exemplar mode: the shipped objective runner runs against the fixture workspace
      -> extract the evidence out of the DTU
      -> mechanical checks (re-run outside the thing that produced the artifacts)
      -> blind grade against rubric.md's anchored criteria
      -> write results OUTSIDE the repo
      -> destroy the DTU

Built on the `amplifier_evaluation` library blocks (AIUser / Extractor / Grader / DTU /
compose_launch_profile / install_agent / load_agent), following the custom-trial-loop precedent
rather than calling `run_trial` directly: this eval needs a per-scenario persona and a
mechanical-checks stage, neither of which the stock loop passes through.

Invoked by run.sh, which resolves the Gitea mirror and pins the ref under test.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import shutil
import sys
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# The amplifier_evaluation library supplies every moving part that touches a model or a container.
# It is imported softly so that `--list` -- pure inspection of the instrument -- works in a plain
# checkout with nothing installed. Any path that actually runs a trial calls `require_library()`
# first and fails with the install instructions rather than an ImportError traceback.
_LIBRARY_IMPORT_ERROR: str | None = None
try:
    from amplifier_evaluation.ai_user import AIUser
    from amplifier_evaluation.extractor import Extractor
    from amplifier_evaluation.grader import Grader
    from amplifier_evaluation.harness.dtu import DTU, cli_available
    from amplifier_evaluation.harness.install import (
        compose_launch_profile,
        install_agent,
        verify_env,
    )
    from amplifier_evaluation.harness.loaders import load_agent
except ImportError as _exc:  # pragma: no cover - exercised only in a bare checkout
    _LIBRARY_IMPORT_ERROR = str(_exc)


def require_library() -> None:
    if _LIBRARY_IMPORT_ERROR is None:
        return
    raise SystemExit(
        f"amplifier_evaluation is not importable ({_LIBRARY_IMPORT_ERROR}).\n"
        "Clone microsoft/amplifier-bundle-evaluation, run `uv sync` there, and activate its "
        ".venv -- or set AMPLIFIER_EVALUATION_ROOT and let run.sh find it.\n"
        "(`--list` works without it; running trials does not.)"
    )

HERE = Path(__file__).resolve().parent
EVAL_ROOT = HERE.parent                      # evals/guidance/
REPO_ROOT = EVAL_ROOT.parent.parent          # the bundle checkout
SCENARIOS_DIR = EVAL_ROOT / "scenarios"
RUBRIC_PATH = EVAL_ROOT / "rubric.md"

log = logging.getLogger("guidance-eval")


# --------------------------------------------------------------------------- results location


def resolve_results_root(explicit: str | None) -> Path:
    """Where run outputs land. Always OUTSIDE the repository.

    Run directories carry full prompts, transcripts, and provider-adjacent material. None of it
    belongs in a source tree that gets pushed, so this function refuses to return a path inside
    the checkout rather than trusting every future caller to remember.

    Resolution order: --results-root, $GUIDANCE_EVAL_RESULTS_ROOT, then the workspace-root
    default (`<workspace>/.amplifier/evaluation/guidance-pilot`).
    """
    if explicit:
        root = Path(explicit).expanduser().resolve()
    elif os.environ.get("GUIDANCE_EVAL_RESULTS_ROOT"):
        root = Path(os.environ["GUIDANCE_EVAL_RESULTS_ROOT"]).expanduser().resolve()
    else:
        root = _workspace_root() / ".amplifier" / "evaluation" / "guidance-pilot"

    repo = REPO_ROOT.resolve()
    if root == repo or repo in root.parents:
        raise SystemExit(
            f"REFUSING to write results inside the repository.\n"
            f"  results root: {root}\n"
            f"  repo root:    {repo}\n"
            f"Run evidence is not source. Pass --results-root, or set "
            f"GUIDANCE_EVAL_RESULTS_ROOT, to a path outside the checkout."
        )
    return root


def _workspace_root() -> Path:
    """The directory the bundle checkout sits in.

    Normally that is simply `REPO_ROOT.parent`. When the harness runs from a git worktree parked
    under `<workspace>/.amplifier/worktrees/<name>`, walking up one level lands inside `.amplifier`
    and the default results path would nest results inside the worktree scaffolding. Climb out of
    any `.amplifier` segment so both layouts resolve to the same real workspace.
    """
    parent = REPO_ROOT.resolve().parent
    for ancestor in [parent, *parent.parents]:
        if ancestor.name == ".amplifier":
            return ancestor.parent
    return parent


# --------------------------------------------------------------------------- rubric + scenarios


@dataclass
class Criterion:
    id: str
    name: str
    points: int
    anchor: str
    anchor_quote: str
    description: str


_YAML_BLOCK = re.compile(r"^```yaml\s*$(.*?)^```\s*$", re.MULTILINE | re.DOTALL)


def load_criteria(rubric_path: Path = RUBRIC_PATH) -> dict[str, Criterion]:
    """Parse the criterion blocks out of rubric.md.

    rubric.md is the single home for the criteria: the prose that argues for a criterion and the
    machine-readable block the grader receives live in the same document, so they cannot drift
    apart the way a doc and its YAML twin always eventually do.
    """
    text = rubric_path.read_text(encoding="utf-8")
    out: dict[str, Criterion] = {}
    for match in _YAML_BLOCK.finditer(text):
        block = yaml.safe_load(match.group(1))
        if not isinstance(block, dict) or "id" not in block:
            continue
        try:
            crit = Criterion(
                id=str(block["id"]),
                name=str(block["name"]),
                points=int(block["points"]),
                anchor=str(block["anchor"]),
                anchor_quote=str(block["anchor_quote"]).strip(),
                description=str(block["description"]).strip(),
            )
        except KeyError as exc:
            raise SystemExit(f"{rubric_path}: criterion block missing field {exc}") from exc
        if not crit.anchor.strip():
            raise SystemExit(
                f"{rubric_path}: criterion {crit.id} has an empty `anchor`. Every criterion "
                f"cites a canonical-spec section or a docs/VISION.md passage; an unanchored "
                f"criterion measures the rubric author's taste."
            )
        out[crit.id] = crit
    if not out:
        raise SystemExit(f"{rubric_path}: no criterion blocks parsed")
    return out


@dataclass
class Scenario:
    id: str
    path: Path
    raw: dict[str, Any]

    @property
    def mode(self) -> str:
        return str(self.raw.get("mode", "session"))

    @property
    def title(self) -> str:
        return str(self.raw.get("title", self.id))

    @property
    def timeout_s(self) -> int:
        return int(self.raw.get("timeout_s", 1800))

    @property
    def criteria_ids(self) -> list[str]:
        return list(self.raw.get("pass_bar", {}).get("criteria", []))

    @property
    def machine_checks(self) -> list[dict[str, Any]]:
        return list(self.raw.get("pass_bar", {}).get("machine_checks", []))

    @property
    def pass_summary(self) -> str:
        return str(self.raw.get("pass_bar", {}).get("summary", "")).strip()


def load_scenario(scenario_id: str) -> Scenario:
    matches = sorted(SCENARIOS_DIR.glob(f"{scenario_id}.y*ml"))
    if not matches:
        available = ", ".join(sorted(p.stem for p in SCENARIOS_DIR.glob("*.y*ml")))
        raise SystemExit(f"unknown scenario {scenario_id!r}. Available: {available}")
    raw = yaml.safe_load(matches[0].read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit(f"{matches[0]}: did not parse to a mapping")
    return Scenario(id=str(raw.get("id", scenario_id)), path=matches[0], raw=raw)


def validate_scenarios(scenarios: list[Scenario], criteria: dict[str, Criterion]) -> None:
    """Fail before spending a cent if a scenario cites a criterion that does not exist."""
    problems: list[str] = []
    for sc in scenarios:
        if not sc.criteria_ids:
            problems.append(f"{sc.id}: pass_bar.criteria is empty")
        for cid in sc.criteria_ids:
            if cid not in criteria:
                problems.append(f"{sc.id}: cites unknown criterion {cid!r}")
        if sc.mode not in ("session", "exemplar"):
            problems.append(f"{sc.id}: unknown mode {sc.mode!r}")
        if sc.mode == "session" and not sc.raw.get("opening_ask"):
            problems.append(f"{sc.id}: session scenario has no opening_ask")
        if sc.mode == "exemplar" and not sc.raw.get("objective"):
            problems.append(f"{sc.id}: exemplar scenario has no objective")
    if problems:
        raise SystemExit("scenario validation failed:\n  " + "\n  ".join(problems))


# --------------------------------------------------------------------------- mechanical checks


@dataclass
class CheckResult:
    id: str
    kind: str
    passed: bool
    detail: str
    why: str = ""


#: Blocks the transcript renderer emits for material the user NEVER SAW -- the model's private
#: reasoning and the raw tool traffic. Each is delimited by a start marker and its matching close
#: so a scoped check can excise exactly the block and keep the visible prose on either side.
_INVISIBLE_BLOCKS: tuple[tuple[str, str], ...] = (
    ("[thinking]", "[/thinking]"),
    ("[tool_use:", "[/tool_use]"),
    ("[tool_result]", "[/tool_result]"),
)

_ROLE_HEADING_RE = re.compile(r"^## (.+)$", re.MULTILINE)


def _strip_invisible(body: str) -> str:
    """Drop the renderer's non-visible blocks, keeping the prose around them.

    Fail-closed by construction: an UNTERMINATED block is kept verbatim. Dropping text that a
    `lacks_all` check searches is the direction that quietly turns a FAIL into a PASS, so it only
    ever happens on a well-formed start/close pair.
    """
    out = body
    for open_tag, close_tag in _INVISIBLE_BLOCKS:
        kept: list[str] = []
        rest = out
        while True:
            start = rest.find(open_tag)
            if start == -1:
                kept.append(rest)
                break
            end = rest.find(close_tag, start)
            if end == -1:
                kept.append(rest)  # unterminated -- keep it, fail closed
                break
            kept.append(rest[:start])
            rest = rest[end + len(close_tag) :]
        out = "".join(kept)
    return out


def assistant_answer_text(transcript: str) -> str:
    """What the session actually SAID TO THE USER -- and nothing else.

    A `lacks_all` check asks whether the session *told the user* to do something. The rendered
    transcript is wider than that question: it also carries the user's own turns (in `qa-02` the
    persona is instructed to propose the anti-pattern out loud) and the assistant's `[thinking]`,
    where a model reasoning its way to a REFUSAL naturally restates the thing it is about to
    refuse. Neither is advice, and scoring either as advice measures the wrong artifact.

    Returns the visible prose of the `assistant` turns. If the transcript carries no assistant
    turns at all -- an unrecovered session falls back to a reconstruction with no role headings --
    the whole transcript is returned rather than an empty string, so a check can never pass
    vacuously because parsing found nothing.
    """
    parts = _ROLE_HEADING_RE.split(transcript)
    answers = [
        _strip_invisible(parts[i + 1])
        for i in range(1, len(parts) - 1, 2)
        if parts[i].strip().lower() == "assistant"
    ]
    if not answers:
        return transcript
    return "\n".join(answers)


class MechanicalChecker:
    """Runs a scenario's `machine_checks` against the DTU and the captured transcript.

    Every check re-derives its answer from an artifact, in the DTU or on disk, AFTER the run that
    produced it has finished. Nothing here asks the session, or the pipeline, how it thinks it did.
    """

    WORKSPACE = "/workspace"

    def __init__(self, dtu: DTU, transcript_text: str) -> None:
        self.dtu = dtu
        self.transcript = transcript_text
        self.transcript_lower = transcript_text.lower()
        self.assistant_answer_lower = assistant_answer_text(transcript_text).lower()

    async def _read(self, rel_path: str) -> tuple[bool, str]:
        res = await self.dtu.exec_cmd(
            ["bash", "-lc", f"cat {self.WORKSPACE}/{rel_path} 2>/dev/null"], timeout_s=60
        )
        return res.returncode == 0 and res.stdout.strip() != "", res.stdout

    async def _read_json(self, rel_path: str) -> tuple[bool, Any, str]:
        ok, text = await self._read(rel_path)
        if not ok:
            return False, None, f"{rel_path} missing or empty"
        try:
            return True, json.loads(text), ""
        except json.JSONDecodeError as exc:
            return False, None, f"{rel_path} is not valid JSON: {exc}"

    async def run(self, checks: list[dict[str, Any]]) -> list[CheckResult]:
        results: list[CheckResult] = []
        for spec in checks:
            try:
                results.append(await self._run_one(spec))
            except Exception as exc:  # noqa: BLE001 - a broken check is a failed check, loudly
                results.append(
                    CheckResult(
                        id=str(spec.get("id", "?")),
                        kind=str(spec.get("kind", "?")),
                        passed=False,
                        detail=f"check raised: {exc!r}",
                        why=str(spec.get("why", "")).strip(),
                    )
                )
        return results

    async def _run_one(self, spec: dict[str, Any]) -> CheckResult:
        cid = str(spec.get("id", "?"))
        kind = str(spec["kind"])
        why = str(spec.get("why", "")).strip()

        def result(passed: bool, detail: str) -> CheckResult:
            return CheckResult(id=cid, kind=kind, passed=passed, detail=detail, why=why)

        if kind == "transcript_contains_any":
            wanted = [str(s) for s in spec["any_of"]]
            hits = [w for w in wanted if w.lower() in self.transcript_lower]
            return result(bool(hits), f"matched {hits}" if hits else f"none of {wanted} appeared")

        if kind == "transcript_lacks_all":
            banned = [str(s) for s in spec["none_of"]]
            hits = [b for b in banned if b.lower() in self.transcript_lower]
            return result(not hits, f"FOUND banned phrasing {hits}" if hits else "clean")

        if kind == "assistant_answer_lacks_all":
            # Same literal test as `transcript_lacks_all`, over what the session actually SAID TO
            # THE USER: assistant turns, minus `[thinking]` and the raw tool traffic. Use this
            # when the banned phrasing is something the USER may legitimately say (a scenario that
            # instructs its persona to propose the anti-pattern) or that the model may legitimately
            # restate while reasoning its way to refusing it.
            banned = [str(s) for s in spec["none_of"]]
            hits = [b for b in banned if b.lower() in self.assistant_answer_lower]
            return result(
                not hits,
                f"FOUND banned phrasing {hits} in the assistant's answer"
                if hits
                else "clean (assistant answer text only)",
            )

        if kind == "file_exists":
            ok, _ = await self._read(str(spec["path"]))
            return result(ok, f"{spec['path']} " + ("present" if ok else "missing or empty"))

        if kind == "file_nonempty":
            ok, text = await self._read(str(spec["path"]))
            return result(ok, f"{len(text.strip())} bytes" if ok else "missing or empty")

        if kind == "file_equals":
            ok, text = await self._read(str(spec["path"]))
            got = text.strip()
            want = str(spec["value"]).strip()
            return result(ok and got == want, f"got {got!r}, wanted {want!r}")

        if kind == "file_not_equals":
            ok, text = await self._read(str(spec["path"]))
            got = text.strip()
            banned = str(spec["not_value"]).strip()
            return result(ok and got != banned, f"got {got!r}, must not be {banned!r}")

        if kind == "file_contains_any":
            ok, text = await self._read(str(spec["path"]))
            low = text.lower()
            wanted = [str(s) for s in spec["any_of"]]
            hits = [w for w in wanted if w.lower() in low]
            return result(ok and bool(hits), f"matched {hits}" if hits else f"none of {wanted}")

        if kind == "json_field_in":
            ok, data, err = await self._read_json(str(spec["path"]))
            if not ok:
                return result(False, err)
            got = data.get(spec["field"]) if isinstance(data, dict) else None
            allowed = [str(v) for v in spec["one_of"]]
            return result(str(got) in allowed, f"{spec['field']}={got!r}, allowed {allowed}")

        if kind == "json_field_equals":
            ok, data, err = await self._read_json(str(spec["path"]))
            if not ok:
                return result(False, err)
            got = data.get(spec["field"]) if isinstance(data, dict) else None
            want = str(spec["value"])
            return result(str(got) == want, f"{spec['field']}={got!r}, wanted {want!r}")

        if kind == "json_field_not":
            ok, data, err = await self._read_json(str(spec["path"]))
            if not ok:
                return result(False, err)
            got = data.get(spec["field"]) if isinstance(data, dict) else None
            banned = str(spec["not_value"])
            return result(str(got) != banned, f"{spec['field']}={got!r}, must not be {banned!r}")

        if kind == "command_exit_zero":
            cwd = self.WORKSPACE if spec.get("cwd_is_workspace") else "/opt/attractor-src"
            cmd = str(spec["command"])
            # Scenario commands cite repo-relative paths (examples/objective/...); resolve them
            # against the pinned checkout while running from the workspace, so a gate script sees
            # the same cwd the pipeline did.
            cmd = cmd.replace("examples/objective/", "/opt/attractor-src/examples/objective/")
            res = await self.dtu.exec_cmd(
                ["bash", "-lc", f"cd {cwd} && {cmd}"], timeout_s=300
            )
            tail = (res.stdout + res.stderr).strip()[-400:]
            return result(res.returncode == 0, f"exit {res.returncode}: {tail}")

        return result(False, f"unknown check kind {kind!r}")


# --------------------------------------------------------------------------- grading


GRADER_STEPS = """\
1. Read EVERY file under `/eval/graded/` in the Digital Twin Universe. That directory is the
   complete, normalized evidence for this trial:
     - `transcript.md`      the session or run under evaluation, in full
     - `scenario.md`        what the user was trying to do, and the bar this scenario sets
     - `artifacts/`         any files the run produced (may be empty; that is often correct)
     - `mechanical.json`    checks already re-run by the harness against the artifacts

   Read them with `amplifier-digital-twin exec <dtu_id> -- cat /eval/graded/<file>`.

2. Ground every score in what the transcript ACTUALLY says. Quote the sentence you are scoring
   on. A criterion scored without a quote is a guess.

3. Judge against the criterion's own `ANCHOR QUOTE`, which is verbatim normative text from this
   project's specification or its vision document. You are scoring whether the session taught
   what the anchor says — not whether it matched your own preferences about pipelines, and not
   whether it was pleasant or thorough.

4. Do NOT reward compliance. In several of these scenarios the correct behavior is refusing the
   user's request and explaining why. A session that cheerfully did what it was asked may deserve
   a LOW score, and a session that pushed back may deserve a high one. Read the scenario's pass
   bar before deciding which situation you are in.

5. Do NOT modify anything. Do not run the pipeline, do not rerun checks, do not fix files.
"""


def build_grader_yaml(
    scenario: Scenario, criteria: dict[str, Criterion], out_path: Path
) -> Path:
    """Render this scenario's cited criteria into a grader.yaml the library can run."""
    rubric: dict[str, Any] = {}
    for cid in scenario.criteria_ids:
        crit = criteria[cid]
        rubric[cid] = {
            "points": crit.points,
            "description": (
                f"{crit.name}\n\n"
                f"ANCHOR: {crit.anchor}\n"
                f"ANCHOR QUOTE (verbatim normative text — grade against this):\n"
                f"{crit.anchor_quote}\n\n"
                f"WHAT TO JUDGE:\n{crit.description}"
            ),
        }

    doc = {
        "evaluations": [
            {
                "name": f"guidance-{scenario.id}",
                "weight": 1.0,
                "steps": GRADER_STEPS,
                "rubric": rubric,
                "mounts": [{"source": "graded", "destination": "/eval/graded"}],
            }
        ]
    }
    out_path.write_text(yaml.safe_dump(doc, sort_keys=False, width=100), encoding="utf-8")
    return out_path


def scenario_context(scenario: Scenario) -> str:
    """The task context handed to the extractor and the grader."""
    parts = [f"# Scenario: {scenario.title}", f"class: {scenario.raw.get('class')}", ""]
    if scenario.mode == "session":
        parts += [
            (
                "A user held a conversation with an Amplifier session that has the attractor "
                "bundle installed. The user's opening message was:"
            ),
            "",
            str(scenario.raw.get("opening_ask", "")).strip(),
            "",
            "The user then pushed back or asked follow-ups according to their own agenda.",
        ]
    else:
        parts += [
            (
                "The shipped objective-runner pipeline was run against a workspace with this "
                "objective, stated exactly as a user stated it:"
            ),
            "",
            str(scenario.raw.get("objective", "")).strip(),
        ]
    parts += ["", "## The bar this scenario sets", "", scenario.pass_summary]
    return "\n".join(parts)


# --------------------------------------------------------------------------- the trial


@dataclass
class TrialOutcome:
    scenario_id: str
    status: str = "pending"
    dtu_id: str | None = None
    checks: list[CheckResult] = field(default_factory=list)
    scores: dict[str, int] = field(default_factory=dict)
    reasoning: dict[str, str] = field(default_factory=dict)
    verdict: str = "UNKNOWN"
    failure: str | None = None
    notes: list[str] = field(default_factory=list)


async def run_trial(
    scenario: Scenario,
    criteria: dict[str, Criterion],
    cfg: dict[str, Any],
    args: argparse.Namespace,
    run_dir: Path,
    ai_user: AIUser | None,
    extractor: Extractor,
    grader: Grader,
) -> TrialOutcome:
    out = TrialOutcome(scenario_id=scenario.id)
    trial_dir = run_dir / scenario.id
    trial_dir.mkdir(parents=True, exist_ok=True)
    graded_dir = trial_dir / "graded"
    (graded_dir / "artifacts").mkdir(parents=True, exist_ok=True)

    agent = load_agent(HERE / "agents" / "attractor-user-install")
    missing = verify_env(agent)
    if missing:
        raise SystemExit(f"missing required host env: {missing}")

    profile_out = trial_dir / "launch-profile.yaml"
    compose_launch_profile(agent, HERE / "profiles" / "guidance-dtu.yaml", profile_out)

    dtu_name = f"{cfg['dtu_name_prefix']}-{scenario.id[:18]}-{uuid.uuid4().hex[:6]}"
    log.info("[%s] launching DTU %s", scenario.id, dtu_name)
    dtu = await DTU.launch(
        profile_out,
        name=dtu_name,
        variables={
            "GITEA_URL": args.gitea_url,
            "GITEA_TOKEN": args.gitea_token,
            "BUNDLE_REPO": args.bundle_repo,
            "BUNDLE_BRANCH": args.bundle_branch,
        },
        launch_timeout_s=1200,
    )
    out.dtu_id = dtu.id
    keep_dtu = False

    try:
        # ---- install -------------------------------------------------------------
        log.info("[%s] installing the bundle (real-user path)", scenario.id)
        out.status = "installing"
        await install_agent(agent, dtu, log_to=trial_dir / "install.log", step_timeout_s=2400)

        # ---- readiness / negative controls ---------------------------------------
        # These run BEFORE any model is paid. A trial that grades a broken environment is worse
        # than a trial that never ran: it produces a number.
        await assert_readiness(dtu, scenario, trial_dir)

        # ---- seed ----------------------------------------------------------------
        fixture = scenario.raw.get("fixture")
        if fixture:
            src = HERE / "fixtures" / str(fixture)
            if not src.is_dir():
                raise RuntimeError(f"fixture {fixture!r} not found at {src}")
            log.info("[%s] seeding fixture %s", scenario.id, fixture)
            await dtu.file_push(src, "/workspace")
            await dtu.exec_cmd(
                ["bash", "-lc", "cd /workspace && ls -la && git init -q . 2>/dev/null; true"],
                timeout_s=120,
            )

        # ---- drive ---------------------------------------------------------------
        out.status = "running"
        if scenario.mode == "session":
            transcript_text = await drive_session(
                scenario, dtu, agent, ai_user, trial_dir, out
            )
        else:
            transcript_text = await drive_exemplar(scenario, dtu, args, trial_dir, out)

        (graded_dir / "transcript.md").write_text(transcript_text, encoding="utf-8")
        (graded_dir / "scenario.md").write_text(scenario_context(scenario), encoding="utf-8")

        # ---- extract -------------------------------------------------------------
        out.status = "extracting"
        await pull_artifacts(scenario, dtu, graded_dir / "artifacts", trial_dir, out)

        # ---- mechanical ----------------------------------------------------------
        out.status = "checking"
        checker = MechanicalChecker(dtu, transcript_text)
        out.checks = await checker.run(scenario.machine_checks)
        (graded_dir / "mechanical.json").write_text(
            json.dumps(
                [
                    {"id": c.id, "kind": c.kind, "passed": c.passed, "detail": c.detail}
                    for c in out.checks
                ],
                indent=2,
            ),
            encoding="utf-8",
        )
        for c in out.checks:
            log.info("[%s] check %s (%s): %s — %s", scenario.id, c.id, c.kind,
                     "PASS" if c.passed else "FAIL", c.detail)

        # ---- grade ---------------------------------------------------------------
        out.status = "grading"
        grader_yaml = build_grader_yaml(scenario, criteria, trial_dir / "grader.yaml")
        grader_result = await grader.run(
            grader_yaml_path=grader_yaml,
            task_context=scenario_context(scenario),
            dtu_id=dtu.id,
            output_dir=trial_dir / "grader",
            grader_data_dir=trial_dir,
        )
        for ev in grader_result.evaluations:
            for name, score in (ev.rubric_scores or {}).items():
                out.scores[name] = int(getattr(score, "points_awarded", 0))
                out.reasoning[name] = str(getattr(score, "reasoning", ""))

        out.verdict, out.notes = decide(scenario, criteria, cfg, out)
        out.status = "completed"

    except Exception as exc:  # noqa: BLE001 - any trial failure must still write an outcome
        out.status = "failed"
        out.failure = f"{type(exc).__name__}: {exc}"
        out.verdict = "ERROR"
        (trial_dir / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        log.error("[%s] trial failed: %s", scenario.id, out.failure)
        keep_dtu = args.keep_dtus_on_failure
    finally:
        if keep_dtu or args.keep_dtus:
            log.warning(
                "[%s] KEEPING DTU %s for post-mortem — destroy it with "
                "`amplifier-digital-twin destroy %s`", scenario.id, dtu.id, dtu.id
            )
        else:
            log.info("[%s] destroying DTU %s", scenario.id, dtu.id)
            try:
                await dtu.destroy()
            except Exception as exc:  # noqa: BLE001 - a destroy failure must not mask the result
                log.error("[%s] DTU destroy failed (%s) — destroy %s by hand",
                          scenario.id, exc, dtu.id)

    (trial_dir / "outcome.json").write_text(
        json.dumps(
            {
                "scenario": out.scenario_id,
                "status": out.status,
                "verdict": out.verdict,
                "dtu_id": out.dtu_id,
                "scores": out.scores,
                "reasoning": out.reasoning,
                "checks": [
                    {"id": c.id, "kind": c.kind, "passed": c.passed, "detail": c.detail,
                     "why": c.why}
                    for c in out.checks
                ],
                "notes": out.notes,
                "failure": out.failure,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return out


async def assert_readiness(dtu: DTU, scenario: Scenario, trial_dir: Path) -> None:
    """Prove the environment before spending model budget on it."""
    gates: list[tuple[str, str]] = [
        ("amplifier-on-path", "amplifier --version"),
        ("bundle-active", "amplifier bundle current | grep -q 'Active bundle: attractor'"),
        ("attractor-cli", "attractor lint --help >/dev/null"),
        ("checkout-present", "test -f /opt/attractor-src/examples/objective/objective-runner.dot"),
        (
            # Negative control for the exemplar path: the shipped runner must parse and lint
            # clean before any scenario is allowed to blame it for a bad result.
            "objective-runner-lints",
            "attractor lint /opt/attractor-src/examples/objective/objective-runner.dot",
        ),
    ]
    if scenario.raw.get("fixture") == "notesvc":
        gates.append(
            # The fixture's redness IS the machine evidence scenario (e) expects the intake to
            # find. If it is green on arrival, the scenario is meaningless and must not run.
            (
                "fixture-is-red",
                (
                    "cd /workspace && pytest -q >/tmp/fixture-pre.txt 2>&1; "
                    "test $? -ne 0 || { echo 'fixture is GREEN on arrival'; exit 1; }"
                ),
            )
        )

    lines = []
    for name, cmd in gates:
        res = await dtu.exec_cmd(["bash", "-lc", f'export PATH="/root/.local/bin:$PATH"; {cmd}'],
                                 timeout_s=300)
        ok = res.returncode == 0
        lines.append(f"[{'OK ' if ok else 'FAIL'}] {name}: exit {res.returncode}\n"
                     f"{(res.stdout + res.stderr).strip()[:800]}\n")
        if not ok:
            (trial_dir / "readiness.txt").write_text("\n".join(lines), encoding="utf-8")
            raise RuntimeError(
                f"readiness gate {name!r} failed (exit {res.returncode}). "
                f"See {trial_dir / 'readiness.txt'}. Aborting before any model spend."
            )
    (trial_dir / "readiness.txt").write_text("\n".join(lines), encoding="utf-8")


async def drive_session(
    scenario: Scenario,
    dtu: DTU,
    agent: Any,
    ai_user: AIUser | None,
    trial_dir: Path,
    out: TrialOutcome,
) -> str:
    """Let the AI user hold the conversation, then recover the real transcript from the DTU."""
    assert ai_user is not None
    script = "\n\n".join(
        [
            "Your opening message to the agent, sent verbatim as your first turn:",
            "---",
            str(scenario.raw["opening_ask"]).strip(),
            "---",
            "After that, follow these rules of engagement:",
            str(scenario.raw.get("follow_up", "")).strip(),
        ]
    )
    log.info("[%s] driving the session (timeout %ss)", scenario.id, scenario.timeout_s)
    result = await asyncio.wait_for(
        ai_user.run(
            scenario=script,
            dtu_id=dtu.id,
            invocation_guide=agent.invocation_md,
            persona=str(scenario.raw.get("persona", "")).strip() or None,
            workspace_dir="/workspace",
        ),
        timeout=scenario.timeout_s + 600,
    )
    (trial_dir / "ai_user.json").write_text(
        json.dumps(
            {
                "elapsed_s": result.elapsed_s,
                "conclude": (
                    {
                        "verdict": getattr(result.conclude, "verdict", None),
                        "summary": getattr(result.conclude, "summary", None),
                    }
                    if result.conclude
                    else None
                ),
                "final_assistant_text": result.final_assistant_text,
            },
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    if result.conclude is None:
        out.notes.append(
            "AI user never called conclude — it ran out of iterations or hit an error. "
            "The transcript below may be partial."
        )

    transcript = await recover_transcript(dtu, trial_dir)
    if not transcript.strip():
        # The AI user's own record is a strictly worse artifact (it is a summary of the thing we
        # wanted), so falling back to it is recorded loudly rather than silently.
        out.notes.append(
            "COULD NOT RECOVER the in-DTU session transcript; graded on the AI user's own "
            "record instead. Treat this trial's grade as provisional."
        )
        transcript = (
            "# WARNING: reconstructed from the AI user's record, not the session transcript\n\n"
            f"{result.final_assistant_text}\n"
        )
    return transcript


# Self-contained: no argv, no interpolation, no shell quoting to get wrong. It globs, picks the
# newest transcript, and renders it. Kept as a module constant so it is obvious that nothing is
# ever formatted into it.
_TRANSCRIPT_RENDER_SCRIPT = r"""
python3 <<'PYEOF'
import json
import pathlib

found = []
for root in (pathlib.Path("/root/.amplifier"), pathlib.Path("/root/.config/amplifier")):
    if root.is_dir():
        found.extend(root.rglob("transcript.jsonl"))

if not found:
    print("NO-TRANSCRIPT-FOUND")
    raise SystemExit(0)

best = max(found, key=lambda f: f.stat().st_mtime)
print("# Session transcript")
print()
print("source: %s" % best)


def render(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    chunks = []
    for c in content:
        if isinstance(c, str):
            chunks.append(c)
        elif isinstance(c, dict):
            kind = c.get("type")
            if kind == "text" and c.get("text"):
                chunks.append(c["text"])
            elif kind == "thinking" and c.get("thinking"):
                chunks.append("[thinking] " + str(c["thinking"])[:2000] + "\n[/thinking]")
            elif kind == "tool_use":
                chunks.append(
                    "[tool_use: %s] %s\n[/tool_use]"
                    % (c.get("name"), json.dumps(c.get("input"))[:600])
                )
            elif kind == "tool_result":
                chunks.append("[tool_result] " + json.dumps(c.get("content"))[:800] + "\n[/tool_result]")
    return "\n".join(chunks)


for line in best.read_text(encoding="utf-8", errors="replace").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        rec = json.loads(line)
    except json.JSONDecodeError:
        continue
    role = rec.get("role") or rec.get("type") or "?"
    body = render(rec.get("content"))
    if not body.strip():
        continue
    print()
    print()
    print("## %s" % role)
    print()
    print(body.strip())
PYEOF
""".strip()


async def recover_transcript(dtu: DTU, trial_dir: Path) -> str:
    """Render the session's own transcript.jsonl into readable markdown.

    Deterministic, in-DTU, and independent of any model: the artifact under grading is the
    conversation itself, and asking a model to summarize it first would destroy the evidence and
    then grade the destruction.

    The renderer takes NO arguments and globs for itself. An earlier version passed the found
    paths as argv AFTER a heredoc terminator, which the shell read as a separate command line:
    `$@` was empty, the script printed nothing, the harness fell back to the AI user's
    three-sentence summary, and the grader produced entirely plausible scores from it. Removing
    the argv removes the class.
    """
    script = _TRANSCRIPT_RENDER_SCRIPT
    res = await dtu.exec_cmd(["bash", "-lc", script], timeout_s=300)
    text = res.stdout

    # Always record what the DTU actually held, so a recovery failure stays diagnosable: the DTU
    # is destroyed by the time anyone reads the results.
    listing = await dtu.exec_cmd(
        [
            "bash",
            "-lc",
            (
                "find /root/.amplifier -maxdepth 6 "
                "\\( -name transcript.jsonl -o -name events.jsonl \\) 2>/dev/null | head -30"
            ),
        ],
        timeout_s=120,
    )
    (trial_dir / "transcript-source-paths.txt").write_text(
        f"render exit: {res.returncode}\n"
        f"render stderr:\n{res.stderr[:2000]}\n\n"
        f"session files present in the DTU:\n{listing.stdout}",
        encoding="utf-8",
    )

    if "NO-TRANSCRIPT-FOUND" in text or not text.strip():
        return ""
    return text


async def drive_exemplar(
    scenario: Scenario, dtu: DTU, args: argparse.Namespace, trial_dir: Path, out: TrialOutcome
) -> str:
    """Run the shipped objective runner against the fixture workspace."""
    objective = str(scenario.raw["objective"]).strip()
    runner_rel = str(scenario.raw.get("runner", "examples/objective/objective-runner.dot"))
    runner_dir = f"/opt/attractor-src/{Path(runner_rel).parent}"
    extra = "".join(
        f' --param {k}="{v}"' for k, v in (scenario.raw.get("runner_params") or {}).items()
    )

    # The objective is written to a file rather than interpolated into the command line: it is
    # deliberately sloppy user prose, and shell-quoting it would be a source of eval-side
    # corruption in exactly the input the scenario exists to test.
    await dtu.exec_cmd(
        ["bash", "-lc", "mkdir -p /opt/eval && cat > /opt/eval/objective.txt <<'OBJEOF'\n"
                        + objective + "\nOBJEOF"],
        timeout_s=60,
    )

    cmd = (
        'export PATH="/root/.local/bin:$PATH"; cd /workspace && '
        f'attractor run /opt/attractor-src/{runner_rel} '
        f'--param goal="$(cat /opt/eval/objective.txt)" '
        f'--param runner_dir="{runner_dir}" '
        f'--param target_dir="$PWD" '
        f'{extra} --cwd . --on-human-gate auto-approve 2>&1'
    )
    log.info("[%s] running the objective runner (timeout %ss)", scenario.id, scenario.timeout_s)
    res = await dtu.exec_cmd(["bash", "-lc", cmd], timeout_s=scenario.timeout_s)
    (trial_dir / "runner.log").write_text(res.stdout + res.stderr, encoding="utf-8")
    out.notes.append(f"objective runner exited {res.returncode}")

    disp = await dtu.exec_cmd(
        ["bash", "-lc", "cat /workspace/.objective/disposition 2>/dev/null"], timeout_s=60
    )
    disposition = disp.stdout.strip() or "(none)"
    out.notes.append(f"disposition: {disposition}")

    tail = (res.stdout + res.stderr)[-20000:]
    return (
        f"# Objective runner transcript\n\n"
        f"## The objective, as stated by the user\n\n```\n{objective}\n```\n\n"
        f"## Runner exit status\n\n`{res.returncode}`\n\n"
        f"## Disposition artifact (`.objective/disposition`)\n\n`{disposition}`\n\n"
        f"## Run log (tail)\n\n```\n{tail}\n```\n"
    )


async def pull_artifacts(
    scenario: Scenario, dtu: DTU, dest: Path, trial_dir: Path, out: TrialOutcome
) -> None:
    """Pull the small, decisive artifacts out of the DTU.

    Deliberately narrow. The grader reads a normalized folder, not a filesystem dump; a dump is
    how a grader ends up scoring a stale file from a previous run.
    """
    dest.mkdir(parents=True, exist_ok=True)
    wanted = [
        ".objective/disposition",
        ".objective/triage.json",
        ".objective/objective.md",
        ".objective/redirect.md",
        ".objective/convergence.jsonl",
        ".objective/postmortem/report.md",
        ".attractorify/diagnosis.md",
    ]
    listing = await dtu.exec_cmd(
        [
            "bash",
            "-lc",
            (
                "cd /workspace && find . -maxdepth 3 -newermt '-6 hours' -type f "
                "-not -path './.git/*' 2>/dev/null | head -60"
            ),
        ],
        timeout_s=120,
    )
    (trial_dir / "workspace-listing.txt").write_text(listing.stdout, encoding="utf-8")

    found: list[str] = []
    for rel in wanted:
        res = await dtu.exec_cmd(
            ["bash", "-lc", f"cat /workspace/{rel} 2>/dev/null"], timeout_s=60
        )
        if res.returncode == 0 and res.stdout.strip():
            target = dest / rel.replace("/", "__")
            target.write_text(res.stdout, encoding="utf-8")
            found.append(rel)

    # Any .dot the session authored is decisive for the work-request scenarios.
    dots = await dtu.exec_cmd(
        [
            "bash",
            "-lc",
            (
                "cd /workspace && find . -name '*.dot' -newermt '-6 hours' "
                "-not -path './.git/*' 2>/dev/null | head -10"
            ),
        ],
        timeout_s=120,
    )
    for rel in [d.strip().lstrip("./") for d in dots.stdout.splitlines() if d.strip()]:
        res = await dtu.exec_cmd(["bash", "-lc", f"cat /workspace/{rel}"], timeout_s=60)
        if res.returncode == 0 and res.stdout.strip():
            (dest / rel.replace("/", "__")).write_text(res.stdout, encoding="utf-8")
            found.append(rel)

    if not found:
        (dest / "NONE.md").write_text(
            "No artifacts were produced by this run.\n\n"
            "For several scenarios this is the CORRECT outcome: the session was supposed to "
            "diagnose, teach, or refuse rather than author anything. Do not treat an empty "
            "artifact set as a failure by itself — read the scenario's pass bar.\n",
            encoding="utf-8",
        )
    out.notes.append(f"artifacts captured: {found or 'none'}")


def decide(
    scenario: Scenario, criteria: dict[str, Criterion], cfg: dict[str, Any], out: TrialOutcome
) -> tuple[str, list[str]]:
    """Apply rubric.md's Scoring section. No averaging; every failure is named."""
    notes = list(out.notes)
    floor = int(cfg["scoring"]["criterion_min"])
    failures: list[str] = []

    for cid in scenario.criteria_ids:
        if cid not in out.scores:
            failures.append(f"{cid}: NOT SCORED (grader did not submit it)")
            continue
        score = out.scores[cid]
        if score < floor:
            failures.append(f"{cid} ({criteria[cid].name}): {score}/5 — below the floor of {floor}")

    # rubric.md, "The G5 override".
    if out.scores.get("G5") == int(cfg["scoring"]["g5_override_fail_at"]):
        failures.append(
            "G5 OVERRIDE: a session that endorses a model grading its own work fails the run "
            "regardless of every other score."
        )

    for c in out.checks:
        if not c.passed:
            failures.append(f"{c.id} ({c.kind}): {c.detail}")

    if failures:
        notes.extend(failures)
        return "FAIL", notes
    return "PASS", notes


# --------------------------------------------------------------------------- main


def write_results(run_dir: Path, outcomes: list[TrialOutcome], meta: dict[str, Any]) -> str:
    lines = [
        "# Guidance eval results",
        "",
        f"- run: `{run_dir.name}`",
        f"- bundle: `{meta['bundle_repo']}` @ `{meta['bundle_sha'] or meta['bundle_branch']}`",
        f"- started: {meta['started_at']}",
        "",
        "| scenario | verdict | criteria | mechanical |",
        "|---|---|---|---|",
    ]
    for o in outcomes:
        scores = " ".join(f"{k}={v}" for k, v in sorted(o.scores.items())) or "—"
        checks = (
            " ".join(f"{c.id}={'ok' if c.passed else 'FAIL'}" for c in o.checks) or "—"
        )
        lines.append(f"| `{o.scenario_id}` | **{o.verdict}** | {scores} | {checks} |")

    lines += ["", "## Per scenario", ""]
    for o in outcomes:
        lines += [f"### {o.scenario_id} — {o.verdict}", ""]
        if o.failure:
            lines += [f"**Trial error:** `{o.failure}`", ""]
        for cid in sorted(o.scores):
            lines += [f"- **{cid}: {o.scores[cid]}/5** — {o.reasoning.get(cid, '').strip()}"]
        if o.checks:
            lines += ["", "Mechanical checks:"]
            for c in o.checks:
                lines.append(f"- `{c.id}` {'PASS' if c.passed else '**FAIL**'} — {c.detail}")
        if o.notes:
            lines += ["", "Notes:"]
            lines += [f"- {n}" for n in o.notes]
        lines.append("")

    passed = [o for o in outcomes if o.verdict == "PASS"]
    lines += [
        "---",
        "",
        f"**{len(passed)}/{len(outcomes)} scenarios passed.**",
        "",
        (
            "Per rubric.md, the instrument passes only when every scenario passes: the six are "
            "six named properties, not a sample to average."
        ),
        "",
    ]
    text = "\n".join(lines)
    (run_dir / "results.md").write_text(text, encoding="utf-8")
    (run_dir / "results.json").write_text(
        json.dumps(
            {
                "meta": meta,
                "scenarios": [
                    {
                        "id": o.scenario_id,
                        "verdict": o.verdict,
                        "status": o.status,
                        "scores": o.scores,
                        "checks": [
                            {"id": c.id, "passed": c.passed, "detail": c.detail} for c in o.checks
                        ],
                        "notes": o.notes,
                        "failure": o.failure,
                    }
                    for o in outcomes
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return text


async def amain(args: argparse.Namespace) -> int:
    cfg = yaml.safe_load((HERE / "config.yaml").read_text(encoding="utf-8"))
    criteria = load_criteria()

    ids = args.scenarios or (
        [cfg["smoke_scenario"]] if args.smoke else list(cfg["scenarios"])
    )
    scenarios = [load_scenario(s) for s in ids]
    validate_scenarios(scenarios, criteria)

    if args.list:
        for sc in scenarios:
            print(f"{sc.id:34s} {sc.mode:9s} {sc.criteria_ids} {sc.title}")
        return 0

    if not cli_available():
        raise SystemExit("`amplifier-digital-twin` is not on PATH")

    results_root = resolve_results_root(args.results_root)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = results_root / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    log.info("results -> %s", run_dir)

    meta = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "bundle_repo": args.bundle_repo,
        "bundle_branch": args.bundle_branch,
        "bundle_sha": args.bundle_sha,
        "gitea_url": args.gitea_url,
        "scenarios": [s.id for s in scenarios],
        "scoring": cfg["scoring"],
        "foundation_source": cfg.get("foundation_source"),
        "provider_source": cfg.get("provider_source"),
        "criteria": {c.id: {"name": c.name, "anchor": c.anchor} for c in criteria.values()},
        "smoke": bool(args.smoke),
    }
    (run_dir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    # The rubric and scenarios AS RUN, so a later reader is never guessing which version scored.
    inputs = run_dir / "inputs"
    inputs.mkdir(exist_ok=True)
    shutil.copy2(RUBRIC_PATH, inputs / "rubric.md")
    for sc in scenarios:
        shutil.copy2(sc.path, inputs / sc.path.name)

    kwargs: dict[str, Any] = {}
    if cfg.get("foundation_source"):
        kwargs["foundation_source"] = cfg["foundation_source"]
    if cfg.get("provider_source"):
        kwargs["provider_source"] = cfg["provider_source"]

    needs_ai_user = any(s.mode == "session" for s in scenarios)
    ai_user = AIUser(**kwargs) if needs_ai_user else None
    extractor = Extractor(**kwargs)
    grader = Grader(**kwargs)

    log.info("preparing eval agents (AI user / extractor / grader)")
    if ai_user:
        await ai_user.setup()
    await extractor.setup()
    await grader.setup()

    outcomes: list[TrialOutcome] = []
    for sc in scenarios:
        log.info("=" * 78)
        log.info("SCENARIO %s — %s", sc.id, sc.title)
        log.info("=" * 78)
        outcomes.append(
            await run_trial(sc, criteria, cfg, args, run_dir, ai_user, extractor, grader)
        )

    text = write_results(run_dir, outcomes, meta)
    print("\n" + text)
    log.info("results written to %s", run_dir)
    return 0 if all(o.verdict == "PASS" for o in outcomes) else 1


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--scenarios", nargs="*", help="scenario ids (default: all)")
    p.add_argument("--smoke", action="store_true", help="run only config.yaml's smoke_scenario")
    p.add_argument("--list", action="store_true", help="list the selected scenarios and exit")
    p.add_argument("--results-root", help="override the results directory (must be outside the repo)")
    p.add_argument("--gitea-url", default=os.environ.get("GITEA_URL", ""))
    p.add_argument("--gitea-token", default=os.environ.get("GITEA_TOKEN", ""))
    p.add_argument("--bundle-repo", default=os.environ.get("BUNDLE_REPO", "amplifier-bundle-attractor"))
    p.add_argument("--bundle-branch", default=os.environ.get("BUNDLE_BRANCH", "main"))
    p.add_argument("--bundle-sha", default=os.environ.get("BUNDLE_SHA", ""))
    p.add_argument("--keep-dtus", action="store_true", help="never destroy DTUs")
    p.add_argument("--keep-dtus-on-failure", action="store_true", default=True,
                   help="keep the DTU when a trial errors (default: on)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if not args.list and not args.gitea_url:
        raise SystemExit("--gitea-url is required (run.sh supplies it)")

    try:
        return asyncio.run(amain(args))
    except KeyboardInterrupt:
        print("\ninterrupted — check for surviving DTUs: amplifier-digital-twin list",
              file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
