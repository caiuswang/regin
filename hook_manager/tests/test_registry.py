"""Tests for the static REGISTRY: every handler is well-formed and targets
only real spec events."""

from hook_manager.core import SPEC_EVENTS
from hook_manager.registry import REGISTRY


def test_registry_is_non_empty():
    assert len(REGISTRY) > 0


def test_every_handler_has_required_fields():
    for h in REGISTRY:
        assert h.name
        assert h.events
        assert h.kind in ('trace', 'gate', 'enrich', 'notify')
        assert callable(h.fn)
        assert callable(h.predicate)
        assert isinstance(h.priority, int)


def test_every_handler_event_is_real_or_star():
    for h in REGISTRY:
        for ev in h.events:
            assert ev == '*' or ev in SPEC_EVENTS, (
                f'{h.name} registers unknown event {ev!r}'
            )


def test_handler_names_are_unique():
    names = [h.name for h in REGISTRY]
    assert len(names) == len(set(names))


def test_describe_handlers_returns_snapshot_per_registry_entry():
    """The web UI renders a toggle row per handler using describe_handlers().
    One snapshot per registered entry — no dupes, no drops."""
    from hook_manager.registry import describe_handlers

    snapshots = describe_handlers()
    assert len(snapshots) == len(REGISTRY)
    names = {s['name'] for s in snapshots}
    assert names == {h.name for h in REGISTRY}
    for s in snapshots:
        assert set(s.keys()) == {
            'name', 'label', 'summary', 'match_hint',
            'events', 'wired_events', 'wired', 'kind',
            'priority', 'default_priority', 'priority_overridden', 'enabled',
        }
        assert isinstance(s['label'], str)
        assert isinstance(s['events'], list)
        assert isinstance(s['wired_events'], list)
        assert isinstance(s['wired'], bool)
        assert s['kind'] in ('trace', 'gate', 'enrich', 'notify')
        assert isinstance(s['priority'], int)
        assert isinstance(s['default_priority'], int)
        assert isinstance(s['priority_overridden'], bool)
        assert isinstance(s['enabled'], bool)


def test_describe_handlers_marks_wired_events():
    from hook_manager.registry import describe_handlers

    snapshots = {s['name']: s for s in describe_handlers(routed_events={'PostToolUse'})}
    assert snapshots['rule_check']['wired'] is True
    assert snapshots['rule_check']['wired_events'] == ['PostToolUse']
    assert snapshots['prompt_trace']['wired'] is False
    assert snapshots['prompt_trace']['wired_events'] == []


def test_describe_handlers_reflects_disabled_state(monkeypatch, tmp_path):
    """Toggling a handler via config flips the `enabled` flag in the
    snapshot. The UI reads this directly — stale cache would mean a
    user toggles and nothing appears to change."""
    from hook_manager import config as cfg
    from hook_manager.registry import describe_handlers

    fake_cfg = tmp_path / 'hook-manager-config.json'
    monkeypatch.setattr(cfg, 'CONFIG_PATH', str(fake_cfg))

    # Pick a handler known to be in the registry.
    target = REGISTRY[0].name
    cfg.set_enabled(target, False)

    snap = {s['name']: s for s in describe_handlers()}
    assert snap[target]['enabled'] is False
    # Others unaffected.
    other = REGISTRY[1].name
    assert snap[other]['enabled'] is True

    # Re-enable and the flag flips back.
    cfg.set_enabled(target, True)
    snap2 = {s['name']: s for s in describe_handlers()}
    assert snap2[target]['enabled'] is True


def test_describe_handlers_surfaces_priority_overrides(monkeypatch, tmp_path):
    """When a handler has a persisted priority override, the snapshot returns
    the overridden value as `priority` and the registry value as
    `default_priority` with `priority_overridden=True`. UI relies on this to
    show "priority N · default M" + a reset button."""
    from hook_manager import config as cfg
    from hook_manager.registry import describe_handlers

    fake_cfg = tmp_path / 'hook-manager-config.json'
    monkeypatch.setattr(cfg, 'CONFIG_PATH', str(fake_cfg))

    target = REGISTRY[0]
    new_priority = target.priority + 50
    cfg.set_priorities({target.name: new_priority})

    snap = {s['name']: s for s in describe_handlers()}
    assert snap[target.name]['priority'] == new_priority
    assert snap[target.name]['default_priority'] == target.priority
    assert snap[target.name]['priority_overridden'] is True

    # Others untouched.
    other = REGISTRY[1]
    assert snap[other.name]['priority'] == other.priority
    assert snap[other.name]['priority_overridden'] is False


def test_gates_run_before_traces_by_priority():
    """Gates must have lower priority numbers than the catch-all
    `trace_payload` so a block/deny decision is made before the payload
    is logged — otherwise a 'blocked' request would still appear in the
    ingest log as if it ran normally."""
    by_name = {h.name: h for h in REGISTRY}
    # `permission_request_pre_tool` is a built-in PreToolUse gate that
    # always ships in the default registry.
    assert by_name['permission_request_pre_tool'].priority < by_name['trace_payload'].priority


def test_safe_import_returns_stub_and_logs_when_module_import_fails(monkeypatch, tmp_path):
    """A broken handler module (e.g. a top-level `from lib import X` after
    the user moved lib/X.py) must NOT take down the registry. The
    failure is logged to hook-errors.jsonl and the handler degrades to a
    silent no-op so other handlers (notably post_tool_trace) still emit."""
    import importlib
    from hook_manager import registry as reg

    # Pin the error log to a tmp path so we don't poison the real one.
    class _StubProvider:
        @staticmethod
        def traces_dir():
            return tmp_path
    monkeypatch.setattr(
        'lib.providers.get_active_provider',
        lambda: _StubProvider,
    )

    def _broken_import(name, package=None):
        raise ImportError(f"simulated: lib.{name} moved during refactor")
    monkeypatch.setattr(importlib, 'import_module', _broken_import)

    stub = reg._safe_import('nonexistent_handler')
    # Stub is callable through attribute access — runner.py calls h.fn(payload)
    # so the resolved callable must accept any args and return None.
    assert stub.handle(object()) is None
    assert stub.handle_start(object()) is None

    # Failure was logged.
    log = tmp_path / 'hook-errors.jsonl'
    assert log.exists()
    import json
    entries = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    assert any(
        e.get('handler') == 'nonexistent_handler'
        and e.get('event') == 'registry_import'
        and 'simulated' in e.get('error', '')
        for e in entries
    )


def test_prompt_trace_runs_before_turn_trace_on_user_prompt_submit():
    """On UserPromptSubmit, `prompt_trace` MUST run before `turn_trace`
    so the new `prompt` span is emitted before the `turn` span.

    Otherwise the `turn` span's timestamp lands a few microseconds
    BEFORE the new prompt's timestamp; sorted by start_time in the
    projection's `_graft_orphans`, it falls under the PREVIOUS prompt
    and widens that prompt's envelope to the next user input —
    inflating its duration by the entire user-idle gap between the
    two prompts.
    """
    by_name = {h.name: h for h in REGISTRY}
    assert by_name['prompt_trace'].priority < by_name['turn_trace'].priority
