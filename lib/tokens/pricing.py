"""Token-cost lookup using the models.dev pricing catalogue.

models.dev publishes a community-maintained price table for Anthropic,
OpenAI, Google etc. — ccflare uses it the same way. Carrying our own
hand-rolled table would mean chasing every model launch in PRs.

Schema (from `https://models.dev/api.json`):
    { <provider>: { ..., "models": { <model_id>: { "cost": {
            "input":       <USD per 1M tokens>,
            "output":      <USD per 1M tokens>,
            "cache_read":  <USD per 1M tokens>,
            "cache_write": <USD per 1M tokens>,
        } } } } }

Disk cache lives 24h under `$XDG_CACHE_HOME/regin/`. Network failures
degrade silently to `None` — pricing must never block ingest.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import httpx


_API_URL = 'https://models.dev/api.json'
_TTL_SECONDS = 24 * 60 * 60


@dataclass(frozen=True)
class TokenBreakdown:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


def _cache_path() -> Path:
    override = os.environ.get('REGIN_PRICING_CACHE')
    if override:
        return Path(override)
    base = os.environ.get('XDG_CACHE_HOME') or os.path.expanduser('~/.cache')
    return Path(base) / 'regin' / 'models.dev.json'


_memo: dict | None = None
_memo_ts: float = 0.0


def _load_cached() -> dict | None:
    p = _cache_path()
    if not p.is_file():
        return None
    try:
        if time.time() - p.stat().st_mtime > _TTL_SECONDS:
            return None
        with p.open('r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save_cached(data: dict) -> None:
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.tmp')
        with tmp.open('w', encoding='utf-8') as f:
            json.dump(data, f)
        tmp.replace(p)
    except OSError:
        pass


def _fetch() -> dict | None:
    try:
        r = httpx.get(_API_URL, timeout=10.0,
                      headers={'User-Agent': 'regin/0.1'})
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return None


def get_catalogue() -> dict | None:
    """Return the full pricing catalogue: memo > disk-cache > network."""
    global _memo, _memo_ts
    if _memo is not None and time.time() - _memo_ts < _TTL_SECONDS:
        return _memo
    data = _load_cached()
    if data is None:
        data = _fetch()
        if data is not None:
            _save_cached(data)
    if data is not None:
        _memo = data
        _memo_ts = time.time()
    return data


def _strip_variant(model: str) -> str:
    # Claude Code reports models like 'claude-opus-4-7[1m]' but models.dev
    # only lists the base id. Strip the bracketed variant suffix.
    idx = model.find('[')
    return model[:idx] if idx > 0 else model


def _find_model(catalogue: dict, model: str) -> dict | None:
    target = _strip_variant(model)
    for provider in catalogue.values():
        if not isinstance(provider, dict):
            continue
        models = provider.get('models')
        if not isinstance(models, dict):
            continue
        m = models.get(target) or models.get(model)
        if isinstance(m, dict):
            return m
    return None


def model_rates(model: str | None) -> dict | None:
    """Return {input, output, cache_read, cache_write} USD per 1M, or None."""
    if not isinstance(model, str) or not model:
        return None
    catalogue = get_catalogue()
    if not isinstance(catalogue, dict):
        return None
    m = _find_model(catalogue, model)
    if m is None:
        return None
    rates = m.get('cost')
    return rates if isinstance(rates, dict) else None


def cost(model: str | None, breakdown: TokenBreakdown) -> Optional[float]:
    """USD cost for a token breakdown under the given model. None if unknown."""
    rates = model_rates(model)
    if rates is None:
        return None
    return (
        (rates.get('input') or 0) * breakdown.input_tokens
        + (rates.get('output') or 0) * breakdown.output_tokens
        + (rates.get('cache_read') or 0) * breakdown.cache_read_tokens
        + (rates.get('cache_write') or 0) * breakdown.cache_creation_tokens
    ) / 1_000_000


def reset_cache() -> None:
    """Drop the in-process memo. Test hook."""
    global _memo, _memo_ts
    _memo = None
    _memo_ts = 0.0
