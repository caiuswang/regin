"""Unit tests for lib.patterns.pattern_importer.

Exercises the three public entry points (import_zip, import_skill_md,
import_upload) plus the helpers migrated to SQLModel in Phase B.4.5
(_register_pattern_doc, _apply_manifest_tags).

Uses tmp_db + PATTERNS_DIR monkeypatch to isolate each test; constructs
minimal-valid SKILL.md and bundle zips in tmp_path.
"""

from __future__ import annotations

import json
import os
import zipfile

import pytest
from sqlmodel import select

from lib.patterns import pattern_importer as pi
from lib.orm import SessionLocal
from lib.orm.models import DocTag, PatternDoc, Tag
from lib.settings import settings


# ── fixtures ─────────────────────────────────────────────────

@pytest.fixture
def tmp_patterns_dir(tmp_path, monkeypatch):
    """Redirect PATTERNS_DIR so import_* doesn't touch the user's repo."""
    patterns = tmp_path / "patterns"
    patterns.mkdir()
    monkeypatch.setattr(settings, "patterns_dir", str(patterns))
    monkeypatch.setattr(settings, "project_root", str(tmp_path))
    return patterns


def _make_bundle(tmp_path, *, slug: str = "demo-pattern",
                 description: str = "Demo pattern for tests",
                 include_content: bool = False,
                 include_manifest: bool = True,
                 manifest_extra: dict | None = None,
                 extras: dict[str, str] | None = None,
                 wrap_top_level: bool = False) -> str:
    """Build a minimal valid zip bundle, return its path."""
    staging = tmp_path / "staging"
    staging.mkdir()
    inner = staging / slug if wrap_top_level else staging
    if wrap_top_level:
        inner.mkdir()

    skill_body = (
        f"---\nname: {slug}\ndescription: {description}\n---\n"
        "# Demo\n\nBody text here.\n"
    )
    (inner / "SKILL.md").write_text(skill_body)

    if include_content:
        # Small shim SKILL.md + separate content.md — the canonical
        # regin-skillhub shape.
        shim = (
            f"---\nname: {slug}\ndescription: {description}\n---\n"
            "See content.md for the actual procedure.\n"
        )
        (inner / "SKILL.md").write_text(shim)
        (inner / "content.md").write_text(
            "# Real Content\n\n## Disciplines\n\n- Do the thing\n"
        )

    if include_manifest:
        manifest = {
            "name": slug,
            "title": "Imported Demo Title",
            "version": "1.0.0",
            "origin": {"source_repo": "example-service"},
            "tags": ["alpha-tag", "beta-tag"],
        }
        if manifest_extra:
            manifest.update(manifest_extra)
        (inner / "manifest.json").write_text(json.dumps(manifest))

    if extras:
        for rel, content in extras.items():
            dst = inner / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(content)

    zip_path = tmp_path / f"{slug}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for dirpath, _, filenames in os.walk(staging):
            for fn in filenames:
                abs_p = os.path.join(dirpath, fn)
                rel = os.path.relpath(abs_p, staging)
                zf.write(abs_p, rel)
    return str(zip_path)


# ── _parse_frontmatter ───────────────────────────────────────

def test_parse_frontmatter_basic():
    text = '---\nname: foo\ntitle: "Hello World"\n---\nbody\n'
    fm, body = pi._parse_frontmatter(text)
    assert fm["name"] == "foo"
    assert fm["title"] == "Hello World"
    assert body == "body\n"


def test_parse_frontmatter_no_frontmatter_passes_through():
    fm, body = pi._parse_frontmatter("just body\n")
    assert fm == {}
    assert body == "just body\n"


def test_parse_frontmatter_missing_close_returns_empty():
    # Starts with --- but no terminator → treated as body.
    fm, body = pi._parse_frontmatter("---\nname: x\nbody without close")
    assert fm == {}


def test_parse_frontmatter_continuation_line_joins():
    text = '---\ndescription: first line\n  continued\n---\nbody\n'
    fm, _ = pi._parse_frontmatter(text)
    assert "continued" in fm["description"]


