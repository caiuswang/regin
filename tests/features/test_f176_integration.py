"""S5 — every build slice of feature 176 merged back into `f174/integration`,
and the built bundle keeps Tiptap out of the entry (Invariant 8).

F176 has no integration branch of its own: its component lives on F174's, so
each slice forks from and merges back into `f174/integration`. Read from git
and from a fresh `vite build` alone — the task rows live in a per-checkout DB,
and a checked-in `web/static/dist` may predate the tree under test.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FRONTEND = REPO_ROOT / "frontend"
INTEGRATION = "f174/integration"

#: S1–S4 build; S5 (this check) lands only its own test, S6/S7 only verify.
BUILD_SLICES = (1, 2, 3, 4)

#: `F176 S3: …`. `Merge branch …` commits are excluded by `--no-merges`, so a
#: merge never stands in for work.
LANDED_IN_SUBJECT = re.compile(r"^F176 S(\d+):")

#: The rich face's dependency tree. `markdown-it` rides in because
#: `prosemirror-markdown` imports it at top level.
RICH_ONLY_SOURCE = re.compile(r"node_modules/(@tiptap/|prosemirror-|markdown-it/)")
RICH_EDITOR_SRC = "src/components/editor/RichEditor.vue"


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=REPO_ROOT,
                          capture_output=True, text=True, timeout=60)


def _has_ref(ref: str) -> bool:
    return _git("rev-parse", "--verify", "-q", f"{ref}^{{commit}}").returncode == 0


def commits_per_slice() -> dict[int, int]:
    """Slice id → non-merge commits reachable from the integration branch that name it.

    Walked with no fork point: once master merges the integration branch a
    `master..` range empties and every slice would read as empty-handed
    exactly when the feature landed.
    """
    proc = _git("log", "--no-merges", "--format=%s", INTEGRATION)
    if proc.returncode != 0:
        pytest.skip(f"git log {INTEGRATION} is not answerable here: {proc.stderr.strip()}")
    counts: dict[int, int] = {}
    for subject in proc.stdout.splitlines():
        found = LANDED_IN_SUBJECT.search(subject)
        if found:
            n = int(found.group(1))
            counts[n] = counts.get(n, 0) + 1
    return counts


def unmerged_slice_branches() -> list[str]:
    """Slice branches whose tip is not an ancestor of the integration branch.

    Only `--is-ancestor`'s exit 1 means "not merged"; any other failure is
    git not answering, and silence is not evidence either way.
    """
    unmerged = []
    for n in BUILD_SLICES:
        branch = f"f176/s{n}"
        if not _has_ref(f"refs/heads/{branch}"):
            continue
        proc = _git("merge-base", "--is-ancestor", branch, INTEGRATION)
        if proc.returncode == 1:
            unmerged.append(branch)
        elif proc.returncode != 0:
            pytest.skip(f"git cannot say whether {branch} is merged: {proc.stderr.strip()}")
    return unmerged


def test_every_slice_merged_back():
    if not _has_ref(INTEGRATION):
        pytest.skip(f"{INTEGRATION} is not in this checkout")

    landed = commits_per_slice()
    empty_handed = [f"S{n}" for n in BUILD_SLICES if landed.get(n, 0) == 0]
    assert not empty_handed, (
        f"slices with no commit on {INTEGRATION}: " + ", ".join(empty_handed))

    unmerged = unmerged_slice_branches()
    assert not unmerged, (
        f"slice branches with work not merged into {INTEGRATION}: "
        + ", ".join(unmerged))


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    if not (FRONTEND / "node_modules" / ".bin" / "vite").exists() or not shutil.which("node"):
        pytest.skip("frontend/node_modules is not installed here")
    out = tmp_path_factory.mktemp("dist")
    proc = subprocess.run(
        [str(FRONTEND / "node_modules" / ".bin" / "vite"), "build",
         "--outDir", str(out), "--emptyOutDir", "--manifest", "--sourcemap"],
        cwd=FRONTEND, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"vite build failed:\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
    return out


def _static_closure(manifest: dict, roots: list[str]) -> set[str]:
    """Manifest keys loaded before first paint: the entries and their static imports."""
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        key = stack.pop()
        if key not in seen:
            seen.add(key)
            stack.extend(manifest[key].get("imports", []))
    return seen


def _sources(out: Path, chunk_file: str) -> list[str]:
    sourcemap = out / f"{chunk_file}.map"
    return json.loads(sourcemap.read_text()).get("sources", []) if sourcemap.exists() else []


#: Rolldown emits no sourcemap for its own tiny runtime helpers (CJS interop,
#: preload helper). Anything larger without a map is unverifiable, not clean.
MAPLESS_HELPER_BYTES = 4096


def _rich_sources_in(out: Path, manifest: dict, keys: set[str]) -> dict[str, list[str]]:
    """Chunk file → first few rich-editor-only sources it bundles, for chunks that bundle any."""
    leaked = {}
    for key in keys:
        chunk = manifest[key]["file"]
        if not (out / f"{chunk}.map").exists():
            if (out / chunk).stat().st_size > MAPLESS_HELPER_BYTES:
                leaked[chunk] = ["<no sourcemap — contents unverifiable>"]
            continue
        hits = sorted(s for s in _sources(out, chunk) if RICH_ONLY_SOURCE.search(s))
        if hits:
            leaked[chunk] = hits[:3]
    return leaked


def test_rich_editor_chunk_not_in_entry(built: Path):
    manifest = json.loads((built / ".vite" / "manifest.json").read_text())
    entries = [k for k, v in manifest.items() if v.get("isEntry")]
    assert entries, "manifest names no entry"
    first_paint = _static_closure(manifest, entries)

    leaked = _rich_sources_in(built, manifest, first_paint)
    assert not leaked, f"rich-editor code reachable without a dynamic import: {leaked}"

    assert RICH_EDITOR_SRC in manifest, f"{RICH_EDITOR_SRC} is not its own chunk"
    assert RICH_EDITOR_SRC not in first_paint, f"{RICH_EDITOR_SRC} is statically imported by the entry"
    rich_sources = _sources(built, manifest[RICH_EDITOR_SRC]["file"])
    assert any("node_modules/@tiptap/" in s for s in rich_sources), (
        "the RichEditor chunk carries no @tiptap source — the check above proves nothing")
