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

A *miss* (the catalogue lacks the requested model, e.g. a model that
launched after the cache was written) forces at most one off-TTL
re-fetch so a brand-new model gets priced without waiting out the full
24h — guarded against hammering models.dev (see `_should_refresh_for_miss`).
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
_FETCH_TIMEOUT_SECONDS = 10.0
# The miss-driven re-fetch runs synchronously on the read/serve path, so it
# uses a tighter timeout than the background TTL refresh: falling back to an
# unpriced (None) turn beats stalling a dashboard render on a slow models.dev.
_MISS_FETCH_TIMEOUT_SECONDS = 4.0

# Guards on the miss-driven off-TTL re-fetch. Skip the re-fetch if we already
# pulled the catalogue from the network within _FRESH_WINDOW_SECONDS (the same
# bytes can't suddenly contain the model), and don't retry the same missing id
# more than once per _MISS_BACKOFF_SECONDS (an id models.dev genuinely lacks
# would otherwise trigger a fetch on every priced turn).
_FRESH_WINDOW_SECONDS = 60
_MISS_BACKOFF_SECONDS = 6 * 60 * 60


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
# Wall-clock of the last successful network fetch, and of the last off-TTL
# re-fetch attempt per missing model id — both feed the miss-refresh guards.
_last_fetch_ts: float = 0.0
_miss_ts: dict[str, float] = {}


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


def _fetch(timeout: float = _FETCH_TIMEOUT_SECONDS) -> dict | None:
    try:
        r = httpx.get(_API_URL, timeout=timeout,
                      headers={'User-Agent': 'regin/0.1'})
        r.raise_for_status()
        return r.json()
    except (httpx.HTTPError, json.JSONDecodeError, ValueError):
        return None


def get_catalogue(force_refresh: bool = False) -> dict | None:
    """Return the full pricing catalogue: memo > disk-cache > network.

    `force_refresh=True` skips both the memo and the disk cache and pulls
    straight from the network — used to pick up a just-launched model the
    cached catalogue predates. A successful fetch still refreshes both caches.
    """
    global _memo, _memo_ts, _last_fetch_ts
    if not force_refresh and _memo is not None and time.time() - _memo_ts < _TTL_SECONDS:
        return _memo
    data = None if force_refresh else _load_cached()
    if data is None:
        timeout = _MISS_FETCH_TIMEOUT_SECONDS if force_refresh else _FETCH_TIMEOUT_SECONDS
        data = _fetch(timeout)
        if data is not None:
            _save_cached(data)
            _last_fetch_ts = time.time()
    if data is not None:
        _memo = data
        _memo_ts = time.time()
    return data


# Map an agent's reported model id onto a models.dev catalogue id when the
# two don't match. Kimi Code CLI reports its managed coding-plan model as
# `kimi-code/kimi-for-coding`; models.dev keys the underlying model as
# `kimi-k2.7-code` (the `kimi-for-coding` *provider* lists it at $0 because the
# plan is a flat subscription, so we price the underlying K2.7 Code model).
_MODEL_ALIASES = {
    'kimi-code/kimi-for-coding': 'kimi-k2.7-code',
    'kimi-for-coding': 'kimi-k2.7-code',
}


def _norm_id(model: str) -> str:
    # Normalize a model id for matching: Claude Code reports a bracketed
    # variant ('claude-opus-4-8[1m]') and some providers append a routing
    # suffix ('claude-opus-4-8@default' on google-vertex). models.dev's
    # base id carries neither, so strip both before comparing.
    idx = model.find('[')
    base = model[:idx] if idx > 0 else model
    return base.split('@', 1)[0]


def _aliased_id(model: str) -> str:
    """Normalized id with any agent->catalogue model alias applied."""
    base = _norm_id(model)
    return _MODEL_ALIASES.get(base, base)


def _has_tiers(m: dict) -> bool:
    c = m.get('cost')
    return isinstance(c, dict) and ('tiers' in c or 'context_over_200k' in c)


def _candidate_models(catalogue: dict, model: str):
    """Yield every catalogue entry whose id matches `model` once both are
    normalized (variant + routing suffix stripped)."""
    target = _aliased_id(model)
    for provider in catalogue.values():
        if not isinstance(provider, dict):
            continue
        models = provider.get('models')
        if not isinstance(models, dict):
            continue
        for key, m in models.items():
            if isinstance(m, dict) and _norm_id(key) == target:
                yield m


def _rate_completeness(m: dict) -> int:
    """Rank one catalogue shard of a model against its siblings.

    models.dev shards the same model across many providers, and the shards
    disagree: some carry the >200K context tier, some omit cache_read /
    cache_write entirely (so cache cost silently bills $0), and some are $0
    subscription mirrors (duo-chat, flat-plan providers). Rank highest a
    tier-bearing shard, then the most complete rate dict, with genuinely-priced
    shards above $0 mirrors. -1 when the shard has no usable cost dict.
    """
    cost_dict = m.get('cost')
    if not isinstance(cost_dict, dict):
        return -1
    present = sum(1 for k in _RATE_KEYS if cost_dict.get(k) is not None)
    tiered = 100 if _has_tiers(m) else 0
    priced = 1 if (cost_dict.get('input') or 0) > 0 else 0
    return tiered + present * 2 + priced