# ── _validate_slug ───────────────────────────────────────────

def test_validate_slug_accepts_kebab():
    pi._validate_slug("my-pattern-123")


def test_validate_slug_rejects_empty():
    with pytest.raises(pi.ImportError_):
        pi._validate_slug("")


def test_validate_slug_rejects_uppercase():
    with pytest.raises(pi.ImportError_):
        pi._validate_slug("MyPattern")


def test_validate_slug_rejects_leading_digit():
    with pytest.raises(pi.ImportError_):
        pi._validate_slug("1pattern")


# ── _derive_title ────────────────────────────────────────────

def test_derive_title_uses_skill_name_not_description():
    fm = {"name": "my-skill", "description": "A short one. The rest goes away."}
    assert pi._derive_title(fm, "my-skill") == "my-skill"


def test_derive_title_falls_back_to_procedure():
    fm = {"procedure": "my-proc"}
    assert pi._derive_title(fm, "my-proc") == "my-proc"


def test_derive_title_final_fallback_is_slug():
    fm = {}
    assert pi._derive_title(fm, "my-pattern") == "my-pattern"


# ── _build_pattern_skill_md ──────────────────────────────────

def test_build_pattern_skill_md_has_regin_frontmatter():
    out = pi._build_pattern_skill_md("slug", "Title", "body-text\n")
    assert out.startswith("---\n")
    assert "procedure: slug" in out
    assert 'title: "Title"' in out
    assert "source_repos: [imported]" in out
    assert "manual: true" in out
    assert "body-text" in out


def test_build_pattern_skill_md_preserves_manifest_description():
    manifest = {"description": "Use before every agent task."}
    out = pi._build_pattern_skill_md("slug", "Title", "body-text\n", manifest)

    assert 'description: "Use before every agent task."' in out


def test_build_pattern_skill_md_honours_manifest_origin():
    manifest = {"origin": {"source_repo": "svc-x", "source_repos": ["y"]}}
    out = pi._build_pattern_skill_md("slug", "T", "b", manifest)
    assert "svc-x" in out and "y" in out


def test_build_pattern_skill_md_dedupes_manifest_origin_repos():
    manifest = {"origin": {"source_repo": "svc-x", "source_repos": ["svc-x", "y"]}}
    out = pi._build_pattern_skill_md("slug", "T", "b", manifest)

    assert "source_repos: [svc-x, y]" in out


def test_build_pattern_skill_md_escapes_quotes_in_title():
    out = pi._build_pattern_skill_md("slug", 'Has "quotes"', "b")
    assert 'title: "Has \\"quotes\\""' in out


# ── _choose_body ─────────────────────────────────────────────

def test_choose_body_prefers_content_md_when_shim_is_short():
    shim = "See content.md for details."
    content = "## Real body"
    assert pi._choose_body(shim, content) == content


def test_choose_body_keeps_shim_when_no_content_md():
    shim = "real body here"
    assert pi._choose_body(shim, None) == shim


def test_choose_body_keeps_shim_when_large():
    shim = "a" * 2000 + " content.md"
    # >1200 chars → treated as the real body, not a shim.
    assert pi._choose_body(shim, "other") == shim


# ── _register_pattern_doc ────────────────────────────────────

def test_register_pattern_doc_inserts_and_returns_id(
        tmp_db, tmp_patterns_dir, tmp_path):
    skill_path = tmp_path / "patterns" / "demo" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("content")

    doc_id = pi._register_pattern_doc("demo", "Demo", str(skill_path))
    assert doc_id > 0

    with SessionLocal() as session:
        row = session.exec(
            select(PatternDoc).where(PatternDoc.slug == "demo")
        ).first()
        assert row is not None
        assert row.title == "Demo"
        assert row.content_hash  # sha256 populated


