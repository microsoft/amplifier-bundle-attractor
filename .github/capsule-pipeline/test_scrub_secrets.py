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

import contextlib
import io
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


# ---- issue #206: the entropy false positive, reproduced from real runs ----
#
# A worker-session event line of the shape the pipeline actually persists to
# logs/<run>/sessions/<id>/events.jsonl. NOTHING here is a credential: a
# base64 attachment fragment, a sha256 content digest, a provider request
# id, a workspace path, and prose. On 4 of 4 real runs this class of line
# tripped `shape=high-entropy-token` and the gate skipped the evidence
# upload (issue #206; e.g. run 31657343281, findings at lines 5/6/10/11/14).
ENTROPY_SHAPE = scrub_secrets.ENTROPY_SHAPE
ENTROPY_B64_BLOB = "dGhlIHF1aWNrIGJyb3duIGZveCBqdW1wcyBvdmVyIHRoZSBsYXp5IGRvZyAwMTIzNDU2Nzg5"
ENTROPY_SHA256_DIGEST = "2d711642b726b04401627ca9fbac32f5c8530fb1903cc4db02258717921a4881"
ENTROPY_REQUEST_ID = "req_01JQ8ZK4M7N2P5R9T3V6X8Y0AB"

REALISTIC_SESSION_EVENT = {
    "event": "tool:post",
    "ts": "2026-08-13T06:31:19.324332Z",
    "payload": {
        "tool": "read_file",
        "cwd": "/home/runner/work/amplifier-bundle-attractor/amplifier-bundle-attractor",
        "note": "reading the capsule brief",
        "request_id": ENTROPY_REQUEST_ID,
        "content_sha256": ENTROPY_SHA256_DIGEST,
        "attachment_b64": ENTROPY_B64_BLOB,
    },
}
REALISTIC_SESSION_LINES = (
    {"event": "session:start", "payload": {"stage": "capsule", "iteration": 3}},
    REALISTIC_SESSION_EVENT,
    {"event": "session:end", "payload": {"note": "no findings", "exit": 0}},
)

