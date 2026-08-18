"""Write-time secret redaction at the session-event persistence seam (issue #198).

Incident, 2026-08-11: a worker agent ran a tool that dumped its environment;
the ``SessionEventPersister`` wrote the ``tool:post`` payload VERBATIM to
``<stage_dir>/sessions/<id>/events.jsonl`` -- including a literal
``OPENAI_API_KEY`` value -- and CI uploaded that file inside a PUBLIC
run-evidence artifact.

These tests pin all four directions the fix has to hold at once:

  1. secret-shaped material is REDACTED, and the redaction is LOUD;
  2. a runtime-random INNOCENT value SURVIVES VERBATIM (no over-redaction --
     the crux: a blanket scrub, or an entropy-based one, would strip the
     legitimate observability Section 26 exists to produce);
  3. redaction happens AT THE WRITE SEAM -- the bytes handed to the file
     object are already clean -- not as a post-hoc pass over a file that
     briefly held the credential;
  4. if the redaction machinery itself fails, the payload is WITHHELD rather
     than written raw (fail-loud, never fall back to unsafe).

NOTHING CREDENTIAL-SHAPED IS HARDCODED HERE.  Every fake secret is minted at
runtime from ``secrets``: a long literal in this file would itself be
secret-shaped material committed to the repo, which the leak scan correctly
refuses, and a test that ships a real-looking key teaches the wrong habit.
"""

from __future__ import annotations

import builtins
import importlib.util
import json
import secrets
import string
import time
from pathlib import Path

import pytest

from amplifier_module_hooks_pipeline_observability import redaction, session_events
from amplifier_module_hooks_pipeline_observability.session_events import (
    SessionEventPersister,
)

# The env-var name is ASSEMBLED rather than written out, so this source file
# never itself contains a `<SENSITIVE_NAME>=<value>` assignment for the leak
# scan to (correctly) flag.
_SECRET_ENV_NAME = "OPENAI_API" + "_KEY"
_PLURAL_ENV_NAME = "total" + "_tokens"


def _fake_openai_key() -> str:
    """A shape-valid but entirely FAKE OpenAI key, minted per test run."""
    return "sk-proj-" + secrets.token_hex(24)


def _innocent_high_entropy_token() -> str:
    """A random value that is NOT secret-shaped -- the AC-4 probe.

    Deliberately built from letters+digits ONLY: with no ``-`` and no ``_``
    in the alphabet, this string structurally CANNOT contain ``sk-``,
    ``gh?_`` or ``github_pat_``, so the no-over-redaction assertion can
    never flake on an unlucky draw.  At 43 characters of mixed-case
    alphanumeric it is exactly the shape of a provider request id or
    correlation token -- ordinary observability, and (see
    ``test_the_innocent_value_is_one_an_entropy_scan_would_have_eaten``)
    exactly what the canonical layer-4 entropy heuristic WOULD destroy.
    """
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(43))


def _env_dump(fake_key: str, innocent: str) -> str:
    """The incident's payload shape: a tool result holding an env dump.

    The credential is NESTED inside a result string, not a top-level field --
    that nesting is what a structure-only redactor misses.
    """
    return (
        "SHELL=/bin/bash\n"
        f"{_SECRET_ENV_NAME}={fake_key}\n"
        f"REQUEST_ID={innocent}\n"
        f"{_PLURAL_ENV_NAME}=41892\n"
        "TERM=xterm\n"
    )


def _read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


async def _persist_env_dump(sessions: Path, fake_key: str, innocent: str) -> Path:
    """Drive one incident-shaped ``tool:post`` through the real persister."""
    persister = SessionEventPersister(lambda: str(sessions))
    await persister.make_handler("tool:post")(
        "tool:post",
        {
            "session_id": "sid-198",
            "tool_name": "bash",
            "tool_input": {"command": "env"},
            "result": _env_dump(fake_key, innocent),
            "call_id": "c1",
        },
    )
    return sessions / "sid-198" / "events.jsonl"


