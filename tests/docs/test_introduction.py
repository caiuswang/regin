"""F153 acceptance: the introduction describes the current product."""

import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

FRAME_SURFACES = ["README.md", "PRODUCT.md"]
RETIRED_FRAMES = ["Agent = Model + Harness", "harness infrastructure"]
LAYERS = ["Workbench", "Runs", "Observability", "Guidance"]


def _read(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


def test_harness_is_no_longer_the_frame():
    for name in FRAME_SURFACES:
        text = _read(name).lower()
        for phrase in RETIRED_FRAMES:
            assert phrase.lower() not in text, f"{name} still says {phrase!r}"
    one_liners = {
        "README.md": _read("README.md").splitlines()[2],
        "CONTEXT.md": " ".join(_read("CONTEXT.md").splitlines()[2:4]),
        "CLAUDE.md": _read("CLAUDE.md").splitlines()[2],
    }
    for name, line in one_liners.items():
        assert "workbench" in line.lower(), f"{name}: {line!r}"
        assert "harness" not in line.lower(), f"{name}: {line!r}"


def test_the_pipeline_leads():
    sections = re.findall(r"^## (.+)$", _read("README.md"), flags=re.M)
    layer_sections = [s for s in sections if s.split(" ")[0] in LAYERS]
    assert [s.split(" ")[0] for s in layer_sections] == LAYERS
    assert sections[0] == layer_sections[0], f"first section is {sections[0]!r}"
    workbench = layer_sections[0].lower()
    for noun in ("features", "tasks", "defects"):
        assert noun in workbench

    purpose = _read("PRODUCT.md").split("## Product Purpose", 1)[1].split("\n## ", 1)[0]
    positions = [purpose.index(f"**{layer}**") for layer in LAYERS]
    assert positions == sorted(positions)


def test_cli_self_description_is_current():
    out = subprocess.run(
        [sys.executable, str(ROOT / "cli" / "regin.py"), "--help"],
        capture_output=True, text=True, check=True, cwd=ROOT,
        env={"COLUMNS": "200", "PATH": "/usr/bin:/bin"},
    ).stdout
    assert "Pattern reference system" not in out
    assert "workbench for building software with AI coding agents" in out

    description = tomllib.loads(_read("pyproject.toml"))["project"]["description"]
    assert "Pattern reference system" not in description
    assert "workbench for building software with AI coding agents" in description

    docstring = _read("cli/regin.py").split('"""')[1]
    assert "workbench for building software with AI coding agents" in docstring


def test_mirror_holds():
    assert (ROOT / "CLAUDE.md").read_bytes() == (ROOT / "AGENTS.md").read_bytes()


def _github_slug(heading: str) -> str:
    slug = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return slug.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    return {_github_slug(h) for h in re.findall(r"^#+ (.+)$", path.read_text(encoding="utf-8"), flags=re.M)}


def test_readme_links_resolve():
    readme = ROOT / "README.md"
    links = re.findall(r"\]\(([^)\s]+)\)", readme.read_text(encoding="utf-8"))
    assert links
    for link in links:
        if re.match(r"[a-z]+:", link):
            continue
        target, _, anchor = link.partition("#")
        path = (readme.parent / target).resolve() if target else readme
        assert path.exists(), f"README link {link!r} → missing {path}"
        if anchor and path.suffix == ".md":
            assert anchor in _anchors(path), f"README link {link!r} → no heading #{anchor}"
