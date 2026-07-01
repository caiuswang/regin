"""Tests for splitting a combined proposal wiki into per-topic sections."""

from __future__ import annotations

from lib.topics.wiki_sections import assign_wiki_sections, split_wiki_sections

_WIKI = """# The Main Agent Loop

Shared intro tying the topics together.

## Agent Loop Engine

Body about the loop engine.

## Agent Tool Execution

Body about tool execution.

## Append-Only Context & Prefix Caching

Body about prefix caching.
"""


def _topics() -> list[dict]:
    return [
        {"id": "agent-loop-engine", "label": "Agent Loop Engine"},
        {"id": "agent-tool-execution", "label": "Agent Tool Execution"},
        {"id": "agent-append-only-context", "label": "Append-Only Context & Prefix Caching"},
    ]


def test_split_returns_intro_and_sections():
    intro, sections = split_wiki_sections(_WIKI)
    assert intro.startswith("# The Main Agent Loop")
    assert "Shared intro" in intro
    assert [title for title, _ in sections] == [
        "Agent Loop Engine",
        "Agent Tool Execution",
        "Append-Only Context & Prefix Caching",
    ]
    # Each section keeps its own heading and stops before the next one.
    assert sections[0][1].startswith("## Agent Loop Engine")
    assert "tool execution" not in sections[0][1].lower()


def test_assign_maps_each_topic_to_a_distinct_section():
    assigned = assign_wiki_sections(_WIKI, _topics())
    assert set(assigned) == {
        "agent-loop-engine",
        "agent-tool-execution",
        "agent-append-only-context",
    }
    # Distinct content per topic — the whole point of the fix.
    bodies = list(assigned.values())
    assert len(set(bodies)) == 3
    assert "loop engine" in assigned["agent-loop-engine"].lower()
    assert "tool execution" in assigned["agent-tool-execution"].lower()


def test_match_falls_back_to_id_slug_when_label_differs():
    # Label doesn't match the heading, but the id slug does.
    topics = [{"id": "agent-loop-engine", "label": "Totally Different Label"}]
    assigned = assign_wiki_sections(_WIKI, topics)
    assert "loop engine" in assigned["agent-loop-engine"].lower()


def test_unmatched_topic_is_omitted_for_fallback():
    topics = _topics() + [{"id": "unrelated", "label": "Nothing Here"}]
    assigned = assign_wiki_sections(_WIKI, topics)
    assert "unrelated" not in assigned  # caller falls back to the full wiki


def test_wiki_with_no_headings_yields_no_sections():
    intro, sections = split_wiki_sections("Just prose, no headings at all.")
    assert intro == "Just prose, no headings at all."
    assert sections == []
    assert assign_wiki_sections("Just prose.", _topics()) == {}


def test_empty_wiki_is_safe():
    assert split_wiki_sections("") == ("", [])
    assert assign_wiki_sections("", _topics()) == {}


def test_matches_legacy_topic_n_prefixed_heading():
    # Real legacy shape: heading has a `Topic N —` prefix, label has a
    # trailing parenthetical qualifier — neither is exact-equal.
    wiki = (
        "## Topic 1 — Bootstrap a regin install\n\nBootstrap body.\n\n"
        "## Topic 2 — Route a query\n\nRoute body.\n"
    )
    topics = [
        {"id": "bootstrap", "label": "Bootstrap a regin install (CLI-only steps)"},
        {"id": "route", "label": "Route a query (keyword matcher)"},
    ]
    assigned = assign_wiki_sections(wiki, topics)
    assert "Bootstrap body." in assigned["bootstrap"]
    assert "Route body." in assigned["route"]
    assert assigned["bootstrap"] != assigned["route"]


def test_token_overlap_matches_reworded_heading():
    wiki = "## The Session Trace Ingest Pipeline\n\nTrace body.\n"
    topics = [{"id": "trace", "label": "Session trace ingest"}]
    assigned = assign_wiki_sections(wiki, topics)
    assert "Trace body." in assigned["trace"]


def test_unrelated_heading_not_force_matched():
    wiki = "## Completely Different Subject\n\nBody.\n"
    topics = [{"id": "auth", "label": "Authentication and login"}]
    assert assign_wiki_sections(wiki, topics) == {}


def test_two_topics_never_claim_the_same_section():
    # Two topics that would both match the one heading — only one wins.
    topics = [
        {"id": "agent-loop-engine", "label": "Agent Loop Engine"},
        {"id": "agent-loop-engine-dup", "label": "Agent Loop Engine"},
    ]
    assigned = assign_wiki_sections(_WIKI, topics)
    assert list(assigned) == ["agent-loop-engine"]