# ---------------------------------------------------------------------------
# (a) Secret-shaped material is redacted, and the redaction is LOUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_secret_shaped_material_is_redacted_and_the_redaction_is_loud(tmp_path):
    """The incident, replayed: the key must not reach disk, and the scrubbed
    record must be DISTINGUISHABLE from a clean one."""
    fake_key = _fake_openai_key()
    innocent = _innocent_high_entropy_token()

    path = await _persist_env_dump(tmp_path / "sessions", fake_key, innocent)
    raw = path.read_text()

    # The leak itself: gone from the bytes on disk.
    assert fake_key not in raw
    assert fake_key.split("sk-proj-")[1] not in raw  # not even the key body

    # LOUD, signal one: the inline marker sits exactly where the value was.
    record = _read_events(path)[0]
    assert f"{_SECRET_ENV_NAME}=[REDACTED:openai-key]" in record["data"]["result"]

    # LOUD, signal two: a top-level counter naming what was removed. The
    # issue's own acceptance language -- "a counter in the event or a
    # companion note ... never silent."
    assert record["redaction"]["count"] >= 1
    assert "openai-key" in record["redaction"]["shapes"]

    # The record is still a well-formed Section 26 record: same keys, same
    # nesting, same neighbouring fields -- only the credential is missing.
    assert record["event"] == "tool:post"
    assert record["data"]["tool_name"] == "bash"
    assert record["data"]["tool_input"]["command"] == "env"
    assert record["data"]["call_id"] == "c1"
    assert "SHELL=/bin/bash" in record["data"]["result"]


@pytest.mark.asyncio
async def test_a_clean_event_is_byte_identical_to_the_pre_fix_shape(tmp_path):
    """No redaction -> no indicator. A scrubbed dump must stand out, which
    only works if a clean one is unmarked (and existing session tooling that
    reads event/timestamp/data keeps reading these files unchanged)."""
    sessions = tmp_path / "sessions"
    persister = SessionEventPersister(lambda: str(sessions))
    await persister.make_handler("tool:post")(
        "tool:post",
        {"session_id": "sid-clean", "tool_name": "bash", "result": "4 passed"},
    )

    record = _read_events(sessions / "sid-clean" / "events.jsonl")[0]
    assert "redaction" not in record
    assert sorted(record) == ["data", "event", "timestamp"]
    assert record["data"]["result"] == "4 passed"


@pytest.mark.asyncio
async def test_a_shapeless_sensitive_assignment_redacts_as_assignment(tmp_path):
    """The second ported shape: `<NAME ending in a sensitive tail>=<value>`
    is redacted even when the value carries no recognizable token prefix."""
    sessions = tmp_path / "sessions"
    persister = SessionEventPersister(lambda: str(sessions))
    opaque = secrets.token_hex(12)  # no vendor prefix -- shape-free value
    name = "SOME_SERVICE" + "_TOKEN"
    await persister.make_handler("tool:post")(
        "tool:post",
        {"session_id": "sid-a", "result": f"{name}={opaque}\n"},
    )

    record = _read_events(sessions / "sid-a" / "events.jsonl")[0]
    assert opaque not in json.dumps(record)
    assert f"{name}=[REDACTED:assignment]" in record["data"]["result"]
    assert record["redaction"]["shapes"] == [f"assignment:{name}"]


# ---------------------------------------------------------------------------
# (b) NO OVER-REDACTION -- the crux
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_innocent_runtime_random_value_survives_verbatim(tmp_path):
    """AC-4: a random but NOT-secret-shaped value must survive the write
    UNTOUCHED, in the same event that had a real credential stripped.

    This is the pin against the two tempting wrong fixes: replacing the whole
    payload, and redacting on entropy.  Either would pass the leak test above
    and silently destroy the forensic record Section 26 exists to create.
    """
    fake_key = _fake_openai_key()
    innocent = _innocent_high_entropy_token()

    path = await _persist_env_dump(tmp_path / "sessions", fake_key, innocent)
    record = _read_events(path)[0]

    # Survives verbatim -- byte for byte, in place, in the same string that
    # had the credential removed two lines above it.
    assert innocent in path.read_text()
    assert f"REQUEST_ID={innocent}" in record["data"]["result"]

    # ...and so does the documented false-positive class the canonical
    # assignment rule was end-anchored to protect (`total_tokens=`, not a
    # credential name -- PR #205 corrupted a shipped artifact over this).
    assert f"{_PLURAL_ENV_NAME}=41892" in record["data"]["result"]

    # Exactly ONE span was removed: the credential, and nothing else.
    assert record["redaction"]["count"] == 1


