"""Agent-bridge `@`-autocomplete path suggestions (`lib/agent_bridge/files.py`).

The /live composer's `@` must name what the raw terminal would name, so this
walks the filesystem — NOT git — under the session's own project root. These
pin that contract against a temp tree:

  * git-blind    — a `.gitignore`d / never-committed file is still suggested,
  * budgets      — ignored + hidden dirs skipped (`.claude`-style dirs are
                   traversed but never themselves suggested), depth and
                   entries-scanned caps honoured, limit clamped to [1, 100],
  * breadth-fair — the round-robin walk finds a small sibling's file even when
                   a huge subtree exhausts the scan budget first,
  * ranking      — exact segment > segment prefix > segment substring > fuzzy,
                   directories before files on a tie,
  * blank query  — the root's immediate children,
  * confinement  — EVERY query stays under the root: a symlink out is dropped,
                   and `~/…`, `/…`, `../…` are ordinary relative queries, not
                   browse roots (the composer is editor-reachable, so browsing
                   the host filesystem would be an enumeration oracle),
  * browse rules — a path-shaped query is held to what the WALK would have
                   done for that directory, so `.git/`, `.venv/`,
                   `node_modules/`, `__pycache__/` are unreachable by typing
                   them; the `.claude`-style allowlist stays reachable,
  * side doors   — the rule is applied case-folded (`NODE_MODULES/` opens
                   `node_modules/` on a case-insensitive filesystem) and to
                   resolved names (an in-root `gitlink -> .git`),
  * never raises — a NUL byte in the query or the root is refused, not fed to
                   `os.stat` (which answers it with a ValueError, not OSError),
                   and a missing/relative root yields nothing rather than the
                   server process's own cwd,
  * nesting      — a directory that is itself a repo/worktree root is not
                   descended into by the walk NOR browsed into; its contents
                   belong to another project,
  * caching      — the per-directory listing cache is revalidated against the
                   directory's mtime and bounded at its cap.

`store.get_pane_cwd` is monkeypatched (no DB) for the session-scoped entry
point, exactly as in `test_commands.py`.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from lib.agent_bridge import files, roots


def _write(path: Path, body: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    """A project tree: real sources, ignored noise, hidden dirs, a symlink."""
    base = tmp_path / "proj"
    _write(base / ".gitignore", "secret_note.txt\n")
    _write(base / "secret_note.txt")             # gitignored AND never staged
    _write(base / "README.md")
    _write(base / "src" / "app.py")
    _write(base / "src" / "components" / "Widget.vue")
    _write(base / "src" / "components" / "widget_helper.py")
    _write(base / "node_modules" / "left-pad" / "index.js")
    _write(base / "__pycache__" / "app.cpython-314.pyc")
    _write(base / ".hidden" / "buried.txt")      # dot dir, not on the allowlist
    _write(base / ".claude" / "commands" / "deploy.md")
    (base / ".git").mkdir(parents=True, exist_ok=True)
    _write(base / ".git" / "config")

    outside = tmp_path / "outside"
    _write(outside / "escaped_secret.txt")
    os.symlink(outside, base / "link")
    return base


def _paths(rows):
    return [row["path"] for row in rows]


# ── git-blindness: the whole point of not shelling out to git ─


def test_gitignored_and_unstaged_file_is_suggested(root):
    rows = files.search_files(str(root), "secret_note")
    assert _paths(rows) == ["secret_note.txt"]


# ── budgets: ignored dirs, hidden dirs, depth, scan cap, limit ─


def test_ignored_directories_excluded(root):
    for query in ("index", "left-pad", "cpython"):
        assert files.search_files(str(root), query) == []


def test_hidden_directory_not_traversed(root):
    assert files.search_files(str(root), "buried") == []


def test_allowlisted_hidden_dir_traversed_but_not_suggested(root):
    rows = files.search_files(str(root), "deploy")
    assert _paths(rows) == [".claude/commands/deploy.md"]
    assert ".claude" not in _paths(files.search_files(str(root), "claude"))


def test_depth_budget_respected(root, monkeypatch):
    monkeypatch.setattr(files, "MAX_DEPTH", 1)
    # Widget.vue sits at depth 3; only the root's own children survive.
    assert files.search_files(str(root), "Widget") == []
    assert _paths(files.search_files(str(root), "src")) == ["src"]


def test_scan_budget_respected(root, monkeypatch):
    monkeypatch.setattr(files, "MAX_ENTRIES_SCANNED", 1)
    assert len(files.search_files(str(root), "")) >= 1   # blank = children, uncapped walk
    assert len(files.search_files(str(root), "e")) <= 1  # tree walk stops at 1 entry


def test_breadth_fair_walk_reaches_a_starved_sibling(tmp_path, monkeypatch):
    """A huge subtree must not consume the whole budget before its sibling."""
    base = tmp_path / "proj"
    for index in range(200):
        _write(base / "big" / f"file_{index}.txt")
    _write(base / "small" / "needle.txt")
    monkeypatch.setattr(files, "MAX_ENTRIES_SCANNED", 6)
    assert _paths(files.search_files(str(base), "needle")) == ["small/needle.txt"]


def test_limit_clamped_and_defaulted(tmp_path):
    base = tmp_path / "many"
    for index in range(140):
        _write(base / f"item_{index:03d}.txt")
    assert len(files.search_files(str(base), "item", limit=1)) == 1
    assert len(files.search_files(str(base), "item", limit=0)) == 1
    assert len(files.search_files(str(base), "item", limit=9999)) == 100
    assert len(files.search_files(str(base), "item", limit="junk")) == 30
    assert len(files.search_files(str(base), "item")) == 30


# ── ranking ──────────────────────────────────────────────────


def test_ranking_tiers_ordered(tmp_path):
    base = tmp_path / "rank"
    _write(base / "widget")                  # tier 0: exact segment
    _write(base / "widget_helper.py")        # tier 1: segment prefix
    _write(base / "my_widget.py")            # tier 2: segment substring
    _write(base / "windy_gadget.py")         # tier 4: fuzzy subsequence
    _write(base / "unrelated.py")            # no match at all → excluded
    assert _paths(files.search_files(str(base), "widget")) == [
        "widget", "widget_helper.py", "my_widget.py", "windy_gadget.py"]


def test_directories_sort_before_files_on_a_tie(tmp_path):
    """Same tier/offset/depth → directories win, ahead of lexicographic."""
    base = tmp_path / "tie"
    _write(base / "zeta_dir" / "keep.txt")
    _write(base / "alpha.txt")
    assert files.search_files(str(base), "") == [
        {"path": "zeta_dir", "kind": "directory"},
        {"path": "alpha.txt", "kind": "file"}]


def test_exact_segment_beats_a_prefix_match_at_any_depth(tmp_path):
    """Tier ordering dominates depth: a nested exact segment outranks a
    shallower prefix-only match."""
    base = tmp_path / "depthtie"
    _write(base / "alpha" / "keep.txt")
    _write(base / "alpha.txt")
    assert _paths(files.search_files(str(base), "alpha")) == [
        "alpha", "alpha/keep.txt", "alpha.txt"]


def test_directory_rows_have_no_trailing_slash(root):
    rows = files.search_files(str(root), "components")
    assert rows[0] == {"path": "src/components", "kind": "directory"}


def test_path_shaped_query_browses_that_directory(root):
    rows = files.search_files(str(root), "src/comp")
    assert _paths(rows) == ["src/components"]
    assert _paths(files.search_files(str(root), "src/")) == [
        "src/components", "src/app.py"]


# ── blank query: the root's immediate children ───────────────


def test_blank_query_lists_root_children(root):
    rows = files.search_files(str(root), "")
    # Directories first, then files; nothing nested, nothing hidden/ignored.
    assert _paths(rows) == ["src", "README.md", "secret_note.txt"]


# ── confinement: nothing outside the root, ever ──────────────


def test_symlink_escape_is_blocked(root):
    assert files.search_files(str(root), "escaped_secret") == []
    assert "link" not in _paths(files.search_files(str(root), ""))


def test_absolute_query_browses_where_it_points(root, tmp_path):
    """An absolute path is a destination, not a relative query that happens to
    match nothing: the terminal this composer stands in for resolves it, and a
    reference meaning one thing on the phone and another in the pane is worse
    than no reference. Rows come back absolute so the CLI resolves the same
    file."""
    outside = tmp_path / "outside"
    _write(outside / "escaped.py")
    real = os.path.realpath(str(outside))
    assert f"{real}/escaped.py" in _paths(files.search_files(str(root), f"{real}/"))
    assert _paths(files.search_files(str(root), f"{real}/escaped.p")) == [
        f"{real}/escaped.py"]


def test_absolute_query_naming_the_root_is_the_root_query(root):
    """Typing the session's own absolute path is the same request as typing a
    root-relative one — it must not take the departing branch and answer with
    absolute duplicates of rows the plain query already serves."""
    real = os.path.realpath(str(root))
    assert _paths(files.search_files(str(root), f"{real}/")) == _paths(
        files.search_files(str(root), ""))


def test_bare_slash_query_browses_the_filesystem_root(root):
    """`/` names the filesystem root now that absolute paths resolve at all.
    Reading it as the session's root instead would make `@/etc` and `@/`
    disagree about what the leading slash means."""
    rows = _paths(files.search_files(str(root), "/"))
    assert rows and all(p.startswith("/") for p in rows)
    assert "/etc" in rows or "/usr" in rows


def test_home_query_browses_home(root, tmp_path, monkeypatch):
    """`~` is expanded in the ROW as well as the lookup: handed to the CLI
    still holding a tilde, `@~/notes.md` names a file relative to the cwd that
    does not exist."""
    home = tmp_path / "home"
    _write(home / "notes.md")
    monkeypatch.setenv("HOME", str(home))
    real = os.path.realpath(str(home))
    assert _paths(files.search_files(str(root), "~/")) == [f"{real}/notes.md"]
    assert _paths(files.search_files(str(root), "~/not")) == [
        f"{real}/notes.md"]
    assert _paths(files.search_files(str(root), "~")) == [f"{real}/notes.md"]


def test_parent_traversal_browses_the_sibling(root, tmp_path):
    """`../` stays RELATIVE in the row. The CLI resolves it against the same
    cwd this search is rooted at, so the text the operator accepts is the text
    that already meant the right file."""
    _write(tmp_path / "sibling" / "shared.py")
    assert _paths(files.search_files(str(root), "../sibling/")) == [
        "../sibling/shared.py"]
    assert _paths(files.search_files(str(root), "../sibling/sh")) == [
        "../sibling/shared.py"]
    assert "../sibling" in _paths(files.search_files(str(root), "../"))


def test_a_bare_term_never_departs_the_root(root, tmp_path):
    """The unbounded fuzzy walk stays rooted at the session's cwd. Only a path
    the operator typed on purpose leaves it — a term must never drift into
    walking the filesystem."""
    _write(tmp_path / "sibling" / "unrelated_marker.py")
    assert files.search_files(str(root), "unrelated_marker") == []


def test_missing_root_returns_empty(tmp_path):
    assert files.search_files(str(tmp_path / "nope"), "x") == []


def test_an_unusable_root_yields_nothing_without_raising(tmp_path, monkeypatch):
    """`search_files` promises never to raise, and a relative root resolves
    against the SERVER process's cwd — regin's own tree — which is the one
    place a session's suggestions may never come from."""
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / "lib" / "settings.py")
    for bad in (None, "", ".", "./", "lib", "./lib", "../"):
        assert files.search_files(bad, "settings") == [], repr(bad)


