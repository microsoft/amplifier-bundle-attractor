"""``OPENAI_BASE_URL`` is OPTIONAL, and it is not a secret.

Owner rulings, 2026-09-07:

  1. **Optional.** When the variable is absent the `luna` instance runs on the
     OpenAI module's own default endpoint ("the default should have been
     fine"). The preflight must NOT refuse for its absence. It still refuses,
     unchanged, for a missing ``OPENAI_API_KEY`` and for an instance the
     graphs address but nothing can serve (the issue-#155 / EXTENSIONS Sec 36
     guarantee: a run never starts unable to reach its second judge).
  2. **Not a secret.** It is an endpoint URL, set as a repository VARIABLE on
     both this repo and `amplifier-bundle-dot-runner`. The workflows therefore
     read ``vars`` first and fall back to ``secrets``, so an operator who
     previously stored it as a secret is not broken by the change.

WHY THESE ARE MECHANICAL. Both halves are the kind of rule that decays into
prose: "optional" reverts the moment someone re-adds the name to a required
list, and the vars-then-secrets expression reverts to ``secrets.`` the moment
someone copies a neighbouring line. Every assertion below reads the shipped
artifact -- the installer script actually executed in a throwaway HOME, the
workflow files as text, the scrubber run as a subprocess -- rather than a
description of it.

HERMETIC BY CONSTRUCTION. The installer is run with an explicitly built
environment (never ``os.environ``), against a fixture graph in a temp dir,
with ``AMPLIFIER_HOME`` pointed at a temp dir. No network, no real
credential, and a developer who happens to export ``OPENAI_BASE_URL`` in
their own shell cannot turn these green or red.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PIPELINE_DIR = _REPO_ROOT / ".github" / "capsule-pipeline"
_INSTALLER = _PIPELINE_DIR / "install_provider_instances.sh"
_TEMPLATE = _PIPELINE_DIR / "provider-instances.yaml"
_SCRUBBER = _PIPELINE_DIR / "scrub_secrets.py"
_WORKFLOW_DIR = _REPO_ROOT / ".github" / "workflows"

#: The workflows that provision and then use the `luna` instance.
_PIPELINE_WORKFLOWS = (
    "capsule-specify.yml",
    "feature-specify.yml",
    "capsule-implement.yml",
)

#: The one expression both repos use, character for character. `vars` first so
#: the repository VARIABLE wins; `secrets` second so an operator who stored it
#: as a secret before 2026-09-07 keeps working.
_BASE_URL_EXPRESSION = "${{ vars.OPENAI_BASE_URL || secrets.OPENAI_BASE_URL }}"

#: A value that is obviously not a real endpoint but is long enough to clear
#: the scrubber's ``MIN_LITERAL_LEN``.
_FAKE_BASE_URL = "https://luna.example.invalid/v1"
_FAKE_API_KEY = "sk-test-not-a-real-key-000000000000"


# ---------------------------------------------------------------------------
# Running the installer hermetically
# ---------------------------------------------------------------------------


def _fixture_graph(tmp_path: Path, instance: str = "luna") -> Path:
    """A minimal graph whose judgment node addresses *instance*.

    The installer derives the instance ids it must provision by grepping
    ``llm_provider="..."`` out of the graphs it is handed, so this is the
    whole contract a fixture needs.
    """
    path = tmp_path / "fixture.dot"
    path.write_text(
        "digraph Fixture {\n"
        '  critique_b [type="llm", llm_provider="%s", llm_model="gpt-5.6-luna"];\n'
        "}\n" % instance,
        encoding="utf-8",
    )
    return path


def _run_installer(
    tmp_path: Path,
    env_extra: dict[str, str],
    graph: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    if not _INSTALLER.is_file():
        pytest.fail(
            f"fail-closed: {_INSTALLER.relative_to(_REPO_ROOT)} is missing. The "
            "shipped pipelines address a provider instance and a GitHub-hosted "
            "runner has no Amplifier settings of its own; without this script no "
            "CI run can serve them."
        )
    home = tmp_path / "home"
    amp_home = home / ".amplifier"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "AMPLIFIER_HOME": str(amp_home),
    }
    env.update(env_extra)
    return subprocess.run(
        ["bash", str(_INSTALLER), str(graph or _fixture_graph(tmp_path))],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=60,
    )


def _installed_settings(tmp_path: Path) -> Path:
    return tmp_path / "home" / ".amplifier" / "settings.yaml"


# ---------------------------------------------------------------------------
# 1. Absent -> the instance is installed WITHOUT a base_url key
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_extra",
    [
        pytest.param({}, id="unset"),
        pytest.param({"OPENAI_BASE_URL": ""}, id="set-but-empty"),
    ],
)
def test_absent_base_url_installs_the_instance_without_the_key(
    tmp_path: Path, env_extra: dict[str, str]
) -> None:
    """Both shapes of "absent", because CI only ever produces the second one.

    ``OPENAI_BASE_URL: ${{ vars.X || secrets.X }}`` sets the variable to the
    EMPTY STRING when neither exists -- GitHub Actions does not drop the env
    entry. An installer that only handled a truly unset name would write
    ``base_url:`` with nothing after it on every real run.
    """
    result = _run_installer(tmp_path, {"OPENAI_API_KEY": _FAKE_API_KEY, **env_extra})

    assert result.returncode == 0, (
        "OPENAI_BASE_URL is OPTIONAL (owner ruling 2026-09-07): with the key "
        "present and only the endpoint absent, the installer must provision the "
        "instance on the provider module's default endpoint, not refuse.\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    settings = _installed_settings(tmp_path)
    assert settings.is_file(), (
        "the installer reported success but wrote no settings file at "
        f"{settings} -- the run would still be refused by the engine's startup "
        "preflight, exactly the failure this step exists to prevent early."
    )

    text = settings.read_text(encoding="utf-8")
    body = "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )
    assert "base_url" not in body, (
        "an absent OPENAI_BASE_URL must be OMITTED from the installed instance "
        "so the OpenAI module's default endpoint applies. Writing the key with "
        "an empty or unexpanded value instead is the silent-misconfiguration "
        "shape: the provider would fail at request time on an endpoint nobody "
        f"chose.\ninstalled file:\n{text}"
    )
    # The rest of the instance must survive -- an omission is not a rewrite.
    assert "id: luna" in body and "${OPENAI_API_KEY}" in body, (
        "the installed instance lost its id or its credential placeholder:\n"
        + text
    )

    assert (
        "OPENAI_BASE_URL is unset" in result.stdout
        and "default endpoint" in result.stdout
    ), (
        "the step log must SAY the endpoint was omitted and the module default "
        "applies. A silently different endpoint is the thing an operator reads "
        "the preflight log to rule out. The phrase is kept identical to "
        "amplifier-bundle-dot-runner's inline preflight ('<NAME> is unset'), so "
        "one grep finds the branch in either repo's job log.\nstdout:\n"
        + result.stdout
    )


def test_the_omission_is_surgical(tmp_path: Path) -> None:
    """RED-proof for the omission: exactly one line differs from the template.

    A ``grep -v`` over the whole file, or a rewrite of the template into some
    other shape, would both satisfy the assertions above while quietly
    dropping or reformatting something else. This states the exact edit.
    """
    _run_installer(tmp_path, {"OPENAI_API_KEY": _FAKE_API_KEY})

    template_lines = _TEMPLATE.read_text(encoding="utf-8").splitlines()
    dropped = [
        line for line in template_lines if line.strip() == "base_url: ${OPENAI_BASE_URL}"
    ]
    assert len(dropped) == 1, (
        "provider-instances.yaml must reference ${OPENAI_BASE_URL} on exactly "
        "one plain `key: ${VAR}` line -- that line shape is what the installer "
        "can omit. Found "
        f"{len(dropped)}."
    )
    expected = [
        line for line in template_lines if line.strip() != "base_url: ${OPENAI_BASE_URL}"
    ]
    actual = _installed_settings(tmp_path).read_text(encoding="utf-8").splitlines()
    assert actual == expected, (
        "with OPENAI_BASE_URL unset the installed file must be the template "
        "MINUS its one base_url line, byte for byte -- nothing else added, "
        "dropped, or reflowed."
    )


# ---------------------------------------------------------------------------
# 2. Present -> the key is written, as a placeholder, never as a value
# ---------------------------------------------------------------------------


def test_present_base_url_is_written_as_a_placeholder(tmp_path: Path) -> None:
    result = _run_installer(
        tmp_path,
        {"OPENAI_API_KEY": _FAKE_API_KEY, "OPENAI_BASE_URL": _FAKE_BASE_URL},
    )
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )

    text = _installed_settings(tmp_path).read_text(encoding="utf-8")
    assert "base_url: ${OPENAI_BASE_URL}" in text, (
        "with OPENAI_BASE_URL set, the instance must carry the base_url key so "
        "the run reaches the endpoint the operator chose:\n" + text
    )
    assert _FAKE_BASE_URL not in text, (
        "the installer must never write a VALUE to disk -- the file holds "
        "${VAR} placeholders and the engine expands them from the process "
        "environment at load time. Expanding here would put a credential-shaped "
        "value in a file the runner keeps."
    )
    assert _FAKE_API_KEY not in text, (
        "the api_key placeholder was expanded -- a live credential would be "
        "written to disk on every CI run."
    )


# ---------------------------------------------------------------------------
# 3. The refusals that did NOT change
# ---------------------------------------------------------------------------


def test_missing_api_key_still_refuses_and_does_not_blame_the_base_url(
    tmp_path: Path,
) -> None:
    result = _run_installer(tmp_path, {"OPENAI_BASE_URL": _FAKE_BASE_URL})

    assert result.returncode != 0, (
        "OPENAI_API_KEY remains REQUIRED: without it the judge cannot run at "
        "all, and a run that starts anyway burns its whole multi-hour budget "
        "before saying so (issue #155)."
    )
    combined = result.stdout + result.stderr
    assert "OPENAI_API_KEY" in combined, (
        "the refusal must NAME the missing credential -- an operator reads this "
        "line to know what to provision.\n" + combined
    )
    assert "OPENAI_BASE_URL" not in combined, (
        "the refusal must not mention OPENAI_BASE_URL: it is optional now, and "
        "naming it here sends the operator to provision a variable that was "
        "never the problem.\n" + combined
    )
    assert not _installed_settings(tmp_path).exists(), (
        "a refused preflight must leave no half-written settings file behind."
    )


def test_an_instance_nothing_defines_is_still_refused(tmp_path: Path) -> None:
    """The #155 / Sec 36 guarantee is untouched by making the endpoint optional."""
    graph = _fixture_graph(tmp_path, instance="nonesuch")
    result = _run_installer(
        tmp_path,
        {"OPENAI_API_KEY": _FAKE_API_KEY, "OPENAI_BASE_URL": _FAKE_BASE_URL},
        graph=graph,
    )
    assert result.returncode != 0, (
        "a graph addressing an instance provider-instances.yaml does not define "
        "must be refused before the job spends anything -- the engine's startup "
        "preflight would refuse it anyway, forty minutes later."
    )
    assert "nonesuch" in result.stdout + result.stderr, (
        "the refusal must name the unaddressable instance."
    )


