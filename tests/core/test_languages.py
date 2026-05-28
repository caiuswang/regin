"""Unit tests for the lib.languages registry."""

from __future__ import annotations

import pytest

from lib import languages
from lib.languages.base import Language
from lib.languages.java import JAVA


# ── registry ────────────────────────────────────────────────

def test_get_returns_java_entry():
    assert languages.get("java") is JAVA


def test_get_unknown_raises():
    with pytest.raises(KeyError):
        languages.get("no-such-language")


def test_all_ids_contains_java():
    assert "java" in languages.all_ids()


def test_find_by_extension_java():
    assert languages.find_by_extension("src/Foo.java") is JAVA


def test_find_by_extension_unknown_returns_none():
    assert languages.find_by_extension("README.md") is None


def test_register_replaces_entry():
    fake = Language(
        id="fake-only-test",
        file_extensions=(".fake",),
    )
    languages.register(fake)
    try:
        assert languages.get("fake-only-test") is fake
        assert languages.find_by_extension("foo.fake") is fake
    finally:
        languages._REGISTRY.pop("fake-only-test", None)


# ── Java entry content ──────────────────────────────────────

def test_java_file_extensions():
    assert JAVA.file_extensions == (".java",)


def test_java_parse_class_metadata_wired():
    assert callable(JAVA.parse_class_metadata)
    meta = JAVA.parse_class_metadata("package x;\npublic class Foo {}\n")
    assert meta == {
        "class_name": "Foo",
        "package": "x",
        "extends": None,
        "implements": [],
    }
