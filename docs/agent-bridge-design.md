# Agent Bridge — design

Send a message *to* a live Claude Code session from outside its terminal —
`curl` from a phone, a webhook, another agent — and have it arrive as if the
user had typed it. This is the inverse of `lib/agent_messages/` (agent →
human); the bridge is human/system → agent.

## Problem

There is no API to push input into a running interactive `claude` process.
The two doors that exist are **hooks** (fire only while the agent is active)
and **the TTY itself**. Hooks cannot reach an agent idling at its prompt, so
any hook-only design has a dead zone exactly when delivery matters most.

## Decision: tmux is the transport

Deliver by typing into the session's tmux pane (`tmux send-keys`), the same
mechanism the trace integration harness
(`tests/trace/integration/harness.py`) uses to drive real sessions.

Why this beats hook injection as the primary path:

- **Covers every agent state.** Idle → the message submits as a prompt.
  Mid-turn → Claude Code queues it as a steering message and processes it at
  the next boundary. Both cases are spike-verified (below).
- **The message is a real user message** — visible in the terminal, in the
  transcript, indistinguishable from typed input. Hook `additionalContext`
  is invisible in the TUI and second-class in the transcript.
- **No delivery-path changes to the hook chain.** Hooks are demoted to a
  registration role (recording where a session physically lives).

Accepted constraint: **only sessions running inside tmux are reachable.**
Sessions in a plain terminal tab or IDE panel are invisible to the bridge; a
hook-injection fallback (PostToolUse `additionalContext` + Stop-block) can
cover them later without changing the server surface.

## Architecture

```
sender ──POST /api/bridge/messages──▶ Flask (regin serve, 127.0.0.1)
                                        │  auth ▸ sanitize ▸ inbox row
                                        ▼
                                 resolve session → pane
                                 (registry, written by the
                                  SessionStart hook: $TMUX_PANE)
                                        │
                                        ▼
                            guarded tmux send-keys delivery
                            (verify target ▸ cancel copy-mode ▸
                             type literal ▸ capture-pane ack ▸ Enter)
```

Three pieces:

1. **Inbox table** — append-only rows: target session, body, sender,
   created/delivered timestamps, delivery outcome. Undeliverable messages
   stay pending rather than failing (graceful degradation).
2. **HTTP endpoint** in the existing Flask app — no new server process;
   `regin serve` is already the hub the hooks and UI talk to.
3. **Pane registry** — the SessionStart hook records `$TMUX_PANE` (when
   present) alongside the session row, so `session_id → pane` resolution is
   exact. Scanning `tmux list-panes` can find *a* claude but cannot tell
   *which* session it is; the hook can. The row stores an identity triple —
   pane id, tmux server pid (`#{pid}`), pane pid (`#{pane_pid}`) — not a
   positional coordinate; see *Pane identity and staleness*.

## Delivery guards

Each guard exists because `send-keys` is fire-and-forget keystroke injection
into a screen. All are implemented and exercised in the spike
(`scripts/bridge_mvp.py`):

- **Target verification (security-critical).** The pane's foreground process
  must be claude. If the agent exited and the pane fell back to a shell, a
  "message" typed there would *execute as a shell command* — this guard is
  what stops message injection from escalating to code execution. Empirical
  finding: the native build reports `pane_current_command` as **`claude.exe`**
  (even on macOS); NVM installs report `claude` or `node`. Allowlist all
  three, and treat the capture-pane ack as the second factor.
- **Copy-mode cancel.** A pane scrolled into copy-mode eats keystrokes;
  check `#{pane_in_mode}` and send `-X cancel` first.
- **Sanitization.** Printable single-line text only: strip ANSI/control
  bytes (a raw `Ctrl-C` would interrupt the agent's work; escapes can drive
  the TUI), flatten newlines (a raw newline submits early), cap length.
  Send with `send-keys -l --` (literal mode, no key-name interpretation).
