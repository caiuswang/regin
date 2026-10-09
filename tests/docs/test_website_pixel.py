"""F154 acceptance, source half: the pixel website's tokens and screenshots."""

import re
import struct
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WEBSITE = ROOT / "website"
SITE_CSS = WEBSITE / "src/assets/site.css"
FONTS = WEBSITE / "public/fonts"
PIXEL_SHOTS = WEBSITE / "src/assets/shots/pixel"

PALETTE = {
    "bg": "#000000", "panel": "#050605", "panel-raised": "#0c0d0c",
    "rule": "#20221f", "edge": "#333733",
    "ink": "#f6f8f6", "body": "#c3c8c4", "soft": "#d5ddd7", "muted": "#9aa39d", "dim": "#6d766f",
    "green": "#2bde5e", "green-hover": "#57e77f", "green-deep": "#15803d",
    "green-wash": "#10311d", "green-ink": "#052912",
    "gold": "#e8cb7b",
}
FONT_FAMILIES = {
    "font-display": "Silkscreen",
    "font-body": "Atkinson Hyperlegible Next",
    "font-mono": "JetBrains Mono",
}
CHROME = {
    "border-w": "2px",
    "radius": "0",
    "shot-shadow": "8px 8px 0 var(--green-wash)",
    "key-shadow": "4px 4px 0 var(--green-deep)",
    "logo-size": "22px",
    "logo-shadow": "inset -4px -4px 0 var(--green-deep)",
    "max-width": "1320px",
    "docs-article-max": "860px",
}
DELIVERED = ["arm", "defects", "features", "handlers", "inbox", "install", "live", "overview",
             "patterns", "rules", "sessions", "taskgraph", "topics", "trace"]
RECAPTURED = ["taskboard", "memory"]
# DesignSync's get_file cap: a binary asset cut at exactly this many bytes is truncated.
TRANSFER_CAP_BYTES = 196_608
MIN_SHOT_WIDTH = 2560
IMAGE_EXT = r"(?:png|webp|jpe?g|gif|avif)"


def _custom_props(css: str) -> dict[str, str]:
    return {m.group(1): m.group(2).strip() for m in re.finditer(r"--([\w-]+):\s*([^;]+);", css)}


def _normalise_hex(value: str) -> str:
    value = value.lower()
    if len(value) == 4:
        value = "#" + "".join(c * 2 for c in value[1:])
    return value


def _font_faces(css: str) -> list[dict[str, str]]:
    faces = []
    for body in re.findall(r"@font-face\s*\{([^}]*)\}", css):
        faces.append({k.strip(): v.strip() for k, v in re.findall(r"([\w-]+)\s*:\s*([^;]+);", body)})
    return faces


def _assert_tokens(props: dict[str, str]) -> None:
    for name, colour in PALETTE.items():
        assert _normalise_hex(props.get(name, "")) == colour, f"--{name} should be {colour}"
    for name, family in FONT_FAMILIES.items():
        assert props.get(name, "").startswith(f'"{family}"'), f"--{name} should lead with {family}"
    for name, value in CHROME.items():
        assert props.get(name) == value, f"--{name} should be {value!r}, got {props.get(name)!r}"


def _assert_silkscreen_self_hosted(css: str) -> None:
    silkscreen = {f.get("font-weight"): f for f in _font_faces(css) if f.get("font-family") == '"Silkscreen"'}
    assert set(silkscreen) == {"400", "700"}
    for face in silkscreen.values():
        src = re.search(r"url\(([^)]+)\)", face["src"]).group(1)
        assert src.startswith("/fonts/"), f"Silkscreen must be self-hosted, got {src}"
        assert (WEBSITE / "public" / src.lstrip("/")).is_file(), src
    assert "SIL Open Font License" in (FONTS / "OFL-silkscreen.txt").read_text(encoding="utf-8")


