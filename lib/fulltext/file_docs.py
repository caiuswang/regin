"""The two file-backed kinds: `pattern` (patterns, wiki pages, skills, rules)
and `file` (files an agent touched).

Both reduce to the same move — ask ripgrep which files hold the query and how
often, then read only the winners to build their titles and preview windows.
The corpus is on disk, so nothing here touches the span store.
"""

from __future__ import annotations

import os

from lib.fulltext.docs import (
    KIND_FILE, KIND_PATTERN, SearchDoc, Scored, body_window, clip_body,
    rank, score_doc,
)
from lib.fulltext.grep import count_matches, read_text
from lib.rules.grit_rule_index import load_rules_index
from lib.settings import settings

MARKDOWN_GLOBS = ["*.md", "*.markdown"]
MARKDOWN_SUFFIXES = (".md", ".markdown")

# A skill or pattern tree routinely vendors a whole Python env or node tree
# alongside its own prose. Indexing those buries every real page under
# third-party README noise (and duplicates any doc that exists in two trees).
VENDOR_DIRS = (".venv", "venv", "node_modules", "site-packages", ".git",
               "__pycache__", "dist", "build")
VENDOR_GLOBS = [f"!**/{name}/**" for name in VENDOR_DIRS]


def _title_of(text: str, fallback: str) -> str:
    """A markdown document's first heading at any level, else its filename.

    Any level, because regin's own wiki pages open at `##` — anchoring on
    `# ` alone titled every one of them with its raw filename.
    """
    for line in text.split("\n", 40)[:40]:
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            if title:
                return title
    return fallback


def _summary_of(text: str) -> str:
    """The first non-heading, non-empty line — the page's one-liner."""
    for line in text.split("\n", 60)[:60]:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and not stripped.startswith("---"):
            return stripped[:200]
    return ""


def _heading_anchor(text: str, query: str) -> str:
    """The slug of the nearest heading ABOVE the first hit, so the row can
    deep-link to the section the match is in rather than the page top."""
    if not query:
        return ""
    needle = query.lower()
    anchor = ""
    for line in text.split("\n"):
        if line.startswith("#"):
            title = line.lstrip("#").strip()
            anchor = "".join(
                ch if ch.isalnum() else "-" for ch in title.lower()
            ).strip("-")
        elif needle in line.lower():
            return anchor
    return ""


def _pattern_roots() -> list[tuple[str, str, str]]:
    """`(root, tag, href_prefix)` per markdown corpus behind the `pattern`
    kind. Skills and rules ride here too: they are documents the old ⌘K
    palette could already reach, and the group's tag names which is which."""
    roots = [
        (str(settings.patterns_dir), "pattern", "/patterns/"),
        (str(settings.skills_dir), "skill", "/skills/"),
    ]
    for repo in settings.repo_paths or []:
        wiki = os.path.join(str(repo), ".regin", "topics", "wiki")
        if os.path.isdir(wiki):
            roots.append((wiki, "wiki", f"/repos/{os.path.basename(str(repo))}/topics?topic="))
    seen: set[str] = set()
    out = []
    for root, tag, href in roots:
        real = os.path.realpath(root)
        if not os.path.isdir(root) or real in seen:
            continue
        seen.add(real)
        out.append((root, tag, href))
    return out


def _slug_for(path: str, root: str) -> str:
    rel = os.path.relpath(path, root)
    for suffix in MARKDOWN_SUFFIXES:
        if rel.endswith(suffix):
            rel = rel[: -len(suffix)]
            break
    return rel


def _dedupe_key(title: str, slug: str) -> tuple[str, str]:
    """The same guide routinely exists in BOTH the patterns tree and the
    skills tree — deployed to one from the other. They are separate files at
    separate paths, and their bytes differ (regin writes frontmatter into its
    own copy), so neither a path nor a content key catches the pair. Identity
    does: same title at the same slug is the same page, and the first root
    wins because `_pattern_roots` lists the canonical one first.
    """
    return (title, slug)