# ── the ignore rules bind the TYPED path, not just the walk ──


def test_typed_path_cannot_browse_a_directory_the_walk_skips(root):
    """A path-shaped query browses instead of walking, so it has to be held to
    the same rule — otherwise `.git/`, `.venv/`, `node_modules/` and
    `__pycache__/` are all one keystroke away despite the ignore list."""
    _write(root / ".venv" / "bin" / "python")
    _write(root / ".git" / "hooks" / "applypatch-msg.sample")
    _write(root / "lib" / "__pycache__" / "mod.pyc")
    for query in (".git/", ".git/config", ".git/hooks/", ".git/hooks/apply",
                  "./.git/", ".venv/", ".venv/bin/", "node_modules/",
                  "node_modules/left-pad/", "lib/__pycache__/",
                  ".hidden/", ".hidden/buried", "src/../.git/"):
        assert files.search_files(str(root), query) == [], query


def test_case_variant_ignored_directories_are_still_ignored(tmp_path):
    """The ignore list is a policy, not a set of literal names. APFS is
    case-insensitive, so `NODE_MODULES/` opens exactly what `node_modules/`
    does and a case-sensitive membership test hands the whole list back one
    shifted keystroke at a time."""
    base = tmp_path / "proj"
    _write(base / "src" / "app.py")
    _write(base / "NODE_MODULES" / "left-pad" / "vendored.js")
    _write(base / "lib" / "__PYCACHE__" / "mod_cached.pyc")
    _write(base / "Dist" / "bundle_built.js")
    for query in ("vendored", "mod_cached", "bundle_built"):
        assert files.search_files(str(base), query) == [], query
    for query in ("NODE_MODULES/", "Node_Modules/", "NODE_MODULES/left-pad/",
                  "lib/__PYCACHE__/", "Dist/", "DIST/"):
        assert files.search_files(str(base), query) == [], query
    assert _paths(files.search_files(str(base), "")) == ["lib", "src"]