def test_the_innocent_value_is_one_an_entropy_scan_would_have_eaten():
    """The 'shape-targeted, not entropy' decision, MEASURED rather than asserted.

    Loads the canonical layer-4 heuristic and shows the AC-4 probe from the
    test above is a value that heuristic WOULD have redacted.  That is the
    whole argument for not porting layer 4 to the write seam: at the upload
    door a false positive costs one run's evidence (measured at 4 runs out of
    4, issue #206); at the WRITE seam it costs the evidence permanently,
    because the original bytes are never written down at all.
    """
    canonical = _load_canonical_scrubber()
    innocent = _innocent_high_entropy_token()

    # The heuristic would have flagged it...
    assert canonical._entropy_suspicious(innocent)
    # ...and the shipped, shape-targeted redactor leaves it alone.
    cleaned, findings = redaction.redact_text(innocent)
    assert cleaned == innocent
    assert findings == []

    # Same story for the legitimate observability the heuristic DOES exclude,
    # kept here so the contrast is on the record rather than assumed.
    for benign in (secrets.token_hex(20), "b3f1c2d4-5e6a-7b8c-9d0e-1f2a3b4c5d6e"):
        assert redaction.redact_text(benign) == (benign, [])


# ---------------------------------------------------------------------------
# (c) The redaction is AT THE WRITE SEAM, not a post-hoc scrub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redaction_is_at_the_write_seam_not_a_post_hoc_pass(
    tmp_path, monkeypatch
):
    """The bytes handed to the file object are ALREADY redacted.

    A post-hoc scrubber (the workflow-level guard that already exists) writes
    the credential and cleans it afterwards, so the file transiently holds a
    live key and anything reading it in that window -- or reading it on a
    path where the scrubber never runs -- sees the secret.  This test proves
    the value never becomes file bytes at all: events.jsonl is opened exactly
    ONCE, in append mode, and the single string passed to write() is already
    clean.  A re-write pass would show up here as a second open.
    """
    real_open = builtins.open
    opens: list[tuple[str, str]] = []
    writes: list[str] = []

    class _SpyFile:
        def __init__(self, fh):
            self._fh = fh

        def write(self, data):
            writes.append(data)
            return self._fh.write(data)

        def __enter__(self):
            self._fh.__enter__()
            return self

        def __exit__(self, *exc):
            return self._fh.__exit__(*exc)

        def __getattr__(self, name):
            return getattr(self._fh, name)

    def _spy_open(file, mode="r", *args, **kwargs):
        fh = real_open(file, mode, *args, **kwargs)
        if str(file).endswith("events.jsonl"):
            opens.append((str(file), mode))
            return _SpyFile(fh)
        return fh

    fake_key = _fake_openai_key()
    innocent = _innocent_high_entropy_token()

    monkeypatch.setattr(builtins, "open", _spy_open)
    path = await _persist_env_dump(tmp_path / "sessions", fake_key, innocent)
    monkeypatch.undo()

    assert [mode for _, mode in opens] == ["a"], (
        "events.jsonl must be opened exactly once, append-only -- a second "
        "open (or a 'w'/'r+' mode) would mean the credential hit disk first "
        "and was cleaned afterwards, which is the leak this fix closes."
    )
    assert len(writes) == 1
    assert fake_key not in writes[0], "the credential never becomes file bytes"
    assert "[REDACTED:openai-key]" in writes[0]
    assert innocent in writes[0]

    # And the byte stream is exactly what landed on disk.
    assert path.read_text() == writes[0]


# ---------------------------------------------------------------------------
# (d) Fail-loud: a broken redactor withholds the payload, never writes it raw
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_redaction_failure_withholds_the_payload_instead_of_writing_it_raw(
    tmp_path, monkeypatch, caplog
):
    """If the redaction machinery throws, falling back to the raw write would
    resurrect the exact leak. The house rule is fail-loud, not
    fall-back-to-unsafe: the payload is withheld and a marker records why."""
    fake_key = _fake_openai_key()
    innocent = _innocent_high_entropy_token()

    def _boom(_text: str):
        # The message quotes the credential ON PURPOSE: an exception message
        # is untrusted content, and the marker must not echo it to disk.
        raise RuntimeError(f"redactor exploded while handling {fake_key}")

    monkeypatch.setattr(session_events, "redact_text", _boom)
    path = await _persist_env_dump(tmp_path / "sessions", fake_key, innocent)

    raw = path.read_text()
    assert fake_key not in raw
    assert "redactor exploded" not in raw  # only the exception TYPE is recorded
    assert innocent not in raw  # the whole payload is withheld, not just the key

    record = _read_events(path)[0]
    assert record["redaction"] == {"error": "RuntimeError", "payload_withheld": True}
    # Still correlatable: which event, which session, when.
    assert record["event"] == "tool:post"
    assert record["data"] == {"session_id": "sid-198"}
    assert record["timestamp"]

    # Loud in the process log too, at ERROR -- not swallowed at debug.
    assert any(
        r.levelname == "ERROR" and "WITHHELD" in r.getMessage() for r in caplog.records
    )


