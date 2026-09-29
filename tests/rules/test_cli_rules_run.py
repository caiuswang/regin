"""Pins `regin rules run` output and exit codes across target shapes (file,
glob, directory with/without engine language extensions) and result counts."""

from __future__ import annotations

from types import SimpleNamespace

import click
import pytest

from cli.commands import rules as rules_cmd
from lib import rule_engines


class _Engine:
    def __init__(self, hits: dict[str, tuple[int, str]], extensions=None):
        self.hits = hits
        self.calls: list[str] = []
        if extensions is not None:
            self.language_extensions = lambda rule: extensions

    def parse_rules(self):
        return [SimpleNamespace(id='r1')]

    def run(self, rule, target_file, repo_root):
        self.calls.append(target_file)
        name = target_file.rsplit('/', 1)[-1]
        if name not in self.hits:
            return None
        count, detail = self.hits[name]
        return SimpleNamespace(match_count=count, file_path=name, detail=detail)


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    for rel in ('a.py', 'b.py', 'c.txt', 'sub/d.py'):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('x')
    (root / 'empty').mkdir()
    return root


def _invoke(monkeypatch, engine, repo, files, extra=(), rule_id='r1', engine_id='e'):
    def _get(eid):
        if eid != 'e':
            raise KeyError(eid)
        return engine
    monkeypatch.setattr(rule_engines, 'get', _get)
    ctx = SimpleNamespace(args=list(extra))
    try:
        rules_cmd.cmd_rules_run(ctx, engine=engine_id, rule_id=rule_id,
                                repo=str(repo), file=files)
        return None
    except click.exceptions.Exit as exc:
        return exc.exit_code


def _norm(out: str, repo) -> list[str]:
    return out.replace(str(repo), '<repo>').splitlines()


def test_run_setup_errors(monkeypatch, capsys, repo):
    engine = _Engine({})
    cases = [
        (dict(engine_id='nope'), ['Engine not configured: nope']),
        (dict(rule_id='zz'), ['Rule not found: zz']),
    ]
    for kwargs, expected in cases:
        assert _invoke(monkeypatch, engine, repo, ['a.py'], **kwargs) == 1
        assert _norm(capsys.readouterr().out, repo) == expected
    assert _invoke(monkeypatch, engine, repo / 'missing', ['a.py']) == 1
    assert _norm(capsys.readouterr().out, repo) == ['Repo not found: <repo>/missing']


@pytest.mark.parametrize('hits,files,extensions,code,expected', [
    ({}, ['{repo}/a.py'], None, None,
     ['OK: r1 reported no matches for <repo>/a.py']),
    ({}, ['{repo}/*.py'], None, None,
     ['OK: r1 reported no matches across 2 file(s)']),
    ({'a.py': (2, 'line 3')}, ['{repo}/a.py'], None, 1,
     ['VIOLATION: r1 matched 2 time(s) in a.py', 'line 3']),
    ({'a.py': (2, '')}, ['{repo}/a.py'], None, 1,
     ['VIOLATION: r1 matched 2 time(s) in a.py']),
    ({'a.py': (2, 'x'), 'd.py': (1, '')}, ['{repo}'], ('.py',), 1,
     ['VIOLATION: r1 matched 3 time(s) across 2 of 3 file(s)',
      '  a.py: 2 match(es) - x', '  d.py: 1 match(es)']),
    ({}, ['{repo}'], (), None,
     ['OK: r1 reported no matches across 4 file(s)']),
    ({}, ['{repo}'], None, None,
     ['OK: r1 reported no matches across 4 file(s)']),
    ({}, ['{repo}/empty'], ('.py',), 1,
     ['No matching files found under directory: <repo>/empty']),
    ({}, ['{repo}/nope.py'], None, 1,
     ['Target file not found: <repo>/nope.py']),
])
def test_run_targets(monkeypatch, capsys, repo, hits, files, extensions, code, expected):
    engine = _Engine(hits, extensions)
    targets = [f.format(repo=repo) for f in files]
    assert _invoke(monkeypatch, engine, repo, targets) == code
    assert _norm(capsys.readouterr().out, repo) == expected


def test_run_many_violations_truncates_listing(monkeypatch, capsys, repo):
    tmp_path = repo / 'many'
    tmp_path.mkdir()
    names = [f'f{i:02d}.py' for i in range(12)]
    for name in names:
        (tmp_path / name).write_text('x')
    engine = _Engine({n: (1, '') for n in names})
    assert _invoke(monkeypatch, engine, tmp_path, [str(tmp_path / 'f00.py')],
                   extra=[str(tmp_path / 'f*.py')]) == 1
    out = capsys.readouterr().out.splitlines()
    assert out[0] == 'VIOLATION: r1 matched 12 time(s) across 12 of 12 file(s)'
    assert out[1:] == [f'  {n}: 1 match(es)' for n in names[:10]] + ['  ... 2 more file(s)']
    assert engine.calls == sorted(engine.calls)