# A capsule-pair line carrying an entropy span. Not a credential and not a
# sensitive assignment -- only the layer-4 heuristic fires on it -- which is
# exactly what makes it the right probe for the scope rule: entropy is the
# ONE class `gate` may quarantine, and it must still hard-block here.
CAPSULE_LINE_WITH_ENTROPY = (
    "set -euo pipefail\n"
    f'EXPECTED_B64="{ENTROPY_B64_BLOB}"\n'
    'test "$(printf %s "$payload" | base64 -w0)" = "$EXPECTED_B64" || exit 1\n'
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

    # ---- issue #206: the entropy gate's split verdict ----

    def test_entropy_span_redaction_is_surgical(self) -> None:
        """The redaction primitive: the suspicious RUN is replaced and
        nothing else moves.

        This is what makes the quarantine honest -- the evidence survives
        because only the guessed-secret span leaves, not the line, not the
        file, and not the JSON structure carrying it.
        """
        line = json.dumps(REALISTIC_SESSION_EVENT)
        redacted, n = scrub_secrets.redact_entropy_text(line)

        self.assertGreaterEqual(n, 1, "the realistic payload must trip the heuristic at all")
        # The span is gone; the marker is there.
        self.assertNotIn(ENTROPY_B64_BLOB, redacted)
        self.assertIn("[REDACTED:entropy]", redacted)
        # Innocent bytes -- prose, paths, field names, the sha256 digest
        # (excluded from the heuristic as pure hex) -- are untouched.
        for innocent in (
            '"event"',
            '"tool:post"',
            "/home/runner/work/amplifier-bundle-attractor",
            "reading the capsule brief",
            ENTROPY_SHA256_DIGEST,
        ):
            self.assertIn(innocent, redacted, f"redaction ate innocent content: {innocent!r}")
        # And it is still the same JSON document, one string value shorter.
        reparsed = json.loads(redacted)
        self.assertEqual(reparsed["event"], "tool:post")
        self.assertEqual(reparsed["payload"]["cwd"], REALISTIC_SESSION_EVENT["payload"]["cwd"])
        # Idempotent: the marker is not itself an entropy candidate, so a
        # second pass (the confirming re-scan's premise) changes nothing.
        again, n2 = scrub_secrets.redact_entropy_text(redacted)
        self.assertEqual(n2, 0)
        self.assertEqual(again, redacted)

    def test_gate_quarantines_entropy_only_evidence(self) -> None:
        """THE ISSUE #206 FLOW, end to end: a realistic worker-session
        events.jsonl blocks the upload today and survives it after.

        `scan` (unchanged, and what the capsule pair gets) still exits 1 on
        this file. `gate` redacts the spans, re-scans clean, and exits 0 so
        the evidence artifact is actually uploaded.
        """
        events = self.write(
            "logs/attractor-run-1/sessions/abc123/events.jsonl",
            "\n".join(json.dumps(e) for e in REALISTIC_SESSION_LINES) + "\n",
        )
        innocent = self.write("logs/run.log", "PIPELINE ok at f09775f4aca234fc2417dbec034cbde\n")
        before = innocent.read_text()

        # 1. The false positive, reproduced: scan blocks on entropy alone.
        pre = run_cli(["scan", str(self.root)])
        self.assertEqual(pre.returncode, 1, pre.stdout)
        self.assertIn(f"shape={ENTROPY_SHAPE}", pre.stdout)
        self.assertNotIn("shape=openai-key", pre.stdout)

        # 2. The gate quarantines instead of blocking.
        proc = run_cli(["gate", str(self.root)])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("::notice::", proc.stdout)
        self.assertIn("QUARANTINED", proc.stdout)
        self.assertIn(str(events), proc.stdout)
        self.assertIn("clean after quarantine", proc.stdout)

        # 3. The spans are gone from disk, the evidence is still evidence.
        after = events.read_text()
        self.assertNotIn(ENTROPY_B64_BLOB, after)
        self.assertIn("[REDACTED:entropy]", after)
        self.assertIn("reading the capsule brief", after)
        for raw in after.splitlines():
            json.loads(raw)  # every line still parses
        # A file with no findings is never rewritten.
        self.assertEqual(innocent.read_text(), before)

        # 4. The guarantee: a plain scan of the quarantined tree is clean,
        #    which is exactly what the gate asserted before returning 0.
        post = run_cli(["scan", str(self.root)])
        self.assertEqual(post.returncode, 0, post.stdout)

    def test_gate_blocks_when_a_real_token_rides_along(self) -> None:
        """MIXED CASE: entropy findings do NOT buy a real credential a
        ride. One known shape anywhere and the whole gate hard-blocks,
        with nothing redacted -- the fail-closed guarantee is unchanged
        for every shape the scrubber actually recognizes."""
        events = self.write(
            "logs/attractor-run-1/sessions/abc123/events.jsonl",
            "\n".join(json.dumps(e) for e in REALISTIC_SESSION_LINES) + "\n",
        )
        leak = self.write("logs/env-dump.log", f"PATH=/usr/bin\nOPENAI_API_KEY={FAKE_OPENAI}\n")
        events_before = events.read_text()
        leak_before = leak.read_text()

        proc = run_cli(["gate", str(self.root)])
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("shape=openai-key", proc.stdout)
        self.assertIn("The upload must be blocked", proc.stdout)
        self.assertNotIn("::notice::", proc.stdout)
        self.assertNotIn("QUARANTINED", proc.stdout)
        # A blocked gate rewrites NOTHING -- not even the entropy spans it
        # would have quarantined on its own. Evidence a human must now
        # inspect is the evidence the run produced.
        self.assertEqual(events.read_text(), events_before)
        self.assertEqual(leak.read_text(), leak_before)

    def test_gate_never_redacts_a_fenced_capsule_pair(self) -> None:
        """PR #207's scope rule, mechanically: inside --never-redact, ANY
        finding blocks -- entropy included -- and no byte is rewritten.

        The capsule pair is the run's reviewed output; its proofs attach
        to its exact bytes (the 2026-08-13 corruption incident). Evidence
        may be redacted to survive; the pair may not.
        """
        pair = self.write("out/work-definition.verify.sh", CAPSULE_LINE_WITH_ENTROPY)
        pair_before = pair.read_text()
        self.write(
            "logs/attractor-run-1/sessions/abc123/events.jsonl",
            json.dumps(REALISTIC_SESSION_EVENT) + "\n",
        )

        proc = run_cli(
            ["gate", "--never-redact", str(self.root / "out"), str(self.root)],
        )
        self.assertEqual(proc.returncode, 1, proc.stdout)
        self.assertIn("--never-redact subtree", proc.stdout)
        self.assertIn("this BLOCKS", proc.stdout)
        self.assertIn("The upload must be blocked", proc.stdout)
        self.assertNotIn("::notice::", proc.stdout)
        # The pair is byte-identical -- the whole point.
        self.assertEqual(pair.read_text(), pair_before)
        self.assertNotIn("[REDACTED:entropy]", pair.read_text())

        # And the read-only verb the workflows point at the pair is
        # unchanged by any of this: entropy there still exits 1.
        scan_pair = run_cli(["scan", str(self.root / "out")])
        self.assertEqual(scan_pair.returncode, 1, scan_pair.stdout)
        self.assertIn(f"shape={ENTROPY_SHAPE}", scan_pair.stdout)
        self.assertEqual(pair.read_text(), pair_before)

    def test_gate_is_clean_and_silent_on_ordinary_evidence(self) -> None:
        """No findings at all -> no redaction, no annotation, exit 0. The
        quarantine path must not fire on evidence that never tripped
        anything."""
        p = self.write(
            "logs/run.log",
            "base_sha=f09775f4aca234fc2417dbec034cbde0bce543a3\n"
            "Classified as: kind=capsule id=work-definition\n",
        )
        before = p.read_text()
        proc = run_cli(["gate", str(self.root)])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("clean -- scanned", proc.stdout)
        self.assertNotIn("::notice::", proc.stdout)
        self.assertEqual(p.read_text(), before)

    def test_gate_blocks_when_the_rescan_does_not_clear(self) -> None:
        """The guarantee is the RE-SCAN, not the redaction: if anything
        survives the entropy pass, the gate blocks exactly as before.

        Simulated by making the redactor a no-op, which is the honest
        model of 'the quarantine failed to clear the finding'."""
        self.write(
            "logs/attractor-run-1/sessions/abc123/events.jsonl",
            json.dumps(REALISTIC_SESSION_EVENT) + "\n",
        )
        original = scrub_secrets.redact_entropy_text
        buf = io.StringIO()
        try:
            scrub_secrets.redact_entropy_text = lambda text: (text, 1)  # type: ignore[assignment]
            with contextlib.redirect_stdout(buf):
                rc = scrub_secrets.cmd_gate([str(self.root)], [])
        finally:
            scrub_secrets.redact_entropy_text = original  # type: ignore[assignment]
        self.assertEqual(rc, 1)
        self.assertIn("QUARANTINE DID NOT CLEAR", buf.getvalue())
        self.assertNotIn("::notice::", buf.getvalue())

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
