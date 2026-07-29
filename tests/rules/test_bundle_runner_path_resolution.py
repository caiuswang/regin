"""The in-tree Node bundle runners must resolve `file_path` against `repo_root`.

BundleEngine spawns a runner with cwd set to the *bundle* directory, so a
relative `file_path` resolved against cwd reads the wrong tree — or silently
reads the working tree when the runner is invoked by hand from a repo root,
which is how a bogus `repo_root` used to still return real metrics.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

RUNNERS = {
    'vue-complexity': REPO_ROOT / 'example/rule/vue-complexity/bin/vue-complexity-runner.mjs',
    'frontend-style-convention': (
        REPO_ROOT / 'example/rule/frontend-style-convention/bin/frontend-ux-runner.mjs'
    ),
}

pytestmark = pytest.mark.skipif(
    shutil.which('node') is None, reason='node is not installed'
)

NESTED_SFC = """\
<template>
  <div><section><article><p><span>deep</span></p></article></section></div>
</template>
"""

FLAT_SFC = """\
<template>
  <div>shallow</div>
</template>
"""


def _run(runner: Path, payload: dict) -> subprocess.CompletedProcess:
    # cwd mirrors BundleEngine.run(), which spawns the runner from the bundle root.
    return subprocess.run(
        ['node', str(runner)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(runner.parent.parent),
        timeout=30,
    )


def _payload(repo_root, file_path: str, checker: str,
             options: dict | None = None) -> dict:
    payload = {'file_path': file_path, 'rule': {'checker': checker,
                                                'options': options or {}}}
    if repo_root is not None:
        payload['repo_root'] = repo_root
    return payload


def _checkout(root: Path, body: str) -> Path:
    """A repo-shaped dir holding `frontend/Foo.vue` — the same relative path in each."""
    target = root / 'frontend' / 'Foo.vue'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding='utf-8')
    return target


_PROBE_CHECKER = """\
export function run({ filePath }) {
  return { matches: 1, details: [filePath] }
}
"""


def _probe_bundle(runner: Path, root: Path) -> Path:
    """A minimal bundle around a copy of `runner`, with a dependency-free checker.

    A runner locates its checkers relative to its own file, so a copy in this
    skeleton dispatches to the probe rather than to the real checkers — which
    lets every runner be tested without installing its bundle's node_modules.
    """
    (root / 'bin').mkdir(parents=True)
    (root / 'checkers').mkdir()
    (root / 'checkers' / 'probe.mjs').write_text(_PROBE_CHECKER, encoding='utf-8')
    copy = root / 'bin' / runner.name
    copy.write_text(runner.read_text(encoding='utf-8'), encoding='utf-8')
    return copy


@pytest.mark.parametrize('runner', RUNNERS.values(), ids=list(RUNNERS))
def test_checker_receives_the_path_resolved_under_repo_root(runner, tmp_path):
    """The resolved path must reach the checker, not just the existence guard."""
    probe = _probe_bundle(runner, tmp_path / 'bundle')
    target = _checkout(tmp_path / 'checkout', NESTED_SFC)

    result = _run(probe, _payload(str(tmp_path / 'checkout'), 'frontend/Foo.vue',
                                  'probe'))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['details'] == [str(target)]


@pytest.mark.parametrize('runner', RUNNERS.values(), ids=list(RUNNERS))
def test_relative_path_under_bogus_repo_root_is_rejected(runner, tmp_path):
    _checkout(tmp_path / 'real', NESTED_SFC)

    result = _run(runner, _payload(str(tmp_path / 'nowhere'), 'frontend/Foo.vue',
                                   'no_such_checker'))

    assert result.returncode == 2
    assert 'no such file' in result.stderr


@pytest.mark.parametrize('runner', RUNNERS.values(), ids=list(RUNNERS))
def test_relative_path_follows_the_named_root(runner, tmp_path):
    """Same relative path, two roots: only the named root's tree is consulted."""
    _checkout(tmp_path / 'has_it', NESTED_SFC)
    (tmp_path / 'lacks_it').mkdir()

    found = _run(runner, _payload(str(tmp_path / 'has_it'), 'frontend/Foo.vue',
                                  'no_such_checker'))
    missing = _run(runner, _payload(str(tmp_path / 'lacks_it'), 'frontend/Foo.vue',
                                    'no_such_checker'))

    assert found.returncode == 0, found.stderr
    assert json.loads(found.stdout) == {'matches': 0, 'details': []}
    assert missing.returncode == 2
    assert 'no such file' in missing.stderr


@pytest.mark.parametrize('runner', RUNNERS.values(), ids=list(RUNNERS))
def test_absolute_path_ignores_repo_root(runner, tmp_path):
    target = _checkout(tmp_path, NESTED_SFC)

    result = _run(runner, _payload('/nonexistent', str(target), 'no_such_checker'))

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {'matches': 0, 'details': []}


@pytest.mark.parametrize('runner', RUNNERS.values(), ids=list(RUNNERS))
def test_relative_repo_root_is_rejected_not_silently_resolved(runner, tmp_path):
    _checkout(tmp_path, NESTED_SFC)

    result = _run(runner, _payload('../..', 'frontend/Foo.vue', 'no_such_checker'))

    assert result.returncode == 2
    assert 'repo_root' in result.stderr


@pytest.mark.parametrize('runner', RUNNERS.values(), ids=list(RUNNERS))
def test_non_string_repo_root_does_not_crash(runner, tmp_path):
    """A junk `repo_root` must be a clean exit-2, never an unhandled throw."""
    target = _checkout(tmp_path, NESTED_SFC)

    relative = _run(runner, _payload(5, 'frontend/Foo.vue', 'no_such_checker'))
    absolute = _run(runner, _payload(5, str(target), 'no_such_checker'))

    assert relative.returncode == 2
    assert 'Error' not in relative.stderr
    # An absolute file_path needs no root, so junk alongside one is ignored.
    assert absolute.returncode == 0, absolute.stderr
    assert json.loads(absolute.stdout) == {'matches': 0, 'details': []}


@pytest.mark.parametrize('runner', RUNNERS.values(), ids=list(RUNNERS))
def test_directory_target_is_rejected(runner, tmp_path):
    _checkout(tmp_path, NESTED_SFC)

    result = _run(runner, _payload(str(tmp_path), 'frontend', 'no_such_checker'))

    assert result.returncode == 2
    assert 'Error' not in result.stderr


@pytest.mark.parametrize('runner', RUNNERS.values(), ids=list(RUNNERS))
def test_missing_file_path_is_rejected(runner):
    result = _run(runner, {'rule': {'checker': 'no_such_checker'}})

    assert result.returncode == 2
    assert 'file_path' in result.stderr


def _max_depth(runner: Path, repo_root: Path) -> dict:
    result = _run(runner, _payload(str(repo_root), 'frontend/Foo.vue',
                                   'template_max_depth', {'threshold': 2}))
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_checker_measures_the_tree_named_by_repo_root(tmp_path):
    """The metrics must track `repo_root`, not just the existence guard."""
    runner = RUNNERS['vue-complexity']
    if not (runner.parent.parent / 'node_modules').is_dir():
        pytest.skip('vue-complexity bundle deps are not installed')
    _checkout(tmp_path / 'deep', NESTED_SFC)
    _checkout(tmp_path / 'flat', FLAT_SFC)

    assert _max_depth(runner, tmp_path / 'deep')['matches'] == 1
    assert _max_depth(runner, tmp_path / 'flat') == {'matches': 0, 'details': []}
