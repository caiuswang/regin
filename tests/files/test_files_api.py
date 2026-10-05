"""Backend acceptance for the file viewer (feature #133, S1)."""

from __future__ import annotations

import os

import pytest

from tests.files.conftest import ADMIN, EDITOR, VIEWER, git


def _meta(client, headers=EDITOR, **args):
    return client.get("/api/files/meta", query_string=args, headers=headers)


def _raw(client, headers=EDITOR, **args):
    return client.get("/api/files/raw", query_string=args, headers=headers)


# ── R1: project sources ──────────────────────────────────────────

def test_project_path_escaping_the_repo_is_refused(flask_client, repo, tmp_path):
    pid, _root = repo
    (tmp_path / "outside.txt").write_text("secret\n")
    for fetch in (_meta, _raw):
        resp = fetch(flask_client, project=pid, path="../outside.txt")
        assert resp.status_code == 403
        assert resp.get_json() == {"error": "outside_root"}
    resp = _meta(flask_client, project=pid, path=str(tmp_path / "outside.txt"))
    assert resp.get_json() == {"error": "outside_root"}


def test_symlink_inside_the_repo_pointing_outside_is_refused(flask_client, repo, tmp_path):
    pid, root = repo
    target = tmp_path / "hosts"
    target.write_text("127.0.0.1 localhost\n")
    (root / "link.txt").symlink_to(target)
    (root / "linkdir").symlink_to(tmp_path)
    for path in ("link.txt", "linkdir/hosts"):
        resp = _raw(flask_client, project=pid, path=path)
        assert resp.status_code == 403
        assert resp.get_json() == {"error": "outside_root"}


def test_symlink_staying_inside_the_repo_is_served(flask_client, repo):
    pid, root = repo
    (root / "alias.py").symlink_to(root / "f.py")
    resp = _raw(flask_client, project=pid, path="alias.py")
    assert resp.status_code == 200
    assert resp.data == b"print('committed')\n"


def test_viewer_role_cannot_read_project_files(flask_client, repo):
    pid, _root = repo
    for fetch in (_meta, _raw):
        resp = fetch(flask_client, headers=VIEWER, project=pid, path="f.py")
        assert resp.status_code == 403
        assert resp.get_json() == {"error": "forbidden"}


def test_editor_and_admin_read_project_files(flask_client, repo):
    pid, root = repo
    for headers in (EDITOR, ADMIN):
        meta = _meta(flask_client, headers=headers, project=pid, path="f.py")
        assert meta.status_code == 200
        body = meta.get_json()
        assert body["preview"] == "code"
        assert body["language"] == "python"
        assert body["abs_path"] == os.path.realpath(root / "f.py")
        assert body["source"] == {"kind": "project", "project_id": pid,
                                  "path": "f.py", "rev": "WORKTREE"}
        assert body["first_line"] == 1


def test_missing_file_returns_missing(flask_client, repo):
    pid, _root = repo
    for fetch in (_meta, _raw):
        resp = fetch(flask_client, project=pid, path="nope/absent.py")
        assert resp.status_code == 404
        assert resp.get_json() == {"error": "missing"}
    resp = _meta(flask_client, project=pid, path="f.py", rev="HEAD~5")
    assert resp.status_code == 404
    resp = _meta(flask_client, project=99999, path="f.py")
    assert resp.get_json() == {"error": "missing"}


@pytest.mark.parametrize("args", [
    {}, {"project": "1"}, {"project": "x", "path": "a"},
    {"project": "1", "session": "s", "path": "a"},
    {"session": "s"}, {"session": "s", "path": "/a", "span": "x"},
    {"project": "1", "path": "a", "rev": "--output=/tmp/x"},
    {"project": "1", "path": "a", "rev": "HEAD:other"},
])
def test_malformed_source_is_a_bad_request(flask_client, args):
    resp = _meta(flask_client, **args)
    assert resp.status_code == 400
    assert resp.get_json() == {"error": "bad_source"}