def test_register_pattern_doc_rejects_duplicate_slug(
        tmp_db, tmp_patterns_dir, tmp_path):
    skill_path = tmp_path / "patterns" / "dup" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("content")

    pi._register_pattern_doc("dup", "X", str(skill_path))
    with pytest.raises(pi.ImportError_):
        pi._register_pattern_doc("dup", "X", str(skill_path))


# ── _apply_manifest_tags ─────────────────────────────────────

def test_apply_manifest_tags_creates_and_links(
        tmp_db, tmp_patterns_dir, tmp_path):
    skill_path = tmp_path / "patterns" / "tagged" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("content")
    doc_id = pi._register_pattern_doc("tagged", "X", str(skill_path))

    pi._apply_manifest_tags(doc_id, ["novel-tag-a", "novel-tag-b"])

    with SessionLocal() as session:
        names = {
            t.name for t in session.exec(
                select(Tag).where(
                    Tag.name.in_(["novel-tag-a", "novel-tag-b"])
                )
            ).all()
        }
        assert names == {"novel-tag-a", "novel-tag-b"}

        links = session.exec(
            select(DocTag).where(DocTag.doc_id == doc_id)
        ).all()
        assert len(links) == 2


def test_apply_manifest_tags_idempotent_on_second_call(
        tmp_db, tmp_patterns_dir, tmp_path):
    skill_path = tmp_path / "patterns" / "idem" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("content")
    doc_id = pi._register_pattern_doc("idem", "X", str(skill_path))

    pi._apply_manifest_tags(doc_id, ["reuse-tag"])
    pi._apply_manifest_tags(doc_id, ["reuse-tag"])  # second pass

    with SessionLocal() as session:
        links = session.exec(
            select(DocTag).where(DocTag.doc_id == doc_id)
        ).all()
        assert len(links) == 1  # no duplicate link


def test_apply_manifest_tags_empty_list_is_noop(
        tmp_db, tmp_patterns_dir, tmp_path):
    skill_path = tmp_path / "patterns" / "notags" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("content")
    doc_id = pi._register_pattern_doc("notags", "X", str(skill_path))

    pi._apply_manifest_tags(doc_id, [])

    with SessionLocal() as session:
        links = session.exec(
            select(DocTag).where(DocTag.doc_id == doc_id)
        ).all()
        assert links == []


def test_apply_manifest_tags_skips_blank_names(
        tmp_db, tmp_patterns_dir, tmp_path):
    skill_path = tmp_path / "patterns" / "blank" / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text("content")
    doc_id = pi._register_pattern_doc("blank", "X", str(skill_path))

    pi._apply_manifest_tags(doc_id, ["   ", "", "real-one"])

    with SessionLocal() as session:
        links = session.exec(
            select(DocTag).where(DocTag.doc_id == doc_id)
        ).all()
        assert len(links) == 1
        tag = session.exec(
            select(Tag).where(Tag.id == links[0].tag_id)
        ).first()
        assert tag.name == "real-one"


# ── import_zip ───────────────────────────────────────────────

def test_import_zip_success_minimal(
        tmp_db, tmp_patterns_dir, tmp_path):
    zip_path = _make_bundle(tmp_path, slug="basic-import",
                              include_content=False,
                              include_manifest=False)
    result = pi.import_zip(zip_path)

    assert result.slug == "basic-import"
    assert result.shape == "zip"
    assert result.doc_id > 0
    assert result.file_count >= 1

    # Pattern dir exists with regin-format SKILL.md.
    skill = tmp_patterns_dir / "basic-import" / "SKILL.md"
    assert skill.exists()
    content = skill.read_text()
    assert "procedure: basic-import" in content
    assert "manual: true" in content


def test_import_zip_prefers_manifest_title_and_tags(
        tmp_db, tmp_patterns_dir, tmp_path):
    zip_path = _make_bundle(tmp_path, slug="with-manifest",
                              include_manifest=True)
    result = pi.import_zip(zip_path)

    assert result.title == "Imported Demo Title"

    # Tags from manifest were attached.
    with SessionLocal() as session:
        links = session.exec(
            select(DocTag).where(DocTag.doc_id == result.doc_id)
        ).all()
        assert len(links) == 2