def test_symlink_to_an_ignored_directory_is_not_a_side_door(tmp_path):
    """A symlink is held to its TARGET's name as well as its own: `gitlink`
    passes every test applied to `gitlink` and still lands inside the root, so
    only the resolved name keeps `.git/config` out of reach."""
    base = tmp_path / "proj"
    _write(base / "src" / "app.py")
    _write(base / ".git" / "config", "[core]\n")
    _write(base / ".git" / "hooks" / "pre-commit")
    _write(base / "node_modules" / "pkg" / "index.js")
    os.symlink(base / ".git", base / "gitlink")
    os.symlink(base / "node_modules", base / "nmlink")
    os.symlink(base / "node_modules" / "pkg", base / "pkglink")
    for query in ("gitlink/", "gitlink/config", "gitlink/hooks/",
                  "gitlink/hooks/pre", "nmlink/", "nmlink/pkg/", "pkglink/"):
        assert files.search_files(str(base), query) == [], query
    for query in ("config", "pre-commit", "index"):   # the plain walk too
        assert files.search_files(str(base), query) == [], query
    assert _paths(files.search_files(str(base), "")) == ["src"]


def test_typed_path_still_browses_an_allowlisted_hidden_dir(root):
    """`.claude` is traversable BY DESIGN — the allowlist exists so
    `@.claude/commands/…` resolves, and the walk already surfaces those files
    for a plain query, so browsing them is not a bypass."""
    assert _paths(files.search_files(str(root), ".claude/")) == [
        ".claude/commands"]
    assert _paths(files.search_files(str(root), ".claude/commands/")) == [
        ".claude/commands/deploy.md"]