# ── R1: session and span sources ─────────────────────────────────

def test_session_source_serves_a_span_named_path_outside_the_cwd(
        flask_client, traced_session, tmp_path):
    cwd = tmp_path / "w"
    cwd.mkdir()
    report = tmp_path / "out" / "report.html"
    report.parent.mkdir()
    report.write_text("<h1>report</h1>\n")
    traced_session("S", cwd, [("sp1", "tool.Write", {"file_path": str(report)})])
    resp = _meta(flask_client, headers=ADMIN, session="S", path=str(report))
    assert resp.status_code == 200
    assert resp.get_json()["preview"] == "html"
    raw = _raw(flask_client, headers=ADMIN, session="S", path=str(report))
    assert raw.data == b"<h1>report</h1>\n"


def test_session_source_serves_a_path_inside_the_cwd(
        flask_client, traced_session, tmp_path):
    cwd = tmp_path / "w"
    cwd.mkdir()
    (cwd / "notes.md").write_text("# hi\n")
    traced_session("S", cwd)
    resp = _meta(flask_client, headers=ADMIN, session="S", path=str(cwd / "notes.md"))
    assert resp.status_code == 200
    assert resp.get_json()["preview"] == "markdown"


def test_session_source_refuses_an_unnamed_path_outside_the_cwd(
        flask_client, traced_session, tmp_path):
    cwd = tmp_path / "w"
    cwd.mkdir()
    hosts = tmp_path / "hosts"
    hosts.write_text("127.0.0.1 localhost\n")
    other = tmp_path / "other.txt"
    other.write_text("named by another session\n")
    traced_session("S", cwd, [("sp1", "tool.Write",
                               {"file_path": str(cwd / "x.py")})])
    traced_session("T", cwd, [("sp2", "tool.Write", {"file_path": str(other)})])
    for path in (hosts, other, cwd / ".." / "hosts"):
        for fetch in (_meta, _raw):
            resp = fetch(flask_client, headers=ADMIN, session="S", path=str(path))
            assert resp.status_code == 403
            assert resp.get_json() == {"error": "outside_root"}


def test_non_admin_cannot_read_session_or_span_sources(
        flask_client, traced_session, tmp_path):
    cwd = tmp_path / "w"
    cwd.mkdir()
    (cwd / "a.py").write_text("x = 1\n")
    traced_session("S", cwd, [("r1", "tool.Read",
                               {"file_path": str(cwd / "a.py"), "content": "x = 1\n"})])
    for headers in (EDITOR, VIEWER):
        for args in ({"session": "S", "path": str(cwd / "a.py")},
                     {"session": "S", "span": "r1"}):
            for fetch in (_meta, _raw):
                resp = fetch(flask_client, headers=headers, **args)
                assert resp.status_code == 403
                assert resp.get_json() == {"error": "forbidden"}


def test_span_source_serves_captured_read_content_at_its_offset(
        flask_client, traced_session, tmp_path):
    captured = "line 120\nline 121\n"
    traced_session("S", tmp_path, [
        ("r1", "tool.Read", {"file_path": "/gone/mod.py", "content": captured,
                             "start_line": 120}),
        ("w1", "tool.Write", {"file_path": "/gone/mod.py"}),
    ])
    meta = _meta(flask_client, headers=ADMIN, session="S", span="r1").get_json()
    assert meta["first_line"] == 120
    assert meta["preview"] == "code"
    assert meta["abs_path"] == "/gone/mod.py"
    assert meta["diffable"] is False
    assert meta["source"] == {"kind": "span", "trace_id": "S", "span_id": "r1"}
    raw = _raw(flask_client, headers=ADMIN, session="S", span="r1")
    assert raw.data == captured.encode()
    for span in ("w1", "absent"):
        resp = _meta(flask_client, headers=ADMIN, session="S", span=span)
        assert resp.status_code == 404
    assert _meta(flask_client, headers=ADMIN, session="other", span="r1").status_code == 404


