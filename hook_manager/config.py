"""Persistent config for hook_manager: per-handler enable/disable and
per-handler priority overrides.

Each Handler registered in `registry.py` can be toggled off without editing
code. Disabled handlers are skipped by the runner. Priorities can also be
overridden — useful when one handler's default priority places its emitted
span at the wrong point in the chain (e.g. rule.check sorting earlier than
the tool.Edit span it follows).

Config lives at `~/.claude/hook-manager-config.json` so it persists across
sessions and can be version-controlled or managed by the web UI.

Shape:
```json
{
  "schema_version": 1,
  "disabled_handlers": ["rule_check", "prompt_trace"],
  "priority_overrides": {"rule_check": 120, "doc_check": 130}
}
```

Handlers not listed in `disabled_handlers` are enabled (default-on); handlers
not present in `priority_overrides` keep their registry-defined priority.
Unknown handler names in either field are ignored — safe under refactor.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Iterable

from lib.providers import build_provider, get_active_provider, is_provider_id, provider_handler_config

CONFIG_PATH = str(get_active_provider().hook_manager_config_path())
_lock = threading.Lock()


def config_path(agent_type: str | None = None) -> str:
    if is_provider_id(agent_type):
        return str(build_provider(str(agent_type)).hook_manager_config_path())
    return CONFIG_PATH


def _load_raw(agent_type: str | None = None) -> dict:
    data: dict = {}
    try:
        with open(config_path(agent_type), 'r') as f:
            file_data = json.load(f)
        if isinstance(file_data, dict):
            data = file_data
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass

    # Merge per-provider handler overrides from regin settings. Settings-based
    # overrides take precedence over the standalone JSON file so the central
    # settings UI can tune providers without hunting for provider-specific files.
    agent_type = agent_type or _active_provider_id()
    if is_provider_id(agent_type):
        cfg = provider_handler_config(str(agent_type))
        if cfg.get('disabled_handlers'):
            file_disabled = [x for x in data.get('disabled_handlers', []) if isinstance(x, str)]
            merged_disabled = set(file_disabled) | set(cfg['disabled_handlers'])
            data['disabled_handlers'] = sorted(merged_disabled)
        if cfg.get('priority_overrides'):
            file_overrides = data.get('priority_overrides') or {}
            if not isinstance(file_overrides, dict):
                file_overrides = {}
            merged_overrides = {**file_overrides, **cfg['priority_overrides']}
            data['priority_overrides'] = {k: merged_overrides[k] for k in sorted(merged_overrides)}
    return data


def _active_provider_id() -> str:
    """Best-effort active provider id for the module-level CONFIG_PATH default."""
    try:
        return get_active_provider().provider_id
    except Exception:
        return "claude"


def _write_raw(data: dict, agent_type: str | None = None) -> None:
    path = config_path(agent_type)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def disabled_set(agent_type: str | None = None) -> frozenset[str]:
    """Return the frozen set of currently-disabled handler names."""
    raw = _load_raw(agent_type).get('disabled_handlers') or []
    if not isinstance(raw, list):
        return frozenset()
    return frozenset(str(x) for x in raw if isinstance(x, str))


def is_enabled(handler_name: str, agent_type: str | None = None) -> bool:
    return handler_name not in disabled_set(agent_type)


def set_enabled(handler_name: str, enabled: bool, agent_type: str | None = None) -> None:
    """Toggle one handler. Atomic via temp-file rename under a process lock."""
    name = handler_name
    with _lock:
        data = _load_raw(agent_type)
        current = [x for x in (data.get('disabled_handlers') or []) if isinstance(x, str)]
        current = list(dict.fromkeys(current))  # de-dup, preserve order
        if enabled and name in current:
            current.remove(name)
        elif not enabled and name not in current:
            current.append(name)
        data['schema_version'] = 1
        data['disabled_handlers'] = sorted(current)
        _write_raw(data, agent_type)


def filter_enabled(handlers: Iterable, agent_type: str | None = None) -> list:
    """Return only handlers whose name is enabled. Accepts anything with `.name`."""
    disabled = disabled_set(agent_type)
    return [h for h in handlers if (getattr(h, 'name', None) or '') not in disabled]


def priority_overrides(agent_type: str | None = None) -> dict[str, int]:
    """Return the persisted `{handler_name: priority}` override map.

    Non-string keys, non-numeric values, and bools (which are technically int
    in Python but never a sensible priority) are dropped. Floats round to int."""
    raw = _load_raw(agent_type).get('priority_overrides') or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, int] = {}
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        if isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            out[k] = int(v)
    return out


def effective_priority(handler_name: str, default: int, agent_type: str | None = None) -> int:
    """Return the override if present, otherwise the registry default."""
    return priority_overrides(agent_type).get(handler_name, default)


def set_priorities(updates: dict[str, int | None], agent_type: str | None = None) -> None:
    """Merge `updates` into the persisted override map.

    `updates[name] = int` sets/replaces the override for that handler.
    `updates[name] = None` removes the override (falls back to registry default).
    Atomic via temp-file rename under the same process lock as `set_enabled`.
    """
    if not isinstance(updates, dict) or not updates:
        return
    with _lock:
        data = _load_raw(agent_type)
        current = data.get('priority_overrides') or {}
        if not isinstance(current, dict):
            current = {}
        # Sanitize the existing map so we never write back a value we'd
        # have rejected on read (defensive against hand-edits).
        merged: dict[str, int] = {}
        for k, v in current.items():
            if isinstance(k, str) and not isinstance(v, bool) and isinstance(v, (int, float)):
                merged[k] = int(v)
        for name, value in updates.items():
            if not isinstance(name, str):
                continue
            if value is None:
                merged.pop(name, None)
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            merged[name] = int(value)
        data['schema_version'] = 1
        # Sort keys for stable on-disk diffs (same rationale as set_enabled).
        data['priority_overrides'] = {k: merged[k] for k in sorted(merged)}
        _write_raw(data, agent_type)


def clear_priorities(agent_type: str | None = None) -> None:
    """Drop all priority overrides — handlers revert to their registry defaults."""
    with _lock:
        data = _load_raw(agent_type)
        if 'priority_overrides' not in data:
            return
        data['schema_version'] = 1
        data['priority_overrides'] = {}
        _write_raw(data, agent_type)
