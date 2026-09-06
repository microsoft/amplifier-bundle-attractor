"""The bundle names itself; the module does not -- PRN-001..PRN-004.

`modules/tool-pipeline-run` used to carry this bundle's name in its own
source: the `@attractor:` mention namespace it showed the calling LLM, and
an `attractor-pipeline-runner` spawn default (attractor-24e stage 1). Both
are now config keys whose defaults name no bundle, and THIS bundle supplies
its own values at the mount in `bundles/attractor-interactive.yaml`.

That split is only stable if both halves are held. Each half fails silently
on its own:

  * Put a bundle name back in the module and nothing breaks here -- it
    breaks for whoever mounts the module next, as an `@mention` their
    bundle never registered, at resolution time rather than read time.
  * Drop a key from the mount and nothing breaks loudly either: the module
    falls back to a neutral *placeholder*, so this bundle's agent would be
    told to write `@<bundle>:path/to/pipeline.dot`.

So the guard is two-sided by construction, and neither side is a
restatement of the other: PRN-001 reads the module's source, PRN-002..004
read the composition surfaces and the files they point at.

Checks:

  PRN-001  `modules/tool-pipeline-run`'s module source
           -> contains no occurrence of this bundle's name, in any case
  PRN-002  the `tool-pipeline-run` mount in `bundles/attractor-interactive.yaml`
           -> supplies BOTH name-bearing keys (`runner_agent`,
              `mention_example`) rather than inheriting a default
  PRN-003  the advertised `mention_example`
           -> its namespace is this repo's own bundle name (`bundle.md`),
              and the path behind it exists on disk
  PRN-004  the configured `runner_agent`
           -> names an agent this repo actually registers under `agents/`

Honest limits:

  * PRN-001 is a substring scan, not a semantic one. It catches the failure
    that actually happened (a bundle name baked into source) and would not
    catch a bundle-specific assumption expressed without the name.
  * PRN-003 checks that the advertised path exists in THIS repo; it does not
    execute foundation's mention resolver, which is the module's own
    concern and is tested there
    (`test_configured_namespace_resolves_end_to_end`).
  * Every check asserts its target exists first. If `modules/tool-pipeline-run`
    later leaves this repo (the stage-2 move), these fail loudly rather than
    passing vacuously -- re-aim or retire this guard deliberately at that
    point, which is the whole reason it fails instead of skipping.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent

MODULE_SOURCE = (
    REPO_ROOT
    / "modules"
    / "tool-pipeline-run"
    / "amplifier_module_tool_pipeline_run"
    / "__init__.py"
)
CONSUMER_BUNDLE = REPO_ROOT / "bundles" / "attractor-interactive.yaml"
ROOT_BUNDLE_DOC = REPO_ROOT / "bundle.md"
AGENTS_DIR = REPO_ROOT / "agents"

RE_AIM = (
    "If tool-pipeline-run has moved out of this repo, re-aim or retire this "
    "guard deliberately (tests/test_pipeline_run_namespace_neutrality.py) -- "
    "do not let it pass vacuously."
)


def _bundle_name() -> str:
    """This repo's own bundle name, from bundle.md's frontmatter."""
    text = ROOT_BUNDLE_DOC.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{ROOT_BUNDLE_DOC} has no YAML frontmatter"
    frontmatter = text.split("---\n", 2)[1]
    name = yaml.safe_load(frontmatter)["bundle"]["name"]
    assert name, f"{ROOT_BUNDLE_DOC} frontmatter declares no bundle.name"
    return name


def _pipeline_run_mount_config() -> dict:
    """The `config:` block of the tool-pipeline-run mount in the consumer bundle."""
    assert CONSUMER_BUNDLE.exists(), f"{CONSUMER_BUNDLE} not found. {RE_AIM}"
    doc = yaml.safe_load(CONSUMER_BUNDLE.read_text(encoding="utf-8"))
    mounts = [
        entry
        for entry in (doc.get("tools") or [])
        if entry.get("module") == "tool-pipeline-run"
    ]
    assert len(mounts) == 1, (
        f"expected exactly one tool-pipeline-run mount in {CONSUMER_BUNDLE.name}, "
        f"found {len(mounts)}. {RE_AIM}"
    )
    return mounts[0].get("config") or {}


def test_prn_001_module_source_names_no_bundle():
    """PRN-001: the module carries no reference to this bundle's name."""
    assert MODULE_SOURCE.exists(), f"{MODULE_SOURCE} not found. {RE_AIM}"

    bundle_name = _bundle_name()
    source = MODULE_SOURCE.read_text(encoding="utf-8")

    offenders = [
        f"{lineno}: {line.strip()}"
        for lineno, line in enumerate(source.splitlines(), start=1)
        if bundle_name.lower() in line.lower()
    ]
    assert not offenders, (
        f"{MODULE_SOURCE.relative_to(REPO_ROOT)} names the '{bundle_name}' bundle. "
        "Its defaults must name no bundle -- the mounting bundle supplies its own "
        "runner_agent / mention_example (see the module README). Offending lines:\n"
        + "\n".join(offenders)
    )


@pytest.mark.parametrize("key", ["runner_agent", "mention_example"])
def test_prn_002_consumer_supplies_both_name_bearing_keys(key):
    """PRN-002: this bundle passes its own names instead of inheriting a default."""
    config = _pipeline_run_mount_config()
    assert key in config, (
        f"{CONSUMER_BUNDLE.name}'s tool-pipeline-run mount does not set '{key}'. "
        "The module's default for it names no bundle, so dropping this key does "
        "not restore this bundle's behavior -- it substitutes a neutral "
        "placeholder into what the agent is told."
    )
    assert config[key], f"'{key}' is set but empty in {CONSUMER_BUNDLE.name}"


def test_prn_003_advertised_mention_example_is_ours_and_exists():
    """PRN-003: the advertised @mention names this bundle and points at a real file."""
    mention = _pipeline_run_mount_config()["mention_example"]
    bundle_name = _bundle_name()

    assert mention.startswith("@"), f"mention_example {mention!r} is not an @mention"
    namespace, _, path = mention[1:].partition(":")
    assert path, f"mention_example {mention!r} has no path after its namespace"

    assert namespace == bundle_name, (
        f"mention_example advertises '@{namespace}:' but this repo's bundle is "
        f"'{bundle_name}' (bundle.md). The agent would be taught a mention that "
        "resolves nowhere."
    )
    assert (REPO_ROOT / path).exists(), (
        f"mention_example points at '{path}', which does not exist in this repo. "
        "An example the agent copies must be a file it can actually reach."
    )


def test_prn_004_configured_runner_agent_is_registered_here():
    """PRN-004: the runner_agent spawned is an agent this repo actually declares."""
    runner_agent = _pipeline_run_mount_config()["runner_agent"]

    assert AGENTS_DIR.is_dir(), f"{AGENTS_DIR} not found. {RE_AIM}"
    declared = set()
    for agent_file in sorted(AGENTS_DIR.glob("*.yaml")):
        doc = yaml.safe_load(agent_file.read_text(encoding="utf-8")) or {}
        name = (doc.get("bundle") or {}).get("name")
        if name:
            declared.add(name)

    assert declared, f"no agent bundle names found under {AGENTS_DIR}. {RE_AIM}"
    assert runner_agent in declared, (
        f"tool-pipeline-run is configured to spawn '{runner_agent}', which no "
        f"agent under agents/ declares. Declared: {sorted(declared)}"
    )