def _find_model(catalogue: dict, model: str) -> dict | None:
    # The same model is sharded across many providers with differing
    # completeness, so pick the best-ranked shard — NOT the first match. A
    # naive first-match can land on a shard missing cache_read/cache_write
    # (billing cache tokens at $0) or a $0 subscription mirror. See
    # `_rate_completeness`; a tier-bearing shard still wins so cost() can apply
    # the >200K rate.
    best = None
    best_score = -1
    for m in _candidate_models(catalogue, model):
        score = _rate_completeness(m)
        if score > best_score:
            best_score = score
            best = m
    return best


def _lookup_rates(catalogue: dict | None, model: str) -> dict | None:
    """Resolve `model` to its rate dict within an already-fetched catalogue."""
    if not isinstance(catalogue, dict):
        return None
    m = _find_model(catalogue, model)
    if m is None:
        return None
    rates = m.get('cost')
    return rates if isinstance(rates, dict) else None


def _lookup_context_limit(catalogue: dict | None, model: str) -> int | None:
    """Largest published `limit.context` for `model` across providers.

    The same model is sharded across many providers and some shards cap
    the window below the native size (a gateway listing a 1M model at
    200K) — take the max so we report the model's native window.
    """
    if not isinstance(catalogue, dict):
        return None
    best = None
    for m in _candidate_models(catalogue, model):
        lim = m.get('limit')
        ctx = lim.get('context') if isinstance(lim, dict) else None
        if isinstance(ctx, (int, float)) and ctx > 0:
            best = max(best or 0, int(ctx))
    return best


def model_context_limit(model: str | None) -> int | None:
    """Context-window size (tokens) for `model` per the catalogue, or None.

    Same miss-driven off-TTL re-fetch as `model_rates`, so a model that
    launched after the cache was written resolves without waiting out the
    24h TTL.
    """
    if not isinstance(model, str) or not model:
        return None
    limit = _lookup_context_limit(get_catalogue(), model)
    if limit is not None:
        _miss_ts.pop(model, None)
        return limit
    if not _should_refresh_for_miss(model):
        return None
    limit = _lookup_context_limit(get_catalogue(force_refresh=True), model)
    if limit is not None:
        _miss_ts.pop(model, None)
    return limit


def _should_refresh_for_miss(model: str) -> bool:
    """Whether a catalogue miss for `model` warrants one off-TTL re-fetch.

    Suppressed when the catalogue we just searched is already network-fresh
    (a re-fetch returns identical bytes) or when we re-fetched for this same
    id inside the backoff window (an id models.dev genuinely lacks must not
    trigger a fetch on every priced turn). Records the attempt when allowed.
    """
    now = time.time()
    if now - _last_fetch_ts < _FRESH_WINDOW_SECONDS:
        return False
    last = _miss_ts.get(model)
    if last is not None and now - last < _MISS_BACKOFF_SECONDS:
        return False
    _miss_ts[model] = now
    return True


def model_rates(model: str | None) -> dict | None:
    """Return {input, output, cache_read, cache_write} USD per 1M, or None.

    On a miss against a (possibly stale) cached catalogue, force one off-TTL
    re-fetch and re-search before giving up — so a model launched after the
    cache was written gets priced rather than silently dropping to None for
    up to 24h. Bounded by `_should_refresh_for_miss`.
    """
    if not isinstance(model, str) or not model:
        return None
    rates = _lookup_rates(get_catalogue(), model)
    if rates is not None:
        _miss_ts.pop(model, None)
        return rates
    if not _should_refresh_for_miss(model):
        return None
    rates = _lookup_rates(get_catalogue(force_refresh=True), model)
    if rates is not None:
        _miss_ts.pop(model, None)
    return rates


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


def cost_components(model: str | None, breakdown: TokenBreakdown,
                    context_tokens: int | None = None) -> Optional[dict]:
    """Per-component USD cost `{input, output, cache_read, cache_write}`, or
    None when the model isn't in the catalogue.

    Same rate resolution as `cost()` (the >200K context tier applies when
    `context_tokens` crosses a published threshold), but left split so
    callers can show where a turn's money actually went. The split matters:
    cache reads bill at ~1/10 the fresh-input rate, so the token-count and
    cost views of the same turn tell very different stories — a long
    session is ~90% cache-read by tokens but only ~a third by dollars.
    """
    rates = model_rates(model)
    if rates is None:
        return None
    eff = _rates_for_context(rates, context_tokens)
    return {
        'input': (eff.get('input') or 0) * breakdown.input_tokens / 1_000_000,
        'output': (eff.get('output') or 0) * breakdown.output_tokens / 1_000_000,
        'cache_read': (eff.get('cache_read') or 0)
        * breakdown.cache_read_tokens / 1_000_000,
        'cache_write': (eff.get('cache_write') or 0)
        * breakdown.cache_creation_tokens / 1_000_000,
    }


def cost(model: str | None, breakdown: TokenBreakdown,
         context_tokens: int | None = None) -> Optional[float]:
    """USD cost for a token breakdown under the given model. None if unknown.

    `context_tokens` is the request's total context size; when provided
    and the model publishes context tiers, the >threshold rate is used.
    Omit it (or pass 0/None) for the flat top-level rate.
    """
    comps = cost_components(model, breakdown, context_tokens)
    if comps is None:
        return None
    return sum(comps.values())


def reset_cache() -> None:
    """Drop the in-process memo and miss-backoff state. Test hook."""
    global _memo, _memo_ts, _last_fetch_ts
    _memo = None
    _memo_ts = 0.0
    _last_fetch_ts = 0.0
    _miss_ts.clear()