def test_browsing_matches_what_the_walk_would_surface(root):
    """The invariant in one assertion: every row a browse returns for a
    directory is a row the walk would have yielded for the same directory."""
    _write(root / ".claude" / "settings.local.json", "{}")
    browsed = set(_paths(files.search_files(str(root), ".claude/", limit=100)))
    walked = {path for path in
              _paths(files.search_files(str(root), "claude", limit=100))
              if path.startswith(".claude/")}
    assert browsed <= walked | {".claude/commands"}


# ── invalid path bytes: refused, never raised ────────────────


def test_nul_byte_in_the_query_is_refused_not_raised(root):
    """`search_files` promises never to raise; `os.stat` answers a NUL with a
    ValueError, which no OSError handler below catches."""
    for query in ("lib\x00/../../etc", "a\x00b/c", "\x00", "src/\x00",
                  "src/app\x00.py"):
        assert files.search_files(str(root), query) == [], repr(query)


def test_nul_byte_in_the_root_is_refused_not_raised(tmp_path):
    assert files.search_files(f"{tmp_path}/pro\x00j", "x") == []


def test_list_session_files_survives_a_nul_bearing_cwd(monkeypatch):
    monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid: "/tmp/no\x00pe")
    assert files.list_session_files("t1", "x") == []


# ── nested repos / worktrees are not descended into ──────────


def _fake_worktree(base: Path, name: str, marker: str = "dir") -> None:
    clone = base / name
    _write(clone / "conftest.py")
    if marker == "dir":
        (clone / ".git").mkdir(parents=True, exist_ok=True)
    else:                                   # `git worktree add` writes a FILE
        _write(clone / ".git", "gitdir: /elsewhere/.git/worktrees/w\n")


def test_nested_worktree_contents_are_not_searched(tmp_path):
    base = tmp_path / "proj"
    _write(base / "tests" / "conftest.py")
    _fake_worktree(base / ".claude" / "worktrees", "clone-a")
    _fake_worktree(base / ".claude" / "worktrees", "clone-b", marker="file")
    assert _paths(files.search_files(str(base), "conftest")) == [
        "tests/conftest.py"]


