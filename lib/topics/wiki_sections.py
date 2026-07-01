"""Split a combined proposal wiki into per-topic sections.

A topic-proposal run holds a single `wiki_md` document authored with a
shared intro followed by one level-2 (`## `) section per proposed topic.
The review UI shows one topic at a time, so it needs each topic's own
section rather than the whole document repeated under every topic. These
pure helpers do that mapping (heading text -> topic) with no I/O, so they
are cheap to unit-test.
"""

from __future__ import annotations

import re
from typing import Any

_H2_RE = re.compile(r"^##[ \t]+(?P<title>.+?)[ \t]*$", re.MULTILINE)


_TOPIC_PREFIX_RE = re.compile(r"^\s*topic\s*\d+\s*[—:\-–]\s*", re.IGNORECASE)


def _norm(text: str) -> str:
    """Case/whitespace-insensitive key for matching a heading to a label."""
    return re.sub(r"\s+", " ", (text or "").strip()).casefold()


def _slug(text: str) -> str:
    """Lowercase alnum slug (`Agent Loop Engine` -> `agent-loop-engine`)."""
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (text or "").casefold())).strip("-")


def _core(text: str) -> str:
    """Heading/label with a leading ``Topic N —`` prefix and a trailing
    parenthetical qualifier stripped, so ``Topic 1 — Bootstrap a regin install``
    and ``Bootstrap a regin install (CLI-only steps)`` reduce to the same key."""
    core = _TOPIC_PREFIX_RE.sub("", text or "")
    core = re.sub(r"\s*\(.*\)\s*$", "", core)
    return _norm(core)


def _tokens(text: str) -> set[str]:
    """Content tokens (length > 2) for fuzzy heading↔label overlap."""
    return {t for t in re.split(r"[^a-z0-9]+", (text or "").casefold()) if len(t) > 2}


def _token_score(label: str, title: str) -> float:
    """Thresholded Jaccard token overlap — the conservative last resort so an
    unrelated topic is left unmatched (UI falls back) rather than mis-assigned."""
    label_tokens, title_tokens = _tokens(label), _tokens(title)
    if not (label_tokens and title_tokens):
        return 0.0
    jaccard = len(label_tokens & title_tokens) / len(label_tokens | title_tokens)
    return 1.0 + jaccard if jaccard >= 0.4 else 0.0


def _match_score(label: str, topic_id: str, title: str) -> float:
    """How strongly a topic (label + id) matches a section heading. 0 = no
    match. Exact/slug are decisive; prefix-stripped equality and containment
    are strong; token overlap is the last resort. Used only by the one-time
    legacy backfill."""
    if _norm(label) and _norm(label) == _norm(title):
        return 5.0
    if _slug(topic_id) and _slug(topic_id) == _slug(title):
        return 5.0
    label_core, title_core = _core(label), _core(title)
    if label_core and label_core == title_core:
        return 4.0
    if label_core and title_core and (label_core in title_core or title_core in label_core):
        return 3.0
    return _token_score(label, title)


def split_wiki_sections(wiki_md: str) -> tuple[str, list[tuple[str, str]]]:
    """Return ``(intro, [(heading_title, section_md), ...])``.

    ``intro`` is the text before the first ``## `` heading (empty if the
    document opens with one). Each section spans from its ``## `` heading up
    to the next ``## `` (or end of document) and keeps its own heading line.
    A document with no ``## `` headings yields ``(wiki_md, [])``.
    """
    if not wiki_md:
        return "", []
    matches = list(_H2_RE.finditer(wiki_md))
    if not matches:
        return wiki_md, []
    intro = wiki_md[: matches[0].start()].strip()
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(wiki_md)
        sections.append((match.group("title"), wiki_md[match.start() : end].strip()))
    return intro, sections


def _scored_pairs(
    topics: list[dict[str, Any]], sections: list[tuple[str, str]]
) -> list[tuple[float, str, int]]:
    """All (score, topic_id, section_index) matches with score > 0, best first."""
    pairs: list[tuple[float, str, int]] = []
    for topic in topics:
        topic_id = topic.get("id")
        if not isinstance(topic_id, str) or not topic_id:
            continue
        label = topic.get("label") or ""
        for index, (title, _body) in enumerate(sections):
            score = _match_score(label, topic_id, title)
            if score > 0:
                pairs.append((score, topic_id, index))
    pairs.sort(key=lambda pair: -pair[0])
    return pairs


def assign_wiki_sections(
    wiki_md: str, topics: list[dict[str, Any]]
) -> dict[str, str]:
    """Map each topic id to its own wiki section (best-effort, one-time legacy
    backfill only — the live path stores per-topic wiki directly).

    Scores every topic↔heading pair (exact label / id-slug, then prefix-strip,
    containment, thresholded token overlap) and greedily assigns highest
    confidence first, each section and topic claimed once. Topics with no
    positive-scoring section are omitted, so the caller falls back to the full
    wiki rather than showing a blank or mis-assigned pane.
    """
    _, sections = split_wiki_sections(wiki_md)
    if not sections:
        return {}
    assigned: dict[str, str] = {}
    used_sections: set[int] = set()
    for _score, topic_id, index in _scored_pairs(topics, sections):
        if topic_id in assigned or index in used_sections:
            continue
        assigned[topic_id] = sections[index][1]
        used_sections.add(index)
    return assigned