# ---------------------------------------------------------------------------
# 4. The workflows read a VARIABLE first, a secret second
# ---------------------------------------------------------------------------


_ASSIGNMENT = re.compile(r"^\s*OPENAI_BASE_URL:\s*(.+?)\s*$", re.M)


@pytest.mark.parametrize("workflow", _PIPELINE_WORKFLOWS)
def test_every_workflow_reads_base_url_from_vars_then_secrets(workflow: str) -> None:
    path = _WORKFLOW_DIR / workflow
    assert path.is_file(), f"fail-closed: {path} is missing."

    values = _ASSIGNMENT.findall(path.read_text(encoding="utf-8"))
    assert values, (
        f"fail-closed: {workflow} sets OPENAI_BASE_URL nowhere. Either the "
        "endpoint stopped reaching the engine (the installed settings file "
        "holds ${VAR} placeholders expanded from the PROCESS environment, so "
        "every step that runs or scrubs needs it) or this guard is watching the "
        "wrong file."
    )
    wrong = [v for v in values if v != _BASE_URL_EXPRESSION]
    assert not wrong, (
        f"{workflow} must read OPENAI_BASE_URL as `{_BASE_URL_EXPRESSION}` -- it "
        "is a repository VARIABLE, not a secret (owner ruling 2026-09-07), with "
        "the secrets fallback kept so an operator who stored it as a secret "
        "before that ruling is not broken. Found:\n"
        + "\n".join(f"  - {v}" for v in wrong)
    )


