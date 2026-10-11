"""F153 acceptance: docs/setup.md carries the operational procedures README dropped."""

import importlib.util
from pathlib import Path

import click

ROOT = Path(__file__).resolve().parents[2]
SETUP = ROOT / "docs" / "setup.md"


def _generator():
    spec = importlib.util.spec_from_file_location("gen_cli_reference", ROOT / "scripts" / "gen_cli_reference.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _section(text: str, heading: str) -> str:
    return text.split(f"\n## {heading}\n", 1)[1].split("\n## ", 1)[0]


SPEC_GROUPS = {"feature", "task", "defects", "stats", "hooks", "bridge", "grade", "messages", "events",
               "gate", "goal", "memory", "wiki", "schema", "logs", "trace", "topics", "rules",
               "pattern", "skills", "users"}


def _visible(group: click.Group) -> dict[str, click.Command]:
    return {name: cmd for name, cmd in group.commands.items() if not cmd.hidden}


def _reference_rows() -> dict[str, str]:
    reference = _section(SETUP.read_text(encoding="utf-8"), "CLI reference")
    return {line.split("`")[1]: line for line in reference.splitlines() if line.startswith("| `regin ")}


def _missing_subcommands(groups: dict[str, click.Group], rows: dict[str, str]) -> list[str]:
    return [
        f"{name} {sub}"
        for name, group in groups.items()
        for sub in _visible(group)
        if f"`{sub}`" not in rows[f"regin {name}"]
    ]


def test_cli_reference_is_complete():
    gen = _generator()
    root = gen.command_tree()
    visible = _visible(root)
    groups = {name: cmd for name, cmd in visible.items() if isinstance(cmd, click.Group)}
    assert SPEC_GROUPS <= set(groups)

    rows = _reference_rows()
    missing = [name for name in visible if f"regin {name}" not in rows]
    assert not missing, f"CLI reference is missing {missing}"
    missing_subs = _missing_subcommands(groups, rows)
    assert not missing_subs, f"CLI reference is missing subcommands {missing_subs}"


def test_cli_reference_is_current():
    gen = _generator()
    doc = SETUP.read_text(encoding="utf-8")
    assert gen.splice(doc, gen.render(gen.command_tree())) == doc, \
        "CLI reference is stale — run scripts/gen_cli_reference.py"
    assert "| `regin uninstall` |" in _section(doc, "CLI reference")


def test_setup_documents_upgrade_and_uninstall_without_rebuild():
    doc = SETUP.read_text(encoding="utf-8")
    assert "git pull && ./scripts/setup.sh" in _section(doc, "Upgrade")
    assert "regin uninstall --dry-run" in _section(doc, "Uninstall")
    for heading in ("Manual Setup", "Troubleshooting"):
        section = _section(doc, heading)
        assert "cli/regin.py init --force" not in section
        assert "cli/regin.py rebuild" not in section
        assert "Run `regin rebuild`" not in section


def test_kimi_walkthrough_lives_in_setup_not_readme():
    assert "tmux -L regin-bridge attach" not in (ROOT / "README.md").read_text(encoding="utf-8")

    walkthrough = _section(SETUP.read_text(encoding="utf-8"), "Run a task with Kimi")
    assert "tmux -L regin-bridge attach -t task-<task id>-a<attempt>" in walkthrough
    for step in ("Install and sign in", "Arm.", "Pick a launch", "Watch it"):
        assert step in walkthrough, f"walkthrough step {step!r} missing"

    limits = walkthrough.split("Known limits:", 1)[1].split("\n\n", 2)[1]
    bullets = [line for line in limits.splitlines() if line.startswith("- ")]
    assert len(bullets) == 5, bullets
    for limit in ("--yolo", "cancel-then-re-prompt", "cannot be armed", "terminal", "workspace trust"):
        assert any(limit in b for b in bullets), f"known limit {limit!r} missing"
