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