# ---------------------------------------------------------------------------
# 5. The scrubber tolerates an EMPTY watched value
# ---------------------------------------------------------------------------


def _run_scrub(tmp_path: Path, root: Path, env_extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
    }
    env.update(env_extra)
    return subprocess.run(
        ["python3", str(_SCRUBBER), "scrub", str(root)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
    )


def test_scrub_leaves_evidence_alone_when_a_watched_var_is_empty(
    tmp_path: Path,
) -> None:
    """An unset OPENAI_BASE_URL must not turn the scrubber into a shredder.

    The watch list redacts the literal VALUE of each watched variable wherever
    it appears. An empty value matches at every position in every file, so a
    scrubber that did not guard the empty case would destroy the whole
    evidence tree the moment the endpoint stopped being set -- the exact
    configuration this change makes normal.
    """
    root = tmp_path / "evidence"
    root.mkdir()
    sample = root / "events.jsonl"
    original = '{"msg": "node critique_b started", "endpoint": "default"}\n'
    sample.write_text(original, encoding="utf-8")

    result = _run_scrub(tmp_path, root, {"OPENAI_BASE_URL": ""})
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert sample.read_text(encoding="utf-8") == original, (
        "an empty watched value must be ignored, not treated as a literal to "
        "redact.\nafter scrub:\n" + sample.read_text(encoding="utf-8")
    )


def test_scrub_still_redacts_a_real_base_url_value(tmp_path: Path) -> None:
    """Positive control: the empty-value test above is not vacuously green."""
    root = tmp_path / "evidence"
    root.mkdir()
    sample = root / "events.jsonl"
    sample.write_text(f'{{"endpoint": "{_FAKE_BASE_URL}"}}\n', encoding="utf-8")

    result = _run_scrub(tmp_path, root, {"OPENAI_BASE_URL": _FAKE_BASE_URL})
    assert result.returncode == 0, (
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert _FAKE_BASE_URL not in sample.read_text(encoding="utf-8"), (
        "OPENAI_BASE_URL stays on the literal watch list: a private endpoint "
        "carries no debugging value in evidence (it is constant across a run) "
        "and redaction is surgical. If this fails, the empty-value test above "
        "proves nothing."
    )