def _assert_only_palette_colours(css: str) -> None:
    palette = set(PALETTE.values())
    stray = sorted({_normalise_hex(h) for h in re.findall(r"#[0-9a-fA-F]{3,8}\b", css)} - palette)
    assert stray == [], f"colours outside the Palette: {stray}"
    functional = re.findall(r"\b(rgb|hsla?|hwb|lab|lch|oklab|oklch|color|color-mix|light-dark)\(", css)
    assert functional == [], f"only rgba() overlays may sit outside the Palette, found {functional}"


def test_palette_and_fonts():
    css = SITE_CSS.read_text(encoding="utf-8")
    _assert_tokens(_custom_props(css))
    _assert_silkscreen_self_hosted(css)
    _assert_only_palette_colours(css)


def test_index_meta_is_dark_and_says_workbench():
    index = (WEBSITE / "index.html").read_text(encoding="utf-8")
    assert '<meta name="theme-color" content="#000" />' in index
    title = re.search(r"<title>([^<]*)</title>", index).group(1).lower()
    description = re.search(r'<meta name="description" content="([^"]*)"', index).group(1).lower()
    for text in (title, description):
        assert "workbench" in text and "harness" not in text


def _webp_size(data: bytes) -> tuple[int, int]:
    chunk = data[12:16]
    if chunk == b"VP8X":
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
    elif chunk == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        width, height = (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    elif chunk == b"VP8 ":
        width, height = (v & 0x3FFF for v in struct.unpack("<HH", data[26:30]))
    else:
        raise AssertionError(f"unknown WebP chunk {chunk!r}")
    return width, height


def _assert_whole_webp(path: Path, data: bytes) -> None:
    assert data[:4] == b"RIFF" and data[8:12] == b"WEBP", f"{path.name} is not a WebP"
    assert len(data) != TRANSFER_CAP_BYTES, f"{path.name} is exactly the DesignSync transfer cap"
    riff_size = int.from_bytes(data[4:8], "little")
    assert riff_size + 8 == len(data), f"{path.name} is truncated: RIFF says {riff_size + 8} bytes, file has {len(data)}"
    offset = 12
    while offset < len(data):
        size = int.from_bytes(data[offset + 4:offset + 8], "little")
        offset += 8 + size + (size & 1)
    assert offset == len(data), f"{path.name}: last chunk runs past the end of the file"


def test_shots_are_pixel_captures():
    shots = {p.stem: p for p in PIXEL_SHOTS.iterdir() if not p.name.startswith(".")}
    assert sorted(shots) == sorted(DELIVERED + RECAPTURED)
    for name, path in shots.items():
        assert path.suffix == ".webp", f"{path.name} should be a .webp"
        data = path.read_bytes()
        _assert_whole_webp(path, data)
        width, _height = _webp_size(data)
        assert width >= MIN_SHOT_WIDTH, f"{name} is {width}px wide, below {MIN_SHOT_WIDTH}"


def _imported_images() -> list[tuple[Path, Path]]:
    found = []
    pattern = re.compile(rf"""['"]([^'"\s]+\.{IMAGE_EXT})['"]""")
    for src in sorted((WEBSITE / "src").rglob("*")):
        if src.suffix not in (".vue", ".js") or not src.is_file():
            continue
        for ref in pattern.findall(src.read_text(encoding="utf-8")):
            if ref.startswith((".", "/src/", "@/")):
                target = (WEBSITE / ref.lstrip("/")) if ref.startswith("/src/") else (src.parent / ref)
                found.append((src, target.resolve()))
    return found


def test_imported_pixel_shots_exist():
    for src, target in _imported_images():
        if PIXEL_SHOTS in target.parents:
            assert target.is_file(), f"{src.relative_to(ROOT)} imports a missing shot {target.name}"


@pytest.mark.xfail(strict=True, reason="pre-pixel views still import the old shots until F154 S5 replaces them")
def test_site_imports_only_pixel_shots():
    imports = _imported_images()
    assert imports, "the site imports no screenshots at all"
    stray = sorted({str(t.relative_to(WEBSITE)) for _, t in imports if PIXEL_SHOTS not in t.parents})
    assert stray == [], f"images imported from outside assets/shots/pixel/: {stray}"
