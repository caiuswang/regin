"""Lightweight scanner for plan files across all providers."""

import os
import re
from datetime import datetime

from lib.providers.registry import build_provider, list_provider_ids

_TITLE_RE = re.compile(r'^#\s+(.+)$', re.MULTILINE)


def _extract_title(content: str) -> str:
    m = _TITLE_RE.search(content)
    return m.group(1).strip() if m else 'Untitled Plan'


def _plans_dirs():
    """Yield (provider_id, path) tuples for all provider plan directories."""
    for pid in list_provider_ids():
        provider = build_provider(pid)
        yield pid, str(provider.plans_dir())


def list_plans() -> list[dict]:
    """Return all plan files from all providers, sorted by newest first."""
    plans = []
    for provider_id, pdir in _plans_dirs():
        if not os.path.isdir(pdir):
            continue
        for fname in os.listdir(pdir):
            if not fname.endswith('.md'):
                continue
            fpath = os.path.join(pdir, fname)
            try:
                st = os.stat(fpath)
                with open(fpath, 'r', encoding='utf-8') as f:
                    content = f.read()
            except (OSError, UnicodeDecodeError):
                continue
            plans.append({
                'filename': fname,
                'title': _extract_title(content),
                'updated_at': datetime.fromtimestamp(st.st_mtime).isoformat(),
                'size': st.st_size,
                'provider': provider_id,
            })
    plans.sort(key=lambda p: p['updated_at'], reverse=True)
    return plans


def get_plan(filename: str) -> dict | None:
    """Return a single plan's content and metadata, searching all providers."""
    if '..' in filename or '/' in filename or '\\' in filename:
        return None
    for _provider_id, pdir in _plans_dirs():
        fpath = os.path.join(pdir, filename)
        if not os.path.isfile(fpath):
            continue
        try:
            st = os.stat(fpath)
            with open(fpath, 'r', encoding='utf-8') as f:
                content = f.read()
        except (OSError, UnicodeDecodeError):
            continue
        return {
            'filename': filename,
            'title': _extract_title(content),
            'content': content,
            'updated_at': datetime.fromtimestamp(st.st_mtime).isoformat(),
            'size': st.st_size,
        }
    return None
