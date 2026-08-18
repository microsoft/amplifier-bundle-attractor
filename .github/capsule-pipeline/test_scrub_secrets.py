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
import secrets
import subprocess
import sys
import tempfile
import time
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


# ---- run 31754414275: the CAPSULE-PROVENANCE false positive ----
#
# The pipeline CONVERGED and packaged a feature capsule; the pre-publication
# capsule-artifacts `scan` then reported shape=high-entropy-token in SIX
# capsule files and blocked the capsule PR -- the second consecutive
# converged capsule destroyed at the door.
#
# Not one of the six was a secret. The maintainer-ratified criteria REQUIRE
# the gate to vendor the provider-rates oracle as a plain file (a pinned copy
# of amplifier_module_provider_anthropic/_cost.py) AND to record "its exact
# version/commit in capsule provenance". A capsule that obeys therefore emits
# a 40-hex commit SHA inside a long URL path in: DEFINITION.md's provenance
# section, the vendored oracle's own header, the gate script (twice -- the two
# ACs that consult the oracle), and the gate's header output, which the rival
# lane copies verbatim into verify-rival.log and then quotes again into
# rival-red-unadjudicated.md via `tail -20`.
#
# ENTROPY_CANDIDATE's character class contains `/` (base64 uses it as data),
# so the regex swallows the whole URL into ONE run and the pure-hex exclusion
# -- which correctly clears the bare SHA -- never gets to see it. Measured:
# SHA alone 3.63 bits/char, path alone 4.14, the two merged 4.62. See the
# STRUCTURAL_HEX_SEGMENT block in scrub_secrets.py.
ORACLE_COMMIT = "dae6d114d7821e2081a05d6e4bcd350c88dc2a41"
ORACLE_RAW_URL = (
    "https://raw.githubusercontent.com/microsoft/amplifier-module-provider-anthropic/"
    f"{ORACLE_COMMIT}/amplifier_module_provider_anthropic/_cost.py"
)
ORACLE_BLOB_URL = (
    "https://github.com/microsoft/amplifier-module-provider-anthropic/blob/"
    f"{ORACLE_COMMIT}/amplifier_module_provider_anthropic/_cost.py"
)

# One reconstructed line per incident site. None of these is a credential;
# every one is content the capsule contract requires.
CAPSULE_PROVENANCE_LINES = (
    # <id>.md:39 -- DEFINITION.md provenance row
    f"| `oracle.py` | pinned copy | {ORACLE_BLOB_URL} |",
    # <id>.oracle.py:4 -- the vendored oracle's own vendoring header
    f"# Vendored from {ORACLE_RAW_URL}",
    # <id>.verify.sh:369 -- AC-1's oracle load, provenance comment
    f"# oracle (AC-1 parity): vendored from {ORACLE_RAW_URL}",
    # <id>.verify.sh:772 -- AC-3's oracle load, same provenance comment
    f"#   expected values derived from {ORACLE_RAW_URL}",
    # <id>.verify-rival.log:2 -- the gate's own header line, and (because the
    # log is shorter than 20 lines) the same bytes again at
    # <id>.rival-red-unadjudicated.md:10 via `tail -20 .ai/verify-rival.log`
    f"Oracle:    .ai/capsule/oracle.py <- {ORACLE_RAW_URL}",
    # the bare provenance shapes the same contract emits
    f"Oracle:    .ai/capsule/oracle.py (commit {ORACLE_COMMIT})",
    f"base_sha={ORACLE_COMMIT}",
    f"repos/microsoft/amplifier-module-provider-anthropic/contents/_cost.py?ref={ORACLE_COMMIT}",
)