# ---------------------------------------------------------------------------
# (e) Issue #289: values that begin with, or contain, a quote/backslash/space
# ---------------------------------------------------------------------------
#
# The pre-#289 value class `[^\s"'\\]{4,}` stopped at the first whitespace,
# quote or backslash, so a secret carrying one of those in its first few
# characters escaped BOTH doors -- this seam and the canonical upload gate.
# The widened grammar is shared (the tripwire below pins it), so it is proven
# HERE too: the write seam is the door where an over-reaching rule costs the
# most, because the persister re-parses its own output and WITHHOLDS a payload
# that no longer parses.


def _escape_case_payloads(tail: str) -> dict[str, str]:
    """Issue #289's named cases plus the variants they generalize to.

    `tail` is minted by the caller from ``secrets`` -- nothing credential-
    shaped is written down in this file.
    """
    name = "MY" + "_PASSWORD"
    key = "SOME_API" + "_KEY"
    return {
        "backslash-inside": f"{name}=abc\\{tail}",
        "apostrophe-inside": f"{name}=abc'{tail}",
        "double-quote-inside": f'{name}=abcd"{tail}',
        "leading-backslash": f"{name}=\\{tail}",
        "double-quoted-with-spaces": f'{key}="{tail} and more"',
        "single-quoted": f"{key}='{tail}'",
        "windows-path-shaped": f"{name}=C:\\Secrets\\{tail}",
    }


def test_issue_289_escape_cases_redact_at_the_write_seam():
    """Direction 1: every escapee is now redacted by the ported rule."""
    tail = secrets.token_hex(8)
    for case, payload in _escape_case_payloads(tail).items():
        cleaned, findings = redaction.redact_text(payload)
        assert tail not in cleaned, f"{case}: the value survived redaction"
        assert redaction.REDACTION_MARKER_PREFIX in cleaned, case
        assert findings and all(f.startswith("assignment:") for f in findings), case


def test_issue_289_the_widened_rule_still_leaves_innocent_content_verbatim():
    """Direction 2 (the crux): nothing innocent moves.

    A value class that reached one byte further than the value would corrupt
    the forensic record this seam exists to produce -- and at THIS door the
    original bytes are never written, so there is nothing left to recover.
    """
    innocent = (
        f"{_PLURAL_ENV_NAME}=41892",
        "path=/some/dir",
        'note="hello world"',
        "model=claude-sonnet-4",
        'the user wrote "my password is wrong" in the ticket',
        json.dumps({"model": "claude", "note": "see docs"}),
        json.dumps({"usage": {"total_tokens": 41892}, "note": "don't worry"}),
    )
    for line in innocent:
        assert redaction.redact_text(line) == (line, []), line


def test_issue_289_a_redaction_never_crosses_a_json_string_boundary():
    """The invariant this seam actually depends on, exercised on the shapes
    that would break it: the redacted line still PARSES, and every field
    other than the one holding the secret is byte-identical."""
    tail = secrets.token_hex(8)
    payloads = list(_escape_case_payloads(tail).values()) + [
        f"MY{'_PASSWORD'}='{tail}",  # unterminated single quote
        f'MY{"_PASSWORD"}="{tail}',  # unterminated double quote
        f"MY{'_PASSWORD'}={tail}\\",  # value ends on a backslash
        f'MY{"_PASSWORD"}={tail}"',  # value ends on a quote
        f"MY{'_PASSWORD'}={tail}\nPATH=/usr/bin\nHOME=/root",  # env dump
    ]
    for payload in payloads:
        record = {
            "event": "tool:post",
            "data": {"result": payload},
            "note": 'keep me: "quoted", don\'t drop, ends with \\',
            "n": 7,
        }
        line = json.dumps(record, ensure_ascii=False)
        cleaned, _ = redaction.redact_text(line)
        parsed = json.loads(cleaned)  # raises if a string boundary was crossed
        assert list(parsed) == list(record)
        assert parsed["note"] == record["note"]
        assert parsed["n"] == record["n"]
        assert tail not in cleaned


