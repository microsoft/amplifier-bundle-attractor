"""Layer B: DOT-aware recursive materialization of a remote git+https:// pipeline.

Fetches the entry .dot plus every recursively-referenced subgraph (cross-repo
included) through the Layer A cache, writes a per-run local view with cross-origin
dot_file= refs rewritten to local paths, and returns (entry_path, cleanup).

The engine then parse_dot()s the local entry and runs the local tree unchanged.

Layer A (amplifier_module_remote_source) is imported LAZILY so importing
loop-pipeline never pulls in httpx.
"""

from __future__ import annotations

import asyncio
import logging
import os
import posixpath
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .dot_parser import parse_dot

if TYPE_CHECKING:
    from .graph import Graph

logger = logging.getLogger(__name__)

_GIT_HTTPS_PREFIX = "git+https://"


@dataclass
class _Ref:
    origin: object            # Origin (Layer A) — typed loosely to avoid a top-level import
    local_relpath: str
    cross_origin: bool


def _repo_root_relpath(origin) -> str:
    return posixpath.join(origin.owner, origin.repo, origin.ref, origin.path)


def _cross_origin_local_relpath(referrer, child) -> str:
    referrer_dir = posixpath.dirname(_repo_root_relpath(referrer))
    return posixpath.relpath(_repo_root_relpath(child), referrer_dir)


def extract_dot_file_refs(text: str, *, origin) -> list[str]:
    """Return all raw dot_file= values, using the engine's own parser (no regex)."""
    from amplifier_module_remote_source import RemoteFetchParseError  # lazy

    try:
        graph = parse_dot(text)
    except Exception as exc:  # noqa: BLE001
        raise RemoteFetchParseError(
            f"Failed to parse DOT from {origin.owner}/{origin.repo}"
            f"@{origin.ref}#{origin.path}: {exc}"
        ) from exc
    return [n.attrs["dot_file"] for n in graph.nodes.values() if n.attrs.get("dot_file")]


def _resolve_ref(raw_ref: str, referrer) -> _Ref | None:
    """Resolve one raw dot_file= value against its referrer origin. Returns None
    for an unexpanded $variable (skipped). Raises on absolute / ..-escape."""
    from amplifier_module_remote_source import (  # lazy
        Origin,
        RemoteFetchPathError,
        parse_uri,
    )

    if "$" in raw_ref:
        logger.warning(
            "Skipping dot_file ref with unexpanded variable (runtime-resolved): %r",
            raw_ref,
        )
        return None

    if raw_ref.startswith(_GIT_HTTPS_PREFIX):
        child = parse_uri(raw_ref)
        return _Ref(child, _cross_origin_local_relpath(referrer, child), True)

    if raw_ref.startswith("/"):
        raise RemoteFetchPathError(
            f"Absolute dot_file= is not allowed in remote graphs: {raw_ref!r}"
        )

    joined = posixpath.normpath(posixpath.join(referrer.dir, raw_ref))
    if joined.startswith("../") or joined == ".." or joined.startswith("/"):
        raise RemoteFetchPathError(
            f"Relative dot_file= escapes repo root "
            f"{referrer.owner}/{referrer.repo}@{referrer.ref}: {raw_ref!r}"
        )
    child = Origin(referrer.host, referrer.owner, referrer.repo, referrer.ref, joined)
    return _Ref(child, raw_ref, False)


