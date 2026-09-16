"""Fan-out substring search over file-backed corpora, driven by ripgrep.

ripgrep is an *accelerator*, not a dependency: it scans a gigabyte of
markdown in tens of milliseconds where the Python walk below takes hundreds,
but every call falls back to that walk when `rg` is absent, so search keeps
working on a machine that never installed it (`regin doctor` reports it as
optional). The split of labour is deliberate — rg answers "which files, how
many hits", and Python reads only the handful of winners to build their
preview windows. Neither leg ever loads the whole corpus into memory.
"""

from __future__ import annotations

import os
import shutil
import subprocess

from lib.activity_log import get_activity_logger

log = get_activity_logger("fulltext")

# A pathological query against a huge tree must not wedge a web worker.
RG_TIMEOUT_SECONDS = 5
# Files whose hit counts are ranked; only the top few are then read.
MAX_MATCHED_FILES = 200


def ripgrep_path() -> str | None:
    return shutil.which("rg")


def _parse_counts(stdout: str) -> dict[str, int]:
    """`rg --count-matches` prints `<path>:<n>` per matching file."""
    counts: dict[str, int] = {}
    for line in stdout.splitlines():
        path, sep, raw = line.rpartition(":")
        if not sep or not path:
            continue
        try:
            counts[path] = int(raw)
        except ValueError:
            continue
    return counts


def _rg_counts(roots: list[str], query: str, globs: list[str]) -> dict[str, int] | None:
    """Per-file match counts via rg, or None when rg is unusable here."""
    exe = ripgrep_path()
    if not exe:
        return None
    # `--with-filename` is not the default when rg is handed exactly one
    # file: it would print a bare count with no path to key it by.
    # `--no-ignore` because the fallback walk honours no ignore file either,
    # and the two legs must return the same corpus: without it, a stray
    # .gitignore inside a patterns tree hid pages on an rg machine and showed
    # them on a machine without rg. Vendored dirs are excluded explicitly by
    # the caller's globs instead, which both legs apply.
    cmd = [exe, "--count-matches", "--fixed-strings", "--ignore-case",
           "--no-messages", "--no-heading", "--hidden", "--no-ignore",
           "--with-filename"]
    for glob in globs:
        cmd += ["--glob", glob]
    cmd += ["--", query, *roots]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=RG_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        log.error("ripgrep_failed", exc_info=True)
        return None
    # 0 = matches, 1 = no matches (both are answers); 2 = a real error, which
    # must fall back rather than silently report an empty corpus.
    if proc.returncode not in (0, 1):
        log.error("ripgrep_error", returncode=proc.returncode)
        return None
    return _parse_counts(proc.stdout)


# Mirrors the `!**/<name>/**` globs callers hand rg, for the fallback walk.
SKIP_DIRS = frozenset({".git", "node_modules", ".venv", "venv",
                       "site-packages", "__pycache__", "dist", "build"})


def _walk(roots: list[str], suffixes: tuple[str, ...]) -> list[str]:
    out: list[str] = []
    for root in roots:
        if os.path.isfile(root):
            out.append(root)
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for name in filenames:
                if not suffixes or name.endswith(suffixes):
                    out.append(os.path.join(dirpath, name))
    return out


def read_text(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


# rg skips a file whose first bytes contain NUL. The fallback has to make the
# same call or a machine without rg surfaces minified blobs and binaries that
# an rg machine never sees — the two legs must agree on the CORPUS, not just
# on how they count within it.
_BINARY_SNIFF_BYTES = 8192


def _is_binary(path: str) -> bool:
    try:
        with open(path, "rb") as fh:
            return b"\x00" in fh.read(_BINARY_SNIFF_BYTES)
    except OSError:
        return True


def _python_counts(roots: list[str], query: str,
                   suffixes: tuple[str, ...]) -> dict[str, int]:
    needle = query.lower()
    counts: dict[str, int] = {}
    for path in _walk(roots, suffixes):
        if _is_binary(path):
            continue
        text = read_text(path)
        if not text:
            continue
        n = text.lower().count(needle)
        if n:
            counts[path] = n
    return counts


def count_matches(roots: list[str], query: str, *, globs: list[str],
                  suffixes: tuple[str, ...]) -> dict[str, int]:
    """`{path: hit count}` for every file under `roots` containing `query`.

    `globs` steer rg; `suffixes` steer the fallback walk. They describe the
    same file set in the two tools' own dialects.
    """
    # Named files are filtered here rather than left to each leg: rg reads an
    # explicitly-named binary up to its first NUL and reports a partial count,
    # while the Python walk drops it — the two legs must agree on the corpus
    # for the `file` kind too, not just for the markdown trees.
    roots = [r for r in roots if r and os.path.exists(r)
             and not (os.path.isfile(r) and _is_binary(r))]
    if not roots or not query:
        return {}
    counts = _rg_counts(roots, query, globs)
    if counts is None:
        counts = _python_counts(roots, query, suffixes)
    if len(counts) <= MAX_MATCHED_FILES:
        return counts
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:MAX_MATCHED_FILES]
    return dict(top)
