"""Unit tests for lib.patterns.pattern_promoter.

lib/pattern_promoter.py was at 0% coverage — this exercises the pure
helpers, DB read, filesystem collectors, zip-bundle builder, multipart
encoder, and the HTTP availability check (with urllib stubbed).

The network-dependent `promote()` end-to-end flow isn't tested (it
would require a mock HTTP server); `_post_bundle` is covered via a
stubbed urlopen. `_git_head` is monkeypatched to a deterministic value
so bundles are reproducible.
"""

from __future__ import annotations

import io
import json
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from lib.settings import settings
from sqlmodel import select

from lib.patterns import pattern_promoter as pp
from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDoc, Tag


# ── fixtures ─────────────────────────────────────────────────

@pytest.fixture
def promoter_env(tmp_path, monkeypatch):
    patterns = tmp_path / "patterns"
    scripts = tmp_path / "scripts"
    grit = tmp_path / ".grit"
    patterns.mkdir()
    scripts.mkdir()
    grit.mkdir()

    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(settings, "project_root", str(tmp_path))
    monkeypatch.setattr(settings, "skillhub_url", "http://127.0.0.1:8322")
    # Deterministic git head so bundle checksums are stable.
    monkeypatch.setattr(pp, "_git_head", lambda: "deadbeef")
    # No grit rules by default.
    from lib.rules import grit_rule_index as gri
    monkeypatch.setattr(gri, "rules_for_guide", lambda _slug: [])
    yield {
        "root": tmp_path,
        "patterns": patterns,
        "scripts": scripts,
        "grit": grit,
    }


def _seed_pattern(patterns_dir, slug: str, *,
                   title: str = "My Pattern",
                   description_para: str = "Short description para.",
                   tags: list[str] | None = None,
                   source_repos: list[str] | None = None,
                   display_title: str | None = None,
                   references: dict[str, str] | None = None,
                   pattern_scripts: dict[str, str] | None = None):
    dir_ = patterns_dir / slug
    dir_.mkdir()
    fm_lines = [
        "---",
        f'title: "{title}"',
        f"procedure: {slug}",
    ]
    if source_repos is not None:
        fm_lines.append(
            f"source_repos: [{', '.join(source_repos)}]"
        )
    if display_title is not None:
        fm_lines.append(f"display_title: {display_title}")
    if tags is not None:
        fm_lines.append(f"tags: [{', '.join(tags)}]")
    fm_lines.append("---")
    body = (
        "\n# Heading\n\n"
        f"{description_para}\n\n"
        "## Disciplines\n\n- do this\n"
    )
    (dir_ / "SKILL.md").write_text("\n".join(fm_lines) + body)

    if references:
        refs_dir = dir_ / "references"
        refs_dir.mkdir()
        for rel, content in references.items():
            p = refs_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)

    if pattern_scripts:
        scripts_dir = dir_ / "scripts"
        scripts_dir.mkdir()
        for rel, content in pattern_scripts.items():
            p = scripts_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)

    return dir_


# ── _resolve_url ─────────────────────────────────────────────

def test_resolve_url_uses_override():
    assert pp._resolve_url("http://example.com/") == "http://example.com"


def test_resolve_url_falls_back_to_setting(monkeypatch):
    monkeypatch.setattr(settings, "skillhub_url", "http://default/")
    assert pp._resolve_url(None) == "http://default"


def test_resolve_url_raises_when_empty(monkeypatch):
    monkeypatch.setattr(settings, "skillhub_url", "")
    with pytest.raises(pp.PromoteError, match="no skillhub_url"):
        pp._resolve_url(None)


# ── _parse_frontmatter ───────────────────────────────────────

def test_parse_frontmatter_basic():
    text = '---\ntitle: "Hello"\nprocedure: slug\n---\nbody\n'
    fm, body = pp._parse_frontmatter(text)
    assert fm["title"] == "Hello"
    assert fm["procedure"] == "slug"
    assert body == "body\n"


def test_parse_frontmatter_list_value():
    text = "---\nsource_repos: [a, b]\n---\nbody\n"
    fm, _ = pp._parse_frontmatter(text)
    assert fm["source_repos"] == ["a", "b"]


def test_parse_frontmatter_empty_list():
    text = "---\ntags: []\n---\nbody"
    fm, _ = pp._parse_frontmatter(text)
    assert fm["tags"] == []


