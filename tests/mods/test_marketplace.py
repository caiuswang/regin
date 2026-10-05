import json
from pathlib import Path

MARKETPLACE_ROOT = Path(__file__).resolve().parents[2] / "regin-plugin"


def _dir_violations(root: Path, d: str, names: list[str]) -> list[str]:
    found = []
    count = names.count(d)
    if count != 1:
        found.append(f"plugins/{d}: {count} marketplace entries")
    manifest = root / "plugins" / d / ".claude-plugin" / "plugin.json"
    if not manifest.is_file():
        found.append(f"plugins/{d}: no .claude-plugin/plugin.json")
    elif json.loads(manifest.read_text())["name"] != d:
        found.append(f"plugins/{d}: plugin.json name differs from its dir")
    return found


def _entry_violation(entry: dict, dirs: list[str]) -> str | None:
    name = entry["name"]
    if name not in dirs:
        return f"marketplace entry {name}: no plugins/{name} dir"
    if entry["source"] != f"./plugins/{name}":
        return f"marketplace entry {name}: source is {entry['source']}"
    return None


def _violations(root: Path) -> list[str]:
    entries = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())["plugins"]
    dirs = sorted(p.name for p in (root / "plugins").iterdir() if p.is_dir())
    names = [e["name"] for e in entries]
    found = [v for d in dirs for v in _dir_violations(root, d, names)]
    return found + [v for e in entries if (v := _entry_violation(e, dirs))]


def test_marketplace_lists_every_plugin_dir():
    assert _violations(MARKETPLACE_ROOT) == []


def test_unlisted_dir_and_orphan_entry_are_both_violations(tmp_path):
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "marketplace.json").write_text(json.dumps({
        "plugins": [{"name": "gone", "source": "./plugins/gone"}],
    }))
    (tmp_path / "plugins" / "unlisted").mkdir(parents=True)

    assert _violations(tmp_path) == [
        "plugins/unlisted: 0 marketplace entries",
        "plugins/unlisted: no .claude-plugin/plugin.json",
        "marketplace entry gone: no plugins/gone dir",
    ]
