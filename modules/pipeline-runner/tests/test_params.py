"""Unit tests for amplifier_module_pipeline_runner.params.parse_param."""

from __future__ import annotations

import pytest

from amplifier_module_pipeline_runner.params import parse_param


def test_key_value():
    assert parse_param("k=v") == ("k", "v")


def test_value_containing_equals():
    assert parse_param("k=a=b=c") == ("k", "a=b=c")


def test_at_file_reads_file_contents(tmp_path):
    file_path = tmp_path / "value.txt"
    file_path.write_text("file contents here", encoding="utf-8")
    key, value = parse_param(f"content=@{file_path}")
    assert key == "content"
    assert value == "file contents here"


def test_at_at_literal_escape():
    key, value = parse_param("handle=@@jdoe")
    assert key == "handle"
    assert value == "@jdoe"


def test_empty_key_raises():
    with pytest.raises(ValueError):
        parse_param("=value")


def test_missing_equals_raises():
    with pytest.raises(ValueError):
        parse_param("no_equals_here")


def test_missing_file_raises():
    with pytest.raises(ValueError):
        parse_param("content=@/nonexistent/path/to/file.txt")