def test_parse_frontmatter_no_marker_returns_empty():
    fm, body = pp._parse_frontmatter("plain body")
    assert fm == {}
    assert body == "plain body"


# ── _strip_inline_frontmatter ────────────────────────────────

def test_strip_inline_frontmatter_removes_block():
    body = "# Title\n\n---\ntitle: X\n---\nreal prose\n"
    out = pp._strip_inline_frontmatter(body)
    assert "title: X" not in out
    assert "real prose" in out


def test_strip_inline_frontmatter_noop_when_no_fm():
    body = "just prose\n\nmore"
    assert pp._strip_inline_frontmatter(body) == body


# ── _derive_description ──────────────────────────────────────

def test_derive_description_joins_title_and_first_para():
    out = pp._derive_description("My Pat", "# H\n\nFirst paragraph.\n\nsecond")
    assert out == "My Pat. First paragraph."


def test_derive_description_falls_back_to_title_when_body_empty():
    out = pp._derive_description("Lone", "")
    assert "Lone" in out


# ── _compute_checksum ────────────────────────────────────────

def test_compute_checksum_is_deterministic():
    h1 = pp._compute_checksum("shim", "body", {"a.md": b"x"})
    h2 = pp._compute_checksum("shim", "body", {"a.md": b"x"})
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_compute_checksum_sensitive_to_content():
    h1 = pp._compute_checksum("shim", "body", {})
    h2 = pp._compute_checksum("shim", "body!", {})
    assert h1 != h2


def test_compute_checksum_domain_separates_refs_and_extras():
    """Same payload moved from refs → extras must yield a different hash."""
    h_ref = pp._compute_checksum("s", "b", {"x.md": b"data"})
    h_extra = pp._compute_checksum("s", "b", {}, extras={"x.md": b"data"})
    assert h_ref != h_extra


# ── _read_pattern ────────────────────────────────────────────

def test_read_pattern_missing_raises(promoter_env):
    with pytest.raises(pp.PromoteError, match="no SKILL.md"):
        pp._read_pattern("nonexistent")


def test_read_pattern_returns_frontmatter_and_body(promoter_env):
    _seed_pattern(promoter_env["patterns"], "basic",
                    description_para="The summary.")
    fm, body = pp._read_pattern("basic")
    assert fm["procedure"] == "basic"
    assert "The summary." in body


# ── _fetch_pattern_metadata ──────────────────────────────────

def test_fetch_pattern_metadata_returns_title_and_tags(tmp_db):
    with SessionLocal() as session:
        doc = PatternDoc(
            slug="p1", title="DB Title",
            file_path="patterns/p1/SKILL.md",
            category="procedure", content_hash="0" * 64,
        )
        session.add(doc)
        session.flush()
        t1 = Tag(name="tag-a", category="concept")
        t2 = Tag(name="tag-b", category="concept")
        session.add(t1)
        session.add(t2)
        session.flush()
        session.add(DocTag(doc_id=doc.id, tag_id=t1.id))
        session.add(DocTag(doc_id=doc.id, tag_id=t2.id))
        session.commit()

    title, tags = pp._fetch_pattern_metadata("p1")
    assert title == "DB Title"
    assert tags == ["tag-a", "tag-b"]


def test_fetch_pattern_metadata_unknown_slug(tmp_db):
    title, tags = pp._fetch_pattern_metadata("unknown")
    assert title is None
    assert tags == []


# ── collectors ──────────────────────────────────────────────

def test_collect_references_walks_subdirs(promoter_env):
    _seed_pattern(
        promoter_env["patterns"], "refs",
        references={"a.md": "A", "sub/b.md": "B"},
    )
    refs = pp._collect_references("refs")
    assert refs["a.md"] == b"A"
    assert refs["sub/b.md"] == b"B"


def test_collect_references_missing_dir_returns_empty(promoter_env):
    _seed_pattern(promoter_env["patterns"], "norefs")
    assert pp._collect_references("norefs") == {}


def test_collect_pattern_scripts_walks(promoter_env):
    _seed_pattern(
        promoter_env["patterns"], "scripted",
        pattern_scripts={"go.sh": "#!/bin/sh\necho hi"},
    )
    out = pp._collect_pattern_scripts("scripted")
    assert "go.sh" in out
    assert out["go.sh"].startswith(b"#!/bin/sh")