def test_import_zip_folds_content_md_into_body(
        tmp_db, tmp_patterns_dir, tmp_path):
    zip_path = _make_bundle(tmp_path, slug="split-bundle",
                              include_content=True,
                              include_manifest=False)
    result = pi.import_zip(zip_path)

    skill = tmp_patterns_dir / result.slug / "SKILL.md"
    text = skill.read_text()
    assert "## Disciplines" in text
    # content.md itself should NOT be copied alongside.
    assert not (tmp_patterns_dir / result.slug / "content.md").exists()


def test_import_zip_copies_extras(
        tmp_db, tmp_patterns_dir, tmp_path):
    zip_path = _make_bundle(
        tmp_path, slug="with-extras",
        include_manifest=False,
        extras={"references/note.md": "note text"},
    )
    result = pi.import_zip(zip_path)
    assert (tmp_patterns_dir / result.slug
            / "references" / "note.md").exists()


def test_import_zip_collapses_top_level_wrapper(
        tmp_db, tmp_patterns_dir, tmp_path):
    zip_path = _make_bundle(tmp_path, slug="wrapped",
                              wrap_top_level=True,
                              include_manifest=False)
    result = pi.import_zip(zip_path)
    assert result.slug == "wrapped"
    assert (tmp_patterns_dir / "wrapped" / "SKILL.md").exists()


def test_import_zip_rejects_missing_file(tmp_db, tmp_patterns_dir, tmp_path):
    with pytest.raises(pi.ImportError_, match="file not found"):
        pi.import_zip(str(tmp_path / "nope.zip"))


def test_import_zip_rejects_non_zip(tmp_db, tmp_patterns_dir, tmp_path):
    fake = tmp_path / "plain.zip"
    fake.write_text("not a zip")
    with pytest.raises(pi.ImportError_, match="not a zip archive"):
        pi.import_zip(str(fake))


def test_import_zip_rejects_zip_slip(tmp_db, tmp_patterns_dir, tmp_path):
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../escape.txt", "bad")
        zf.writestr("SKILL.md", "---\nname: x\n---\nbody")
    with pytest.raises(pi.ImportError_, match="unsafe path"):
        pi.import_zip(str(zip_path))


def test_import_zip_rejects_missing_skill_md(
        tmp_db, tmp_patterns_dir, tmp_path):
    zip_path = tmp_path / "noskill.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("README.md", "# nothing")
    with pytest.raises(pi.ImportError_, match="missing SKILL.md"):
        pi.import_zip(str(zip_path))


def test_import_zip_rejects_existing_pattern_dir(
        tmp_db, tmp_patterns_dir, tmp_path):
    # Pre-create the target dir.
    (tmp_patterns_dir / "already-here").mkdir()

    zip_path = _make_bundle(tmp_path, slug="already-here",
                              include_manifest=False)
    with pytest.raises(pi.ImportConflictError, match="already exists"):
        pi.import_zip(zip_path)


def test_import_zip_force_overwrites_existing_pattern_dir(
        tmp_db, tmp_patterns_dir, tmp_path):
    """force=True replaces an existing pattern on disk and in DB."""
    zip_path = _make_bundle(tmp_path, slug="force-overwrite",
                              include_manifest=False)
    first = pi.import_zip(zip_path)
    assert first.file_count >= 1

    # Re-import with force=True — use a fresh tmp_path to avoid
    # _make_bundle staging collision.
    tmp_path_v2 = tmp_path / "v2"
    tmp_path_v2.mkdir()
    zip_path_v2 = _make_bundle(tmp_path_v2, slug="force-overwrite",
                                 include_manifest=False,
                                 extras={"references/v2.md": "v2"})
    second = pi.import_zip(zip_path_v2, force=True)
    assert second.doc_id != first.doc_id
    assert (tmp_patterns_dir / "force-overwrite" / "references" / "v2.md").exists()


