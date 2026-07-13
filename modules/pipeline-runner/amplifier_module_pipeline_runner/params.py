"""CLI-style ``key=value`` param parsing.

Extracted (verbatim behavior) from dot-graph-runner's ``cli._parse_param``,
generalized to raise ``ValueError`` instead of ``argparse.ArgumentTypeError``
so it is reusable outside argparse (the CLI layer catches ``ValueError`` and
converts it to a fail-loud exit).
"""

from __future__ import annotations

from pathlib import Path


def parse_param(raw: str) -> tuple[str, str]:
    """Parse a ``key=value`` param argument.

    Splits on the FIRST ``=`` only, so values may themselves contain ``=``.

    Curl-style ``@file`` convention: if ``value`` starts with ``@``, the
    param value is read from the file at the path after the ``@`` instead of
    being taken literally. This makes it practical to pass big/multi-line
    values (a checkbox worklist, a spec) without shell quoting gymnastics,
    e.g. ``outfile=@./worklist.md``. The path is resolved with
    ``expanduser()``; if relative, it is resolved relative to the current
    working directory. A missing or unreadable file raises ``ValueError``
    (no silent fallback to the literal ``@...`` string).

    To pass a literal value that itself starts with ``@``, escape it with a
    second ``@``: ``handle=@@jdoe`` yields the literal string ``@jdoe``.

    Args:
        raw: A ``key=value`` string (see above for value conventions).

    Returns:
        (key, value) tuple.

    Raises:
        ValueError: If ``raw`` has no ``=``, an empty key, or an unreadable
            ``@file`` reference.

    Examples:
        >>> parse_param("k=v")
        ('k', 'v')
        >>> parse_param("k=a=b")
        ('k', 'a=b')
        >>> parse_param("k=@@literal")
        ('k', '@literal')
    """
    if "=" not in raw:
        raise ValueError(f"--param must be key=value, got: {raw!r}")
    key, _, value = raw.partition("=")
    key = key.strip()
    if not key:
        raise ValueError(f"--param key must be non-empty, got: {raw!r}")

    if value.startswith("@@"):
        # Escaped literal: @@foo -> literal value "@foo"
        value = value[1:]
    elif value.startswith("@"):
        # curl-style file reference: @path/to/file -> file's full contents
        raw_path = value[1:]
        file_path = Path(raw_path).expanduser()
        try:
            value = file_path.read_text(encoding="utf-8")
        except OSError as e:
            raise ValueError(
                f"--param {key}=@{raw_path}: could not read file {file_path}: {e}"
            ) from e

    return key, value


def parse_params(raws: list[str]) -> dict[str, str]:
    """Parse a list of ``key=value`` param strings into a flat dict.

    Later entries overwrite earlier ones for the same key (matches the CLI's
    ``--param`` (action=append) semantics: last one wins).

    Args:
        raws: List of raw ``key=value`` strings.

    Returns:
        Flat dict of parsed params.

    Raises:
        ValueError: Propagated from ``parse_param`` on any malformed entry.
    """
    params: dict[str, str] = {}
    for raw in raws:
        key, value = parse_param(raw)
        params[key] = value
    return params
