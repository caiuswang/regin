"""Pins `scripts/ablate_pattern_router.py`: `score()` metrics and the
report `main()` prints, with the retrieval legs stubbed."""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'ablate_pattern_router.py'

_EVAL_SET = [
    ('q1', 'alpha', 'T'),
    ('q2', 'beta', 'T'),
    ('q3', 'gamma', 'D'),
]

_RESULTS = {
    'q1': ['alpha', 'x'],
    'q2': ['x', 'y', 'beta'],
    'q3': ['a', 'b', 'c', 'd', 'e', 'gamma'],
}


@pytest.fixture
def ablate(monkeypatch):
    fake = types.ModuleType('tests.test_pattern_router_eval')
    fake.EVAL_SET = _EVAL_SET
    monkeypatch.setitem(sys.modules, 'tests.test_pattern_router_eval', fake)
    spec = importlib.util.spec_from_file_location('ablate_under_test', _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_score_aggregates_and_families(ablate):
    out = ablate.score(_RESULTS)
    assert out['n'] == 3
    assert out['rows'] == [('T', 'q1', 'alpha', 1), ('T', 'q2', 'beta', 3),
                           ('D', 'q3', 'gamma', 6)]
    assert (out['top1'], out['recall5']) == (pytest.approx(1 / 3), pytest.approx(2 / 3))
    assert out['mrr5'] == pytest.approx((1 + 1 / 3) / 3)
    assert out['families'] == {
        'T': {'n': 2, 'top1': 0.5, 'recall5': 1.0, 'mrr5': pytest.approx(2 / 3)},
        'D': {'n': 1, 'top1': 0.0, 'recall5': 0.0, 'mrr5': 0.0},
    }


def test_main_report(ablate, monkeypatch, capsys):
    def boom(query, k=5):
        raise RuntimeError('down')

    monkeypatch.setattr(ablate, 'run_bm25', lambda q, k=5: _RESULTS[q])
    monkeypatch.setattr(ablate, 'run_dense', boom)
    monkeypatch.setattr(ablate, 'run_hybrid',
                        lambda q, k=5, rerank=False: [] if rerank else _RESULTS[q])
    ablate.main()
    lines = [line.rstrip() for line in capsys.readouterr().out.splitlines()]
    assert lines == [line.rstrip() for line in _EXPECTED_REPORT.splitlines()]


_EXPECTED_REPORT = """\
  !! dense-only crashed on 'q1': down
  !! dense-only crashed on 'q2': down
  !! dense-only crashed on 'q3': down

=== Aggregate (N=3) ===
config                    top-1   R@5    MRR@5
BM25-only                 0.33   0.67   0.44
dense-only                0.00   0.00   0.00
hybrid RRF (no rerank)    0.33   0.67   0.44
hybrid + rerank           0.00   0.00   0.00

=== Per-family ===
  T/D/L = pattern title-paraphrase / description / lexical
  WT/WD/WL = wiki   title-paraphrase / description / lexical
config                    D.top1/R5  T.top1/R5
BM25-only                   0.00/0.00       0.50/1.00
dense-only                  0.00/0.00       0.00/0.00
hybrid RRF (no rerank)      0.00/0.00       0.50/1.00
hybrid + rerank             0.00/0.00       0.00/0.00

=== Per-query ranks (∞ = miss / not in top-5) ===
fam expected                        BM25 dense   RRF   +rr  query
T   alpha                              1     ∞     1     ∞  q1
T   beta                               3     ∞     3     ∞  q2
D   gamma                              ∞     ∞     ∞     ∞  q3
"""
