"""Unit tests for lib.db_rebuild.

lib/db_rebuild.py was at 0% coverage. Covers the pure helpers
(_parse_frontmatter, _content_hash),
DB-touching helpers (_seed_tags, _rebuild_patterns, _backup_local_tables,
_restore_local_tables, _recreate_schema, _rediscover_repos) and the
end-to-end rebuild_from_files orchestration.
"""

from __future__ import annotations

import hashlib

from lib import db_rebuild as dbr
from lib.orm.engine import get_connection
from lib.settings import settings


def _stats() -> dict:
    """Fresh stats dict matching what rebuild_from_files() builds internally."""
    return {
        "backed_up": {},
        "restored": {},
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


# ── _backup_local_tables / _restore_local_tables ────────────

def test_backup_and_restore_local_tables(tmp_db):
    conn = get_connection()
    try:
        # Seed an experiments row (schema: pattern_slug, name, conceal_spec).
        conn.execute(
            "INSERT INTO experiments (pattern_slug, name, conceal_spec) "
            "VALUES (?, ?, ?)",
            ("some-proc", "test-exp", '{"sections": ["Disciplines"]}'),
        )
        conn.commit()

        backup = dbr._backup_local_tables(conn, _stats())
        assert "experiments" in backup
        assert len(backup["experiments"]["rows"]) == 1

        # Wipe the table and restore.
        conn.execute("DELETE FROM experiments")
        conn.commit()
        dbr._restore_local_tables(conn, backup, _stats())
        rows = conn.execute(
            "SELECT pattern_slug FROM experiments"
        ).fetchall()
        assert rows[0]["pattern_slug"] == "some-proc"
    finally:
        conn.close()


def test_backup_local_tables_handles_missing_table(tmp_db, monkeypatch):
    """Tables listed in _LOCAL_TABLES but absent from DB don't crash the backup."""
    monkeypatch.setattr(dbr, "_LOCAL_TABLES",
                        {"experiments", "nonexistent_table"})
    conn = get_connection()
    try:
        backup = dbr._backup_local_tables(conn, _stats())
        # nonexistent_table silently skipped.
        assert "nonexistent_table" not in backup
    finally:
        conn.close()


def test_restore_local_tables_empty_backup_is_noop(tmp_db):
    conn = get_connection()
    try:
        dbr._restore_local_tables(conn, {}, _stats())
    finally:
        conn.close()


# ── _recreate_schema ────────────────────────────────────────

def test_recreate_schema_drops_and_rebuilds(tmp_db):
    conn = get_connection()
    try:
        # Create an extra table not in the schema.
        conn.execute("CREATE TABLE junk (x INTEGER)")
        conn.execute("INSERT INTO junk VALUES (1)")
        conn.commit()

        dbr._recreate_schema(conn)

        # junk table gone.
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='junk'"
        ).fetchone()
        assert row is None

        # pattern_docs exists (part of the real schema).
        row = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='pattern_docs'"
        ).fetchone()
        assert row is not None
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

def test_rebuild_from_files_end_to_end(tmp_db, monkeypatch, tmp_path):
    # Seed a pattern, a tags.yaml, and an experiments row that must
    # survive the rebuild.
    patterns = tmp_path / "patterns"
    patterns.mkdir()
    (patterns / "rebuild-me").mkdir()
    (patterns / "rebuild-me" / "SKILL.md").write_text(
        "---\ntitle: Rebuilt\nsource_repos: [svc]\n---\nbody\n"
    )

    tags_yaml = tmp_path / "tags.yaml"
    tags_yaml.write_text("concept:\n  - rebuild-smoke\n")

    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(settings, "project_root", str(tmp_path))
    monkeypatch.setattr(settings, "tags_path", str(tags_yaml))

    # Stub repo discovery so the rebuild doesn't try to scan real
    # sibling directories.
    from lib.sync import repo_discovery
    monkeypatch.setattr(repo_discovery, "scan_repos", lambda: [])
    monkeypatch.setattr(repo_discovery, "register_repos",
                        lambda repos: {"added": 0, "updated": 0})

    # Seed an experiments row we expect to preserve.
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO experiments (pattern_slug, name, conceal_spec) "
            "VALUES (?, ?, ?)",
            ("p", "preserve-me", '{"sections": []}'),
        )
        conn.commit()
    finally:
        conn.close()

    dbr.rebuild_from_files(preserve_local=True)

    conn = get_connection()
    try:
        # Pattern doc exists.
        row = conn.execute(
            "SELECT title FROM pattern_docs WHERE slug = 'rebuild-me'"
        ).fetchone()
        assert row is not None
        assert row["title"] == "Rebuilt"

        # Tag seeded.
        row = conn.execute(
            "SELECT 1 FROM tags WHERE name = 'rebuild-smoke'"
        ).fetchone()
        assert row is not None

        # Experiment preserved.
        row = conn.execute(
            "SELECT 1 FROM experiments WHERE pattern_slug = 'p'"
        ).fetchone()
        assert row is not None
    finally:
        conn.close()


def test_rebuild_from_files_without_preserve_local_drops_experiments(
        tmp_db, monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "patterns_dir", str(tmp_path / "patterns"))
    monkeypatch.setattr(settings, "project_root", str(tmp_path))
    monkeypatch.setattr(settings, "tags_path", str(tmp_path / "missing.yaml"))
    from lib.sync import repo_discovery
    monkeypatch.setattr(repo_discovery, "scan_repos", lambda: [])
    monkeypatch.setattr(repo_discovery, "register_repos",
                        lambda repos: {"added": 0, "updated": 0})

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO experiments (pattern_slug, name, conceal_spec) "
            "VALUES (?, ?, ?)",
            ("p", "drop-me", '{"sections": []}'),
        )
        conn.commit()
    finally:
        conn.close()

    dbr.rebuild_from_files(preserve_local=False)

    conn = get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM experiments"
        ).fetchone()[0]
        assert count == 0  # not preserved
    finally:
        conn.close()
