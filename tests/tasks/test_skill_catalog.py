import json

from lib.tasks.skill_catalog import available_skills


def _skill(root, name):
    (root / ".claude" / "skills" / name).mkdir(parents=True)
    (root / ".claude" / "skills" / name / "SKILL.md").write_text("x")


def _names(cwd, home):
    return {s["name"]: s["source"] for s in available_skills(cwd, home=home)}


def test_project_user_and_command_skills_are_listed(tmp_path):
    home, repo = tmp_path / "home", tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    _skill(repo, "local-only")
    _skill(home, "everywhere")
    _skill(home, "local-only")
    (repo / ".claude" / "commands").mkdir()
    (repo / ".claude" / "commands" / "ship.md").write_text("x")
    (repo / ".claude" / "skills" / "no-skill-md").mkdir()

    assert _names(repo, home) == {
        "local-only": "project", "everywhere": "user", "ship": "project"}


def test_a_subdirectory_sees_its_checkout_but_not_beyond(tmp_path):
    home, outer = tmp_path / "home", tmp_path / "outer"
    repo = outer / "repo"
    (repo / ".git").mkdir(parents=True)
    _skill(outer, "outside")
    _skill(repo, "inside")
    sub = repo / "pkg"
    sub.mkdir()
    assert set(_names(sub, home)) == {"inside"}


def test_a_skill_another_repo_carries_is_not_offered(tmp_path):
    home = tmp_path / "home"
    a, b = tmp_path / "a", tmp_path / "b"
    for repo in (a, b):
        (repo / ".git").mkdir(parents=True)
    _skill(a, "goal-verified")
    assert "goal-verified" not in _names(b, home)


def test_only_enabled_plugins_installed_for_this_project_count(tmp_path):
    home, repo, other = tmp_path / "home", tmp_path / "repo", tmp_path / "other"
    (repo / ".git").mkdir(parents=True)
    (other / ".git").mkdir(parents=True)
    plugins = {}
    for key, scope, project in (("glob@m", "user", None),
                                ("here@m", "project", repo),
                                ("there@m", "project", other),
                                ("off@m", "user", None)):
        path = tmp_path / "cache" / key
        (path / "skills" / "s").mkdir(parents=True)
        (path / "skills" / "s" / "SKILL.md").write_text("x")
        plugins[key] = [{"scope": scope, "installPath": str(path),
                         **({"projectPath": str(project)} if project else {})}]
    (home / ".claude" / "plugins").mkdir(parents=True)
    (home / ".claude" / "plugins" / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": plugins}))
    (home / ".claude" / "settings.json").write_text(json.dumps(
        {"enabledPlugins": {"glob@m": True, "there@m": True, "off@m": True}}))
    (repo / ".claude").mkdir()
    (repo / ".claude" / "settings.json").write_text(json.dumps(
        {"enabledPlugins": {"here@m": True}}))
    (repo / ".claude" / "settings.local.json").write_text(json.dumps(
        {"enabledPlugins": {"off@m": False}}))

    assert _names(repo, home) == {"glob:s": "plugin", "here:s": "plugin"}


def test_a_missing_directory_offers_nothing(tmp_path):
    assert available_skills(tmp_path / "gone", home=tmp_path) == []


def test_the_endpoint_answers_per_cwd(flask_client, tmp_path):
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    _skill(repo, "only-here")
    resp = flask_client.get(f"/api/agent-runs/skill-options?cwd={repo}")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["cwd"] == str(repo)
    assert {"name": "only-here", "source": "project"} in body["skills"]