async def materialize_remote_dot(
    entry_uri: str,
    *,
    limits=None,
    token: str | None = None,
    cache=None,
    base_url: str | None = None,
) -> tuple[Path, Callable[[], None]]:
    """Materialize a remote pipeline tree into a per-run local view.

    Returns (entry_local_path, cleanup). Blobs persist in the shared cache;
    ``cleanup()`` removes only the per-run view dir. On failure the partial view
    is removed here (nothing has executed yet) and the error propagates.
    """
    from amplifier_module_remote_source import (  # lazy — keeps import network-free
        BlobCache,
        FetchLimits,
        parse_uri,
        resolve_token,
    )

    limits = limits or FetchLimits()
    cache = cache or BlobCache()
    token = token if token is not None else resolve_token()

    entry = parse_uri(entry_uri)
    view_dir = tempfile.mkdtemp(prefix="attractor-remote-")
    entry_local_path = Path(view_dir) / _repo_root_relpath(entry)

    seen: dict[tuple, str] = {}
    total_bytes = 0
    file_count = 0
    sem = asyncio.Semaphore(limits.max_concurrency)

    async def _guarded(origin) -> bytes:
        async with sem:
            content, _sha = await cache.get(
                origin, token=token, base_url=base_url, limits=limits
            )
            return content

    try:
        frontier = [entry]
        depth = 0
        while frontier:
            if depth >= limits.max_depth:
                from amplifier_module_remote_source import RemoteFetchLimitError

                raise RemoteFetchLimitError(
                    f"max_depth={limits.max_depth} exceeded at {frontier[0].path!r}"
                )
            # `frontier` is already deduped (via the `deduped`/`queued` pass at
            # the end of the previous iteration, and the initial `[entry]`
            # list has no duplicates to begin with), so this `not in seen`
            # filter is a defensive no-op in the current control flow -- kept
            # as belt-and-suspenders in case that invariant ever changes.
            batch = [o for o in frontier if o.key() not in seen]
            results = await asyncio.gather(*(_guarded(o) for o in batch))

            next_frontier = []
            for origin, raw in zip(batch, results):
                file_count += 1
                total_bytes += len(raw)
                from amplifier_module_remote_source import RemoteFetchLimitError

                if file_count > limits.max_files:
                    raise RemoteFetchLimitError(
                        f"max_files={limits.max_files} exceeded at {origin.path!r}"
                    )
                if total_bytes > limits.max_total_bytes:
                    raise RemoteFetchLimitError(
                        f"max_total_bytes={limits.max_total_bytes} exceeded "
                        f"at {origin.path!r}"
                    )

                text = raw.decode("utf-8")
                local_path = os.path.join(view_dir, _repo_root_relpath(origin))
                rewrites = []
                for raw_ref in extract_dot_file_refs(text, origin=origin):
                    ref = _resolve_ref(raw_ref, origin)
                    if ref is None:
                        continue
                    if ref.cross_origin:
                        rewrites.append((raw_ref, ref.local_relpath))
                    if ref.origin.key() not in seen:
                        next_frontier.append(ref.origin)

                for old, new in rewrites:
                    text = re.sub(
                        r'(dot_file\s*=\s*)"' + re.escape(old) + r'"',
                        lambda m, _new=new: f'{m.group(1)}"{_new}"',
                        text,
                    )

                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                with open(local_path, "w", encoding="utf-8") as fh:
                    fh.write(text)
                seen[origin.key()] = local_path

            deduped, queued = [], set()
            for o in next_frontier:
                if o.key() in seen or o.key() in queued:
                    continue
                queued.add(o.key())
                deduped.append(o)
            frontier = deduped
            depth += 1

        def _cleanup() -> None:
            shutil.rmtree(view_dir, ignore_errors=True)

        return entry_local_path, _cleanup
    except BaseException:
        shutil.rmtree(view_dir, ignore_errors=True)
        raise


async def load_remote_or_local_graph(
    source: "Graph | str",
) -> tuple["Graph", Callable[[], None]]:
    """Return ``(graph, cleanup)`` for a DOT source that may be local or remote.

    This is the single materialize -> parse -> set-``source_dir`` -> cleanup
    -on-exception sequence shared by both engine entry points --
    ``pipeline_runner.runner._load_graph`` (the direct-engine path) and the
    mounted ``PipelineOrchestrator.execute()`` in this package. Both used to
    maintain their own hand-synced copy of this sequence; keeping it in one
    place makes the two hooks structurally unable to diverge again (see
    AGENTS.md's partial-coverage-symmetry note -- this exact divergence risk
    already cost a regression test once).

    If ``source`` is a ``git+https://`` URL: materializes the remote tree
    (async, before parse) via ``materialize_remote_dot``, parses the local
    entry, and sets ``graph.source_dir`` to the materialized entry's parent
    directory so subgraph ``dot_file=`` refs resolve against the fetched tree
    rather than cwd. If parsing the materialized entry fails, ``cleanup()`` is
    called before the exception propagates so the per-run view never leaks.

    Otherwise: ``source`` is treated as local. A ``str`` is parsed as raw DOT
    text; anything else (an already-parsed ``Graph``) is passed through
    unchanged. ``cleanup()`` is a no-op in this path.

    Returns:
        ``(graph, cleanup)``. The caller is responsible for calling
        ``cleanup()`` (typically in a ``finally:``) once the graph is no
        longer needed, regardless of which path was taken.
    """
    if isinstance(source, str) and source.startswith(_GIT_HTTPS_PREFIX):
        entry_path, cleanup = await materialize_remote_dot(source)
        try:
            graph = parse_dot(entry_path.read_text(encoding="utf-8"))
            graph.source_dir = str(entry_path.parent)
            return graph, cleanup
        except BaseException:
            cleanup()
            raise

    graph = parse_dot(source) if isinstance(source, str) else source
    return graph, (lambda: None)
