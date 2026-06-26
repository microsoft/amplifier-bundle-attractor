"""
Guard test: every context.include path in agents/, profiles/, and bundles/ YAML
files must resolve to an existing file when treated as a path relative to the
YAML file's own directory.

WHY THIS TEST EXISTS
--------------------
The loader (_prepared.py:421) does ``if context_path.exists(): read_text(...)``
and silently skips non-existent paths (comment at :451 confirms "silently
skipped").  A bare ``context/system-anthropic.md`` in a file under ``agents/``
resolves to ``agents/context/system-anthropic.md``, which does not exist — so
the system prompt is never set and the "Layer-1 base prompt is empty" warning
fires every LLM turn.  The loader gives no error; this test is the only
regression guard.

Namespaced refs (``attractor:context/...``) are framework-resolved and skipped
here — only plain relative paths are checked.
"""

import pytest
import yaml
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
SEARCH_DIRS = ["agents", "profiles", "bundles"]


def _collect_yaml_includes():
    """Return a list of (yaml_path, include_entry) pairs for all context.include items."""
    cases = []
    for dir_name in SEARCH_DIRS:
        search_dir = REPO_ROOT / dir_name
        if not search_dir.exists():
            continue
        for yaml_path in sorted(search_dir.rglob("*.yaml")):
            try:
                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            context = data.get("context")
            if not isinstance(context, dict):
                continue
            includes = context.get("include")
            if not isinstance(includes, list):
                continue
            for entry in includes:
                if isinstance(entry, str):
                    cases.append(
                        pytest.param(
                            yaml_path,
                            entry,
                            id=f"{yaml_path.relative_to(REPO_ROOT)}::{entry}",
                        )
                    )
    return cases


@pytest.mark.parametrize("yaml_path,include_entry", _collect_yaml_includes())
def test_context_include_resolves(yaml_path: Path, include_entry: str) -> None:
    """Every non-namespaced context.include must resolve to an existing file.

    A namespaced ref (containing ``:``) is handled by the framework and skipped.
    All other entries are treated as paths relative to the YAML file's own
    directory — the same resolution the loader uses — and must exist on disk.
    Failure message names the offending YAML + include so a regression is
    immediately actionable.
    """
    if ":" in include_entry:
        pytest.skip(f"Namespaced ref (framework-resolved), skipping: {include_entry!r}")

    resolved = (yaml_path.parent / include_entry).resolve()
    assert resolved.exists(), (
        f"\nMissing context include:\n"
        f"  YAML:    {yaml_path.relative_to(REPO_ROOT)}\n"
        f"  include: {include_entry!r}\n"
        f"  resolves to: {resolved}\n"
        f"  (file does not exist)\n\n"
        f"Hint: bare paths are resolved relative to the YAML's own directory.\n"
        f"If the file lives at bundle-root context/, use '../context/<file>' "
        f"(or a namespaced ref once foundation supports it)."
    )
