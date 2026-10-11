"""S7 — nothing tracked assumes the author's machine.

Covers spec R7 (plugin paths, auth secret, doctor) and invariant 6: runtime
code in tracked files never names `/Users/taowang`.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AUTHOR_PATH = "/Users/taowang"
SCANNED = ["lib", "cli", "web", "hook_manager", "scripts", "regin-plugin",
           "config/settings.json"]
_COMMENT = {".py": "#", ".sh": "#", ".ts": "//", ".tsx": "//", ".js": "//",
            ".mjs": "//", ".vue": "//"}


def _is_test_file(rel: str) -> bool:
    name = os.path.basename(rel)
    return ("/tests/" in f"/{rel}" or name.startswith("test_")
            or re.search(r"\.test\.[jt]sx?$", name) is not None)


def _runtime_hits(rel: str) -> list[str]:
    marker = _COMMENT.get(Path(rel).suffix)
    hits = []
    text = (REPO_ROOT / rel).read_text(errors="replace")
    for n, line in enumerate(text.splitlines(), 1):
        at = line.find(AUTHOR_PATH)
        if at < 0 or (marker and marker in line[:at]):
            continue
        hits.append(f"{rel}:{n}: {line.strip()}")
    return hits


def test_shipped_plugins_carry_no_author_path():
    tracked = subprocess.run(
        ["git", "ls-files", "--", *SCANNED], cwd=REPO_ROOT,
        capture_output=True, text=True, check=True,
    ).stdout.split()
    hits = [h for rel in tracked if not _is_test_file(rel)
            and (REPO_ROOT / rel).is_file() for h in _runtime_hits(rel)]
    assert hits == []

    for rel in ("regin-plugin/plugins/regin-band/hooks/register.tsx",
                "regin-plugin/plugins/regin-bridge/hooks/register.ts"):
        src = (REPO_ROOT / rel).read_text()
        assert "$.env.get('REGIN_HOME')" in src, rel
        assert "/regin`" in src, rel


def test_jwt_secret_follows_the_config_dir(tmp_path):
    config_dir = tmp_path / "config"
    env = {k: v for k, v in os.environ.items() if not k.startswith("REGIN_")}
    env.update(REGIN_CONFIG_DIR=str(config_dir),
               REGIN_TRACE_DB_PATH=str(tmp_path / "regin.db"),
               REGIN_DATA_DIR=str(tmp_path / "data"))
    out = subprocess.run(
        [sys.executable, "-c",
         "from lib import auth; s = auth._get_secret(); print(auth._SECRET_PATH); print(s)"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=True,
    ).stdout.split()
    secret_path, secret = out[-2], out[-1]
    assert Path(secret_path) == config_dir / "jwt_secret.txt"
    assert (config_dir / "jwt_secret.txt").read_text().strip() == secret


def test_doctor_expands_home_in_bundle_paths(tmp_path, monkeypatch):
    from lib import doctor
    from lib.settings import RuleEngineConfig, settings

    monkeypatch.setenv("HOME", str(tmp_path))
    bundle = tmp_path / ".local" / "share" / "regin" / "patterns" / "vue-complexity"
    bundle.mkdir(parents=True)
    monkeypatch.setattr(settings, "rule_engines", [RuleEngineConfig(
        id="vue-complexity", kind="bundle",
        bundle_root=Path("~/.local/share/regin/patterns/vue-complexity"),
    )])

    rows = doctor._rule_engine_items(doctor._check_tool, doctor._check_module)

    assert [(r["id"], r["present"], r["path"]) for r in rows] == [
        ("engine_vue-complexity_bundle", True, str(bundle)),
    ]
