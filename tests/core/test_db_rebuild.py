"""Unit tests for lib.db_rebuild.

Covers the pure helpers (_parse_frontmatter, _content_hash), the
DB-touching helpers (_seed_tags, _rebuild_patterns, _recreate_cache_tables,
_rediscover_repos), the end-to-end rebuild_from_files orchestration, and the
rule that a rebuild touches nothing outside the cache tables.
"""

from __future__ import annotations

import hashlib

from lib import db_rebuild as dbr
from lib.orm.engine import get_connection
from lib.settings import settings


def _stats() -> dict:
    """Fresh stats dict matching what rebuild_from_files() builds internally."""
    return {
        "tags_seeded": None,
        "patterns_rebuilt": 0,
        "patterns_skipped": [],
        "repos": None,
    }


# ── _parse_frontmatter ───────────────────────────────────────

def test_parse_frontmatter_reads_valid(tmp_path):
    f = tmp_path / "skill.md"
    f.write_text('---\ntitle: Hello\nprocedure: slug\n---\nbody\n')
    out = dbr._parse_frontmatter(str(f))
    assert out == {"title": "Hello", "procedure": "slug"}


def test_parse_frontmatter_missing_file_returns_none(tmp_path):
    assert dbr._parse_frontmatter(str(tmp_path / "nope.md")) is None


def test_parse_frontmatter_no_fm_returns_none(tmp_path):
    f = tmp_path / "plain.md"
    f.write_text("plain markdown")
    assert dbr._parse_frontmatter(str(f)) is None


def test_parse_frontmatter_missing_close_returns_none(tmp_path):
    f = tmp_path / "broken.md"
    f.write_text("---\ntitle: x\nno closing")
    assert dbr._parse_frontmatter(str(f)) is None


def test_parse_frontmatter_invalid_yaml_returns_none(tmp_path):
    f = tmp_path / "bad.md"
    f.write_text("---\ntitle: [unclosed\n---\nbody")
    assert dbr._parse_frontmatter(str(f)) is None


# ── _content_hash ────────────────────────────────────────────

def test_content_hash_sha256(tmp_path):
    f = tmp_path / "a.bin"
    payload = b"hello world"
    f.write_bytes(payload)
    assert dbr._content_hash(str(f)) == hashlib.sha256(payload).hexdigest()


def test_content_hash_missing_file_returns_none(tmp_path):
    assert dbr._content_hash(str(tmp_path / "nope.txt")) is None


# ── _seed_tags ───────────────────────────────────────────────

def test_seed_tags_missing_config_is_noop(tmp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "tags_path",
                        str(tmp_path / "nope.yaml"))
    conn = get_connection()
    try:
        before = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        dbr._seed_tags(conn, _stats())
        after = conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        assert after == before
    finally:
        conn.close()


def test_seed_tags_inserts_new_tags_from_yaml(
        tmp_db, monkeypatch, tmp_path):
    tags_yaml = tmp_path / "tags.yaml"
    tags_yaml.write_text(
        "layer:\n  - xxx-brand-new\n"
        "concept:\n  - yyy-fresh\n"
    )
    monkeypatch.setattr(settings, "tags_path", str(tags_yaml))

    conn = get_connection()
    try:
        dbr._seed_tags(conn, _stats())
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM tags WHERE name IN "
            "('xxx-brand-new', 'yyy-fresh')"
        ).fetchall()}
        assert names == {"xxx-brand-new", "yyy-fresh"}
    finally:
        conn.close()


def test_seed_tags_is_idempotent(tmp_db, monkeypatch, tmp_path):
    tags_yaml = tmp_path / "tags.yaml"
    tags_yaml.write_text("concept:\n  - duplicate-tag\n")
    monkeypatch.setattr(settings, "tags_path", str(tags_yaml))

    conn = get_connection()
    try:
        dbr._seed_tags(conn, _stats())
        dbr._seed_tags(conn, _stats())  # second pass
        count = conn.execute(
            "SELECT COUNT(*) FROM tags WHERE name = ?",
            ("duplicate-tag",),
        ).fetchone()[0]
        assert count == 1
    finally:
        conn.close()


# ── _rebuild_patterns ───────────────────────────────────────

def test_rebuild_patterns_inserts_from_skill_md(
        tmp_db, monkeypatch, tmp_path):
    patterns = tmp_path / "patterns"
    patterns.mkdir()
    slug_dir = patterns / "api-demo"
    slug_dir.mkdir()
    (slug_dir / "SKILL.md").write_text(
        '---\ntitle: "API Demo"\n---\nbody\n'
    )
    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(settings, "project_root", str(tmp_path))

    conn = get_connection()
    try:
        dbr._rebuild_patterns(conn, _stats())
        row = conn.execute(
            "SELECT slug, title FROM pattern_docs WHERE slug=?",
            ("api-demo",),
        ).fetchone()
        assert row is not None
        assert row["title"] == "API Demo"
    finally:
        conn.close()


def test_rebuild_patterns_skips_missing_frontmatter(
        tmp_db, monkeypatch, tmp_path):
    patterns = tmp_path / "patterns"
    patterns.mkdir()
    slug_dir = patterns / "plain"
    slug_dir.mkdir()
    (slug_dir / "SKILL.md").write_text("no frontmatter here")
    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(settings, "project_root", str(tmp_path))

    conn = get_connection()
    try:
        dbr._rebuild_patterns(conn, _stats())
        row = conn.execute(
            "SELECT 1 FROM pattern_docs WHERE slug = 'plain'"
        ).fetchone()
        assert row is None
    finally:
        conn.close()