def test_collect_runner_scripts_reads_from_root(promoter_env):
    (promoter_env["scripts"] / "check_grit.sh").write_text(
        "#!/bin/sh\necho check"
    )
    (promoter_env["scripts"] / "find_applicable_files.py").write_text(
        "# python"
    )
    out = pp._collect_runner_scripts()
    assert "check_grit.sh" in out
    assert "find_applicable_files.py" in out


def test_collect_runner_scripts_missing_scripts_dir(promoter_env):
    # Scripts dir is present from fixture but empty → returns {}
    assert pp._collect_runner_scripts() == {}


def test_collect_grit_rules_empty_when_no_rules(promoter_env):
    files, rules_json, ids = pp._collect_grit_rules("anything")
    assert files == {}
    assert rules_json == {}
    assert ids == []


def test_collect_grit_rules_with_rules(promoter_env, monkeypatch):
    # Stub rules_for_guide to return one rule; write the source file.
    src_rel = ".grit/patterns/java/my_rule.grit"
    (promoter_env["grit"] / "patterns" / "java").mkdir(parents=True)
    (promoter_env["root"] / src_rel).write_text("pattern my_rule() {}")

    from lib.rules import grit_rule_index as gri
    monkeypatch.setattr(gri, "rules_for_guide", lambda _slug: [{
        "id": "my_rule", "layer": "entity",
        "triggers": ["*Entity.java"], "severity": "error",
        "guide": "some-slug", "summary": "s",
        "source_file": src_rel,
    }])

    files, rules_json, ids = pp._collect_grit_rules("some-slug")
    assert ids == ["my_rule"]
    assert "patterns/java/my_rule.grit" in files
    assert rules_json["by_layer"] == {"entity": ["my_rule"]}


# ── build_bundle ────────────────────────────────────────────

def test_build_bundle_rejects_invalid_slug(promoter_env):
    with pytest.raises(pp.PromoteError, match="invalid pattern slug"):
        pp.build_bundle("Not-A-Slug")


def test_build_bundle_rejects_invalid_version(promoter_env):
    _seed_pattern(promoter_env["patterns"], "ok")
    with pytest.raises(pp.PromoteError, match="invalid version"):
        pp.build_bundle("ok", version="vNEXT")


def test_build_bundle_produces_valid_zip(promoter_env, tmp_db):
    _seed_pattern(
        promoter_env["patterns"], "demo",
        source_repos=["svc-a", "svc-b"],
    )
    filename, data = pp.build_bundle("demo", version="1.2.3",
                                       author="tester")
    assert filename == "demo-1.2.3.zip"
    assert zipfile.is_zipfile(io.BytesIO(data))

    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert {"manifest.json", "SKILL.md", "content.md"} <= names

        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["schema_version"] == pp.SCHEMA_VERSION
        assert manifest["name"] == "demo"
        assert manifest["version"] == "1.2.3"
        assert manifest["author"] == "tester"
        assert manifest["origin"]["source_commit"] == "deadbeef"
        assert manifest["origin"]["source_repos"] == ["svc-a", "svc-b"]
        assert manifest["checksum"].startswith("sha256:")
        # No rules or scripts → no bundled block.
        assert "bundled" not in manifest

        shim = zf.read("SKILL.md").decode()
        assert f"name: demo" in shim
        assert "content.md" in shim


def test_build_bundle_uses_db_tags_over_frontmatter(promoter_env, tmp_db):
    # Pattern has frontmatter tag 'fm-tag'; DB has 'db-tag' — DB wins.
    _seed_pattern(
        promoter_env["patterns"], "tagged",
        tags=["fm-tag"],
    )
    with SessionLocal() as session:
        doc = PatternDoc(
            slug="tagged", title="X",
            file_path="patterns/tagged/SKILL.md",
            category="procedure", content_hash="0" * 64,
        )
        session.add(doc)
        session.flush()
        t = Tag(name="db-tag", category="concept")
        session.add(t)
        session.flush()
        session.add(DocTag(doc_id=doc.id, tag_id=t.id))
        session.commit()

    _, data = pp.build_bundle("tagged")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["tags"] == ["db-tag"]


