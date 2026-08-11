"""Unit tests for scrub_secrets.py (stdlib only; no pytest required).

Run from this directory:

    python3 -m unittest test_scrub_secrets -v

The fixture in test_incident_shape_events_jsonl reproduces the exact shape
of the 2026-08 incident: a tool:post payload persisted verbatim into
events.jsonl carrying a literal `OPENAI_API_KEY=sk-proj-...` value inside a
JSON string.
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
