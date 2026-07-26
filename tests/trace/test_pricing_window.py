"""Kimi pricing/window parity: model aliases and priced-shard ranking.

Guards two defects that made a Kimi session render a fabricated vitals strip:
`kimi-code/k3` resolved to nothing (NULL cost, window falling back to the
observed peak), and `kimi-code/kimi-for-coding` resolved to the all-zero
flat-plan shard (a recompute would zero out real historical costs).
"""

from __future__ import annotations

import pytest

from lib.tokens import pricing
from lib.tokens.model_windows import infer_window
from lib.tokens.pricing import (
    TokenBreakdown, cost, model_context_limit, model_rates, reset_cache,
)


# Mirrors the real models.dev shard shapes: the flat-plan provider publishes
# all four rate keys as zeros, the genuinely-priced shards omit cache_write.
_CATALOGUE = {
    'kimi-for-coding': {
        'id': 'kimi-for-coding',
        'models': {
            'k3': {
                'id': 'k3',
                'cost': {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0},
                'limit': {'context': 1_048_576},
            },
            'kimi-for-coding': {
                'id': 'kimi-for-coding',
                'cost': {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0},
                'limit': {'context': 262_144},
            },
        },
    },
    'alibaba-token-plan': {
        'id': 'alibaba-token-plan',
        'models': {
            'kimi-k2.7-code': {
                'id': 'kimi-k2.7-code',
                'cost': {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0},
                'limit': {'context': 262_144},
            },
        },
    },
    'moonshotai': {
        'id': 'moonshotai',
        'models': {
            'kimi-k3': {
                'id': 'kimi-k3',
                'cost': {'input': 3, 'output': 15, 'cache_read': 0.3},
                'limit': {'context': 1_048_576},
            },
            'kimi-k2.7-code': {
                'id': 'kimi-k2.7-code',
                'cost': {'input': 0.95, 'output': 4, 'cache_read': 0.19},
                'limit': {'context': 262_144},
            },
        },
    },
    'anthropic': {
        'id': 'anthropic',
        'models': {
            'claude-opus-4-7': {
                'id': 'claude-opus-4-7',
                'cost': {
                    'input': 5, 'output': 25, 'cache_read': 0.5, 'cache_write': 6.25,
                    'tiers': [{'input': 10, 'output': 37.5,
                               'tier': {'type': 'context', 'size': 200_000}}],
                },
                'limit': {'context': 1_000_000},
            },
        },
    },
    'some-flat-plan': {
        'id': 'some-flat-plan',
        'models': {
            'claude-opus-4-7': {
                'id': 'claude-opus-4-7',
                'cost': {'input': 0, 'output': 0, 'cache_read': 0, 'cache_write': 0},
                'limit': {'context': 200_000},
            },
        },
    },
}


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setenv('REGIN_PRICING_CACHE', str(tmp_path / 'models.json'))
    monkeypatch.setattr(pricing, '_fetch', lambda *_a, **_k: _CATALOGUE)
    reset_cache()
    yield
    reset_cache()


def test_k3_alias_resolves_priced_rates():
    rates = model_rates('kimi-code/k3')
    assert rates is not None
    assert rates['input'] > 0
    assert rates == {'input': 3, 'output': 15, 'cache_read': 0.3}


def test_k3_alias_produces_a_real_cost():
    c = cost('kimi-code/k3', TokenBreakdown(cache_read_tokens=26_400_000))
    assert c is not None and c > 0


def test_k3_alias_resolves_the_model_context_window():
    assert model_context_limit('kimi-code/k3') == 1_048_576


def test_k3_window_is_the_model_window_not_the_observed_peak():
    # Before the alias existed the model was unknown, so infer_window fell
    # back to peak_tokens and the gauge read a confident 230K / 230K.
    peak = 230_328
    assert infer_window('kimi-code/k3', peak) == 1_048_576


def test_bare_k3_alias_also_resolves():
    assert (model_rates('k3') or {}).get('input') == 3


def test_kimi_for_coding_alias_beats_the_zero_flat_plan_shard():
    for reported in ('kimi-code/kimi-for-coding', 'kimi-for-coding'):
        rates = model_rates(reported)
        assert rates is not None, reported
        assert rates['input'] > 0, reported
        assert rates == {'input': 0.95, 'output': 4, 'cache_read': 0.19}


def test_priced_shard_never_loses_to_a_zero_mirror_with_more_keys():
    priced = {'cost': {'input': 0.95, 'output': 4, 'cache_read': 0.19}}
    zero_mirror = {'cost': {'input': 0, 'output': 0,
                            'cache_read': 0, 'cache_write': 0}}
    assert pricing._rate_completeness(priced) > pricing._rate_completeness(zero_mirror)


def test_claude_alias_resolution_is_unchanged():
    # The tier-bearing Anthropic shard must still win over the $0 mirror, and
    # the >200K tier must still apply.
    assert model_rates('claude-opus-4-7')['input'] == 5
    assert model_rates('claude-opus-4-7[1m]')['input'] == 5
    assert model_context_limit('claude-opus-4-7') == 1_000_000
    flat = cost('claude-opus-4-7', TokenBreakdown(input_tokens=1_000_000))
    assert flat == pytest.approx(5.0)
    tiered = cost('claude-opus-4-7', TokenBreakdown(input_tokens=1_000_000),
                  context_tokens=300_000)
    assert tiered == pytest.approx(10.0)


def test_no_claude_id_is_aliased():
    assert not [k for k in pricing._MODEL_ALIASES if 'claude' in k]
