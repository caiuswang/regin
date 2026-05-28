"""Tests for generated approved-topic wiki files."""

from __future__ import annotations

import pytest

from lib.topics.wiki import generate_wiki, render_index, render_topic_page
from lib.topics import bootstrap, load_graph, save_graph


def test_render_index_links_topics():
    content = render_index({
        "repo": "repo",
        "topics": {
            "web": {"label": "Web", "intent": "Web API."},
        },
    })

    assert "# Topic Wiki: repo" in content
    assert "[Web](web.md) - Web API." in content


def test_render_topic_page_groups_refs_by_role():
    content = render_topic_page("web", {
        "label": "Web",
        "aliases": ["api"],
        "intent": "Web API.",
        "refs": [
            {"path": "tests/test_web.py", "role": "test"},
            {"path": "web/app.py", "role": "entrypoint"},
        ],
        "edges": [{"type": "related", "target": "cli"}],
        "commands": ["pytest tests/test_web.py"],
        "include_globs": ["web/**"],
        "exclude_globs": ["web/generated/**"],
    }, {"cli": {"label": "CLI"}})

    assert "# Web" in content
    assert "### Entrypoint" in content
    assert "- `web/app.py`" in content
    assert "### Test" in content
    assert "- related: `cli` (CLI)" in content


def test_generate_wiki_writes_only_index(fake_git_repo):
    """Phase-E follow-up: generate_wiki no longer overwrites per-topic
    files. Only `index.md` is rewritten from the graph; per-topic
    content is owned by accept-from-proposal (`_persist_per_topic_wiki`).
    """
    (fake_git_repo / "web").mkdir()
    (fake_git_repo / "web" / "app.py").write_text("app")
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["web"] = {
        "label": "Web",
        "aliases": ["api"],
        "intent": "Web API.",
        "status": "active",
        "refs": [{"path": "web/app.py", "role": "entrypoint"}],
        "edges": [],
        "commands": [],
        "include_globs": ["web/**"],
        "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    # Plant a rich per-topic wiki page that generate_wiki should NOT clobber.
    wiki_dir = fake_git_repo / ".regin/topics/wiki"
    wiki_dir.mkdir(parents=True, exist_ok=True)
    rich = "# Web (custom narrative)\n\nUser-authored content here."
    (wiki_dir / "web.md").write_text(rich)

    written = generate_wiki(fake_git_repo)

    assert written == [fake_git_repo / ".regin/topics/wiki/index.md"]
    # The custom narrative is intact.
    assert (fake_git_repo / ".regin/topics/wiki/web.md").read_text() == rich
    # Index still lists the topic.
    assert "Web API." in (fake_git_repo / ".regin/topics/wiki/index.md").read_text()


def test_generate_wiki_rejects_invalid_graph(fake_git_repo):
    bootstrap(fake_git_repo)
    graph = load_graph(fake_git_repo)
    graph["topics"]["bad"] = {
        "label": "Bad",
        "aliases": [],
        "intent": "Bad ref.",
        "status": "active",
        "refs": [{"path": "missing.py", "role": "implementation"}],
        "edges": [],
        "commands": [],
        "include_globs": [],
        "exclude_globs": [],
    }
    save_graph(fake_git_repo, graph)

    with pytest.raises(ValueError, match="ref does not exist"):
        generate_wiki(fake_git_repo)
