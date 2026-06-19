"""Unit tests for lib.tokens.pricing."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from lib.tokens import pricing
from lib.tokens.pricing import (
    TokenBreakdown, cost, model_context_limit, model_rates, reset_cache,
)


_FAKE_CATALOGUE = {
    'anthropic': {
        'id': 'anthropic',
        'models': {
            'claude-opus-4-7': {
                'id': 'claude-opus-4-7',
                'cost': {'input': 5, 'output': 25, 'cache_read': 0.5, 'cache_write': 6.25},
            },
            'claude-sonnet-4-6': {
                'id': 'claude-sonnet-4-6',
                'cost': {'input': 3, 'output': 15, 'cache_read': 0.3, 'cache_write': 3.75},
            },
        },
    },
    'openai': {
        'id': 'openai',
        'models': {
            'gpt-4o-mini': {
                'id': 'gpt-4o-mini',
                'cost': {'input': 0.15, 'output': 0.6},
            },
        },
    },
}


_FAKE_CATALOGUE_KIMI = {
    'moonshotai': {
        'id': 'moonshotai',
        'models': {
            'kimi-k2.7-code': {
                'id': 'kimi-k2.7-code',
                'cost': {'input': 0.95, 'output': 4, 'cache_read': 0.19},
            },
        },
    },
}


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('REGIN_PRICING_CACHE', str(tmp_path / 'models.json'))
    reset_cache()
    yield
    reset_cache()


def test_cost_returns_none_for_unknown_model(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _FAKE_CATALOGUE)
    assert cost('unknown-model', TokenBreakdown(input_tokens=1000)) is None


def test_kimi_model_id_aliases_to_catalogue_entry(monkeypatch):
    # Kimi reports 'kimi-code/kimi-for-coding'; it must price as the underlying
    # 'kimi-k2.7-code' (K2.7 Code) catalogue entry.
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _FAKE_CATALOGUE_KIMI)
    rates = model_rates('kimi-code/kimi-for-coding')
    assert rates == {'input': 0.95, 'output': 4, 'cache_read': 0.19}
    # 1M output tokens at $4/M = $4
    assert cost('kimi-code/kimi-for-coding',
                TokenBreakdown(output_tokens=1_000_000)) == pytest.approx(4.0)


def test_cost_returns_none_for_empty_or_non_string_model(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _FAKE_CATALOGUE)
    assert cost(None, TokenBreakdown(input_tokens=1000)) is None
    assert cost('', TokenBreakdown(input_tokens=1000)) is None


def test_cost_computes_for_known_model(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _FAKE_CATALOGUE)
    # 1M input tokens at $5/M = $5
    c = cost('claude-opus-4-7', TokenBreakdown(input_tokens=1_000_000))
    assert c == pytest.approx(5.0)


def test_cost_sums_all_four_buckets(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _FAKE_CATALOGUE)
    c = cost('claude-opus-4-7', TokenBreakdown(
        input_tokens=1_000_000,
        output_tokens=200_000,
        cache_read_tokens=500_000,
        cache_creation_tokens=10_000,
    ))
    # 5 + 25*0.2 + 0.5*0.5 + 6.25*0.01 = 5 + 5 + 0.25 + 0.0625 = 10.3125
    assert c == pytest.approx(10.3125)


def test_strip_variant_handles_1m_suffix(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _FAKE_CATALOGUE)
    c = cost('claude-opus-4-7[1m]', TokenBreakdown(input_tokens=1_000_000))
    assert c == pytest.approx(5.0)


def test_model_rates_searches_all_providers(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _FAKE_CATALOGUE)
    assert model_rates('gpt-4o-mini') == {'input': 0.15, 'output': 0.6}


def test_network_failure_returns_none(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: None)
    assert cost('claude-opus-4-7', TokenBreakdown(input_tokens=1_000_000)) is None


def test_disk_cache_avoids_repeated_network(tmp_path, monkeypatch):
    cache_file = tmp_path / 'models.json'
    monkeypatch.setenv('REGIN_PRICING_CACHE', str(cache_file))
    reset_cache()
    calls = {'n': 0}
    def fake_fetch(*_a, **_k):
        calls['n'] += 1
        return _FAKE_CATALOGUE
    monkeypatch.setattr(pricing, '_fetch', fake_fetch)
    cost('claude-opus-4-7', TokenBreakdown(input_tokens=1))
    assert calls['n'] == 1
    assert cache_file.exists()
    reset_cache()  # drop memo
    cost('claude-opus-4-7', TokenBreakdown(input_tokens=1))
    # Disk cache should serve this; no new fetch
    assert calls['n'] == 1


def test_disk_cache_expires_after_ttl(tmp_path, monkeypatch):
    cache_file = tmp_path / 'models.json'
    monkeypatch.setenv('REGIN_PRICING_CACHE', str(cache_file))
    reset_cache()
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _FAKE_CATALOGUE)
    cost('claude-opus-4-7', TokenBreakdown(input_tokens=1))
    # Backdate the cache file beyond TTL
    import os
    past = time.time() - (25 * 60 * 60)
    os.utime(cache_file, (past, past))
    reset_cache()
    calls = {'n': 0}
    def fake_fetch(*_a, **_k):
        calls['n'] += 1
        return _FAKE_CATALOGUE
    monkeypatch.setattr(pricing, '_fetch', fake_fetch)
    cost('claude-opus-4-7', TokenBreakdown(input_tokens=1))
    assert calls['n'] == 1


# ── model_context_limit ─────────────────────────────────────────

_LIMIT_CATALOGUE = {
    # Same model sharded across providers with different window caps —
    # the lookup must report the largest (native) one.
    'anthropic': {
        'id': 'anthropic',
        'models': {
            'claude-fable-5': {'id': 'claude-fable-5',
                               'cost': {'input': 5, 'output': 25},
                               'limit': {'context': 1_000_000, 'output': 128_000}},
        },
    },
    'cappedprov': {
        'id': 'cappedprov',
        'models': {
            'claude-fable-5': {'id': 'claude-fable-5',
                               'cost': {'input': 5, 'output': 25},
                               'limit': {'context': 200_000, 'output': 128_000}},
        },
    },
}


def test_context_limit_takes_max_across_providers(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _LIMIT_CATALOGUE)
    assert model_context_limit('claude-fable-5') == 1_000_000


def test_context_limit_strips_variant_suffix(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _LIMIT_CATALOGUE)
    assert model_context_limit('claude-fable-5[1m]') == 1_000_000


def test_context_limit_none_for_unknown_model(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _LIMIT_CATALOGUE)
    assert model_context_limit('ghost-model') is None
    assert model_context_limit(None) is None
    assert model_context_limit('') is None


def test_context_limit_miss_refetches_once(tmp_path, monkeypatch):
    # Cache predates the model launch → one off-TTL re-fetch resolves it.
    cache_file = tmp_path / 'models.json'
    cache_file.write_text(json.dumps(_FAKE_CATALOGUE))  # lacks fable-5
    reset_cache()
    calls = {'n': 0}

    def fake_fetch(*_a, **_k):
        calls['n'] += 1
        return _LIMIT_CATALOGUE

    monkeypatch.setattr(pricing, '_fetch', fake_fetch)
    assert model_context_limit('claude-fable-5') == 1_000_000
    assert calls['n'] == 1


_TIERED_CATALOGUE = {
    # Same model under two providers: only the second carries context-tier
    # pricing. _find_model must prefer the tier-bearing entry.
    'flatprov': {
        'id': 'flatprov',
        'models': {
            'claude-opus-4-7': {
                'id': 'claude-opus-4-7',
                'cost': {'input': 5, 'output': 25},
            },
        },
    },
    'tierprov': {
        'id': 'tierprov',
        'models': {
            'claude-opus-4-7': {
                'id': 'claude-opus-4-7',
                'cost': {
                    'input': 5, 'output': 25, 'cache_read': 0.5, 'cache_write': 6.25,
                    'tiers': [{
                        'input': 10, 'output': 37.5,
                        'cache_read': 1, 'cache_write': 12.5,
                        'tier': {'type': 'context', 'size': 200000},
                    }],
                    'context_over_200k': {'input': 10, 'output': 37.5},
                },
            },
        },
    },
}


_SUFFIXED_CATALOGUE = {
    # The flat entry uses the bare id; the tier-bearing entry is keyed
    # with a provider routing suffix ('@default', as google-vertex does
    # for claude-opus-4-8). The resolver must normalize the suffix and
    # still prefer the tiered entry.
    'anthropic': {
        'id': 'anthropic',
        'models': {
            'claude-opus-4-8': {'id': 'claude-opus-4-8',
                                'cost': {'input': 5, 'output': 25}},
        },
    },
    'google-vertex': {
        'id': 'google-vertex',
        'models': {
            'claude-opus-4-8@default': {
                'id': 'claude-opus-4-8@default',
                'cost': {
                    'input': 5, 'output': 25,
                    'tiers': [{'input': 10, 'output': 37.5,
                               'tier': {'type': 'context', 'size': 200000}}],
                },
            },
        },
    },
}


def test_resolver_prefers_tier_bearing_entry(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _TIERED_CATALOGUE)
    assert 'tiers' in (model_rates('claude-opus-4-7') or {})


def test_resolver_matches_routing_suffix_key(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _SUFFIXED_CATALOGUE)
    # bare id should resolve the '@default'-suffixed tiered entry
    assert 'tiers' in (model_rates('claude-opus-4-8') or {})
    b = TokenBreakdown(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost('claude-opus-4-8', b, context_tokens=700_000) == pytest.approx(47.5)
    assert cost('claude-opus-4-8[1m]', b, context_tokens=700_000) == pytest.approx(47.5)


def test_cost_uses_base_rate_below_threshold(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _TIERED_CATALOGUE)
    b = TokenBreakdown(input_tokens=1_000_000, output_tokens=1_000_000)
    # no context → base; context under 200K → base
    assert cost('claude-opus-4-7', b) == pytest.approx(30.0)
    assert cost('claude-opus-4-7', b, context_tokens=100_000) == pytest.approx(30.0)


def test_cost_applies_over_tier_above_threshold(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _TIERED_CATALOGUE)
    b = TokenBreakdown(input_tokens=1_000_000, output_tokens=1_000_000)
    # 10*1 + 37.5*1 = 47.5
    assert cost('claude-opus-4-7', b, context_tokens=300_000) == pytest.approx(47.5)


def test_cost_tier_resolves_through_variant_suffix(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _TIERED_CATALOGUE)
    b = TokenBreakdown(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost('claude-opus-4-7[1m]', b, context_tokens=300_000) == pytest.approx(47.5)


def test_flat_model_ignores_context(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _FAKE_CATALOGUE)
    b = TokenBreakdown(input_tokens=1_000_000)
    # _FAKE_CATALOGUE has no tiers — context must not change the price
    assert cost('claude-opus-4-7', b, context_tokens=900_000) == pytest.approx(5.0)


def test_malformed_cache_file_falls_back_to_fetch(tmp_path, monkeypatch):
    cache_file = tmp_path / 'models.json'
    cache_file.write_text('not json at all')
    monkeypatch.setenv('REGIN_PRICING_CACHE', str(cache_file))
    reset_cache()
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _FAKE_CATALOGUE)
    c = cost('claude-opus-4-7', TokenBreakdown(input_tokens=1_000_000))
    assert c == pytest.approx(5.0)


def test_miss_refetches_when_cache_predates_model(tmp_path, monkeypatch):
    # A valid (within-TTL) disk cache that simply predates a model launch:
    # the lookup must force one off-TTL re-fetch and find the new model
    # rather than silently returning None until the 24h TTL expires.
    cache_file = tmp_path / 'models.json'
    old = {'anthropic': {'id': 'anthropic', 'models': {
        'claude-opus-4-7': {'id': 'claude-opus-4-7',
                            'cost': {'input': 5, 'output': 25}}}}}
    new = {'anthropic': {'id': 'anthropic', 'models': {
        'claude-opus-4-7': {'id': 'claude-opus-4-7',
                            'cost': {'input': 5, 'output': 25}},
        'claude-haiku-4-5': {'id': 'claude-haiku-4-5',
                             'cost': {'input': 1, 'output': 5}}}}}
    cache_file.write_text(json.dumps(old))  # fresh mtime → served, but stale
    reset_cache()
    calls = {'n': 0}

    def fake_fetch(*_a, **_k):
        calls['n'] += 1
        return new

    monkeypatch.setattr(pricing, '_fetch', fake_fetch)
    assert model_rates('claude-haiku-4-5') == {'input': 1, 'output': 5}
    assert calls['n'] == 1
    # A model already present in the cache must not trigger any fetch.
    calls['n'] = 0
    assert model_rates('claude-opus-4-7') is not None
    assert calls['n'] == 0


def test_unknown_model_refetches_at_most_once_in_backoff(tmp_path, monkeypatch):
    # An id models.dev genuinely lacks must not re-fetch on every priced turn.
    # FRESH_WINDOW disabled so the backoff guard is what's under test.
    cache_file = tmp_path / 'models.json'
    cache_file.write_text(json.dumps(_FAKE_CATALOGUE))  # valid, lacks 'ghost'
    reset_cache()
    monkeypatch.setattr(pricing, '_FRESH_WINDOW_SECONDS', 0)
    calls = {'n': 0}

    def fake_fetch(*_a, **_k):
        calls['n'] += 1
        return _FAKE_CATALOGUE  # still no 'ghost'

    monkeypatch.setattr(pricing, '_fetch', fake_fetch)
    assert model_rates('ghost-model') is None
    assert calls['n'] == 1                     # one off-TTL re-fetch attempt
    assert model_rates('ghost-model') is None
    assert calls['n'] == 1                     # backoff suppresses the second


def test_miss_skips_double_fetch_right_after_cold_fetch(tmp_path, monkeypatch):
    # No disk cache → the first lookup fetches fresh; a model missing from
    # that just-fetched catalogue must not trigger an immediate second fetch.
    monkeypatch.setenv('REGIN_PRICING_CACHE', str(tmp_path / 'cold.json'))
    reset_cache()
    calls = {'n': 0}

    def fake_fetch(*_a, **_k):
        calls['n'] += 1
        return _FAKE_CATALOGUE

    monkeypatch.setattr(pricing, '_fetch', fake_fetch)
    assert model_rates('ghost-model') is None
    assert calls['n'] == 1


def test_force_refresh_uses_shorter_timeout(tmp_path, monkeypatch):
    # On a miss against a valid cache, the synchronous re-fetch runs on the
    # latency-sensitive read path, so it uses the tighter miss timeout.
    cache_file = tmp_path / 'models.json'
    cache_file.write_text(json.dumps(_FAKE_CATALOGUE))  # valid, lacks 'ghost'
    reset_cache()
    seen = []

    def fake_fetch(timeout=None, *_a, **_k):
        seen.append(timeout)
        return _FAKE_CATALOGUE

    monkeypatch.setattr(pricing, '_fetch', fake_fetch)
    assert model_rates('ghost-model') is None
    assert seen == [pricing._MISS_FETCH_TIMEOUT_SECONDS]
    assert pricing._MISS_FETCH_TIMEOUT_SECONDS < pricing._FETCH_TIMEOUT_SECONDS


def test_background_fetch_uses_default_timeout(tmp_path, monkeypatch):
    # The ordinary (non-force) TTL refresh keeps the longer timeout.
    monkeypatch.setenv('REGIN_PRICING_CACHE', str(tmp_path / 'cold.json'))
    reset_cache()
    seen = []

    def fake_fetch(timeout=None, *_a, **_k):
        seen.append(timeout)
        return _FAKE_CATALOGUE

    monkeypatch.setattr(pricing, '_fetch', fake_fetch)
    assert model_rates('claude-opus-4-7') is not None
    assert seen == [pricing._FETCH_TIMEOUT_SECONDS]
