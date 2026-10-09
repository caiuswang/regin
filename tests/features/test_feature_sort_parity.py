"""The Features list's sort select restates `lib/features/index.py::SORT_KEYS`.

A select option the server does not know falls back to its default silently,
and a server key with no option is unreachable — so the two lists are held
equal here rather than trusted to stay in step.
"""
import re
from pathlib import Path

from lib.features import index

SORT_JS = Path(__file__).resolve().parents[2] / "frontend/src/utils/featureSort.js"


def _js_options() -> list[tuple[str, str]]:
    text = SORT_JS.read_text(encoding="utf-8")
    block = text.split("FEATURE_SORT_OPTIONS = [", 1)[1].split("]", 1)[0]
    return re.findall(r"value: '(\w+):(asc|desc)'", block)


def _js_default() -> tuple[str, str]:
    text = SORT_JS.read_text(encoding="utf-8")
    key, direction = re.search(
        r"DEFAULT_FEATURE_SORT = '(\w+):(asc|desc)'", text).groups()
    return key, direction


def test_features_default_sort_newest():
    key, direction = _js_default()
    assert key == index.DEFAULT_SORT_KEY == "created"
    assert direction.upper() == index.SORT_KEYS[key][1] == "DESC"
    assert {k for k, _ in _js_options()} == set(index.SORT_KEYS)


def test_feature_sort_options_use_the_server_direction():
    for key, direction in _js_options():
        assert direction.upper() == index.SORT_KEYS[key][1], key