def _rule_docs() -> list[SearchDoc]:
    """Lint rules as search documents.

    Rules are the one corpus in this kind that is NOT markdown on disk — they
    live in the generated `.grit/rules.json` index — so ripgrep cannot reach
    them and they are read from the index instead. They belong here because a
    rule is a document the reader looks up by name or summary, exactly like a
    pattern or a wiki page.
    """
    out: list[SearchDoc] = []
    for rule in load_rules_index().get("rules", []):
        rule_id = rule.get("id") or ""
        if not rule_id:
            continue
        guide = rule.get("guide") or ""
        body = "\n".join(
            str(rule.get(k) or "")
            for k in ("summary", "layer", "severity", "guide")
        ).strip()
        out.append(SearchDoc(
            kind=KIND_PATTERN,
            id=f"rule/{rule_id}",
            title=rule_id,
            sub=rule.get("summary") or guide,
            tag="rule",
            crumb=f"rule · {guide}" if guide else "rule",
            body=body,
            mono=False,
            href=f"/rules/{rule_id}",
        ))
    return out


def search_pattern_docs(query: str, limit: int) -> list[Scored]:
    scored: list[Scored] = []
    seen_docs: set[tuple[str, str]] = set()
    order = 0
    for root, tag, href_prefix in _pattern_roots():
        counts = count_matches([root], query, globs=MARKDOWN_GLOBS + VENDOR_GLOBS,
                               suffixes=MARKDOWN_SUFFIXES)
        for path in sorted(counts, key=lambda p: -counts[p]):
            text = clip_body(read_text(path))
            if not text:
                continue
            slug = _slug_for(path, root)
            anchor = _heading_anchor(text, query)
            doc = SearchDoc(
                kind=KIND_PATTERN,
                id=f"{tag}/{slug}",
                title=_title_of(text, os.path.basename(slug)),
                sub=_summary_of(text),
                tag=tag,
                crumb=f"{tag} · {slug}",
                body=text,
                mono=False,
                href=(f"{href_prefix}{slug}" if href_prefix.endswith("=")
                      else f"{href_prefix}{slug}" + (f"#{anchor}" if anchor else "")),
            )
            key = _dedupe_key(doc.title, slug)
            if key in seen_docs:
                continue
            hit = score_doc(doc, query, order)
            order += 1
            if hit:
                seen_docs.add(key)
                hit.doc.body = body_window(doc.body, query)
                scored.append(hit)
    for doc in _rule_docs():
        hit = score_doc(doc, query, order)
        order += 1
        if hit:
            scored.append(hit)
    return rank(scored)[:limit]


def search_file_docs(touched: list[dict], query: str, limit: int) -> list[Scored]:
    """Rank files an agent touched. `touched` rows come from the span store
    (`{path, span_id, trace_id, additions, deletions}`), so a hit deep-links
    to the edit span that produced the state rather than to a file viewer
    regin does not have.
    """
    by_path = {row["path"]: row for row in touched if row.get("path")}
    existing = [p for p in by_path if os.path.isfile(p)]
    if not existing or not query:
        return []
    counts = count_matches(existing, query, globs=[], suffixes=())
    scored: list[Scored] = []
    for order, path in enumerate(sorted(counts, key=lambda p: -counts[p])):
        hit = _score_file(path, by_path.get(path) or {}, query, order)
        if hit:
            scored.append(hit)
    return rank(scored)[:limit]


def _score_file(path: str, row: dict, query: str, order: int) -> Scored | None:
    doc = _file_doc(path, row)
    if doc is None:
        return None
    hit = score_doc(doc, query, order)
    if hit:
        hit.doc.body = body_window(doc.body, query)
    return hit


def _diff_tag(row: dict) -> str:
    adds, dels = row.get("additions"), row.get("deletions")
    return f"+{adds or 0} −{dels or 0}" if (adds or dels) else ""


def _edit_span_href(row: dict) -> str:
    """A touched file opens at the edit span that produced its current
    state — regin has no standalone file viewer to send it to."""
    trace_id, span_id = row.get("trace_id"), row.get("span_id")
    return f"/trace/sessions/{trace_id}?span={span_id}" if trace_id and span_id else ""


def _file_doc(path: str, row: dict) -> SearchDoc | None:
    text = clip_body(read_text(path))
    if not text:
        return None
    return SearchDoc(
        kind=KIND_FILE,
        id=path,
        title=os.path.basename(path),
        sub=path,
        tag=_diff_tag(row),
        crumb=f"file · {path}",
        body=text,
        mono=True,
        href=_edit_span_href(row),
    )