def test_issue_289_a_serialized_env_dump_loses_only_the_secret():
    """The incident's own shape and the sharpest over-redaction trap: a whole
    env dump on ONE serialized line, its records separated by `\\n` ESCAPES.
    A rule that treated a backslash as ordinary would swallow the lot."""
    tail = secrets.token_hex(8)
    name = "MY" + "_PASSWORD"
    line = json.dumps(
        {"result": f"{name}={tail}\nPATH=/usr/bin\nHOME=/root\n{_PLURAL_ENV_NAME}=41892"}
    )
    cleaned, findings = redaction.redact_text(line)
    assert tail not in cleaned
    assert findings == [f"assignment:{name}"]
    result = json.loads(cleaned)["result"]
    assert "PATH=/usr/bin" in result
    assert "HOME=/root" in result
    assert f"{_PLURAL_ENV_NAME}=41892" in result


# ---------------------------------------------------------------------------
# (f) Post-review regression: the backslash-run ReDoS, and its over-redacting
#     twin.  ONE root cause, so the probes live together.
# ---------------------------------------------------------------------------
#
# Found by PR #292's OWN adversarial review, before merge.  Branch (c) joins a
# backslash two ways -- the atomic pair `\\` and the lone alternative -- and
# nothing stopped the LONE one from also claiming a backslash that was
# followed by another backslash.  A backslash was therefore AMBIGUOUS, a run
# of N of them had Fibonacci-many tilings, and the trailing `(?<!\\)` rejects
# every tiling that ends on a backslash, so the engine enumerated them all.
#
# This seam is where that costs the most: the input is attacker-influenced
# TOOL OUTPUT, arriving on the hot write path, and the realistic carrier is
# mundane -- a Windows path, an escaped blob, any `NAME=` followed by a
# backslash run.  Measured HERE at PR #292's head, before the `[\s\\]` fence:
#
#   raw `PASSWORD=` + 40 backslashes                     15.8 s
#   serialized event, secret + 20 trailing backslashes   18.4 s
#
# ...and the SAME ambiguity shifted parity across a following `\n` escape,
# swallowing the `PATH=/usr/bin` line that section (e) promises survives.
# After the fence: a 40,000-backslash run returns in ~4 ms, and PATH survives.

#: Wall-clock budget, not a benchmark.  The honest cost of these probes is
#: microseconds; 500x headroom over the measured 4 ms keeps this immune to CI
#: jitter while staying unreachable for an exponentially-backtracking rule.
_REDOS_BUDGET_S = 2.0


def _redact_within_budget(text: str, label: str) -> tuple[str, list[str]]:
    """Redact `text`, failing if it did not finish inside the budget."""
    started = time.perf_counter()
    cleaned, findings = redaction.redact_text(text)
    elapsed = time.perf_counter() - started
    assert elapsed < _REDOS_BUDGET_S, (
        f"{label}: took {elapsed:.3f}s (budget {_REDOS_BUDGET_S}s) -- the "
        "backslash joiner is ambiguous again and the run is being re-tiled "
        "exponentially"
    )
    return cleaned, findings


def test_a_backslash_run_does_not_blow_up_the_write_seam():
    """The ReDoS itself, raw and serialized.

    The ladder is SMALLEST-FIRST on purpose: a regressed rule blows the
    budget on the first probe in ~16s and never reaches the 40,000-backslash
    one (which, ambiguous, would not finish this century).  A test that FAILS
    beats a test that HANGS.
    """
    tail = secrets.token_hex(8)
    name = "MY" + "_PASSWORD"
    probes = (
        ("raw, 40 trailing backslashes", "PASSWORD=" + "\\" * 40),
        (
            "serialized event, 20 trailing backslashes",
            json.dumps({"output": f"{name}={tail}" + "\\" * 20}),
        ),
        (
            "serialized event, 40 trailing backslashes",
            json.dumps({"output": f"{name}={tail}" + "\\" * 40}),
        ),
        ("raw, 40000 backslashes (linearity)", "PASSWORD=" + "\\" * 40000),
    )
    for label, probe in probes:
        cleaned, _ = _redact_within_budget(probe, label)
        assert tail not in cleaned, label