- **Capture-pane ack.** After typing and before Enter, capture the pane and
  verify the text is visible in the composer. This is the delivery
  guarantee: if a dialog, `/command` menu, or `!` shell swallowed the
  keystrokes, the bridge reports *undelivered* instead of submitting blind.

## Pane identity and staleness

`$TMUX_PANE` is the pane's **unique id** (`%N`), not a position. tmux keeps
it stable for the life of the pane across every rearrangement — window
moves, renumbering, `join-pane`/`break-pane`/`swap-pane` into a different
window or session — and all `-t %N` targeting (send-keys, display-message,
capture-pane) follows the pane wherever it goes. Verified on an isolated
socket: a registered pane moved into another session still resolved and
received keystrokes by its original id. So "the user moved the pane" is a
non-event for the registry.

The registry must instead survive the two ways an id genuinely dies:

- **Pane closed.** `display-message -t %N` fails → target verification
  fails closed; the message stays pending with the refusal detail.
- **tmux server restart.** Pane ids are per-server-lifetime and are
  **recycled** — verified: after `kill-server`, the first new pane is `%0`
  again. A registry row from the previous server could therefore point at
  an unrelated pane (possibly running someone else's claude). Guard: the
  registration records the tmux server pid (`#{pid}`) and the pane's shell
  pid (`#{pane_pid}`); delivery re-reads both and refuses on any mismatch.
  A server restart also kills every process in its panes, so the old
  claude session is gone regardless — when the user relaunches or
  `--resume`s it, SessionStart fires again (resume is a SessionStart
  source) and re-registers fresh coordinates. Stale rows never self-renew;
  they only fail closed.

Related fail-closed cases the same guards catch: claude suspended with
`Ctrl-Z` (foreground command becomes the shell → refused) and anything the
identity checks can't prove, with the capture-pane ack as the final
backstop.

## Security model

The honest framing: **this endpoint is remote keystroke injection into a
terminal, and the bearer token is equivalent to SSH access to the machine.**
Controls, ranked:

1. **Bind 127.0.0.1 only.** Remote senders come in via a tunnel
   (Tailscale/SSH), never a wider bind.
2. **Two credentials, one delivery layer.** Headless/external callers
   (`/api/bridge/*`) authenticate with the bearer token — long random value
   in settings, constant-time compare, separately revocable from the web-UI
   auth. Header-only auth also defeats drive-by `fetch()` from a browser
   tab (CORS preflight fails without the header). The /live composer's
   proxy (`POST /api/sessions/<sid>/bridge-send`,
   `web/blueprints/bridge.py`) instead grants agent-steering to
   **editor-role web JWTs** — a deliberate, product-approved surface, gated
   `require_editor` because steering outranks every editor-gated mutation.
   The invariant that holds on both paths: the bridge token itself never
   reaches the browser (the proxy calls the delivery layer in-process, no
   token-carrying HTTP hop), and the rate limit plus the pane-identity/ack
   guards below apply identically.
3. **Target verification** (above) — the injection→shell-execution
   escalation is closed by refusing non-claude panes.
4. **Sanitization** (above) — no control bytes, no ANSI, no tmux key names.
5. **Per-session opt-in.** Only sessions whose registration marks them
   bridge-reachable accept delivery; default off.
6. **Audit trail.** Every accept/reject/delivery outcome logged via
   `lib/activity_log.py` (writes at INFO, secret-key auto-redaction), plus
   the inbox row itself. Message bodies persist in the inbox and session
   transcript — senders must not post secrets.
7. **Per-session rate limit** — queued steering messages are cost and chaos
   if a client loops.

An authenticated sender fully controls the agent — that is the feature, not
a flaw to mitigate. Sender identity is recorded on the inbox row so the
trail shows what came from where.

## Feasibility spike

`scripts/bridge_mvp.py` (stdlib-only, no regin imports) is a working
miniature of the whole path: `POST /send` → guards → delivery. Its
`--selftest` launches a real `claude --model=Haiku` in tmux and proved,
end-to-end (all PASS):

- **Idle injection** — posted instruction submitted at the prompt; the agent
  created a sentinel file with exact requested content.
- **Busy steering** — a second message typed mid-turn (~18s before the
  running task finished, per sentinel mtimes) was queued by Claude Code and
  processed immediately after the task — `steer_ok.txt` landed 1s after
  `busy_done.txt`.
- **Shell refusal** — a post aimed at a plain fish pane was refused by
  target verification; the keystrokes never reached the shell.

Spike-inherited gotchas encoded in the script: absolutize the claude binary
path (the tmux server's login shell may lack the NVM bin dir), and `env -u
CLAUDE_CODE_SESSION_ID -u CLAUDE_CODE_CHILD_SESSION` when a bridge target is
launched from inside another Claude session.

## v1 implementation plan

Each slice is independently shippable and ends verified (unit tests +, for
slices 3-4, a live selftest derived from the spike). File seams below were
confirmed against the code, not assumed.

### Slice 1 — pane registry (hook side)

A SessionStart handler in `hook_manager/handlers/` (registered in
`hook_manager/registry.py`) reads `$TMUX_PANE` / `$TMUX` from `os.environ`
— handler processes see the full environment; the hook payload does not
carry env — resolves the identity triple (pane id, tmux server pid, pane
pid; see *Pane identity and staleness*) and records a `session → pane` row
via the
`lib.orm.engine.get_connection` idiom already used by
`session_lifecycle.py`. Reachability is **opt-in**: the row is only marked
bridge-reachable when the session was launched with the opt-in env var
(`REGIN_BRIDGE=1`); default off.

### Slice 2 — schema, settings, store

- `db/schema.sql` (the fresh-install source of truth — there are no
  separate migrations for tables like this): `bridge_messages` mirroring
  the `agent_messages` DDL idiom (`CREATE TABLE IF NOT EXISTS`, text
  timestamps, explicit indexes), plus the registry table from slice 1.
- `lib/settings.py`: `AgentBridgeConfig(BaseModel)` attached as
  `Settings.agent_bridge`, mirroring `AgentMessagesConfig`: `enabled`
  (default false), `token` (set only in gitignored
  `config/settings.local.json`), `rate_limit_per_minute`, `max_text_len`,
  `allowed_pane_commands`.
- `lib/agent_bridge/store.py` mirroring `lib/agent_messages/store.py`:
  single-writer module, `SessionLocal()`, activity logger
  `get_activity_logger("agent_bridge")`.

### Slice 3 — delivery engine

`lib/agent_bridge/delivery.py`: the spike's guarded `deliver()`
(sanitize → target verification → copy-mode cancel → `send-keys -l --` →
capture-pane ack → Enter) promoted to library code, plus session→pane
resolution from the registry with the identity re-check (server pid + pane
pid must match the registered triple), per-session rate limiting, and
audit logging of every accept/reject/outcome. A message that fails the ack stays
`pending` with the refusal detail on the row — v1 has no automatic retry.

### Slice 4 — HTTP surface

Blueprint mirroring `web/blueprints/trace/agent_messages.py`:

- `POST /api/bridge/messages` `{session_id, text, sender}` — enqueue +
  attempt delivery, return the outcome.
- `GET /api/bridge/sessions` — live reachable sessions.
- `GET /api/bridge/messages` — inbox with delivery status.

Auth: the app's deny-by-default JWT gate (`web/app.py`) would 401 these, so
the POST endpoint joins the machine-ingest allowlist and enforces its own
bearer check against `settings.agent_bridge.token` (constant-time compare)
— deliberately a *different* credential from the web-UI JWT so each is
separately revocable. Server binding stays the `cli/commands/server.py`
default of 127.0.0.1.

### Deferred beyond v1

- Hook-injection fallback (PostToolUse `additionalContext` + Stop-block)
  for sessions not under tmux.
- A send box in the web UI's live session view.
- Multiline bodies via bracketed paste (v1 flattens newlines).

When v1 lands, `scripts/bridge_mvp.py` retires: its guards live on in
`lib/agent_bridge/delivery.py` and its selftest becomes the integration
test.
