"""Unit tests for repo-local topic graphs."""

from __future__ import annotations

import json
import os
import stat
import subprocess

import pytest

from lib import topics


def test_bootstrap_creates_empty_graph(fake_git_repo):
    paths = topics.bootstrap(fake_git_repo)

    assert paths["topic"] == topics.topic_split_dir(fake_git_repo)
    assert topics.topic_meta_path(fake_git_repo).exists()
    graph = topics.load_graph(fake_git_repo)
    assert graph["repo"] == fake_git_repo.name
    assert graph["topics"] == {}


def test_scan_updates_existing_topic_refs(fake_git_repo):
    (fake_git_repo / "web").mkdir()
    (fake_git_repo / "web" / "app.py").write_text("app")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "files"])

    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"]["web"] = {
        "label": "Web",
        "aliases": [],
        "intent": "Web routes.",
        "status": "active",
        "refs": [],
        "edges": [],
        "commands": [],
        "include_globs": ["web/**"],
        "exclude_globs": [],
    }
    topics.save_graph(fake_git_repo, graph)

    result = topics.scan(fake_git_repo)

    # scan now routes ref refreshes to the local overlay, leaving the
    # git-tracked base untouched; read the merged effective graph.
    graph = topics.load_graph_merged(fake_git_repo)
    assert result["updated_topics"] == ["web"]
    # scan no longer invents roles; new refs are added role-less.
    assert graph["topics"]["web"]["refs"] == [{"path": "web/app.py"}]
    # base topic.json keeps the topic ref-less; the overlay carries the refresh.
    assert topics.load_graph(fake_git_repo)["topics"]["web"]["refs"] == []


def test_scan_preserves_existing_ref_tier(fake_git_repo):
    """A full rescan reconciles refs to the matched file set but must round-trip
    a curated `tier` — otherwise the drift-exclusion tag would silently reset on
    the next scan and reference-only files would start emitting debt again."""
    (fake_git_repo / "web").mkdir()
    (fake_git_repo / "web" / "app.py").write_text("app")
    subprocess.check_call(["git", "-C", str(fake_git_repo), "add", "."])
    subprocess.check_call(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "files"])

    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"]["web"] = {
        "label": "Web", "aliases": [], "intent": "Web routes.", "status": "active",
        "refs": [{"path": "web/app.py", "role": "entrypoint", "tier": "reference"}],
        "edges": [], "commands": [],
        "include_globs": ["web/**"], "exclude_globs": [],
    }
    topics.save_graph(fake_git_repo, graph)

    topics.scan(fake_git_repo)

    merged = topics.load_graph_merged(fake_git_repo)
    ref = merged["topics"]["web"]["refs"][0]
    assert ref["role"] == "entrypoint"
    assert ref["tier"] == "reference"   # survived the reconcile


