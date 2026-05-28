"""Per-engine, per-rule disable list maintained by regin.

Grit has its own internal disabled-rules tracking (`grit_rule_index.set_rules_disabled`).
This module covers the other engines (e.g. `bundle` engines), where rules are
parsed fresh from a bundle on every request and have no engine-side disable knob.
The list is a single JSON file at `<data_dir>/disabled_engine_rules.json`.

Shape: `{ "<engine_id>": ["<rule_id>", ...] }` — entries are disable markers; missing
entries mean the rule is enabled.
"""

from __future__ import annotations

import json
from pathlib import Path

from lib.settings import settings
from lib.activity_log import get_activity_logger as _get_activity_logger


def _rules_log():
    return _get_activity_logger("rules")


def _path() -> Path:
    return Path(settings.data_dir) / "disabled_engine_rules.json"


def load() -> dict[str, list[str]]:
    p = _path()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text() or "{}")
    except json.JSONDecodeError:
        return {}
    return {k: sorted(set(v)) for k, v in data.items() if isinstance(v, list)}


def save(data: dict[str, list[str]]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    cleaned = {k: sorted(set(v)) for k, v in data.items() if v}
    p.write_text(json.dumps(cleaned, indent=2, sort_keys=True))


def is_disabled(engine_id: str, rule_id: str) -> bool:
    return rule_id in load().get(engine_id, [])


def disabled_ids(engine_id: str) -> set[str]:
    return set(load().get(engine_id, []))


def set_disabled(engine_id: str, rule_ids: list[str], disabled: bool) -> None:
    data = load()
    current = set(data.get(engine_id, []))
    if disabled:
        current.update(rule_ids)
    else:
        current.difference_update(rule_ids)
    if current:
        data[engine_id] = sorted(current)
    else:
        data.pop(engine_id, None)
    save(data)
    _rules_log().write(
        "engine_rules_toggled",
        engine_id=engine_id, rule_ids=sorted(rule_ids),
        disabled=disabled, total_disabled=len(current),
    )
