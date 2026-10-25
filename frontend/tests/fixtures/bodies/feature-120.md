# Task runs that survive a server restart

## Intent
A task run today is a child of the `regin serve` process, so restarting the server ends every run in flight. That forbids the one thing a slice that changes server code needs for a live check: a restart. Operators work around it with manual "restart server" slices that stall the chain.

Add a second way to launch a run: in a detached tmux session that the server does not own. Such a run keeps going across a server restart, including one it triggers itself, and settles the same way an SDK run does.

Non-goals:
- replacing the SDK launch path, which stays the default;
- remote or multi-host runners;
- migrating a run between the two launch modes while it is live.

## Decisions
| # | Decision | Rejected alternative | Why |
|---|---|---|---|
| D1 | `launch` is an arm parameter: `sdk` (default) or `tmux` | A global setting | The need is per slice: only slices that restart the server need it |
| D2 | A tmux run settles when the agent calls `regin task finish`, the same report every run already makes. Settling runs verify, writes the terminal state and fires ready dependents in whichever process handles the report. | Have the server watch the pane | A watcher dies with the server, which is the case this exists for |
| D3 | A server that starts up and finds a tmux run whose tmux session is still alive leaves it `running`. It is reaped only when its tmux session is gone. | Reap every run on startup | Today's startup reap is what ends runs on restart |
| D4 | Stop / Watch / Interrupt on a tmux run act through its tmux session. The trace comes in through the existing hook ingest. | An SDK-only control surface | Controls a run cannot use are dead buttons |
| D5 | Firing a ready dependent from a process that is not the server launches the dependent in the dependent's own armed mode. An `sdk` dependent is instead marked `fire_deferred` on its pending attempt, with its edges kept, and the server fires it on the next fire it handles (any settle) or at boot, whichever comes first. The CLI never launches it. | Launch SDK runs from the CLI; fire only at boot | An SDK child of a short-lived CLI dies with it. A settle can land between a restart's boot pass and its first request, so boot alone strands it |
| D6 | `regin task finish` settles in-process only when the connection is refused (nothing listening). It runs verify inline and claims it as `verify_owner=cli`, in the same UPDATE that stamps the claim. The call bypasses HTTP proxies. | Fall back on any error | Through a proxy, a down server answers 502/503 and the fallback never runs. A timeout may be a server still settling |
| D7 | At boot, an open tmux attempt whose verify claim is server-owned (the thread died with the server) has its verify re-run. A CLI-owned claim is left alone | Release every stale claim | A CLI verify runs inside the live pane; a second copy would race it in the same tree |

## Lifecycle (tmux run)
```
armed ──fire──▶ running(tmux alive) ──task finish──▶ verify ──▶ done | failed ──▶ fire dependents
running ──server restart──▶ running (session alive, D3)
running ──tmux session gone, no finish──▶ failed ("tmux session ended without a report")
```

## Invariants
- I1 A server restart never changes the status of a tmux run whose tmux session is alive.
- I2 Verify and dependent firing run once per attempt, whichever process receives the report.
- I3 No polling or timeouts: liveness is read when the server starts and when a control acts, never on an interval.
- I4 Liveness is GONE only on tmux's literal "no server running" / "can't find session". Anything else (missing binary, timeout) is UNKNOWN, which counts as live, except for an attempt the operator Stopped, which is live only when tmux says LIVE.
- I5 `fire_deferred` and `verify_owner` are bookkeeping on `params_json`. They are stripped from every serialized attempt and when an attempt starts.

## Acceptance tests
| Test | Checks |
|---|---|
| test_arm_accepts_launch_mode | `launch` ∈ {sdk, tmux}; unknown values refused |
| test_tmux_run_starts_detached_session | fire → tmux session named for the attempt; run `running` |
| test_startup_reap_spares_live_tmux_run | D3 / I1 |
| test_startup_reap_fails_dead_tmux_run | tmux session gone → failed with reason |
| test_task_finish_settles_tmux_run_and_fires_dependents | D2 / I2 |
| test_tmux_run_survives_server_restart | e2e: run restarts the server itself and still settles done |
| test_stop_and_watch_on_tmux_run | D4 |
| test_arm_sheet_offers_launch_mode | UI: arm sheet shows launch mode, prefilled per R4b of #119 (source 1 and source 3; never from remembered prefs) |
| test_a_deferred_sdk_dependent_fires_on_the_servers_next_fire | D5 |
| test_boot_leaves_a_verify_the_cli_is_running_alone / test_boot_reruns_a_verify_the_previous_server_died_in | D7 |

## Known gaps
- An operator Stop does not settle an attempt whose verify is claimed by someone else. The next boot after the pane is gone does, and Run again is not blocked.
- `test_tmux_run_survives_server_restart` restarts via a second `create_app()` in-process with a real tmux session. The agent does not trigger a real `regin serve` restart itself.

## Follow-up once shipped
Retire #119's MANUAL restart slices: re-arm the backend slices with `launch=tmux` and let each one do its own live check.