def test_validate_detects_duplicate_alias_and_broken_refs(fake_git_repo):
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "a": {
            "label": "A", "aliases": ["same"], "intent": "A", "status": "active",
            "refs": [{"path": "missing.py", "role": "implementation"}],
            "edges": [{"target": "missing", "type": "related"}],
            "commands": [], "include_globs": [], "exclude_globs": [],
        },
        "b": {
            "label": "B", "aliases": ["same"], "intent": "B", "status": "active",
            "refs": [], "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)

    result = topics.validate(fake_git_repo)

    assert not result.ok
    assert any("duplicate alias" in error for error in result.errors)
    assert any("ref does not exist" in error for error in result.errors)
    assert any("edge target does not exist" in error for error in result.errors)


def _commit_on_branch(repo, branch, path, body="x\n"):
    """Create `path` on a new branch, then return to the original branch so the
    file is absent from the working tree but carried by another branch tip."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    here = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", "-b", branch], check=True, env=env)
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)
    subprocess.run(["git", "-C", str(repo), "add", "-f", path], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", f"add {path}"], check=True, env=env)
    subprocess.run(["git", "-C", str(repo), "checkout", "-q", here], check=True, env=env)


def _git(repo, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)


def _graph_with_ref(repo, path):
    """Approve a single topic anchoring `path`, with another branch carrying an
    unrelated file so the branch-tip lookup has something to walk."""
    _commit_on_branch(repo, "feat/keeps-something", "unrelated.py")
    topics.bootstrap(repo)
    graph = topics.load_graph(repo)
    graph["topics"] = {
        "a": {
            "label": "A", "aliases": [], "intent": "A", "status": "active",
            "refs": [{"path": path, "role": "implementation"}],
            "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
        },
    }
    topics.save_graph(repo, graph)


def test_validate_errors_when_this_commit_renames_an_anchored_file(fake_git_repo):
    """`git mv` destroys the anchored path just as a delete does. Rename
    detection reports it as `R`, so a delete-only filter let it through."""
    (fake_git_repo / "movable.py").write_text("x\n" * 40)
    _git(fake_git_repo, "add", "-f", "movable.py")
    _git(fake_git_repo, "commit", "-q", "-m", "add")
    _graph_with_ref(fake_git_repo, "movable.py")
    _git(fake_git_repo, "mv", "movable.py", "moved.py")

    result = topics.validate(fake_git_repo)

    assert not result.ok
    assert any("removed by this commit" in error for error in result.errors)


def test_validate_errors_when_this_commit_deletes_a_dir_refs_subtree(fake_git_repo):
    """A dir ref names a subtree, so `git rm -r` of it stages member deletions,
    never the directory itself — matching on the members is what catches it."""
    (fake_git_repo / "pkg").mkdir()
    (fake_git_repo / "pkg" / "a.py").write_text("x\n")
    (fake_git_repo / "pkg" / "b.py").write_text("y\n")
    _git(fake_git_repo, "add", "-f", "pkg")
    _git(fake_git_repo, "commit", "-q", "-m", "add")
    _graph_with_ref(fake_git_repo, "pkg/")
    _git(fake_git_repo, "rm", "-q", "-r", "pkg")

    result = topics.validate(fake_git_repo)

    assert not result.ok
    assert any("removed by this commit" in error for error in result.errors)


def test_validate_handles_non_ascii_ref_paths(fake_git_repo):
    """git C-escapes non-ASCII paths unless asked not to, so a `café` anchor
    matched nothing: its deletion went unnoticed and, worse, a branch-owned one
    was reported dead — re-wedging commits."""
    _commit_on_branch(fake_git_repo, "feat/uni", "docs/café_ünicode.md")
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "a": {
            "label": "A", "aliases": [], "intent": "A", "status": "active",
            "refs": [{"path": "docs/café_ünicode.md", "role": "implementation"}],
            "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)

    result = topics.validate(fake_git_repo)

    assert result.ok
    assert any("present on another branch" in w for w in result.warnings)


def test_validate_does_not_let_an_unusable_ref_path_condemn_the_others(fake_git_repo):
    """git refuses `../outside.py` as a pathspec and fails the whole batched
    lookup. Swallowing that would report every other absent ref as dead and
    wedge commits — the CAI-25 bug — so the lookup fails open instead."""
    _commit_on_branch(fake_git_repo, "feat/holder", "held.py")
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "a": {
            "label": "A", "aliases": [], "intent": "A", "status": "active",
            "refs": [{"path": "../outside.py"}, {"path": "held.py"}],
            "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)

    result = topics.validate(fake_git_repo)

    assert not any("held.py" in error for error in result.errors)
    assert any("held.py" in warning for warning in result.warnings)


def test_validate_warns_not_errors_on_ref_owned_by_another_branch(fake_git_repo):
    """A topic anchoring a file that lives on an unmerged branch must not make
    the whole graph invalid — that wedged the pre-commit hook and every other
    `validate().ok` gate (CAI-25)."""
    _commit_on_branch(fake_git_repo, "feat/elsewhere", "elsewhere.py")
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "a": {
            "label": "A", "aliases": [], "intent": "A", "status": "active",
            "refs": [{"path": "elsewhere.py", "role": "implementation"}],
            "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)

    result = topics.validate(fake_git_repo)

    assert result.ok
    assert not (fake_git_repo / "elsewhere.py").exists()
    assert any("present on another branch" in w for w in result.warnings)


def test_validate_errors_when_this_commit_deletes_an_anchored_file(fake_git_repo):
    """Deleting an anchored file must still fail the pre-commit gate even though
    another branch keeps the path alive — otherwise deletions silently rot the
    graph, since a stale branch tip would mask every one of them."""
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    (fake_git_repo / "doomed.py").write_text("x\n")
    subprocess.run(["git", "-C", str(fake_git_repo), "add", "-f", "doomed.py"], check=True, env=env)
    subprocess.run(["git", "-C", str(fake_git_repo), "commit", "-q", "-m", "add"], check=True, env=env)
    _commit_on_branch(fake_git_repo, "feat/keeps-it", "unrelated.py")

    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "a": {
            "label": "A", "aliases": [], "intent": "A", "status": "active",
            "refs": [{"path": "doomed.py", "role": "implementation"}],
            "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)
    subprocess.run(["git", "-C", str(fake_git_repo), "rm", "-q", "doomed.py"], check=True, env=env)

    result = topics.validate(fake_git_repo)

    assert not result.ok
    assert any("removed by this commit" in error for error in result.errors)


def test_validate_still_errors_on_ref_no_branch_carries(fake_git_repo):
    """The deletion case keeps its teeth: a path no branch tip carries is a
    genuinely dead anchor, not a not-checked-out one."""
    _commit_on_branch(fake_git_repo, "feat/unrelated", "unrelated.py")
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "a": {
            "label": "A", "aliases": [], "intent": "A", "status": "active",
            "refs": [{"path": "nowhere.py", "role": "implementation"}],
            "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)

    result = topics.validate(fake_git_repo)

    assert not result.ok
    assert any("ref does not exist: nowhere.py" in error for error in result.errors)


def test_validate_rejects_invalid_ref_tier(fake_git_repo):
    (fake_git_repo / "a.py").write_text("x\n")
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "a": {
            "label": "A", "aliases": [], "intent": "A", "status": "active",
            "refs": [{"path": "a.py", "tier": "bogus"}],
            "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)

    result = topics.validate(fake_git_repo)

    assert not result.ok
    assert any("invalid tier" in error for error in result.errors)


def test_validate_accepts_valid_ref_tier(fake_git_repo):
    (fake_git_repo / "a.py").write_text("x\n")
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "a": {
            "label": "A", "aliases": [], "intent": "A", "status": "active",
            "refs": [{"path": "a.py", "tier": "reference"}],
            "edges": [], "commands": [], "include_globs": [], "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)

    result = topics.validate(fake_git_repo)

    assert result.ok


def test_match_topic_uses_aliases_and_refs(fake_git_repo):
    (fake_git_repo / "README.md").write_text("readme")
    topics.bootstrap(fake_git_repo, seeds=True, force=True)
    topics.scan(fake_git_repo)

    match = topics.match_topic(fake_git_repo, "readme")

    assert match is not None
    assert match["id"] == "overview"


def test_match_topic_uses_exact_ref_path(fake_git_repo):
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "web": {
            "label": "Web",
            "aliases": [],
            "intent": "Web routes.",
            "status": "active",
            "refs": [{"path": "web/app.py", "role": "entrypoint"}],
            "edges": [],
            "commands": [],
            "include_globs": ["web/**"],
            "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)

    match = topics.match_topic(fake_git_repo, "web/app.py")

    assert match is not None
    assert match["id"] == "web"


def test_match_topic_uses_partial_ref_path(fake_git_repo):
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "web": {
            "label": "Web",
            "aliases": [],
            "intent": "Web routes.",
            "status": "active",
            "refs": [{"path": "web/app.py", "role": "entrypoint"}],
            "edges": [],
            "commands": [],
            "include_globs": ["web/**"],
            "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)

    basename_match = topics.match_topic(fake_git_repo, "app.py")
    path_match = topics.match_topic(fake_git_repo, "web/app")

    assert basename_match is not None
    assert basename_match["id"] == "web"
    assert path_match is not None
    assert path_match["id"] == "web"


def test_best_topic_for_text_matches_on_ref_path_in_body(fake_git_repo):
    """Long text (a memory body) links to a topic only when one of that
    topic's ref file paths appears in the body — the precision signal the
    backfill relies on, where `match_topic`'s short-query strategies would
    instead fall to the over-eager fuzzy fallback."""
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "web": {
            "label": "Web", "aliases": [], "intent": "Web routes.",
            "status": "active",
            "refs": [{"path": "web/app.py", "role": "entrypoint"}],
            "edges": [], "commands": [],
            "include_globs": ["web/**"], "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)

    body = ("When the Flask blueprint in web/app.py 500s, restart the "
            "backend so the route table reloads.")
    assert topics.best_topic_for_text(fake_git_repo, body) == "web"
    # A body that merely shares common words links to nothing.
    assert topics.best_topic_for_text(
        fake_git_repo, "remember to restart the backend after edits") is None


def test_route_topic_returns_ordered_refs_wiki_and_related(fake_git_repo):
    (fake_git_repo / "web").mkdir()
    (fake_git_repo / "web" / "app.py").write_text("app")
    (fake_git_repo / "tests").mkdir()
    (fake_git_repo / "tests" / "test_web.py").write_text("test")
    (fake_git_repo / "cli.py").write_text("cli")
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "web": {
            "label": "Web",
            "aliases": ["api"],
            "intent": "Web routes.",
            "status": "active",
            "refs": [
                {"path": "tests/test_web.py", "role": "test"},
                {"path": "web/app.py", "role": "entrypoint"},
            ],
            "edges": [{"type": "related", "target": "cli"}],
            "commands": [],
            "include_globs": ["web/**"],
            "exclude_globs": [],
        },
        "cli": {
            "label": "CLI",
            "aliases": [],
            "intent": "Command line.",
            "status": "active",
            "refs": [{"path": "cli.py", "role": "entrypoint"}],
            "edges": [],
            "commands": [],
            "include_globs": ["cli.py"],
            "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)
    wiki = fake_git_repo / ".regin/topics/wiki"
    wiki.mkdir(parents=True)
    (wiki / "web.md").write_text("# Web")
    (wiki / "index.md").write_text("# Index")

    routed = topics.route_topic(fake_git_repo, "api")

    assert routed["status"] == "approved"
    assert routed["topic"]["id"] == "web"
    assert routed["refs"][0]["path"] == "web/app.py"
    assert routed["wiki"] == [".regin/topics/wiki/web.md", ".regin/topics/wiki/index.md"]
    assert routed["wiki_pages"][0] == {
        "path": ".regin/topics/wiki/web.md",
        "content": "# Web",
        "truncated": False,
    }
    assert routed["related"][0]["id"] == "cli"
    assert routed["related"][0]["refs"][0]["path"] == "cli.py"

    file_routed = topics.route_topic(fake_git_repo, "web/app.py")

    assert file_routed["status"] == "approved"
    assert file_routed["topic"]["id"] == "web"


def test_route_topic_bounds_wiki_page_content(fake_git_repo):
    topics.bootstrap(fake_git_repo)
    graph = topics.load_graph(fake_git_repo)
    graph["topics"] = {
        "web": {
            "label": "Web",
            "aliases": [],
            "intent": "Web routes.",
            "status": "active",
            "refs": [],
            "edges": [],
            "commands": [],
            "include_globs": [],
            "exclude_globs": [],
        },
    }
    topics.save_graph(fake_git_repo, graph)
    wiki = fake_git_repo / ".regin/topics/wiki"
    wiki.mkdir(parents=True)
    (wiki / "web.md").write_text("abcdef")
    (wiki / "index.md").write_text("index")

    routed = topics.route_topic(fake_git_repo, "web", max_wiki_chars=3)

    assert routed["wiki_pages"] == [
        {"path": ".regin/topics/wiki/web.md", "content": "abc", "truncated": True}
    ]


def test_generate_topic_router_skill_mentions_wiki_and_unapproved_context(fake_git_repo):
    content = topics.generate_topic_router_skill(fake_git_repo)

    assert "procedure: topic-router" in content
    assert "Identify 2-6 concise search keywords" in content
    assert "regin topics route <keywords>" in content
    assert "wiki_pages" in content
    assert ".regin/topics/wiki/<topic>.md" in content
    assert "unapproved context" in content


def test_install_pre_commit_hook_writes_check_and_staged_scan(fake_git_repo):
    path = topics.install_pre_commit_hook(fake_git_repo)

    mode = os.stat(path).st_mode
    content = path.read_text()
    assert mode & stat.S_IXUSR
    assert "topics check --repo" in content
    assert "topics scan --repo" in content
    assert "--staged" in content


def test_install_topic_hooks_writes_all_hooks(fake_git_repo):
    """The multi-user post-pull sync plus the post-commit drift follow need
    four hooks, not just pre-commit — `install_topic_hooks` returns all of
    them and each is executable. `install_pre_commit_hook` survives as a
    back-compat wrapper so existing callers keep working."""
    paths = topics.install_topic_hooks(fake_git_repo)

    assert set(paths) == {"pre-commit", "post-commit", "post-merge",
                          "post-checkout"}
    for name, path in paths.items():
        assert path.exists(), f"{name} hook not written"
        assert os.stat(path).st_mode & stat.S_IXUSR, f"{name} hook not executable"


def test_post_commit_hook_runs_drift_without_blocking(fake_git_repo):
    """`post-commit` follows file renames into refs/memory but must never
    break the commit — hence the `|| true` trailer — and is a no-op unless
    `mechanical_autoapply` is on (enforced in the command itself)."""
    paths = topics.install_topic_hooks(fake_git_repo)
    content = paths["post-commit"].read_text()

    assert "topics drift" in content
    assert "|| true" in content


def test_post_merge_hook_runs_topics_import_silently(fake_git_repo):
    """`post-merge` after a `git pull` must call `regin topics import`
    with `--reason git_pull` (provenance) and `--quiet` (silence the
    common no-op case), and must never break `git pull` if import
    fails — hence the `|| true` trailer."""
    paths = topics.install_topic_hooks(fake_git_repo)
    content = paths["post-merge"].read_text()

    assert "topics import" in content
    assert "--reason git_pull" in content
    assert "--quiet" in content
    assert "|| true" in content, "import failure must not break git pull"


def test_post_checkout_hook_skips_file_checkouts(fake_git_repo):
    """`post-checkout` fires on every `git switch` and on file checkouts.
    Only branch checkouts can have changed `topic.json`, so the hook
    must short-circuit on the file-checkout case ($3 == 0)."""
    paths = topics.install_topic_hooks(fake_git_repo)
    content = paths["post-checkout"].read_text()

    assert '"${3:-0}" = "1"' in content
    assert "topics import" in content
    assert "|| true" in content
