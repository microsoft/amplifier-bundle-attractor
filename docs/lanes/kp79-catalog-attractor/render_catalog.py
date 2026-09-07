#!/usr/bin/env python3
"""Render this repo's two ALWAYS-ON catalog surfaces from a scratch session.

Both surfaces are injected into every session's head on every turn, so their
byte size is a per-turn tax paid whether or not anything here is ever used:

  1. the ``delegate`` tool's live ``description`` -- which carries the
     "Available agents:" catalog block, and
  2. the ``<system-reminder source="hooks-skills-visibility">`` block that
     ``tool-skills`` injects, which carries every registered skill's
     ``description``.

A real ``AmplifierSession`` is built from the caller-supplied bundle and the
values are read straight off the mounted tool / emitted hook. **No prompt is
executed, so no LLM call is made and the cost is $0.**

    usage: render_catalog.py <bundle-uri-or-path> <out-dir>

Writes ``<out-dir>/delegate-catalog.txt`` and
``<out-dir>/skills-visibility.txt`` and prints the byte counts.

Forked from the kp79 android-tester lane's renderer (delegate surface) and
the kv98 context-intelligence lane's renderer (skills surface); this one does
both from ONE session so the two surfaces are always measured on the same
tree at the same instant.
"""

import asyncio
import re
import sys
from pathlib import Path

SKILLS_PATTERN = re.compile(
    r'<system-reminder source="hooks-skills-visibility">.*?</system-reminder>',
    re.S,
)


def _tool_candidates(session):
    """Every mounted tool instance, from the coordinator's `tools` mount point."""
    mounted = session.coordinator.get("tools")
    if mounted is None:
        return []
    if hasattr(mounted, "values"):
        return list(mounted.values())
    return list(mounted)


def _stringify(value: object) -> str:
    """Flatten an arbitrary hook-emit result into searchable text."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return "\n".join(_stringify(v) for v in value.values())
    if isinstance(value, (list, tuple, set)):
        return "\n".join(_stringify(v) for v in value)
    ci = getattr(value, "context_injection", None)
    if isinstance(ci, str):
        return ci
    return ""


async def main(bundle_uri: str, out_dir: str) -> int:
    from amplifier_app_cli.paths import create_session_from_bundle

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    session = await create_session_from_bundle(bundle_uri, install_deps=False)
    async with session:
        candidates = _tool_candidates(session)
        if not candidates:
            print("ERROR: could not locate tool registry on session", file=sys.stderr)
            return 2

        delegate = next(
            (t for t in candidates if getattr(t, "name", None) == "delegate"), None
        )
        if delegate is None:
            names = ", ".join(str(getattr(t, "name", t)) for t in candidates)
            print(f"ERROR: delegate tool not found. Tools: {names}", file=sys.stderr)
            return 3

        desc = delegate.description
        if "Available agents:" not in desc:
            print("ERROR: delegate description carries no agent catalog", file=sys.stderr)
            return 4
        if "No description" in desc:
            print(
                "ERROR: catalog rendered 'No description' -- agent metadata did not "
                "load, so this is measuring the wrong tree",
                file=sys.stderr,
            )
            return 5
        (out / "delegate-catalog.txt").write_text(desc, encoding="utf-8")
        print(f"delegate-catalog.txt   bytes={len(desc.encode('utf-8'))}")

        emitted = await session.coordinator.hooks.emit(
            "provider:request",
            {"provider": "kp79-probe", "messages": [], "model": "kp79-probe"},
        )
        haystacks = [_stringify(emitted)]
        context = session.coordinator.get("context")
        if context is not None:
            for m in await context.get_messages_for_request():
                if m.get("role") == "system":
                    haystacks.append(m.get("content") or "")

        for hay in haystacks:
            found = SKILLS_PATTERN.search(hay)
            if found:
                block = found.group(0)
                (out / "skills-visibility.txt").write_text(block, encoding="utf-8")
                print(f"skills-visibility.txt  bytes={len(block.encode('utf-8'))}")
                return 0
        print("ERROR: skills-visibility block not rendered on any surface", file=sys.stderr)
        return 6


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: render_catalog.py <bundle-uri> <out-dir>", file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(asyncio.run(main(sys.argv[1], sys.argv[2])))