def test_build_bundle_honors_display_title(promoter_env, tmp_db):
    _seed_pattern(
        promoter_env["patterns"], "titled",
        display_title="Pretty Display",
    )
    _, data = pp.build_bundle("titled")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["title"] == "Pretty Display"


def test_build_bundle_includes_references_and_scripts(
        promoter_env, tmp_db):
    _seed_pattern(
        promoter_env["patterns"], "withfiles",
        references={"note.md": "a note"},
        pattern_scripts={"do.sh": "#!/bin/sh\necho"},
    )
    _, data = pp.build_bundle("withfiles")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = set(zf.namelist())
        assert "references/note.md" in names
        assert "scripts/do.sh" in names
        manifest = json.loads(zf.read("manifest.json"))
        assert manifest["bundled"]["scripts"] == ["do.sh"]


# ── _build_multipart ────────────────────────────────────────

def test_build_multipart_contains_bundle_and_force():
    body, ct = pp._build_multipart("x.zip", b"FAKE", force=True)
    assert ct.startswith("multipart/form-data; boundary=")
    text = body.decode("latin-1")
    assert 'filename="x.zip"' in text
    assert "application/zip" in text
    assert "FAKE" in text
    assert "true" in text  # force=true


def test_build_multipart_force_false_sends_false():
    body, _ = pp._build_multipart("y.zip", b"Z", force=False)
    assert b"\r\nfalse\r\n" in body


# ── is_available ────────────────────────────────────────────

def test_is_available_happy_path(monkeypatch):
    monkeypatch.setattr(settings, "skillhub_url", "http://skillhub")
    fake_response = MagicMock()
    fake_response.status = 200
    fake_response.read.return_value = b'{"service": "regin-skillhub"}'
    fake_response.__enter__ = lambda self: self
    fake_response.__exit__ = lambda *a: None

    with patch("urllib.request.urlopen", return_value=fake_response):
        out = pp.is_available()
    assert out["available"] is True
    assert out["url"] == "http://skillhub"


def test_is_available_wrong_service(monkeypatch):
    monkeypatch.setattr(settings, "skillhub_url", "http://skillhub")
    fake = MagicMock()
    fake.status = 200
    fake.read.return_value = b'{"service": "something-else"}'
    fake.__enter__ = lambda self: self
    fake.__exit__ = lambda *a: None

    with patch("urllib.request.urlopen", return_value=fake):
        out = pp.is_available()
    assert out["available"] is False
    assert "not regin-skillhub" in out["reason"]


def test_is_available_http_error(monkeypatch):
    import urllib.error
    monkeypatch.setattr(settings, "skillhub_url", "http://skillhub")

    def boom(*a, **kw):
        raise urllib.error.URLError("connection refused")

    with patch("urllib.request.urlopen", side_effect=boom):
        out = pp.is_available()
    assert out["available"] is False
    assert "cannot reach" in out["reason"]


def test_is_available_no_url_configured(monkeypatch):
    monkeypatch.setattr(settings, "skillhub_url", "")
    out = pp.is_available()
    assert out["available"] is False
    assert "no skillhub_url" in out["reason"]


def test_is_available_non_200_status(monkeypatch):
    monkeypatch.setattr(settings, "skillhub_url", "http://skillhub")
    fake = MagicMock()
    fake.status = 503
    fake.read.return_value = b"{}"
    fake.__enter__ = lambda self: self
    fake.__exit__ = lambda *a: None
    with patch("urllib.request.urlopen", return_value=fake):
        out = pp.is_available()
    assert out["available"] is False
    assert "HTTP 503" in out["reason"]


def test_is_available_bad_json_response(monkeypatch):
    monkeypatch.setattr(settings, "skillhub_url", "http://skillhub")
    fake = MagicMock()
    fake.status = 200
    fake.read.return_value = b"{ not json"
    fake.__enter__ = lambda self: self
    fake.__exit__ = lambda *a: None
    with patch("urllib.request.urlopen", return_value=fake):
        out = pp.is_available()
    assert out["available"] is False
    assert "unexpected error" in out["reason"]


# ── _parse_frontmatter continuation + edge cases ────────────

def test_parse_frontmatter_continuation_line_joins():
    text = '---\ndescription: first line\n  continued\n---\nbody\n'
    fm, _ = pp._parse_frontmatter(text)
    assert "continued" in fm["description"]


