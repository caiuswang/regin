"""Unit tests for lib.tags.tag_index.

Both index generators (generate_tag_index, generate_repo_index) write
markdown files under $PATTERNS_DIR/_index/. Uses tmp_config_dir to
isolate the write path + tmp_db for the read state, then reads back
what got written.
"""

from __future__ import annotations

from lib.tags.tag_index import generate_repo_index, generate_tag_index


def _seed(doc_tags, repo="demo"):
    """Create patterns + tags + doc_tag links, plus a registered repo with a
    project-scope deployment per pattern (generate_repo_index reads the
    pattern->repo link from pattern_deployments, not a column)."""
    from lib.orm import SessionLocal
    from lib.orm.models import (
        DocTag, PatternDeployment, PatternDoc, Repo, Tag,
    )
    with SessionLocal() as s:
        repo_row = Repo(name=repo, path=f"/tmp/{repo}")
        s.add(repo_row)
        s.flush()
        for slug, title, cat, tags in doc_tags:
            pd = PatternDoc(
                slug=slug, title=title, file_path=f"{slug}/SKILL.md",
                category=cat,
            )
            s.add(pd)
            s.flush()
            s.add(PatternDeployment(
                pattern_slug=slug, scope="project",
                project_id=repo_row.id,
                deployed_path=f"/tmp/{repo}/.claude/skills/{slug}",
            ))
            for tag_name, tag_cat in tags:
                tag = Tag(name=tag_name, category=tag_cat)
                s.add(tag)
                s.flush()
                s.add(DocTag(doc_id=pd.id, tag_id=tag.id))
        s.commit()


def test_generate_tag_index_writes_index_file(tmp_db, tmp_config_dir):
    # schema.sql seeds canonical tags like "entity" already; use names
    # that don't clash.
    _seed([
        ("alpha", "Alpha Title", "procedure",
         [("fresh-layer-tag", "made-up-category")]),
    ])
    path = generate_tag_index()
    content = open(path).read()
    assert path.endswith("/_index/tag-index.md")
    assert "## Made-Up-Category" in content
    assert "fresh-layer-tag" in content
    assert "Alpha Title" in content


def test_generate_tag_index_skips_tags_with_zero_docs(tmp_db, tmp_config_dir):
    """Tags that are defined but link to no patterns are omitted."""
    from lib.orm import SessionLocal
    from lib.orm.models import Tag
    # Seed a pattern with one tag so a section exists.
    _seed([("a", "A", "procedure", [("used-tag", "concept")])])
    # And a tag with no doc links.
    with SessionLocal() as s:
        s.add(Tag(name="unused-tag", category="concept"))
        s.commit()
    content = open(generate_tag_index()).read()
    assert "used-tag" in content
    assert "unused-tag" not in content


def test_generate_repo_index_writes_index_file(tmp_db, tmp_config_dir):
    _seed([
        ("a", "Alpha", "procedure", []),
        ("b", "Beta", "procedure", []),
    ], repo="example-service")
    path = generate_repo_index()
    content = open(path).read()
    assert path.endswith("/_index/repo-index.md")
    assert "example-service (2 patterns)" in content
    # Entries show up as markdown list items.
    assert "Alpha" in content
    assert "Beta" in content


def test_generate_repo_index_groups_by_category(tmp_db, tmp_config_dir):
    _seed([
        ("p", "Proc", "procedure", []),
        ("m", "Manual", "manual", []),
    ])
    content = open(generate_repo_index()).read()
    # Both categories appear as H3 headings.
    assert "### procedure" in content
    assert "### manual" in content


def test_generate_tag_index_empty_db_is_harmless(tmp_db, tmp_config_dir):
    path = generate_tag_index()
    content = open(path).read()
    assert "# Tag Cross-Reference Index" in content
    # No sections when no tags exist.
    assert "## " not in content.split("# Tag Cross-Reference Index")[1]