def test_a_secret_ending_in_backslashes_still_redacts_at_the_write_seam():
    """Direction 1 must not regress into a NON-redaction: the value still
    goes, however many backslashes trail it, and the record still parses."""
    tail = secrets.token_hex(8)
    name = "MY" + "_PASSWORD"
    for k in (1, 2, 3, 20, 40):
        raw = f"{name}={tail}" + "\\" * k
        cleaned, findings = _redact_within_budget(raw, f"raw k={k}")
        assert tail not in cleaned, k
        assert redaction.REDACTION_MARKER_PREFIX in cleaned, k
        assert findings == [f"assignment:{name}"], k

        line = json.dumps({"output": f"{name}={tail}" + "\\" * k})
        cleaned, findings = _redact_within_budget(line, f"serialized k={k}")
        assert tail not in cleaned, k
        assert findings == [f"assignment:{name}"], k
        json.loads(cleaned)  # raises if the escape run was left dangling


def test_a_secret_ending_in_a_backslash_does_not_eat_the_next_line():
    r"""The OVER-REDACTION half of the same defect -- the half a timing
    budget would never catch.

    A sensitive `<NAME>` assigned `<secret>\`, then `\n`, then
    `PATH=/usr/bin`, serialized (the name is not spelled out here: this
    file's own rule is that it never contains a literal
    `<SENSITIVE_NAME>=<value>` for the leak scan to flag). The
    secret's own backslash and the separator escape sit adjacent, and an odd
    tiling used to pair the SECOND and THIRD backslashes, leaving the
    separator's `n` to be eaten as ordinary value material -- taking the
    whole PATH line with it.  At THIS seam the original bytes are never
    written, so an over-redaction here is not recoverable.
    """
    tail = secrets.token_hex(8)
    name = "MY" + "_PASSWORD"
    for k in (1, 2, 3, 5):
        dump = (
            f"{name}={tail}" + "\\" * k
            + f"\nPATH=/usr/bin\nHOME=/root\n{_PLURAL_ENV_NAME}=41892"
        )
        cleaned, findings = _redact_within_budget(
            json.dumps({"result": dump}), f"env dump k={k}"
        )
        assert tail not in cleaned, k
        assert findings == [f"assignment:{name}"], k
        result = json.loads(cleaned)["result"]
        assert redaction.REDACTION_MARKER_PREFIX in result, k
        assert "PATH=/usr/bin" in result, k
        assert "HOME=/root" in result, k
        assert f"{_PLURAL_ENV_NAME}=41892" in result, k


@pytest.mark.asyncio
async def test_a_quoted_secret_redacts_at_the_write_seam(tmp_path):
    """End to end through the real persister (the #288 seam), on a value the
    pre-#289 rule truncated at the first space: the file must never hold the
    secret, the record must still parse, and the quotes must survive so the
    line stays readable."""
    sessions = tmp_path / "sessions"
    persister = SessionEventPersister(lambda: str(sessions))
    tail = secrets.token_hex(8)
    name = "SOME_SERVICE" + "_TOKEN"
    await persister.make_handler("tool:post")(
        "tool:post",
        {"session_id": "sid-289", "result": f'{name}="{tail} with spaces"\n'},
    )

    path = sessions / "sid-289" / "events.jsonl"
    assert tail not in path.read_text()
    record = _read_events(path)[0]
    assert f'{name}="[REDACTED:assignment]"' in record["data"]["result"]
    assert record["redaction"]["shapes"] == [f"assignment:{name}"]


# ---------------------------------------------------------------------------
# Drift tripwire: the ported shapes must stay identical to the canonical set
# ---------------------------------------------------------------------------