def test_import_zip_target_slug_renames_on_import(
        tmp_db, tmp_patterns_dir, tmp_path):
    """target_slug overrides the slug derived from SKILL.md frontmatter."""
    zip_path = _make_bundle(tmp_path, slug="orig-slug",
                              include_manifest=False)
    result = pi.import_zip(zip_path, target_slug="renamed-slug")
    assert result.slug == "renamed-slug"
    assert (tmp_patterns_dir / "renamed-slug" / "SKILL.md").exists()


def test_import_zip_target_slug_avoids_conflict(
        tmp_db, tmp_patterns_dir, tmp_path):
    """Using target_slug to side-step an existing pattern."""
    zip_path = _make_bundle(tmp_path, slug="avoid-me",
                              include_manifest=False)
    pi.import_zip(zip_path)

    # Same bundle, different slug.
    result = pi.import_zip(zip_path, target_slug="avoid-me-too")
    assert result.slug == "avoid-me-too"


def test_import_zip_rolls_back_on_duplicate_slug(
        tmp_db, tmp_patterns_dir, tmp_path):
    """If _register_pattern_doc raises, the freshly-created patterns/
    directory should be cleaned up."""
    # Seed the DB with a pre-existing pattern_doc for 'collides'.
    with SessionLocal() as session:
        session.add(PatternDoc(
            slug="collides", title="Existing", file_path="patterns/collides/SKILL.md",
            category="procedure", content_hash="0" * 64,
        ))
        session.commit()

    zip_path = _make_bundle(tmp_path, slug="collides",
                              include_manifest=False)
    with pytest.raises(pi.ImportConflictError):
        pi.import_zip(zip_path)

    # pattern_dir was cleaned up after rollback.
    assert not (tmp_patterns_dir / "collides").exists()


def test_import_zip_tolerates_bad_manifest_json(
        tmp_db, tmp_patterns_dir, tmp_path):
    """Manifest with invalid JSON is treated as absent — no crash."""
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "SKILL.md").write_text(
        "---\nname: bad-manifest\ndescription: d\n---\n# Hi\nbody"
    )
    (staging / "manifest.json").write_text("{ not json")

    zip_path = tmp_path / "bm.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for name in ("SKILL.md", "manifest.json"):
            zf.write(staging / name, name)

    result = pi.import_zip(str(zip_path))
    assert result.slug == "bad-manifest"


# ── import_skill_md ──────────────────────────────────────────

def test_import_skill_md_from_str(tmp_db, tmp_patterns_dir):
    md = (
        "---\nname: bare-md\ndescription: One-liner.\n---\n"
        "# Heading\n\nbody"
    )
    result = pi.import_skill_md(md)
    assert result.shape == "skill-md"
    assert result.slug == "bare-md"
    assert result.file_count == 1
    assert (tmp_patterns_dir / "bare-md" / "SKILL.md").exists()


def test_import_skill_md_from_bytes(tmp_db, tmp_patterns_dir):
    md = b"---\nname: bytes-md\ndescription: x\n---\nbody"
    result = pi.import_skill_md(md)
    assert result.slug == "bytes-md"


def test_import_skill_md_rejects_missing_name(tmp_db, tmp_patterns_dir):
    md = "---\ndescription: no name\n---\nbody"
    with pytest.raises(pi.ImportError_, match="missing `name:`"):
        pi.import_skill_md(md)


def test_import_skill_md_rollback_on_duplicate(tmp_db, tmp_patterns_dir):
    md = "---\nname: md-dup\ndescription: x\n---\nbody"
    pi.import_skill_md(md)
    # Second call: patterns/md-dup/ already exists → ImportConflictError.
    with pytest.raises(pi.ImportConflictError, match="already exists"):
        pi.import_skill_md(md)


def test_import_skill_md_force_overwrites(tmp_db, tmp_patterns_dir):
    md = "---\nname: md-force\ndescription: x\n---\nbody"
    first = pi.import_skill_md(md)
    second = pi.import_skill_md(md, force=True)
    assert second.doc_id != first.doc_id


