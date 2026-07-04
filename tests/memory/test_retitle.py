"""LLM title-upgrade for auto-derived lesson titles (`lib/memory/retitle.py`)."""

from __future__ import annotations

import json

import lib.memory as memory
from lib.memory.retitle import (AUTO_TITLE_TAG, RetitleResult, _clean_title,
                                _looks_auto_titled, _parse_titles,
                                needs_retitle, retitle_memories)


class StubLLM:
    """Queued responses, one per `complete()` (None once drained — exactly
    what an unconfigured external agent yields)."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.prompts = []

    def complete(self, prompt, *, max_tokens=1024):
        self.prompts.append(prompt)
        return self._responses.pop(0) if self._responses else None


# ── detection ────────────────────────────────────────────────────────────────

def test_looks_auto_titled_true_for_body_slice():
    body = "A CLI write command must emit its per-item detail on run so it is auditable"
    assert _looks_auto_titled(body[:80] + "…", body) is True


def test_looks_auto_titled_false_for_crafted_headline():
    # ends in ellipsis but is NOT a verbatim slice of the body → left alone
    assert _looks_auto_titled("A brittle categorical is safe as a weight…",
                              "Some unrelated body text about weights and axes.") is False


def test_needs_retitle_honors_tag_and_kind():
    assert needs_retitle({"kind": "lesson", "tags": [AUTO_TITLE_TAG],
                          "title": "anything", "body": "b"}) is True
    assert needs_retitle({"kind": "fact", "tags": [AUTO_TITLE_TAG],
                          "title": "x…", "body": "x y"}) is False


# ── parsing ──────────────────────────────────────────────────────────────────

def test_parse_titles_tolerates_fences_and_prose():
    ans = 'noise ```json [{"i":0,"title":"Do X first"},{"i":1,"title":"Y…"}] ``` tail'
    assert _parse_titles(ans, 2) == {0: "Do X first", 1: "Y"}


def test_parse_titles_drops_out_of_range_and_empty():
    ans = json.dumps([{"i": 5, "title": "oob"}, {"i": 0, "title": ""}])
    assert _parse_titles(ans, 2) == {}


def test_clean_title_word_boundary_clip():
    long = "word " * 30  # 150 chars, all words
    out = _clean_title(long)
    assert len(out) <= 80 and not out.endswith(" ") and " word" not in out[-1:]


# ── end-to-end over an isolated store ────────────────────────────────────────

def _auto_lesson(body):
    from lib.memory.store import title_from_body
    return memory.remember(body, kind="lesson", title=title_from_body(body),
                           tags=["send_to_user", AUTO_TITLE_TAG], is_test=True)


def test_retitle_updates_and_drops_tag():
    body = ("Restart the vite dev server after editing vite.config.js — vite does "
            "not hot-reload its own config, so a new proxy rule never takes effect.")
    mid = _auto_lesson(body)
    ans = json.dumps([{"i": 0, "title": "Restart vite after vite.config proxy edits"}])
    res = retitle_memories(memory.get_store(), StubLLM([ans]), include_tests=True)
    assert isinstance(res, RetitleResult)
    assert res.retitled == 1
    row = memory.get(mid)
    assert row.title == "Restart vite after vite.config proxy edits"
    assert AUTO_TITLE_TAG not in json.loads(row.tags or "[]")


def test_retitle_leaves_crafted_titles_untouched():
    memory.remember("Some body.", kind="lesson",
                    title="A crafted one-line rule", tags=["send_to_user"],
                    is_test=True)
    res = retitle_memories(memory.get_store(), StubLLM(['[]']),
                           include_tests=True)
    assert res.candidates == 0


def test_retitle_dry_run_reports_without_writing():
    body = "A staleness detector and its fix primitive must key off the same column."
    mid = _auto_lesson(body)
    before = memory.get(mid).title
    ans = json.dumps([{"i": 0, "title": "Key a staleness detector and its fix off one column"}])
    res = retitle_memories(memory.get_store(), StubLLM([ans]),
                           include_tests=True, dry_run=True)
    assert res.retitled == 0 and len(res.changes) == 1
    assert memory.get(mid).title == before
