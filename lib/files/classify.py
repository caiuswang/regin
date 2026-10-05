"""R2 — the one classifier. `FileMeta.preview` is computed here and nowhere
else; a wrong classification is fixed in this table, never in a renderer."""

from __future__ import annotations

import codecs
import json
import mimetypes
import os
from dataclasses import dataclass
from typing import Callable

TEXT_CAP = 2 * 1024 * 1024
MEDIA_CAP = 50 * 1024 * 1024
SNIFF_BYTES = 8 * 1024

Reader = Callable[[int | None], bytes]

# extension → (preview, mime). Media kinds are capped at MEDIA_CAP, the
# rendered text kinds at TEXT_CAP.
_MEDIA = {
    **{e: ("image", None) for e in (
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".avif", ".bmp", ".ico")},
    ".pdf": ("pdf", "application/pdf"),
    **{e: ("audio", None) for e in (".mp3", ".wav", ".ogg", ".m4a", ".flac")},
    **{e: ("video", None) for e in (".mp4", ".webm", ".mov")},
}
_RENDERED_TEXT = {
    ".svg": ("svg", "image/svg+xml"),
    ".md": ("markdown", "text/markdown; charset=utf-8"),
    ".markdown": ("markdown", "text/markdown; charset=utf-8"),
    ".html": ("html", "text/html; charset=utf-8"),
    ".htm": ("html", "text/html; charset=utf-8"),
    ".ipynb": ("notebook", "application/x-ipynb+json"),
    ".csv": ("csv", "text/csv; charset=utf-8"),
    ".tsv": ("csv", "text/tab-separated-values; charset=utf-8"),
}
_EXTRA_MIME = {
    ".avif": "image/avif", ".webp": "image/webp", ".m4a": "audio/mp4",
    ".flac": "audio/flac", ".webm": "video/webm", ".mov": "video/quicktime",
}

# Monaco `basic-languages` ids.
_LANGUAGE_BY_EXT = {
    ".py": "python", ".pyi": "python", ".js": "javascript", ".mjs": "javascript",
    ".cjs": "javascript", ".jsx": "javascript", ".ts": "typescript",
    ".tsx": "typescript", ".json": "json", ".ipynb": "json", ".md": "markdown",
    ".markdown": "markdown", ".html": "html", ".htm": "html", ".vue": "html",
    ".css": "css", ".scss": "scss", ".less": "less", ".yaml": "yaml",
    ".yml": "yaml", ".sh": "shell", ".bash": "shell", ".zsh": "shell",
    ".fish": "shell", ".go": "go", ".rs": "rust", ".java": "java",
    ".kt": "kotlin", ".kts": "kotlin", ".c": "c", ".h": "c", ".cpp": "cpp",
    ".cc": "cpp", ".hpp": "cpp", ".cs": "csharp", ".rb": "ruby",
    ".php": "php", ".sql": "sql", ".xml": "xml", ".svg": "xml",
    ".swift": "swift", ".lua": "lua", ".r": "r", ".scala": "scala",
    ".dart": "dart", ".graphql": "graphql", ".gql": "graphql",
    ".proto": "protobuf", ".ini": "ini", ".cfg": "ini", ".toml": "ini",
    ".ps1": "powershell", ".bat": "bat", ".pl": "perl", ".m": "objective-c",
    ".ex": "elixir", ".exs": "elixir", ".clj": "clojure", ".tf": "hcl",
}
_LANGUAGE_BY_NAME = {"dockerfile": "dockerfile"}


# The kinds whose bytes are text, so a HEAD-vs-worktree text diff means
# something; an image, a binary or a too_large file is never diffed.
TEXT_KINDS = frozenset({"code", "markdown", "html", "svg", "csv", "notebook"})


@dataclass(frozen=True)
class Classification:
    preview: str
    mime: str
    language: str | None


def language_for(name: str) -> str:
    lower = name.lower()
    if lower in _LANGUAGE_BY_NAME:
        return _LANGUAGE_BY_NAME[lower]
    return _LANGUAGE_BY_EXT.get(os.path.splitext(lower)[1], "plaintext")


def _guess_mime(name: str, ext: str) -> str:
    return (_EXTRA_MIME.get(ext) or mimetypes.guess_type(name)[0]
            or "application/octet-stream")


def _is_notebook(data: bytes) -> bool:
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError):
        return False
    return isinstance(doc, dict) and doc.get("nbformat") == 4 \
        and isinstance(doc.get("cells"), list)


def _decodes(data: bytes, *, final: bool) -> bool:
    try:
        codecs.getincrementaldecoder("utf-8-sig")().decode(data, final=final)
    except UnicodeDecodeError:
        return False
    return True


def _classify_media(name: str, ext: str, size: int) -> Classification:
    preview, mime = _MEDIA[ext]
    mime = mime or _guess_mime(name, ext)
    return Classification("too_large" if size > MEDIA_CAP else preview, mime, None)


def _classify_rendered(name: str, ext: str, size: int, read: Reader) -> Classification:
    preview, mime = _RENDERED_TEXT[ext]
    if size > TEXT_CAP:
        return Classification("too_large", mime, None)
    if preview == "notebook" and not _is_notebook(read(None)):
        return _classify_text(name, ext, size, read)
    return Classification(preview, mime, language_for(name))


def _classify_text(name: str, ext: str, size: int, read: Reader) -> Classification:
    head = read(SNIFF_BYTES)
    if b"\x00" in head:
        return Classification("binary", _guess_mime(name, ext), None)
    if size > TEXT_CAP:
        preview = "too_large" if _decodes(head, final=False) else "binary"
        return Classification(preview, _guess_mime(name, ext), None)
    if not _decodes(read(None), final=True):
        return Classification("binary", _guess_mime(name, ext), None)
    return Classification("code", "text/plain; charset=utf-8", language_for(name))


def classify(name: str, size: int, read: Reader) -> Classification:
    """`read(n)` returns the first `n` bytes, `read(None)` all of them; it is
    called only when the extension alone cannot decide."""
    ext = os.path.splitext(name.lower())[1]
    if ext in _MEDIA:
        return _classify_media(name, ext, size)
    if ext in _RENDERED_TEXT:
        return _classify_rendered(name, ext, size, read)
    return _classify_text(name, ext, size, read)
