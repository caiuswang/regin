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


def _norm_id(model: str) -> str:
    # Normalize a model id for matching: Claude Code reports a bracketed
    # variant ('claude-opus-4-8[1m]') and some providers append a routing
    # suffix ('claude-opus-4-8@default' on google-vertex). models.dev's
    # base id carries neither, so strip both before comparing.
    idx = model.find('[')
    base = model[:idx] if idx > 0 else model
    return base.split('@', 1)[0]


def _has_tiers(m: dict) -> bool:
    c = m.get('cost')
    return isinstance(c, dict) and ('tiers' in c or 'context_over_200k' in c)


def _candidate_models(catalogue: dict, model: str):
    """Yield every catalogue entry whose id matches `model` once both are
    normalized (variant + routing suffix stripped)."""
    target = _norm_id(model)
    for provider in catalogue.values():
        if not isinstance(provider, dict):
            continue
        models = provider.get('models')
        if not isinstance(models, dict):
            continue
        for key, m in models.items():
            if isinstance(m, dict) and _norm_id(key) == target:
                yield m


def _find_model(catalogue: dict, model: str) -> dict | None:
    # models.dev shards the same model across many providers (and key
    # shapes), and only some carry context-tier pricing (the >200K rate).
    # Prefer a tier-bearing entry so cost() can apply the higher tier;
    # fall back to the first plain match when none expose tiers.
    fallback = None
    for m in _candidate_models(catalogue, model):
        if _has_tiers(m):
            return m
        if fallback is None:
            fallback = m
    return fallback


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


_RATE_KEYS = ('input', 'output', 'cache_read', 'cache_write')


def _best_context_tier(tiers: list, context_tokens: int) -> dict | None:
    """The highest context tier whose threshold `context_tokens` exceeds."""
    best_size = -1
    chosen = None
    for t in tiers:
        if not isinstance(t, dict):
            continue
        info = t.get('tier') or {}
        size = info.get('size')
        ok = (info.get('type') == 'context'
              and isinstance(size, (int, float))
              and context_tokens > size and size > best_size)
        if ok:
            best_size = size
            chosen = t
    return chosen


def _rates_for_context(rates: dict, context_tokens: int | None) -> dict:
    """Effective per-1M rates, applying the context tier when the
    request's context size crosses a threshold.

    models.dev encodes tiered pricing as
    ``cost.tiers = [{input, output, ..., tier:{type:'context', size:N}}]``
    (Anthropic's 1M-context models bill ~2x input / 1.5x output above
    200K). Below the lowest threshold — or when no tiers are published —
    the top-level rates apply, so this is a no-op for flat models.
    """
    eff = {k: rates.get(k) for k in _RATE_KEYS}
    tiers = rates.get('tiers')
    if not context_tokens or not isinstance(tiers, list):
        return eff
    tier = _best_context_tier(tiers, context_tokens)
    if tier:
        for k in _RATE_KEYS:
            if tier.get(k) is not None:
                eff[k] = tier[k]
    return eff


def cost(model: str | None, breakdown: TokenBreakdown,
         context_tokens: int | None = None) -> Optional[float]:
    """USD cost for a token breakdown under the given model. None if unknown.

    `context_tokens` is the request's total context size; when provided
    and the model publishes context tiers, the >threshold rate is used.
    Omit it (or pass 0/None) for the flat top-level rate.
    """
    rates = model_rates(model)
    if rates is None:
        return None
    eff = _rates_for_context(rates, context_tokens)
    return (
        (eff.get('input') or 0) * breakdown.input_tokens
        + (eff.get('output') or 0) * breakdown.output_tokens
        + (eff.get('cache_read') or 0) * breakdown.cache_read_tokens
        + (eff.get('cache_write') or 0) * breakdown.cache_creation_tokens
    ) / 1_000_000


def reset_cache() -> None:
    """Drop the in-process memo. Test hook."""
    global _memo, _memo_ts
    _memo = None
    _memo_ts = 0.0