def test_import_skill_md_target_slug_renames(tmp_db, tmp_patterns_dir):
    md = "---\nname: orig\ndescription: x\n---\nbody"
    result = pi.import_skill_md(md, target_slug="custom-name")
    assert result.slug == "custom-name"
    assert (tmp_patterns_dir / "custom-name" / "SKILL.md").exists()


# ── import_upload ────────────────────────────────────────────

def test_import_upload_dispatches_md(tmp_db, tmp_patterns_dir):
    md = b"---\nname: upload-md\ndescription: x\n---\nbody"
    result = pi.import_upload("upload.md", md)
    assert result.slug == "upload-md"
    assert result.shape == "skill-md"


def test_import_upload_dispatches_zip(
        tmp_db, tmp_patterns_dir, tmp_path):
    zip_path = _make_bundle(tmp_path, slug="upload-zip",
                              include_manifest=False)
    with open(zip_path, "rb") as f:
        data = f.read()
    result = pi.import_upload("upload-zip.zip", data)
    assert result.slug == "upload-zip"
    assert result.shape == "zip"


def test_import_upload_case_insensitive_extension(
        tmp_db, tmp_patterns_dir):
    md = b"---\nname: caps-md\ndescription: x\n---\nbody"
    result = pi.import_upload("Upload.MD", md)
    assert result.slug == "caps-md"


def test_import_upload_rejects_unsupported_extension(
        tmp_db, tmp_patterns_dir):
    with pytest.raises(pi.ImportError_, match="unsupported file type"):
        pi.import_upload("bundle.tar.gz", b"blob")


# ── import_skill_directory ───────────────────────────────────

def _make_skill_dir(parent, slug, *, description="Dir-imported skill",
                    include_content=False, extras=None):
    """Build a Claude-skill-shaped directory under `parent/<slug>/`."""
    d = parent / slug
    d.mkdir()
    (d / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: {description}\n---\n"
        "# Body\n\nProcedural text here.\n"
    )
    if include_content:
        (d / "SKILL.md").write_text(
            f"---\nname: {slug}\ndescription: {description}\n---\n"
            "See content.md.\n"
        )
        (d / "content.md").write_text(
            "# Real Content\n\n## Disciplines\n\n- Step 1\n"
        )
    for rel, body in (extras or {}).items():
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
    return d


