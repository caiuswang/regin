"""Unit tests for lib.tokens.pricing."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from lib.tokens import pricing
from lib.tokens.pricing import TokenBreakdown, cost, model_rates, reset_cache


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


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('REGIN_PRICING_CACHE', str(tmp_path / 'models.json'))
    reset_cache()
    yield
    reset_cache()


def test_cost_returns_none_for_unknown_model(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: _FAKE_CATALOGUE)
    assert cost('unknown-model', TokenBreakdown(input_tokens=1000)) is None


def test_cost_returns_none_for_empty_or_non_string_model(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: _FAKE_CATALOGUE)
    assert cost(None, TokenBreakdown(input_tokens=1000)) is None
    assert cost('', TokenBreakdown(input_tokens=1000)) is None


def test_cost_computes_for_known_model(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: _FAKE_CATALOGUE)
    # 1M input tokens at $5/M = $5
    c = cost('claude-opus-4-7', TokenBreakdown(input_tokens=1_000_000))
    assert c == pytest.approx(5.0)


def test_cost_sums_all_four_buckets(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: _FAKE_CATALOGUE)
    c = cost('claude-opus-4-7', TokenBreakdown(
        input_tokens=1_000_000,
        output_tokens=200_000,
        cache_read_tokens=500_000,
        cache_creation_tokens=10_000,
    ))
    # 5 + 25*0.2 + 0.5*0.5 + 6.25*0.01 = 5 + 5 + 0.25 + 0.0625 = 10.3125
    assert c == pytest.approx(10.3125)


def test_strip_variant_handles_1m_suffix(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: _FAKE_CATALOGUE)
    c = cost('claude-opus-4-7[1m]', TokenBreakdown(input_tokens=1_000_000))
    assert c == pytest.approx(5.0)


def test_model_rates_searches_all_providers(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: _FAKE_CATALOGUE)
    assert model_rates('gpt-4o-mini') == {'input': 0.15, 'output': 0.6}


def test_network_failure_returns_none(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: None)
    assert cost('claude-opus-4-7', TokenBreakdown(input_tokens=1_000_000)) is None


def test_disk_cache_avoids_repeated_network(tmp_path, monkeypatch):
    cache_file = tmp_path / 'models.json'
    monkeypatch.setenv('REGIN_PRICING_CACHE', str(cache_file))
    reset_cache()
    calls = {'n': 0}
    def fake_fetch():
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
    monkeypatch.setattr(pricing, '_fetch', lambda: _FAKE_CATALOGUE)
    cost('claude-opus-4-7', TokenBreakdown(input_tokens=1))
    # Backdate the cache file beyond TTL
    import os
    past = time.time() - (25 * 60 * 60)
    os.utime(cache_file, (past, past))
    reset_cache()
    calls = {'n': 0}
    def fake_fetch():
        calls['n'] += 1
        return _FAKE_CATALOGUE
    monkeypatch.setattr(pricing, '_fetch', fake_fetch)
    cost('claude-opus-4-7', TokenBreakdown(input_tokens=1))
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
    monkeypatch.setattr(pricing, '_fetch', lambda: _TIERED_CATALOGUE)
    assert 'tiers' in (model_rates('claude-opus-4-7') or {})


def test_resolver_matches_routing_suffix_key(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: _SUFFIXED_CATALOGUE)
    # bare id should resolve the '@default'-suffixed tiered entry
    assert 'tiers' in (model_rates('claude-opus-4-8') or {})
    b = TokenBreakdown(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost('claude-opus-4-8', b, context_tokens=700_000) == pytest.approx(47.5)
    assert cost('claude-opus-4-8[1m]', b, context_tokens=700_000) == pytest.approx(47.5)


def test_cost_uses_base_rate_below_threshold(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: _TIERED_CATALOGUE)
    b = TokenBreakdown(input_tokens=1_000_000, output_tokens=1_000_000)
    # no context → base; context under 200K → base
    assert cost('claude-opus-4-7', b) == pytest.approx(30.0)
    assert cost('claude-opus-4-7', b, context_tokens=100_000) == pytest.approx(30.0)


def test_cost_applies_over_tier_above_threshold(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: _TIERED_CATALOGUE)
    b = TokenBreakdown(input_tokens=1_000_000, output_tokens=1_000_000)
    # 10*1 + 37.5*1 = 47.5
    assert cost('claude-opus-4-7', b, context_tokens=300_000) == pytest.approx(47.5)


def test_cost_tier_resolves_through_variant_suffix(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: _TIERED_CATALOGUE)
    b = TokenBreakdown(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost('claude-opus-4-7[1m]', b, context_tokens=300_000) == pytest.approx(47.5)


def test_flat_model_ignores_context(monkeypatch):
    monkeypatch.setattr(pricing, '_fetch', lambda: _FAKE_CATALOGUE)
    b = TokenBreakdown(input_tokens=1_000_000)
    # _FAKE_CATALOGUE has no tiers — context must not change the price
    assert cost('claude-opus-4-7', b, context_tokens=900_000) == pytest.approx(5.0)


def test_malformed_cache_file_falls_back_to_fetch(tmp_path, monkeypatch):
    cache_file = tmp_path / 'models.json'
    cache_file.write_text('not json at all')
    monkeypatch.setenv('REGIN_PRICING_CACHE', str(cache_file))
    reset_cache()
    monkeypatch.setattr(pricing, '_fetch', lambda: _FAKE_CATALOGUE)
    c = cost('claude-opus-4-7', TokenBreakdown(input_tokens=1_000_000))
    assert c == pytest.approx(5.0)