# ── raw headers ──────────────────────────────────────────────────

def test_raw_responses_carry_nosniff_and_a_sandbox_csp(
        flask_client, repo, traced_session, tmp_path):
    pid, root = repo
    (root / "r.html").write_text("<script>alert(1)</script>")
    traced_session("S", tmp_path, [("r1", "tool.Read",
                                    {"file_path": "/x/a.txt", "content": "hi"})])
    responses = [
        _raw(flask_client, project=pid, path="r.html"),
        _raw(flask_client, project=pid, path="f.py", rev="HEAD"),
        _raw(flask_client, headers=ADMIN, session="S", span="r1"),
    ]
    for resp in responses:
        assert resp.status_code == 200
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["Content-Security-Policy"] == "sandbox"
        assert "attachment" not in resp.headers.get("Content-Disposition", "")
    assert responses[0].mimetype == "text/html"
    dl = _raw(flask_client, project=pid, path="r.html", download="1")
    assert dl.headers["Content-Disposition"].startswith("attachment")
    assert "r.html" in dl.headers["Content-Disposition"]
    assert dl.headers["Content-Security-Policy"] == "sandbox"


# ── git rev ──────────────────────────────────────────────────────

def test_head_rev_serves_committed_bytes_while_the_worktree_differs(flask_client, repo):
    pid, root = repo
    (root / "f.py").write_text("print('edited')\n")
    head = _raw(flask_client, project=pid, path="f.py", rev="HEAD")
    assert head.status_code == 200
    assert head.data.decode() == git(root, "show", "HEAD:f.py")
    assert _raw(flask_client, project=pid, path="f.py").data == b"print('edited')\n"
    meta = _meta(flask_client, project=pid, path="f.py").get_json()
    assert meta["diffable"] is True
    assert _meta(flask_client, project=pid, path="f.py",
                 rev="HEAD").get_json()["size"] == len(head.data)


def test_diffable_is_false_for_unmodified_untracked_and_non_git(
        flask_client, repo, traced_session, tmp_path):
    pid, root = repo
    (root / "new.py").write_text("x = 1\n")
    assert _meta(flask_client, project=pid, path="f.py").get_json()["diffable"] is False
    assert _meta(flask_client, project=pid, path="new.py").get_json()["diffable"] is False
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("x = 1\n")
    traced_session("S", plain)
    meta = _meta(flask_client, headers=ADMIN, session="S", path=str(plain / "a.py"))
    assert meta.get_json()["diffable"] is False


def test_session_source_reads_head_inside_a_git_cwd(flask_client, repo, traced_session):
    _pid, root = repo
    (root / "f.py").write_text("print('edited')\n")
    traced_session("S", root)
    resp = _raw(flask_client, headers=ADMIN, session="S", path=str(root / "f.py"),
                rev="HEAD")
    assert resp.data == b"print('committed')\n"


def test_reading_a_repo_runs_no_command_its_git_config_names(
        flask_client, repo, tmp_path):
    pid, root = repo
    marker = tmp_path / "PWNED"
    git(root, "config", "core.fsmonitor", f"touch {marker}; false")
    git(root, "config", "filter.evil.clean", f"touch {marker}; cat")
    (root / ".gitattributes").write_text("*.py filter=evil\n")
    (root / "f.py").write_text("print('edited')\n")
    meta = _meta(flask_client, project=pid, path="f.py").get_json()
    _raw(flask_client, project=pid, path="f.py", rev="HEAD")
    assert meta["diffable"] is True
    assert not marker.exists()


def test_nul_in_a_path_is_a_bad_request(flask_client, repo):
    pid, _root = repo
    for fetch in (_meta, _raw):
        resp = fetch(flask_client, project=pid, path="f.py\x00x")
        assert resp.status_code == 400
        resp = fetch(flask_client, headers=ADMIN, session="S", path="/a\x00b")
        assert resp.status_code == 400