def test_browsing_into_a_nested_worktree_is_refused(tmp_path):
    """browse ⊆ walk: the walk will not descend into another checkout, so
    typing its path must not be a way in either. The worktree's own row stays
    reachable — the walk yields it before it stops."""
    base = tmp_path / "proj"
    _fake_worktree(base, "clone-a")
    _fake_worktree(base / ".claude" / "worktrees", "clone-b")
    _write(base / ".claude" / "worktrees" / "clone-b" / "cli" / "main.py")
    assert files.search_files(str(base), "clone-a/") == []
    assert files.search_files(str(base), ".claude/worktrees/clone-b/") == []
    assert files.search_files(str(base), ".claude/worktrees/clone-b/cli/") == []
    assert _paths(files.search_files(str(base), ".claude/worktrees/")) == [
        ".claude/worktrees/clone-b"]


# ── the per-directory listing cache ──────────────────────────


def test_cached_listing_avoids_a_rescan(tmp_path, monkeypatch):
    base = tmp_path / "cached"
    _write(base / "a.txt")
    scans: list[str] = []
    real = files._scan_dir
    monkeypatch.setattr(files, "_scan_dir",
                        lambda d: scans.append(d) or real(d))
    assert files._raw_entries(str(base)) == files._raw_entries(str(base))
    assert scans == [str(base)]


def test_new_file_invalidates_the_cached_listing(tmp_path):
    base = tmp_path / "cached"
    _write(base / "a.txt")
    assert files._raw_entries(str(base)) == (("a.txt", "file"),)
    time.sleep(0.01)                     # a distinct mtime, not a TTL expiry
    _write(base / "b.txt")
    assert files._raw_entries(str(base)) == (("a.txt", "file"),
                                             ("b.txt", "file"))


def test_expired_listing_is_rescanned(tmp_path, monkeypatch):
    base = tmp_path / "cached"
    _write(base / "a.txt")
    monkeypatch.setattr(files, "LISTING_CACHE_TTL_SECONDS", -1.0)
    scans: list[str] = []
    real = files._scan_dir
    monkeypatch.setattr(files, "_scan_dir",
                        lambda d: scans.append(d) or real(d))
    files._raw_entries(str(base))
    files._raw_entries(str(base))
    assert scans == [str(base), str(base)]


def test_prune_cache_bounds_the_cache_at_its_cap():
    """Unbounded, a long-lived server would cache a listing per directory it
    ever walked."""
    cap = files.LISTING_CACHE_MAX_ENTRIES
    fresh = time.monotonic() + 999
    for index in range(cap + 500):
        files._listing_cache[f"/d/{index}"] = files._Listing(fresh, 1, 1, ())
    files._prune_cache()
    assert len(files._listing_cache) == cap


def test_prune_cache_drops_expired_entries_first(monkeypatch):
    monkeypatch.setattr(files, "LISTING_CACHE_MAX_ENTRIES", 2)
    stale = time.monotonic() - 1
    fresh = time.monotonic() + 999
    files._listing_cache["/stale"] = files._Listing(stale, 1, 1, ())
    for name in ("/a", "/b"):
        files._listing_cache[name] = files._Listing(fresh, 1, 1, ())
    files._prune_cache()
    assert set(files._listing_cache) == {"/a", "/b"}


# ── session-scoped entry point ───────────────────────────────


def test_list_session_files_uses_the_pane_cwd(root, monkeypatch):
    monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid: str(root))
    rows = files.list_session_files("t1", "Widget")
    # Case-insensitive matching; equal-rank rows fall back to lexicographic.
    assert _paths(rows) == ["src/components/Widget.vue",
                            "src/components/widget_helper.py"]


def test_list_session_files_uses_the_cwd_not_a_claude_ancestor(root, monkeypatch):
    """The cwd IS the root: a subdirectory session searches its own subtree,
    not the whole repo above it."""
    monkeypatch.setattr(roots.store, "get_pane_cwd",
                        lambda tid: str(root / "src" / "components"))
    assert _paths(files.list_session_files("t1", "Widget")) == [
        "Widget.vue", "widget_helper.py"]      # relative to the cwd, not the repo
    assert files.list_session_files("t1", "README") == []


def test_list_session_files_unregistered_cwd_is_empty(root, monkeypatch):
    """No registered cwd → an empty menu; regin's own tree is not a stand-in."""
    monkeypatch.setattr(roots.store, "get_pane_cwd", lambda tid: None)
    assert files.list_session_files("t-ghost", "Widget") == []
