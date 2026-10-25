# Remove annoying test notification

Running regin's own test suite delivered real push notifications to the
developer's phone. The suite is supposed to be hermetic; the push fan-out
was the last unguarded outbound channel.

## Root cause

`lib/agent_messages/store.py::record_message` calls
`push.registry.maybe_dispatch` on every write, and `active_channels()`
reads the *live* `settings.agent_messages` — i.e. the developer's real
`config/settings.json`, not a pristine one.

`tests/conftest.py::_default_feature_config` already reset that block, but
`pyproject.toml` declares **two** testpaths and a conftest under `tests/`
cannot reach `hook_manager/tests`.

| Fact | Value |
|---|---|
| Real Feishu POSTs per full suite run (measured) | 21 |
| Origin | `hook_manager/tests/{test_plan_trace,test_replay,test_env_handlers,test_permission_request}.py` |
| Producing handlers | PermissionRequest, ExitPlanMode |
| Why only on a real machine | `push_permission_events` / `push_plan_events` default `False`; the developer's config sets both `True` |
| Child-process (`python -m hook_manager`) escapes | 0 (measured: real-DB `agent_messages` count unchanged) |

## Design

`conftest.py::_block_push_transport` — autouse, repo-root, sitting beside
the ingest / badge-notify / spawn / bridge fixtures it mirrors.

| Layer | Mechanism | Closes |
|---|---|---|
| 1 | `settings.agent_messages` ← `AgentMessagesConfig()` | the developer's config reaching **either** testpath; no channel configured → `maybe_dispatch` returns `None` |
| 2 | `base.http_post_json` ← silent stub | a test that hand-configures a channel and would still reach a socket |

Layer 2 reaches all three channels only because each calls
`base.http_post_json(...)` through the module; a channel importing the name
directly would keep a live binding. The stub is silent, not raising: the
registry swallows a channel exception into `webhook_status='failed'`, so a
raise would rewrite a column rather than fail a test.

## Invariants

- No pytest process, parent or child, makes an outbound push HTTP call.
- `registry.configured_channels() == []` by default in both testpaths.
- Tests that legitimately exercise push (`tests/trace/test_push_channels.py`)
  still test real behaviour: their per-test `monkeypatch` is applied after
  the autouse fixture and therefore wins.

## Acceptance

- Given a machine with `lark_enabled=True` and a real webhook URL,
  when the full suite runs, then a `urlopen` / `http.client` spy records
  zero push requests. **Verified** — 3 localhost reachability probes only.
- Given `_block_push_transport` is neutralized, when
  `hook_manager/tests/test_push_isolation.py` runs, then all 6 tests fail.
  **Verified.**

The guard lives under `hook_manager/tests` deliberately: under `tests/` it
would pass with the fixture deleted, because `_default_feature_config`
already clears the config there — it would be guarding the wrong fixture.

## Not built

No production-code kill switch (an env flag on the registry) and no
per-channel `REGIN_AGENT_MESSAGES__*_ENABLED` env vars for children: the
child path was measured to push zero times, so the in-process fixture is
sufficient and a child guard would be untested machinery.
