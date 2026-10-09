"""F153 acceptance, website half: the site introduces regin as a workbench."""

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEBSITE = ROOT / "website"

RETIRED_FRAMES = ["Agent = Model + Harness", "harness infrastructure"]
LAYERS = ["Workbench", "Runs", "Observability", "Guidance"]
NAV = ["Introduction", "Getting Started", "Pipeline", "Runs", "Dashboard",
       "Guidance", "Architecture", "Configuration", "CLI"]
ROUTES = ["/", "/getting-started", "/pipeline", "/runs", "/dashboard", "/guidance",
          "/architecture", "/configuration", "/cli", "/topics", "/topics/routing"]


def _frame_surfaces() -> list[Path]:
    fixed = ["index.html", "src/router.js", "src/views/HomeView.vue", "src/views/PipelineView.vue",
             "src/views/RunsView.vue", "src/views/GuidanceView.vue"]
    return ([WEBSITE / f for f in fixed]
            + sorted((WEBSITE / "src/content").glob("*.js"))
            + sorted((WEBSITE / "src/components").rglob("*.vue")))


def test_harness_is_no_longer_the_frame():
    for path in _frame_surfaces():
        text = path.read_text(encoding="utf-8").lower()
        for phrase in RETIRED_FRAMES:
            assert phrase.lower() not in text, f"{path.relative_to(ROOT)} still says {phrase!r}"

    index = (WEBSITE / "index.html").read_text(encoding="utf-8")
    heads = re.findall(r'<title>([^<]*)</title>|<meta (?:name|property)="[^"]*(?:description|title)" content="([^"]*)"', index)
    assert heads
    for groups in heads:
        value = "".join(groups).lower()
        assert "harness" not in value, value
    assert "workbench" in re.search(r"<title>([^<]*)</title>", index).group(1).lower()

    base_title = re.search(r"BASE_TITLE = '([^']*)'", (WEBSITE / "src/router.js").read_text(encoding="utf-8")).group(1)
    assert "workbench" in base_title.lower() and "harness" not in base_title.lower()


def test_layers_keep_their_order_on_the_website():
    features = (WEBSITE / "src/content/features.js").read_text(encoding="utf-8")
    layers_block = features.split("export const LAYERS", 1)[1].split("export const", 1)[0]
    assert re.findall(r"name: '([^']*)'", layers_block) == LAYERS
    pillars_block = features.split("export const PILLARS", 1)[1].split("export const", 1)[0]
    assert re.findall(r"layer: '([^']*)'", pillars_block) == LAYERS

    header = (WEBSITE / "src/components/SiteHeader.vue").read_text(encoding="utf-8")
    nav_block = header.split("const NAV = [", 1)[1].split("]\n", 1)[0]
    assert re.findall(r"label: '([^']*)'", nav_block) == NAV


def _top_level_groups() -> list[str]:
    from typer.main import get_command

    sys.path.insert(0, str(ROOT))
    from cli.app import app

    command = get_command(app)
    return sorted(name for name, cmd in command.commands.items() if getattr(cmd, "commands", None))


def test_cli_reference_is_complete():
    groups = _top_level_groups()
    assert {"feature", "task", "defects", "memory", "users"} <= set(groups)
    cli_js = (WEBSITE / "src/content/cli.js").read_text(encoding="utf-8")
    block = cli_js.split("export const COMMAND_GROUPS", 1)[1].split("export const", 1)[0]
    listed = set(re.findall(r"group: 'regin ([a-z-]+)'", block))
    missing = [g for g in groups if g not in listed]
    assert not missing, f"COMMAND_GROUPS misses {missing}"


def _png_size(path: Path) -> tuple[int, int]:
    head = path.read_bytes()[:24]
    assert head[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} is not a PNG"
    return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")


@pytest.mark.parametrize("view", ["PipelineView", "RunsView"])
def test_new_pages_show_the_real_product(view):
    source = (WEBSITE / "src/views" / f"{view}.vue").read_text(encoding="utf-8")
    imported = set(re.findall(r"from '\.\./assets/shots/([\w-]+)\.(?:png|webp)'", source))
    bases = {name.rsplit("-", 1)[0] for name in imported if name.endswith(("-light", "-dark"))}
    assert bases, f"{view} shows no dashboard screenshot"
    for name in (f"{base}-{theme}" for base in bases for theme in ("light", "dark")):
        assert name in imported, f"{view} does not import {name}"
        _assert_dashboard_capture(name)
    _assert_shots_wired_by_theme(view, source)


def _assert_shots_wired_by_theme(view: str, source: str) -> None:
    files = dict(re.findall(r"import (\w+) from '\.\./assets/shots/([\w-]+)\.(?:png|webp)'", source))
    shot_objects = {name: (files.get(png), files.get(webp)) for name, png, webp
                    in re.findall(r"const (\w+) = \{ png: (\w+), webp: (\w+) \}", source)}
    props = re.findall(r"<ThemedShot :light=\"(\w+)\" :dark=\"(\w+)\"", source)
    assert props, f"{view} renders no <ThemedShot>"
    for light, dark in props:
        for theme, obj in (("light", light), ("dark", dark)):
            png, webp = shot_objects[obj]
            assert png and png.endswith(f"-{theme}") and png == webp, f"{view} :{theme} shows {png}/{webp}"


def _assert_dashboard_capture(name: str) -> None:
    shots = WEBSITE / "src/assets/shots"
    assert (shots / f"{name}.webp").is_file(), f"missing {name}.webp"
    width, height = _png_size(shots / f"{name}.png")
    assert width >= 1280 and height >= 720, f"{name}.png is {width}x{height}, not a full dashboard capture"


def test_website_builds_and_routes_resolve():
    router = (WEBSITE / "src/router.js").read_text(encoding="utf-8")
    declared = dict(re.findall(r"path: '([^']*)', name: '[^']*', component: \(\) => import\('\./views/(\w+)\.vue'\)", router))
    for route in ROUTES:
        view = declared.get(route)
        assert view and view != "NotFoundView", f"{route} has no page"
        assert (WEBSITE / "src/views" / f"{view}.vue").exists(), f"{route} → missing {view}.vue"

    if not (WEBSITE / "node_modules").exists() or shutil.which("npm") is None:
        pytest.skip("website/node_modules not installed — run `cd website && npm ci`")
    result = subprocess.run(["npm", "run", "build"], cwd=WEBSITE, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