def _load_canonical_scrubber():
    """Load ``.github/capsule-pipeline/scrub_secrets.py`` BY PATH.

    The engine module deliberately does NOT import this at runtime -- it is a
    workflow script under ``.github/``, stdlib-only so it runs on a bare
    Actions runner, and not a package on any import path.  A TEST may reach
    it, and that is what keeps "two copies" from becoming "two behaviors".
    """
    root = Path(__file__).resolve().parents[3]
    script = root / ".github" / "capsule-pipeline" / "scrub_secrets.py"
    if not script.exists():  # module installed standalone, outside the repo
        pytest.skip(f"canonical scrubber not present at {script}")
    spec = importlib.util.spec_from_file_location("_canonical_scrub", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ported_shapes_have_not_drifted_from_the_canonical_set():
    """The redaction shapes here are a COPY of the repo's canonical set, and
    a copy that is allowed to drift is worse than no copy at all."""
    canonical = _load_canonical_scrubber()

    assert [(s, p.pattern) for s, p in redaction.TOKEN_PATTERNS] == [
        (s, p.pattern) for s, p in canonical.TOKEN_PATTERNS
    ]
    assert redaction.SENSITIVE_NAME_TAILS == canonical.SENSITIVE_NAME_TAILS
    assert redaction.ASSIGNMENT_PATTERN.pattern == canonical.ASSIGNMENT_PATTERN.pattern
    assert redaction.ASSIGNMENT_PATTERN.flags == canonical.ASSIGNMENT_PATTERN.flags


def test_every_ported_shape_actually_redacts():
    """Each named shape is exercised, so a pattern cannot rot into a no-op."""
    cases = {
        "openai-key": "sk-proj-" + secrets.token_hex(24),
        "github-fine-grained-pat": "github_pat_" + secrets.token_hex(22),
        "github-token": "ghp_" + secrets.token_hex(20),
    }
    for shape, value in cases.items():
        cleaned, findings = redaction.redact_text(f"prefix {value} suffix")
        assert value not in cleaned
        assert cleaned == f"prefix [REDACTED:{shape}] suffix"
        assert findings == [shape]


def test_the_two_doors_do_not_merely_SHARE_a_pattern_they_BEHAVE_alike():
    """Pattern equality is necessary but not sufficient: the two copies also
    apply the pattern with their own substitution function, and a drift THERE
    would be invisible to the equality assertions above.

    So the same probe corpus goes through both doors and the REDACTED TEXT
    must match byte for byte -- including the issue #289 shapes, where the
    substitution has to re-emit an opening quote that comes from one of two
    alternative groups.
    """
    canonical = _load_canonical_scrubber()
    tail = secrets.token_hex(8)
    name = "MY" + "_PASSWORD"
    key = "SOME_API" + "_KEY"
    probes = [
        f"{name}={tail}",
        f"{name}=abc\\{tail}",
        f"{name}=abc'{tail}",
        f'{name}=abcd"{tail}',
        f'{key}="{tail} with spaces"',
        f"{key}='{tail}'",
        f"{name}=\\{tail}",
        f"{_PLURAL_ENV_NAME}=41892",
        'note="hello world"',
        json.dumps({"result": f"{name}={tail}\nPATH=/usr/bin"}),
        json.dumps({"result": f'{key}="{tail} x"', "note": "keep"}),
        "prefix sk-proj-" + secrets.token_hex(24) + " suffix",
        # ...and the post-review backslash-run corpus (section (f)): the
        # shapes that were exponential AND over-redacting must agree at both
        # doors too -- a fence applied to only ONE door would show up here as
        # well as in the pattern-equality tripwire above.
        f"{name}={tail}" + "\\",
        f"{name}={tail}" + "\\" * 2,
        f"{name}={tail}" + "\\" * 3,
        f"{name}={tail}" + "\\" * 40,
        "PASSWORD=" + "\\" * 40,
        json.dumps({"output": f"{name}={tail}" + "\\" * 20}),
        json.dumps({"result": f"{name}={tail}" + "\\" + "\nPATH=/usr/bin\nHOME=/root"}),
    ]
    for probe in probes:
        ported, ported_findings = redaction.redact_text(probe)
        canon, canon_shapes = canonical.scrub_text(probe, {})
        assert ported == canon, f"the two doors redacted {probe!r} differently"
        # The shape vocabularies are reported differently by design (the gate
        # de-duplicates per pattern, the seam counts spans), so compare the
        # SET of shapes, which is the part both doors promise.
        assert set(ported_findings) == set(canon_shapes), probe
