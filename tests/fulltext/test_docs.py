"""Scoring, snippet windowing and LIKE-pattern rules for full-text search."""

from __future__ import annotations

from lib.fulltext.docs import (
    LIKE_ESCAPE, MAX_COUNTED_BODY_HITS, SearchDoc, body_window, clip_body,
    count_hits, like_patterns, rank, score_doc,
)


def _doc(**kw) -> SearchDoc:
    base = {"kind": "span", "id": "s1", "title": "tool.Edit", "sub": "",
            "body": ""}
    base.update(kw)
    return SearchDoc(**base)


def test_count_hits_is_case_insensitive_and_non_overlapping():
    assert count_hits("aaaa", "aa") == 2
    assert count_hits("Redis redis REDIS", "redis") == 3
    assert count_hits("", "x") == 0
    assert count_hits("abc", "") == 0


def test_title_match_outranks_a_long_body_of_hits():
    title_hit = score_doc(_doc(title="redis client"), "redis")
    body_hits = score_doc(_doc(title="tool.Bash", body="redis\n" * 400), "redis")
    assert title_hit.score > body_hits.score


def test_body_hits_saturate():
    many = score_doc(_doc(body="redis\n" * 400), "redis")
    assert many.score == MAX_COUNTED_BODY_HITS
    assert many.hits == 400, "the true count still rides on the row's pill"


def test_sub_match_scores_between_title_and_body():
    sub = score_doc(_doc(sub="turn #4 · redis.py"), "redis")
    body = score_doc(_doc(body="redis"), "redis")
    title = score_doc(_doc(title="redis"), "redis")
    assert body.score < sub.score < title.score


def test_document_with_no_hit_anywhere_is_dropped():
    assert score_doc(_doc(title="a", sub="b", body="c"), "redis") is None


def test_rank_breaks_ties_on_document_order():
    docs = [score_doc(_doc(id=str(i), body="redis"), "redis", order=i)
            for i in range(3)]
    assert [s.order for s in rank(list(reversed(docs)))] == [0, 1, 2]


def test_body_window_centres_on_the_hit_and_caps_lines():
    body = "\n".join(["pad"] * 100 + ["redis here"] + ["pad"] * 100)
    win = body_window(body, "redis", lines=20).split("\n")
    assert len(win) == 20
    assert win[6] == "redis here", "six lines of lead-in above the hit"


def test_body_window_clamps_at_the_end_of_the_document():
    body = "\n".join(["pad"] * 100 + ["redis here"])
    win = body_window(body, "redis", lines=20).split("\n")
    assert len(win) < 20
    assert win[-1] == "redis here"


def test_body_window_starts_at_zero_when_the_hit_is_near_the_top():
    body = "\n".join(["a"] * 5 + ["redis"] + ["b"] * 100)
    assert body_window(body, "redis", lines=20).startswith("a\na")


def test_body_window_returns_short_bodies_untouched():
    assert body_window("one\ntwo", "two") == "one\ntwo"


def test_clip_body_marks_the_truncation_it_performed():
    clipped = clip_body("x" * 300_000)
    assert "truncated" in clipped
    assert len(clipped) < 300_000


def test_like_patterns_cover_both_json_encodings_of_a_cjk_query():
    patterns = like_patterns("中文")
    assert any("\\u4e2d" in p for p in patterns), "ensure_ascii form"
    assert any("中文" in p for p in patterns), "raw form"


def test_like_patterns_neutralise_sql_wildcards():
    (pattern,) = like_patterns("100%_x")
    assert pattern == f"%100{LIKE_ESCAPE}%{LIKE_ESCAPE}_x%"


def test_body_window_always_contains_the_hit_even_in_a_tiny_window():
    """A 6-line quote must not come back with the match scrolled off it."""
    body = "\n".join(["pad"] * 8 + ["redis here"] + ["pad"] * 8)
    win = body_window(body, "redis", lines=6)
    assert "redis here" in win
    assert len(win.split("\n")) == 6


def test_body_window_keeps_the_documents_opening_for_an_early_hit():
    body = "\n".join(["first"] + ["pad"] * 3 + ["redis"] + ["pad"] * 80)
    assert body_window(body, "redis", lines=34).startswith("first")