def test_parse_frontmatter_no_closing_marker_returns_empty():
    text = "---\nname: x\nno close marker"
    fm, body = pp._parse_frontmatter(text)
    assert fm == {}
    assert body == text


# ── _git_head ───────────────────────────────────────────────

def test_git_head_returns_unknown_when_git_not_available(monkeypatch):
    import subprocess
    def boom(*a, **kw):
        raise FileNotFoundError("git missing")
    monkeypatch.setattr(subprocess, "check_output", boom)
    assert pp._git_head() == "unknown"


def test_git_head_returns_unknown_on_non_repo(monkeypatch):
    import subprocess
    def boom(*a, **kw):
        raise subprocess.CalledProcessError(128, "git")
    monkeypatch.setattr(subprocess, "check_output", boom)
    assert pp._git_head() == "unknown"


# ── _post_bundle ────────────────────────────────────────────

def test_post_bundle_success(monkeypatch):
    fake = MagicMock()
    fake.read.return_value = b'{"ok": true, "skill_id": "x"}'
    fake.__enter__ = lambda self: self
    fake.__exit__ = lambda *a: None
    with patch("urllib.request.urlopen", return_value=fake):
        out = pp._post_bundle("http://hub", "x.zip", b"bytes", False)
    assert out == {"ok": True, "skill_id": "x"}


def test_post_bundle_http_error_wraps_as_promote_error():
    import io
    import urllib.error
    err = urllib.error.HTTPError(
        "http://hub/api/import", 409, "conflict", {},
        io.BytesIO(b""),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(pp.PromoteError, match="HTTP 409"):
            pp._post_bundle("http://hub", "x.zip", b"bytes", False)


def test_post_bundle_http_error_with_json_detail():
    import io
    import urllib.error
    err = urllib.error.HTTPError(
        "http://hub/api/import", 400, "bad", {},
        io.BytesIO(b'{"error": "slug already taken"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(pp.PromoteError, match="slug already taken"):
            pp._post_bundle("http://hub", "x.zip", b"bytes", False)


def test_post_bundle_url_error_wraps_as_promote_error():
    import urllib.error

    def boom(*a, **kw):
        raise urllib.error.URLError("connection refused")

    with patch("urllib.request.urlopen", side_effect=boom):
        with pytest.raises(pp.PromoteError, match="cannot reach"):
            pp._post_bundle("http://hub", "x.zip", b"bytes", False)


# ── promote() end-to-end with stubbed HTTP ──────────────────

def test_promote_end_to_end_success(
        promoter_env, tmp_db, monkeypatch):
    _seed_pattern(promoter_env["patterns"], "promo-ok",
                    source_repos=["svc-a"])
    fake_post = MagicMock(return_value={"ok": True, "skill_id": "promo-ok"})
    monkeypatch.setattr(pp, "_post_bundle", fake_post)

    result = pp.promote("promo-ok", version="2.0.0",
                         skillhub_url="http://hub")
    assert result["slug"] == "promo-ok"
    assert result["version"] == "2.0.0"
    assert result["bundle_filename"] == "promo-ok-2.0.0.zip"
    assert result["url"] == "http://hub"
    # _post_bundle was called with the right URL shape.
    fake_post.assert_called_once()
    args, _ = fake_post.call_args
    assert args[0] == "http://hub"
    assert args[1] == "promo-ok-2.0.0.zip"


def test_promote_rejected_by_server_raises(
        promoter_env, tmp_db, monkeypatch):
    _seed_pattern(promoter_env["patterns"], "promo-err")
    monkeypatch.setattr(
        pp, "_post_bundle",
        lambda url, filename, data, force: {"ok": False,
                                              "error": "slug already exists"},
    )
    with pytest.raises(pp.PromoteError,
                        match="slug already exists"):
        pp.promote("promo-err", skillhub_url="http://hub")


def test_promote_ok_without_error_message_falls_back(
        promoter_env, tmp_db, monkeypatch):
    _seed_pattern(promoter_env["patterns"], "pe")
    monkeypatch.setattr(
        pp, "_post_bundle",
        lambda url, filename, data, force: {"ok": False},
    )
    with pytest.raises(pp.PromoteError, match="import failed"):
        pp.promote("pe", skillhub_url="http://hub")