def test_rebuild_patterns_ignores_underscore_dirs(
        tmp_db, monkeypatch, tmp_path):
    patterns = tmp_path / "patterns"
    patterns.mkdir()
    (patterns / "_index").mkdir()
    (patterns / "_index" / "SKILL.md").write_text(
        "---\ntitle: idx\n---\n"
    )
    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(settings, "project_root", str(tmp_path))

    conn = get_connection()
    try:
        dbr._rebuild_patterns(conn, _stats())
        row = conn.execute(
            "SELECT 1 FROM pattern_docs WHERE slug = '_index'"
        ).fetchone()
        assert row is None
    finally:
        conn.close()


# ── _recreate_cache_tables ──────────────────────────────────

def test_recreate_cache_tables_empties_only_the_cache(tmp_db):
    conn = get_connection()
    try:
        conn.execute("INSERT INTO tags (name, category) VALUES ('t', 'concept')")
        conn.execute("CREATE TABLE junk (x INTEGER)")
        conn.execute("INSERT INTO junk VALUES (1)")
        conn.commit()

        dbr._recreate_cache_tables(conn)

        assert conn.execute("SELECT COUNT(*) FROM tags").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM junk").fetchone()[0] == 1
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='trigger' "
            "AND name='pattern_docs_ad_patterns_fts'").fetchone()
    finally:
        conn.close()


# ── _rediscover_repos ───────────────────────────────────────

def test_rediscover_repos_calls_repo_discovery(monkeypatch):
    from lib.sync import repo_discovery
    calls = []
    monkeypatch.setattr(repo_discovery, "scan_repos",
                        lambda: calls.append("scan") or [])
    monkeypatch.setattr(repo_discovery, "register_repos",
                        lambda repos: {"added": 0, "updated": 0})
    dbr._rediscover_repos(_stats())
    assert calls == ["scan"]


def test_rediscover_repos_swallows_exceptions(monkeypatch):
    from lib.sync import repo_discovery

    def boom():
        raise RuntimeError("discovery failed")

    monkeypatch.setattr(repo_discovery, "scan_repos", boom)
    # Must not raise.
    dbr._rediscover_repos(_stats())


# ── rebuild_from_files ──────────────────────────────────────

def _stub_files(monkeypatch, tmp_path, repos=()):
    monkeypatch.setattr(settings, "patterns_dir", str(tmp_path / "patterns"))
    monkeypatch.setattr(settings, "project_root", str(tmp_path))
    monkeypatch.setattr(settings, "tags_path", str(tmp_path / "missing.yaml"))
    from lib.sync import repo_discovery
    monkeypatch.setattr(repo_discovery, "scan_repos", lambda: list(repos))


def test_rebuild_from_files_end_to_end(tmp_db, monkeypatch, tmp_path):
    patterns = tmp_path / "patterns"
    (patterns / "rebuild-me").mkdir(parents=True)
    (patterns / "rebuild-me" / "SKILL.md").write_text(
        "---\ntitle: Rebuilt\nsource_repos: [svc]\n---\nbody\n"
    )
    tags_yaml = tmp_path / "tags.yaml"
    tags_yaml.write_text("concept:\n  - rebuild-smoke\n")
    _stub_files(monkeypatch, tmp_path)
    monkeypatch.setattr(settings, "tags_path", str(tags_yaml))

    dbr.rebuild_from_files()

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT title FROM pattern_docs WHERE slug = 'rebuild-me'"
        ).fetchone()
        assert row["title"] == "Rebuilt"
        assert conn.execute(
            "SELECT 1 FROM tags WHERE name = 'rebuild-smoke'").fetchone()
    finally:
        conn.close()


_NON_CACHE = ("sessions", "session_repos", "features", "agent_tasks",
              "api_keys", "experiments", "alembic_version")


def _counts(conn) -> dict:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in _NON_CACHE}


def test_rebuild_keeps_non_cache_tables(tmp_db, monkeypatch, tmp_path):
    from cli.commands import db as db_cmd
    from lib import db_migrate

    repo_path = str(tmp_path / "svc")
    _stub_files(monkeypatch, tmp_path, repos=[
        {"name": "svc", "path": repo_path, "default_branch": "main"}])
    db_migrate.stamp_head()
    conn = get_connection()
    try:
        conn.execute("INSERT INTO repos (id, name, path, default_branch) "
                     "VALUES (7, 'svc', ?, 'main')", (repo_path,))
        conn.execute("INSERT INTO sessions (trace_id, started_at, last_seen) "
                     "VALUES ('t1', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO session_repos (trace_id, repo_id) "
                     "VALUES ('t1', 7)")
        conn.execute("INSERT INTO features (title) VALUES ('f')")
        conn.execute("INSERT INTO agent_tasks (title, source) "
                     "VALUES ('t', 'manual')")
        conn.execute("INSERT INTO api_keys (name, key_hash, key_prefix) "
                     "VALUES ('k', 'h', 'p')")
        conn.execute("INSERT INTO experiments (pattern_slug, name, "
                     "conceal_spec) VALUES ('p', 'e', '{}')")
        conn.commit()
        before = _counts(conn)
        stamp = conn.execute(
            "SELECT version_num FROM alembic_version").fetchall()
    finally:
        conn.close()

    db_cmd.cmd_rebuild()

    conn = get_connection()
    try:
        assert _counts(conn) == before
        assert conn.execute(
            "SELECT version_num FROM alembic_version").fetchall() == stamp
        # The repo a session points at keeps its id across the rebuild.
        assert conn.execute(
            "SELECT id FROM repos WHERE name = 'svc'").fetchone()[0] == 7
    finally:
        conn.close()
