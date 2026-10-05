"""R2 — classification, through the API and on the classifier directly."""

from __future__ import annotations

import json

import pytest

from lib.files.classify import MEDIA_CAP, TEXT_CAP, classify
from tests.files.conftest import EDITOR

_NOTEBOOK = json.dumps({"nbformat": 4, "nbformat_minor": 5, "metadata": {},
                        "cells": [{"cell_type": "markdown", "source": "# x"}]})

R2_EXAMPLES = [
    ("a.png", b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "image"),
    ("a.svg", b'<svg xmlns="http://www.w3.org/2000/svg"/>', "svg"),
    ("a.pdf", b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n", "pdf"),
    ("README.md", b"# Title\n\ntext\n", "markdown"),
    ("r.html", b"<!doctype html><p>hi</p>", "html"),
    ("n.ipynb", _NOTEBOOK.encode(), "notebook"),
    ("d.csv", b"a,b\n1,2\n", "csv"),
    ("main.py", "print('héllo')\n".encode(), "code"),
    ("Makefile", b"all:\n\techo hi\n", "code"),
    ("blob.dat", b"abc\x00def", "binary"),
    ("old.txt", b"caf\xe9 au lait\n", "binary"),
    ("big.log", b"x" * (3 * 1024 * 1024), "too_large"),
]


@pytest.mark.parametrize("name,content,preview", R2_EXAMPLES)
def test_classification_follows_r2(flask_client, repo, name, content, preview):
    pid, root = repo
    (root / name).write_bytes(content)
    resp = flask_client.get("/api/files/meta", headers=EDITOR,
                            query_string={"project": pid, "path": name})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["preview"] == preview
    assert body["size"] == len(content)


def _reader(data: bytes):
    return lambda n: data if n is None else data[:n]


def test_caps_are_per_kind():
    assert classify("v.mp4", MEDIA_CAP + 1, _reader(b"")).preview == "too_large"
    assert classify("v.mp4", MEDIA_CAP, _reader(b"")).preview == "video"
    assert classify("a.md", TEXT_CAP + 1, _reader(b"")).preview == "too_large"
    big_binary = b"\x00" * 10
    assert classify("x.bin", TEXT_CAP * 10, _reader(big_binary)).preview == "binary"


def test_media_is_classified_without_reading_bytes():
    def refuse(_n):
        raise AssertionError("media must not be read")
    assert classify("song.mp3", 10, refuse).preview == "audio"
    assert classify("pic.JPG", 10, refuse).preview == "image"


def test_bom_utf8_is_code_and_a_split_multibyte_head_is_still_text():
    assert classify("a.txt", 6, _reader(b"\xef\xbb\xbfabc")).preview == "code"
    head = ("é" * (TEXT_CAP // 2 + 10)).encode()
    assert classify("big.txt", len(head), _reader(head)).preview == "too_large"


def test_ipynb_that_is_not_nbformat_4_falls_back_to_code():
    bad = b'{"nbformat": 3, "worksheets": []}'
    got = classify("old.ipynb", len(bad), _reader(bad))
    assert (got.preview, got.language) == ("code", "json")


def test_language_and_mime():
    got = classify("Dockerfile", 4, _reader(b"FROM"))
    assert (got.preview, got.language, got.mime) == (
        "code", "dockerfile", "text/plain; charset=utf-8")
    assert classify("x.unknownext", 1, _reader(b"x")).language == "plaintext"
    assert classify("a.pdf", 1, _reader(b"")).mime == "application/pdf"
    assert classify("a.svg", 1, _reader(b"<svg/>")).mime == "image/svg+xml"