# THE EXCLUSION MUST NOT BECOME A HIDING PLACE. Each of these carries real
# random material; a digest-length hex segment sitting next to it changes
# nothing, and every one must still fire.
ENTROPY_MUST_STILL_FIRE = (
    # a genuinely random 32-char base64 token: mixed case + digits + / and +
    "attachment=T3k9/Qm2+Wz7XbR4pLdV6sYh1NcAg8Uj",
    # the same, parked next to a git SHA in a path -- the SHA is removed from
    # the estimate, the random blob is not
    f"artifacts/{ORACLE_COMMIT}/T3k9/Qm2+Wz7XbR4pLdV6sYh1NcAg8Uj",
    # base64url random material (the `-`/`_` alphabet)
    "resume=qm7Zt0Xb-Rk3LpW9sVh2Ye8NcAd1Gf6U",
    # an AWS-style secret access key: base64 alphabet including `/`
    "wJalrXUtnFEMI/K7MDENG/bPxRfiCYzEXAMPLEKEY9x",
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

    # ---- run 31754414275 capsule-provenance regression, BOTH directions ----

    def test_capsule_provenance_shapes_do_not_trip_entropy(self) -> None:
        """Every reconstructed incident line must scan CLEAN.

        BEFORE this fix each URL-shaped line reported
        shape=high-entropy-token and hard-failed the capsule-artifacts scan
        (no quarantine arm exists for the pair -- a finding there fails the
        whole run), destroying a converged capsule over a 40-hex commit SHA
        the criteria REQUIRE the capsule to carry.
        """
        for line in CAPSULE_PROVENANCE_LINES:
            with self.subTest(line=line[:60]):
                self.assertEqual(
                    scrub_secrets.scan_text(line, {}),
                    [],
                    f"capsule provenance must not be secret-shaped: {line}",
                )

    def test_capsule_artifacts_scan_clean_end_to_end(self) -> None:
        """The six incident sites, as files, through the real CLI verb the
        workflow runs on the capsule pair."""
        cid = "per-call-cost-exposure"
        self.write(f"out/{cid}.md", f"| `oracle.py` | pinned copy | {ORACLE_BLOB_URL} |\n")
        self.write(
            f"out/{cid}.oracle.py",
            '"""Anthropic pricing rates and cost computation."""\n'
            "#\n"
            f"# Vendored from {ORACLE_RAW_URL}\n"
            "from decimal import Decimal\n",
        )
        self.write(
            f"out/{cid}.verify.sh",
            "set -euo pipefail\n"
            f"# oracle (AC-1 parity): vendored from {ORACLE_RAW_URL}\n"
            f"#   expected values derived from {ORACLE_RAW_URL}\n",
        )
        self.write(
            f"out/{cid}.verify-rival.log",
            "=== DEFINITION.verify.sh: per-call cost exposure gate ===\n"
            f"Oracle:    .ai/capsule/oracle.py <- {ORACLE_RAW_URL}\n"
            f"Base SHA:  {ORACLE_COMMIT}\n",
        )
        self.write(
            f"out/{cid}.rival-red-unadjudicated.md",
            "# Finding: RIVAL RED\n\n"
            "--- gate output under the rival patch (tail of .ai/verify-rival.log) ---\n"
            "=== DEFINITION.verify.sh: per-call cost exposure gate ===\n"
            f"Oracle:    .ai/capsule/oracle.py <- {ORACLE_RAW_URL}\n",
        )
        proc = run_cli(["scan", str(self.root)])
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("no secret-shaped material", proc.stdout)
        self.assertNotIn(scrub_secrets.ENTROPY_SHAPE, proc.stdout)

    def test_structural_hex_segments_are_removed_before_the_estimate(self) -> None:
        """The mechanism, directly: a digest-length pure-hex SEGMENT is
        dropped exactly as `-`/`_` already are; anything else is identity."""
        run = (
            "com/microsoft/amplifier-module-provider-anthropic/"
            f"{ORACLE_COMMIT}/amplifier_module_provider_anthropic/_cost"
        )
        reduced = scrub_secrets._without_structural_hex(run)
        self.assertNotIn(ORACLE_COMMIT, reduced)
        self.assertIn("amplifier-module-provider-anthropic", reduced)
        # The whole point: the mixture scored above threshold, neither part does.
        self.assertGreaterEqual(scrub_secrets._shannon_entropy(run), scrub_secrets.ENTROPY_THRESHOLD)
        self.assertLess(scrub_secrets._shannon_entropy(reduced), scrub_secrets.ENTROPY_THRESHOLD)
        self.assertLess(scrub_secrets._shannon_entropy(ORACLE_COMMIT), 4.0)
        # Identity for a run with no digest-length hex segment.
        for token in ("T3k9/Qm2+Wz7XbR4pLdV6sYh1NcAg8Uj", "abcdef1234/notahexsegment"):
            self.assertEqual(scrub_secrets._without_structural_hex(token), token)
        # A short hex span is NOT structure -- it stays in the estimate.
        self.assertEqual(scrub_secrets._without_structural_hex("beefcafe"), "beefcafe")

    def test_entropy_still_fires_on_random_material(self) -> None:
        """The narrowing must never buy random material a pass -- including
        random material parked right next to a git SHA."""
        for line in ENTROPY_MUST_STILL_FIRE:
            with self.subTest(line=line[:60]):
                self.assertIn(
                    scrub_secrets.ENTROPY_SHAPE,
                    scrub_secrets.scan_text(line, {}),
                    f"random material must still be detected: {line}",
                )

    def test_real_credential_battery_still_detected(self) -> None:
        """End-to-end, through the CLI: every known credential shape, a
        sensitive assignment, and a genuinely random base64 token still fail
        the scan. Values are never echoed into the log."""
        random_b64 = "T3k9/Qm2+Wz7XbR4pLdV6sYh1NcAg8Uj"
        cases = {
            "openai-key": (f"OPENAI_API_KEY={FAKE_OPENAI}", "openai-key"),
            "github-token": (f"tok {FAKE_GHP} end", "github-token"),
            "github-fine-grained-pat": (f"pat {FAKE_FG_PAT} end", "github-fine-grained-pat"),
            "assignment": ("CLIENT_SECRET=hunter2-value-9931", "assignment:CLIENT_SECRET"),
            "random-base64": (f"blob: {random_b64}", scrub_secrets.ENTROPY_SHAPE),
            "sha-adjacent-random": (
                f"artifacts/{ORACLE_COMMIT}/{random_b64}",
                scrub_secrets.ENTROPY_SHAPE,
            ),
        }
        for name, (content, shape) in cases.items():
            with self.subTest(case=name):
                sub = tempfile.TemporaryDirectory()
                self.addCleanup(sub.cleanup)
                Path(sub.name, "x.log").write_text(content + "\n")
                proc = run_cli(["scan", sub.name])
                self.assertEqual(proc.returncode, 1, f"{name}: {proc.stdout}")
                self.assertIn(f"shape={shape}", proc.stdout)
                self.assertIn("The upload must be blocked", proc.stdout)

    def test_missing_root_is_not_an_error(self) -> None:
        proc = run_cli(["scrub", str(self.root / "does-not-exist")])
        self.assertEqual(proc.returncode, 0)
        proc2 = run_cli(["scan", str(self.root / "does-not-exist")])
        self.assertEqual(proc2.returncode, 0)


# ---- issue #289: the VALUE grammar (quote / backslash / space escapees) ----
#
# The pre-#289 value class `[^\s"'\\]{4,}` stopped at the first whitespace,
# quote or backslash, so a sensitive assignment whose value merely CONTAINED
# one of those in its first few characters escaped BOTH scrub doors -- this
# gate and the session-event write seam that ports these same patterns.
# Measured in PR #288's adversarial review: 0 of 500 20-char-tail escapees
# caught. These tests pin BOTH directions of the widening:
#
#   1. the escape cases are now redacted (the fix), and
#   2. nothing innocent moved (the crux -- an over-reaching value class is
#      how this file corrupted a shipped artifact in 2026-08-13, and at the
#      write seam it would destroy the JSON record instead of the secret).
#
# Every fake secret below is MINTED AT RUNTIME. A long credential-shaped
# literal committed to this file would be secret-shaped material in the repo,
# which the leak scan correctly refuses.


def fake_tail(nbytes: int = 8) -> str:
    """A fake secret tail, minted per run.

    `token_hex` deliberately: hex is provably sub-threshold for the layer-4
    entropy heuristic (16 symbols cannot carry 4.5 bits/char) and cannot
    contain `sk-`/`gh?_`/`github_pat_`, so a finding on these probes can only
    be the LAYER-2 assignment rule -- never a lucky entropy or token hit.
    """
    return secrets.token_hex(nbytes)


class AssignmentValueGrammarTests(unittest.TestCase):
    """Issue #289: values that begin with, or contain, a quote/backslash/space."""

    def scrub(self, text: str) -> tuple[str, list[str]]:
        return scrub_secrets.scrub_text(text, {})

    # ---- direction 1: the escapees are caught ----

    def test_issue_289_escape_cases_are_now_redacted(self) -> None:
        """The issue's named cases, plus the variants they generalize to.

        Each probe is `<SENSITIVE_NAME>=<value>` where the value carries a
        quote/backslash/space in the first few characters. Before #289 the
        first two redacted NOTHING and the rest leaked a tail; all of them
        must now come out with the value gone.
        """
        tail = fake_tail()
        cases = {
            # value contains a backslash at char 4 -- redacted NOTHING before
            "backslash-inside": f"PASSWORD=abc\\{tail}",
            # value contains a single quote at char 4 -- redacted NOTHING before
            "apostrophe-inside": f"PASSWORD=abc'{tail}",
            # value contains a double quote -- the tail survived before
            "double-quote-inside": f"MY_PASSWORD=abcd\"{tail}",
            # value BEGINS with a backslash / quote
            "leading-backslash": f"GH_TOKEN=\\{tail}",
            "leading-double-quote": f'GH_TOKEN="{tail}"',
            "leading-single-quote": f"CLIENT_SECRET='{tail}'",
            # quoted value with internal spaces -- truncated at the space before
            "quoted-with-spaces": f'OPENAI_API_KEY="{tail} and more {tail}"',
            "single-quoted-with-spaces": f"AWS_SECRET_ACCESS_KEY='{tail} more'",
            # a Windows-path-shaped value (backslashes throughout)
            "path-shaped": f"DB_PASSWORD=C:\\Secrets\\{tail}",
        }
        for name, line in cases.items():
            with self.subTest(case=name):
                after, shapes = self.scrub(line)
                self.assertNotIn(tail, after, f"{name}: the value survived the scrub")
                self.assertIn("[REDACTED:assignment]", after)
                self.assertTrue(shapes, f"{name}: redacted but reported no shape")

                # And the READ-ONLY gate must independently SEE it, or the
                # upload gate stays green on a line that carries a secret.
                sub = tempfile.TemporaryDirectory()
                self.addCleanup(sub.cleanup)
                Path(sub.name, "x.log").write_text(line + "\n")
                proc = run_cli(["scan", sub.name])
                self.assertEqual(proc.returncode, 1, f"{name}: {proc.stdout}")
                self.assertIn("shape=assignment:", proc.stdout)

    def test_a_quoted_value_keeps_its_quotes(self) -> None:
        """Structure is preserved: the opening quote is re-emitted and the
        closing quote is matched by LOOKAHEAD, never consumed. A reader (and
        a JSON parser) still sees a quoted value, just not the secret."""
        tail = fake_tail()
        for line, expected in (
            (f'OPENAI_API_KEY="{tail} x"', 'OPENAI_API_KEY="[REDACTED:assignment]"'),
            (f"CLIENT_SECRET='{tail} x'", "CLIENT_SECRET='[REDACTED:assignment]'"),
        ):
            with self.subTest(line=line.split("=")[0]):
                after, _ = self.scrub(line)
                self.assertEqual(after, expected)

    def test_an_already_redacted_value_is_not_re_redacted(self) -> None:
        """The negative lookahead now has to cover three spellings of an
        already-scrubbed value, not one."""
        for line in (
            "PASSWORD=[REDACTED:assignment]",
            'API_KEY="[REDACTED:assignment]"',
            "CLIENT_SECRET='[REDACTED:assignment]'",
            r'{"out": "API_KEY=\"[REDACTED:assignment]\""}',
        ):
            with self.subTest(line=line):
                after, shapes = self.scrub(line)
                self.assertEqual(after, line)
                self.assertEqual(shapes, [])

    # ---- direction 2 (the crux): nothing innocent moved ----

    def test_innocent_lines_survive_byte_identical(self) -> None:
        """The widened value class must not reach one byte further than the
        value. These are the shapes ordinary evidence is made of."""
        innocent = (
            "total_tokens=41892",
            "input_tokens=5000, output_tokens=120",
            "path=/some/dir",
            'note="hello world"',
            "model=claude-sonnet-4",
            'log: user said "the password is wrong" and retried',
            json.dumps({"model": "claude", "note": "see docs"}),
            json.dumps({"usage": {"total_tokens": 41892}, "note": "don't worry"}),
            "api_key_name=OPENAI_API_KEY_ALT",
            "password_field=pw1",
        )
        for line in innocent:
            with self.subTest(line=line):
                after, shapes = self.scrub(line)
                self.assertEqual(after, line, "innocent content was rewritten")
                self.assertEqual(shapes, [])

    def test_only_the_value_is_redacted_not_what_follows_it(self) -> None:
        """An unquoted value still stops DEAD at whitespace, and a quoted one
        at its closing quote -- so a trailing non-secret token on the same
        line, and the rest of the file, survive."""
        tail = fake_tail()
        # NB: the survivor is spelled `next_field`, not `trailing_token` --
        # `..._token` IS a sensitive name tail, so that spelling would be a
        # correctly-redacted second assignment, not a survivor.
        cases = (
            (f"PASSWORD={tail} next_field=keepme", "next_field=keepme"),
            (f'API_KEY="{tail}" next_field=keepme', "next_field=keepme"),
            (f"PASSWORD=abc\\{tail} next_field=keepme", "next_field=keepme"),
            (f"PASSWORD=abcd\"{tail} next_field=keepme", "next_field=keepme"),
        )
        for line, survivor in cases:
            with self.subTest(line=line.split("=")[0]):
                after, _ = self.scrub(line)
                self.assertNotIn(tail, after)
                self.assertIn(survivor, after)

    def test_a_serialized_env_dump_loses_only_the_secret_line(self) -> None:
        """The incident's own shape, and the sharpest over-redaction trap: an
        ENTIRE env dump serialized onto ONE JSON line, its lines separated by
        `\\n` ESCAPES rather than real newlines. A value class that treated a
        backslash as ordinary would swallow the whole dump."""
        tail = fake_tail()
        line = json.dumps(
            {"result": f"MY_PASSWORD={tail}\nPATH=/usr/bin\nHOME=/root\ntotal_tokens=41892"}
        )
        after, shapes = self.scrub(line)
        self.assertNotIn(tail, after)
        self.assertEqual(shapes, ["assignment:MY_PASSWORD"])
        parsed = json.loads(after)
        self.assertIn("PATH=/usr/bin", parsed["result"])
        self.assertIn("HOME=/root", parsed["result"])
        self.assertIn("total_tokens=41892", parsed["result"])

    def test_a_redaction_never_crosses_a_json_string_boundary(self) -> None:
        """The write-seam invariant, proven here at the canonical door: the
        redacted line still PARSES and every field other than the one holding
        the secret is byte-identical.

        This is not decoration. The persister that shares this rule re-parses
        its redacted line and WITHHOLDS the payload when it no longer parses,
        so a rule that crosses a string boundary costs the whole event.
        """
        tail = fake_tail()
        payloads = (
            f"MY_PASSWORD={tail}",
            f'MY_PASSWORD="{tail} with spaces"',
            f"MY_PASSWORD='{tail}'",
            f"MY_PASSWORD=abc\\{tail}",
            f'MY_PASSWORD=abcd"{tail}',
            f"MY_PASSWORD='{tail}",  # unterminated single quote
            f'MY_PASSWORD="{tail}',  # unterminated double quote
            f"MY_PASSWORD={tail}\\",  # value ends on a backslash
            f'MY_PASSWORD={tail}"',  # value ends on a quote
            f"MY_PASSWORD={tail}\nPATH=/usr/bin",
        )
        for payload in payloads:
            with self.subTest(payload=payload.replace(tail, "<tail>")):
                record = {
                    "event": "tool:post",
                    "data": {"result": payload},
                    "note": 'keep me: "quoted", don\'t drop, ends with \\',
                    "n": 7,
                }
                after, _ = self.scrub(json.dumps(record))
                parsed = json.loads(after)  # raises if the boundary was crossed
                self.assertEqual(list(parsed), list(record))
                self.assertEqual(parsed["note"], record["note"])
                self.assertEqual(parsed["n"], record["n"])
                self.assertNotIn(tail, after)

    # ---- post-review regression: the backslash-run ReDoS and its twin ----
    #
    # Found by #292's OWN adversarial review, before merge. The widened
    # branch (c) joined a backslash in two ways -- the atomic pair `\\` and
    # the lone alternative -- and NOTHING said the lone one could not also
    # claim a backslash that was followed by another backslash. That made a
    # backslash AMBIGUOUS, so a run of N of them had Fibonacci-many tilings,
    # and the trailing `(?<!\\)` rejects every tiling that ends on a
    # backslash, so the engine enumerated the lot. ONE root cause, TWO
    # symptoms, so ONE probe family pins both. Measured at PR #292's head,
    # before the `[\s\\]` fence:
    #
    #   PASSWORD= + 40 backslashes (raw)                     15.8 s
    #   serialized event, secret + 20 trailing backslashes   18.4 s
    #   ...and MY_PASSWORD=<secret>\ + \n + PATH=/usr/bin
    #      redacted the PATH LINE AWAY (parity shift across the escape)
    #
    # After: 4 ms for a 40,000-backslash run, and the PATH line survives.

    # A wall-clock budget, not a benchmark: the fixed cost of these probes is
    # microseconds, so ANY number near this bound means the tilings came
    # back. 500x headroom over the measured 4 ms keeps it immune to CI
    # jitter while still being unreachable for an exponential rule.
    REDOS_BUDGET_S = 2.0

    def assert_fast(self, text: str, label: str) -> tuple[str, list[str]]:
        """Scrub `text`, failing if it did not finish inside the budget."""
        started = time.perf_counter()
        after, shapes = self.scrub(text)
        elapsed = time.perf_counter() - started
        self.assertLess(
            elapsed,
            self.REDOS_BUDGET_S,
            f"{label}: took {elapsed:.3f}s (budget {self.REDOS_BUDGET_S}s) -- "
            "the backslash joiner is ambiguous again and the run is being "
            "re-tiled exponentially",
        )
        return after, shapes

    def test_a_backslash_run_does_not_blow_up_the_matcher(self) -> None:
        """The ReDoS itself, on the raw and the serialized shape.

        The ladder is ordered SMALLEST-FIRST on purpose: a regressed rule
        blows the budget on the first probe in ~16s and never reaches the
        40,000-backslash one (which, ambiguous, would not finish this
        century). A test that FAILS beats a test that HANGS.
        """
        tail = fake_tail()
        probes = (
            ("raw, 40 trailing backslashes", "PASSWORD=" + "\\" * 40),
            (
                "serialized event, 20 trailing backslashes",
                json.dumps({"output": f"DB_PASSWORD={tail}" + "\\" * 20}),
            ),
            (
                "serialized event, 40 trailing backslashes",
                json.dumps({"output": f"DB_PASSWORD={tail}" + "\\" * 40}),
            ),
            ("raw, 40000 backslashes (linearity)", "PASSWORD=" + "\\" * 40000),
        )
        for label, probe in probes:
            with self.subTest(probe=label):
                after, _ = self.assert_fast(probe, label)
                self.assertNotIn(tail, after)

    def test_a_secret_ending_in_backslashes_still_redacts(self) -> None:
        """Direction 1 is not allowed to regress into a non-redaction: the
        value must still GO, raw and serialized, however many backslashes
        trail it."""
        tail = fake_tail()
        for k in (1, 2, 3, 20, 40):
            with self.subTest(backslashes=k):
                raw = f"PASSWORD={tail}" + "\\" * k
                after, shapes = self.assert_fast(raw, f"raw k={k}")
                self.assertIn("[REDACTED:assignment]", after)
                self.assertNotIn(tail, after)
                self.assertEqual(shapes, ["assignment:PASSWORD"])

                line = json.dumps({"output": f"DB_PASSWORD={tail}" + "\\" * k})
                after, shapes = self.assert_fast(line, f"serialized k={k}")
                self.assertIn("[REDACTED:assignment]", after)
                self.assertNotIn(tail, after)
                self.assertEqual(shapes, ["assignment:DB_PASSWORD"])
                json.loads(after)  # raises if the escape run was left dangling

    def test_a_secret_ending_in_a_backslash_does_not_eat_the_next_line(self) -> None:
        r"""The OVER-REDACTION half of the same defect, pinned separately
        because it is the one a timing budget would never catch.

        `MY_PASSWORD=<secret>\` + `\n` + `PATH=/usr/bin`: serialized, the
        secret's own backslash and the separator escape sit adjacent, and an
        odd tiling used to pair the SECOND and THIRD backslashes -- leaving
        the separator's `n` to be eaten as ordinary value material, taking
        the whole PATH line with it. The PATH/HOME-survive invariant this
        file states two screens up was, in that shape, false.
        """
        tail = fake_tail()
        for k in (1, 2, 3, 5):
            with self.subTest(backslashes=k):
                dump = (
                    f"MY_PASSWORD={tail}" + "\\" * k
                    + "\nPATH=/usr/bin\nHOME=/root\ntotal_tokens=41892"
                )
                after, shapes = self.assert_fast(json.dumps({"result": dump}), "dump")
                self.assertNotIn(tail, after)
                self.assertEqual(shapes, ["assignment:MY_PASSWORD"])
                result = json.loads(after)["result"]
                self.assertIn("[REDACTED:assignment]", result)
                self.assertIn("PATH=/usr/bin", result)
                self.assertIn("HOME=/root", result)
                self.assertIn("total_tokens=41892", result)

    def test_capsule_gate_token_math_still_survives_the_widened_rule(self) -> None:
        """The 2026-08-13 corruption class, re-run against the WIDER value
        rule: the name anchor is what protects these, and widening the value
        must not have quietly given the name back its reach."""
        original = "\n".join(CAPSULE_GATE_LINES_FROM_PR_205) + "\n"
        after, shapes = self.scrub(original)
        self.assertEqual(after, original)
        self.assertEqual(shapes, [])

    def test_known_residual_single_quoted_value_holding_a_double_quote(self) -> None:
        """An honestly-pinned RESIDUAL, not a claim of completeness.

        The single-quoted branch excludes `"` from its content so that an
        unterminated `'` inside a JSON string cannot run past that string's
        closing `"` and delete an unrelated field. The price is that a
        single-quoted value which itself contains a double quote is not
        covered. Unchanged from the pre-#289 rule (which also missed it), so
        this is a residual, not a regression -- and it is a NON-redaction,
        never an over-redaction.
        """
        tail = fake_tail()
        line = f"CLIENT_SECRET='he said \"{tail}\"'"
        after, shapes = self.scrub(line)
        self.assertEqual(after, line)  # residual: not redacted
        self.assertEqual(shapes, [])
        # The gate's entropy layer is the backstop for material like this;
        # what matters for THIS rule is that it does not over-reach instead.

if __name__ == "__main__":
    unittest.main()