def test_import_skill_directory_basic(tmp_db, tmp_patterns_dir, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_skill_dir(src, "dir-basic")

    result = pi.import_skill_directory(str(src / "dir-basic"))

    assert result.slug == "dir-basic"
    assert result.shape == "dir"
    assert result.doc_id > 0
    skill = tmp_patterns_dir / "dir-basic" / "SKILL.md"
    assert skill.exists()
    text = skill.read_text()
    assert "procedure: dir-basic" in text
    assert "manual: true" in text


def test_import_skill_directory_folds_content_md(
        tmp_db, tmp_patterns_dir, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_skill_dir(src, "dir-content", include_content=True)

    result = pi.import_skill_directory(str(src / "dir-content"))

    skill = tmp_patterns_dir / result.slug / "SKILL.md"
    text = skill.read_text()
    assert "## Disciplines" in text
    assert not (tmp_patterns_dir / result.slug / "content.md").exists()


def test_import_skill_directory_copies_extras(
        tmp_db, tmp_patterns_dir, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_skill_dir(src, "dir-extras", extras={"references/note.md": "n"})

    pi.import_skill_directory(str(src / "dir-extras"))

    assert (tmp_patterns_dir / "dir-extras" / "references" / "note.md").exists()


def test_import_skill_directory_conflict(tmp_db, tmp_patterns_dir, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_skill_dir(src, "dir-dup")

    pi.import_skill_directory(str(src / "dir-dup"))
    with pytest.raises(pi.ImportConflictError):
        pi.import_skill_directory(str(src / "dir-dup"))


def test_import_skill_directory_force_overwrites(
        tmp_db, tmp_patterns_dir, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_skill_dir(src, "dir-force")

    pi.import_skill_directory(str(src / "dir-force"))
    result = pi.import_skill_directory(str(src / "dir-force"), force=True)
    assert result.slug == "dir-force"


def test_import_skill_directory_target_slug(
        tmp_db, tmp_patterns_dir, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    _make_skill_dir(src, "dir-orig")

    result = pi.import_skill_directory(
        str(src / "dir-orig"), target_slug="dir-renamed",
    )
    assert result.slug == "dir-renamed"
    assert (tmp_patterns_dir / "dir-renamed" / "SKILL.md").exists()
    assert not (tmp_patterns_dir / "dir-orig").exists()


def test_import_skill_directory_rejects_non_dir(
        tmp_db, tmp_patterns_dir, tmp_path):
    with pytest.raises(pi.ImportError_, match="not a directory"):
        pi.import_skill_directory(str(tmp_path / "missing"))


def test_import_skill_directory_rejects_missing_skill_md(
        tmp_db, tmp_patterns_dir, tmp_path):
    d = tmp_path / "empty-dir"
    d.mkdir()
    with pytest.raises(pi.ImportError_, match="missing SKILL.md"):
        pi.import_skill_directory(str(d))


# ── batch_import_skill_directory: selective import ───────────

def _make_scan_root(tmp_path, *names):
    """A parent dir with one `<name>/SKILL.md` skill folder per name."""
    root = tmp_path / "scan-root"
    root.mkdir()
    for n in names:
        _make_skill_dir(root, n)
    return root


def test_batch_import_no_selection_imports_all(
        tmp_db, tmp_patterns_dir, tmp_path):
    """only=None (the default) preserves the historical import-everything."""
    root = _make_scan_root(tmp_path, "b-alpha", "b-beta", "b-gamma")
    results = pi.batch_import_skill_directory(str(root))
    imported = {r.name for r in results if r.status == "imported"}
    assert imported == {"b-alpha", "b-beta", "b-gamma"}


def test_batch_import_selective_subset(
        tmp_db, tmp_patterns_dir, tmp_path):
    """only={a,c} imports exactly those; the unselected one never appears."""
    root = _make_scan_root(tmp_path, "s-alpha", "s-beta", "s-gamma")
    results = pi.batch_import_skill_directory(
        str(root), only={"s-alpha", "s-gamma"},
    )
    names = {r.name for r in results}
    assert names == {"s-alpha", "s-gamma"}       # s-beta excluded entirely
    assert all(r.status == "imported" for r in results)
    assert (tmp_patterns_dir / "s-alpha").exists()
    assert not (tmp_patterns_dir / "s-beta").exists()


def test_batch_import_empty_selection_imports_nothing(
        tmp_db, tmp_patterns_dir, tmp_path):
    """An empty collection is distinct from None: it imports nothing."""
    root = _make_scan_root(tmp_path, "e-alpha", "e-beta")
    results = pi.batch_import_skill_directory(str(root), only=set())
    assert results == []
    assert not (tmp_patterns_dir / "e-alpha").exists()


def test_batch_import_unknown_selection_name_ignored(
        tmp_db, tmp_patterns_dir, tmp_path):
    """Names not present in the scan are silently skipped, no crash."""
    root = _make_scan_root(tmp_path, "u-alpha")
    results = pi.batch_import_skill_directory(
        str(root), only={"u-alpha", "does-not-exist"},
    )
    assert {r.name for r in results} == {"u-alpha"}


def test_batch_import_selective_dry_run_plans_only_selected(
        tmp_db, tmp_patterns_dir, tmp_path):
    """Selection composes with dry_run — only chosen candidates are planned."""
    root = _make_scan_root(tmp_path, "d-alpha", "d-beta")
    results = pi.batch_import_skill_directory(
        str(root), only={"d-beta"}, dry_run=True,
    )
    assert {r.name for r in results} == {"d-beta"}
    assert all(r.status == "planned" for r in results)
    assert not (tmp_patterns_dir / "d-beta").exists()  # dry-run wrote nothing
