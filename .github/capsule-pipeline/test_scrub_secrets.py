"""Unit tests for scrub_secrets.py (stdlib only; no pytest required).

Run from this directory:

    python3 -m unittest test_scrub_secrets -v

The fixture in test_incident_shape_events_jsonl reproduces the exact shape
of the 2026-08 incident: a tool:post payload persisted verbatim into
events.jsonl carrying a literal `OPENAI_API_KEY=sk-proj-...` value inside a
JSON string.

CAPSULE_GATE_LINES_FROM_PR_205 reproduces the SECOND incident (2026-08-13):
the assignment rule used to match any name CONTAINING `_TOKEN`, so it
rewrote the token-accounting assignments in a shipped capsule gate --
`input_tokens=`, `output_tokens=`, `total_tokens=`, `cache_read_tokens=`,
`reasoning_tokens=` -- into `[REDACTED:assignment]`, swallowing the
trailing comma with the value and leaving a Python heredoc that no longer
parses. Those lines are quoted verbatim from the pre-corruption form of
`.github/capsule-pipeline/proposals/issue-204/
cost-exposure-unified-llm-loop-pipeline.verify.sh` (PR #205: 54 markers
across 31 lines). The pair of directional tests below is the regression:
these shapes must survive BYTE-IDENTICAL, and real credential assignments
must still be redacted.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scrub_secrets  # noqa: E402

SCRIPT = Path(__file__).resolve().parent / "scrub_secrets.py"

# Deliberately fake but shape-exact secrets (planted, never real).
FAKE_OPENAI = "sk-proj-" + "Ab1" * 22 + "XYZq"  # 78 chars total
FAKE_GHP = "ghp_" + "Zx9Ab" * 8  # classic PAT shape
FAKE_FG_PAT = "github_pat_" + "11AABBCC0" * 5
FAKE_ASSIGNMENT_VALUE = "hunter2-value-9931"
FAKE_NOVEL = "xai-" + "qZ3vB8kN1pW6yT4mJ0hRdC7fLsGuE2aX"  # novel prefix + random tail

# The 2026-08-13 corruption class, quoted from PR #205's shipped gate in its
# PRE-corruption form. Every one of these was rewritten in place by the old
# CONTAINS-based assignment rule; all must now survive byte-identical.
# (Shapes 1-4 are the literal line shapes recoverable from the corrupted
# file; the rest are the same class in other idioms a token-math gate uses.)
CAPSULE_GATE_LINES_FROM_PR_205 = (
    "            dict(model=MODEL, input_tokens=inp1, output_tokens=0),",
    "            dict(model=MODEL, input_tokens=inp3, output_tokens=out3, cache_read_tokens=cr3),",
    "                        total_tokens=in3b + out3b,",
    "        u4 = Usage(input_tokens=inp4, output_tokens=out4, total_tokens=inp4 + out4)",
    "            reasoning_tokens=r_a,",
    "            cache_read_tokens=cr_a, cache_write_tokens=cw_a,",
    "                 cache_read_input_tokens=0, cache_creation_input_tokens=1024, speed=None),",
    "max_tokens=4096",
    "input_tokens=5000",
    "token_count=1234",
)

# Real credential assignments -- the shape the scrubber exists for. Every
# one must STILL be redacted after the narrowing.
CREDENTIAL_ASSIGNMENT_NAMES = (
    "GITHUB_TOKEN",
    "GH_TOKEN",
    "CAPSULE_PR_TOKEN",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "MY_API_KEY",
    "X_SECRET",
    "CLIENT_SECRET",
    "AWS_SECRET_ACCESS_KEY",
    "GOOGLE_APPLICATION_CREDENTIALS",
    "PASSWORD",
    "DB_PASSWORD",
)


def run_cli(args: list[str], env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in scrub_secrets.DEFAULT_WATCH_ENV}
    env["SCRUB_WATCH_ENV"] = ""
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


class ScrubTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def write(self, rel: str, content: str) -> Path:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return p

    def test_incident_shape_events_jsonl(self) -> None:
        """The exact incident shape: env dump inside a JSON string in
        events.jsonl. After scrub: no original token survives, the JSON
        still parses, and innocent content is untouched."""
        payload = {
            "event": "tool:post",
            "payload": {
                "tool": "bash",
                "output": (
                    "PATH=/usr/bin\nHOME=/root\n"
                    f"OPENAI_API_KEY={FAKE_OPENAI}\n"
                    f"SOME_SERVICE_TOKEN={FAKE_ASSIGNMENT_VALUE}\n"
                    "LANG=C.UTF-8\n"
                ),
            },
        }
        innocent = {"event": "session:start", "payload": {"cwd": "/work", "note": "all fine"}}
        events = self.write(
            "stage/sessions/abc/events.jsonl",
            json.dumps(payload) + "\n" + json.dumps(innocent) + "\n",
        )
        log = self.write("logs/run.log", f"exporting {FAKE_GHP} and {FAKE_FG_PAT} now\n")

        proc = run_cli(["scrub", str(self.root)])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        events_text = events.read_text()
        log_text = log.read_text()
        # No original secret survives anywhere.
        for secret in (FAKE_OPENAI, FAKE_GHP, FAKE_FG_PAT, FAKE_ASSIGNMENT_VALUE):
            self.assertNotIn(secret, events_text)
            self.assertNotIn(secret, log_text)
        # Redaction markers present.
        self.assertIn("[REDACTED:", events_text)
        self.assertIn("[REDACTED:github-token]", log_text)
        self.assertIn("[REDACTED:github-fine-grained-pat]", log_text)
        # JSON lines still parse; innocent line byte-identical.
        lines = events_text.splitlines()
        scrubbed = json.loads(lines[0])
        self.assertEqual(scrubbed["event"], "tool:post")
        self.assertIn("PATH=/usr/bin", scrubbed["payload"]["output"])
        self.assertIn("LANG=C.UTF-8", scrubbed["payload"]["output"])
        self.assertEqual(json.loads(lines[1]), innocent)
        # The leaking variable NAME survives (evidence of WHAT leaked).
        self.assertIn("OPENAI_API_KEY=", scrubbed["payload"]["output"])

        # And the residual gate is clean after scrubbing.
        proc2 = run_cli(["scan", str(self.root)])
        self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)

    def test_literal_env_value_redacted_regardless_of_shape(self) -> None:
        secret = "totally-unpatterned value 42 with spaces? no: x"[:24] + "ZQ"
        f = self.write("out/status.json", json.dumps({"note": f"leaked -> {secret} <-"}))
        proc = run_cli(
            ["scrub", str(self.root)],
            env_extra={"OPENAI_API_KEY": secret},
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        text = f.read_text()
        self.assertNotIn(secret, text)
        self.assertIn("[REDACTED:env:OPENAI_API_KEY]", text)
        self.assertTrue(json.loads(text))  # still valid JSON

    def test_residual_gate_fires_on_novel_prefix(self) -> None:
        """A secret the scrub patterns do NOT know (novel prefix, random
        tail) must still fire the scan gate via the entropy heuristic."""
        f = self.write("logs/tool.log", f"auth header was {FAKE_NOVEL}\n")
        proc = run_cli(["scrub", str(self.root)])
        self.assertEqual(proc.returncode, 0)
        # Scrub did NOT catch it (that's the premise of this test).
        self.assertIn(FAKE_NOVEL, f.read_text())
        proc2 = run_cli(["scan", str(self.root)])
        self.assertEqual(proc2.returncode, 1, "gate must fire on residual secret")
        self.assertIn("shape=high-entropy-token", proc2.stdout)
        self.assertIn("The upload must be blocked", proc2.stdout)
        # The finding never prints the secret value itself.
        self.assertNotIn(FAKE_NOVEL, proc2.stdout + proc2.stderr)

    def test_scan_clean_on_routine_evidence(self) -> None:
        """Routine evidence content (git SHAs, digests, paths, prose) must
        NOT fire the gate -- false positives block honest uploads."""
        self.write(
            "logs/routine.log",
            "commit 41a989a1b0aad2d13bfec95fd0149110299aabbccdd0011223344556\n"
            "sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08\n"
            "downloading capsule-implement-issue-155-31099116800.zip\n"
            "/home/runner/work/_temp/capsule-implement/logs/attractor-run\n"
            "PipelineEngine executed node verify_gate_with_long_name (iteration 3)\n",
        )
        self.write("out/status.json", json.dumps({"status": "success", "iterations": 3}))
        proc = run_cli(["scan", str(self.root)])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_scan_fires_on_each_known_shape(self) -> None:
        cases = {
            "openai-key": f"key={FAKE_OPENAI}",
            "github-token": f"tok {FAKE_GHP} end",
            "github-fine-grained-pat": f"pat {FAKE_FG_PAT} end",
            "assignment:MY_PASSWORD": "MY_PASSWORD=supersecretvalue99",
        }
        for shape, content in cases.items():
            with self.subTest(shape=shape):
                sub = tempfile.TemporaryDirectory()
                self.addCleanup(sub.cleanup)
                Path(sub.name, "x.log").write_text(content + "\n")
                proc = run_cli(["scan", sub.name])
                self.assertEqual(proc.returncode, 1, f"{shape}: {proc.stdout}")
                self.assertIn(f"shape={shape}", proc.stdout)

    # ---- 2026-08-13 artifact-corruption regression, BOTH directions ----

    def test_capsule_gate_token_math_survives_scrub(self) -> None:
        """DIRECTION 1 (the corruption): the token-accounting assignments
        that a real shipped capsule gate is full of must come out of the
        scrubber BYTE-IDENTICAL.

        The old CONTAINS-based rule rewrote every one of these -- 54
        markers across 31 lines of PR #205's shipped
        `cost-exposure-unified-llm-loop-pipeline.verify.sh` -- swallowing
        the trailing comma along with the value and leaving a Python
        heredoc that no longer parses.
        """
        original = "\n".join(CAPSULE_GATE_LINES_FROM_PR_205) + "\n"
        f = self.write("out/capsule.verify.sh", original)

        proc = run_cli(["scrub", str(self.root)])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

        after = f.read_text()
        self.assertEqual(
            after,
            original,
            "the scrubber mutated a capsule gate's token-accounting lines "
            "-- this is the 2026-08-13 corruption regressing",
        )
        self.assertNotIn("[REDACTED:", after)

        # And it must not merely survive the SCRUB: a `scan` FINDING on
        # capsule_out is now a hard failure of the whole specify run (both
        # specify workflows scan the capsule pair instead of scrubbing it),
        # so a false positive here would block every honest capsule whose
        # subject happens to be token math.
        proc2 = run_cli(["scan", str(self.root)])
        self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)

    def test_credential_assignment_shapes_still_redact(self) -> None:
        """DIRECTION 2 (the thing the scrubber is FOR): narrowing the name
        match must not let a real credential assignment through -- neither
        past `scrub` nor past the `scan` gate."""
        value = "hunter2-value-9931-abcdef"
        for name in CREDENTIAL_ASSIGNMENT_NAMES:
            with self.subTest(name=name):
                sub = tempfile.TemporaryDirectory()
                self.addCleanup(sub.cleanup)
                p = Path(sub.name, "env.log")
                p.write_text(f"PATH=/usr/bin\n{name}={value}\nLANG=C.UTF-8\n")

                proc = run_cli(["scrub", sub.name])
                self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                after = p.read_text()
                self.assertNotIn(value, after, f"{name}= leaked its value past the scrubber")
                self.assertIn(f"{name}=[REDACTED:assignment]", after)
                # Innocent neighbours untouched.
                self.assertIn("PATH=/usr/bin", after)
                self.assertIn("LANG=C.UTF-8", after)

                # scan must independently see the unscrubbed shape.
                p.write_text(f"{name}={value}\n")
                proc2 = run_cli(["scan", sub.name])
                self.assertEqual(proc2.returncode, 1, f"{name}: {proc2.stdout}")
                self.assertIn(f"shape=assignment:{name}", proc2.stdout)

    def test_assignment_name_match_is_end_anchored(self) -> None:
        """The rule itself, stated directly: a sensitive word at the END of
        the name matches; the same word merely CONTAINED does not."""
        redacts = ("SERVICE_TOKEN", "A_SECRET", "SOME_PASSWORD", "V2_API_KEY")
        survives = (
            "input_tokens",
            "output_tokens",
            "total_tokens",
            "max_tokens",
            "cache_read_tokens",
            "cache_creation_input_tokens",
            "reasoning_tokens",
            "token_count",
            "token_budget",
            "secret_name",
            "password_field",
            "credential_path",
            "api_key_name",
        )
        for name in redacts:
            with self.subTest(name=name, expect="redact"):
                text, shapes = scrub_secrets.scrub_text(f"{name}=valuevalue\n", {})
                self.assertEqual(text, f"{name}=[REDACTED:assignment]\n")
                self.assertEqual(shapes, [f"assignment:{name}"])
        for name in survives:
            with self.subTest(name=name, expect="survive"):
                line = f"{name}=somevalue,\n"
                text, shapes = scrub_secrets.scrub_text(line, {})
                self.assertEqual(text, line)
                self.assertEqual(shapes, [])

    def test_binaryish_file_does_not_crash(self) -> None:
        p = self.root / "blob.bin"
        p.write_bytes(bytes(range(256)) + FAKE_GHP.encode() + b"\x00\xff")
        proc = run_cli(["scrub", str(self.root)])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = p.read_bytes()
        self.assertNotIn(FAKE_GHP.encode(), data)
        self.assertIn(b"[REDACTED:github-token]", data)
        # Non-matched bytes preserved.
        self.assertTrue(data.startswith(bytes(range(256))))

    def test_missing_root_is_not_an_error(self) -> None:
        proc = run_cli(["scrub", str(self.root / "does-not-exist")])
        self.assertEqual(proc.returncode, 0)
        proc2 = run_cli(["scan", str(self.root / "does-not-exist")])
        self.assertEqual(proc2.returncode, 0)


if __name__ == "__main__":
    unittest.main()
