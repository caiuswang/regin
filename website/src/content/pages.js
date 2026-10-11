// The prototype's CONTENT block lists (Regin Docs.dc.html), verbatim. Hash
// links (#/x) are written as the site's history routes (/x).
import { C, H, L, N, P, S, T } from './blocks.js'
import { COMMAND_GROUPS } from './cli.js'
import { DOCS_PAGES } from './docs.js'
import arm from '../assets/shots/pixel/arm.webp'
import handlers from '../assets/shots/pixel/handlers.webp'
import inbox from '../assets/shots/pixel/inbox.webp'
import install from '../assets/shots/pixel/install.webp'
import live from '../assets/shots/pixel/live.webp'
import memory from '../assets/shots/pixel/memory.webp'
import overview from '../assets/shots/pixel/overview.webp'
import patterns from '../assets/shots/pixel/patterns.webp'
import rules from '../assets/shots/pixel/rules.webp'
import sessions from '../assets/shots/pixel/sessions.webp'
import topics from '../assets/shots/pixel/topics.webp'

export const GETTING_STARTED = [
  ['Quick start', [
    P('The setup script creates a Python venv, builds the frontend, initializes the database, discovers repos, and prompts you to create your user account.'),
    C('git clone https://github.com/caiuswang/regin\ncd regin\n./scripts/setup.sh /path/to/your/source-repos\n\n.venv/bin/python cli/regin.py serve\n# Open http://localhost:8321 and log in'),
  ]],
  ['Activate the hooks', [
    N('WARN', '**Serving the dashboard is not enough on its own.** Neither `setup.sh` nor `regin serve` wires regin into your agent — until you install the hooks, no traces are captured, no rules fire, no memory is injected, and `send_to_user` messages go nowhere.'),
    P('regin drives every Claude Code event through one dispatcher — `python -m hook_manager <Event>` — registered in the provider\'s settings file (`~/.claude/settings.json`). Install it once from the running dashboard, under **Settings → Hook Installers**:'),
    S(install, 'Settings → Hook Installers: the Claude Code card with the settings.json path and the Hook Manager dispatcher installed.', 'Install Hook Manager for Claude Code. It writes the dispatcher into the settings.json shown on the card; the optional Debug Hook only logs raw payloads.'),
    P('Prefer the shell? `.venv/bin/python cli/regin.py hooks install --provider claude` writes the same wiring; `regin hooks status` shows installed vs. expected and `regin hooks repair` rewrites any entry that has drifted. New Claude sessions pick the hooks up automatically.'),
  ]],
  ['Manual setup', [
    H('1. Install dependencies'),
    C('python3 -m venv .venv\n.venv/bin/pip install -e .\ncd frontend && npm install && npx vite build && cd ..'),
    H('2. Initialize the database'),
    C('.venv/bin/python cli/regin.py init --force\n.venv/bin/python cli/regin.py rebuild'),
    P('`init --force` is a true local reset: it recreates the SQLite DB, removes deployed skill directories, clears experiments, tags, accounts and deployment records, and rotates the JWT secret.'),
    H('3. Configure local settings'),
    P('Create `config/settings.local.json` (machine-local, gitignored), then register repos on the `/repos` page or with `regin add-repo /path/to/repo`.'),
    C('{\n  "mode": "standalone",\n  "active_provider": "claude"\n}'),
    H('4. Create your account and serve'),
    C('.venv/bin/python cli/regin.py users init-db\n.venv/bin/python cli/regin.py users create <username> <password> --display-name "Your Name"\n.venv/bin/python cli/regin.py serve'),
    N('WARN', '**Don\'t expose `regin serve` to the network until the first user exists.** It binds to 127.0.0.1 by default. The first user to register becomes admin — expose the port before any account exists and anyone who can reach it can.'),
    P('Once the admin exists, bind all interfaces with `--host 0.0.0.0 --port 8321`. For anything past a trusted LAN, front it with a reverse proxy — `regin serve` runs Flask\'s development server.'),
    H('5. Wire the hooks into your agent'),
    P('Finish with the **Activate the hooks** step above, or nothing gets captured or enforced.'),
  ]],
  ['Server modes', [
    P('**Standalone** (default) keeps everything in local SQLite — single user, no MySQL. **Shared** is team mode: accounts and audit logs move to a MySQL instance every member connects to.'),
    C('{\n  "mode": "shared",\n  "database_url": "mysql://user:password@host:3306/regin"\n}'),
    P('Switching modes deletes nothing: patterns, repos, tags, experiments and rule triggers always live in local SQLite. You may need to re-create your admin user after a switch.'),
  ]],
  ['Accounts & roles', [
    P('The first user created gets `admin` automatically. Admins manage roles at `/account` or via `regin users`.'),
    T(['ROLE', 'VIEW', 'EDIT PATTERNS / SKILLS / RULES / SETTINGS', 'MANAGE USERS'], '90px 70px minmax(200px,2fr) 110px', [['admin', 'all', 'all', 'yes'], ['editor', 'all', 'all', 'no'], ['viewer', 'all', 'own profile only', 'no']], '540px'),
  ]],
  ['Shared vs local state', [
    P('Each machine runs its own instance. Patterns, rule-engine sources and tag definitions live in your XDG data directory, not the repo. Relocate the whole tree with `REGIN_DATA_DIR`.'),
    T(['LAYER', 'WHAT', 'WHERE'], 'minmax(140px,1fr) minmax(180px,1.4fr) minmax(200px,1.6fr)', [
      ['Git (shared)', 'Team settings', 'config/settings.json'],
      ['XDG data dir', 'Patterns, rule-engine sources + indexes, tag definitions', '~/.local/share/regin/'],
      ['Auth / audit', 'User accounts, roles, audit log', 'MySQL when shared; SQLite when standalone'],
      ['SQLite (local)', 'Pattern index, repos, experiments, rule triggers, trace', 'db/*.db — rebuilt via regin rebuild'],
      ['Local files', 'Machine-specific paths, JWT secret', 'config/settings.local.json, config/jwt_secret.txt'],
    ], '600px', [2]),
    P('Next: take the [dashboard tour](/dashboard), tune the instance on [Configuration](/configuration), or see the [CLI reference](/cli).'),
  ]],
]

export const RUNS = [
  ['Arm, then launch', [
    P('A task is **armed** with a provider, a model and a launch mode, then started. The *channel* arm runs it under `regin serve` with a typed connection (Claude through the Agent SDK, Kimi over ACP); the *pane* arm runs it in a tmux pane on regin\'s own tmux server, which survives a server restart.'),
    S(arm, 'The Arm task dialog: working directory, git worktree option, agent, model, effort, permission mode, skills and launch choice.', 'Arming a task: where it runs, which agent and model, the skills it opens with, and the launch arm.'),
    P('A run settles on the agent\'s `regin task finish` report — a turn ending is not the work being done. When it settles, the tasks that depend on it are released.'),
  ]],
  ['Providers', [
    T(['AGENT', 'LAUNCH MODES', 'LIMITS'], '120px 160px minmax(240px,1fr)', [
      ['Claude Code', 'Agent SDK, or a tmux pane', 'Full: questions and permission prompts answered from the browser; queued prompts editable before they run.'],
      ['Kimi Code', 'ACP, or a tmux pane', 'Experimental. Runs are unattended and a steer is cancel-then-re-prompt.'],
      ['Codex, generic', '—', 'Adapters exist for tracing; cannot be armed for task runs.'],
    ], '560px'),
  ]],
  ['Follow and steer', [
    P('`/live` follows a running session: its state, its recent steps, any question it is blocked on, with steer and Stop. Because regin owns a channel run\'s process, an `AskUserQuestion` or gated tool call is parked and answered from the browser. Sessions you started yourself can be steered through the **agent bridge** — off by default ([configuration](/configuration)).'),
    S(live, 'The Live view following a running session, with cancel turn, stop agent and the steer box.', '/live following the run that captured this screenshot — cancel turn, stop agent and the steer box below.'),
  ]],
  ['Hearing back', [
    P('Agents report progress, results, blockers and lessons through `send_to_user`. Messages land in `/inbox` with a live unread badge; high-severity ones can fan out to a webhook, Telegram or Lark. CLI: `regin messages`, `regin bridge`, `regin events`.'),
  ]],
  ['Mobile card', [
    N('WARN', 'Experimental. `/live` renders a phone-sized card that can answer permission prompts while you are away from the desk. Expect rough edges and breaking changes.'),
  ]],
]

export const DASHBOARD = [
  ['Pipeline', [
    N('TIP', 'Empty on first run? Expected until you [install the hooks](/getting-started) — the dashboard only shows what the hooks capture.'),
    P('Where the day starts. **Pipeline** puts Overview, Features and Tasks behind one tab strip, scoped to a project. It wears the *Pixel* theme by default. The [Pipeline page](/pipeline) follows one feature through it end to end.'),
    S(overview, 'The Pipeline Overview scoped to the regin project.', 'The Overview scoped to the regin project.'),
  ]],
  ['Session traces', [
    P('Every session the hooks capture lands in **Trace** — searchable, filterable by repo, tag, date and status. Each row rolls up spans, edits, context use and elapsed-vs-active time; open one for a turn-by-turn replay with per-tool cost. Grade a session after the fact with `regin grade run`.'),
    S(sessions, 'The Trace session list with filters and per-session spans, edits, context and time.', 'The session list, filtered to today.'),
  ]],
  ['Live steering', [
    P('Watch a session *as it runs* — the current turn, tool calls landing in real time, a live context meter. With the [agent bridge](/configuration) enabled, the composer sends a steering message straight into the running turn.'),
    S(live, 'The Live view mid-turn with the steer composer.', '/live following the session that captured this tour.'),
  ]],
  ['Inbox & messages', [
    P('Everything agents push with `send_to_user` — progress, results, warnings, blockers, lessons — across every session, filterable by type. The durable record of what each session decided and why.'),
    S(inbox, 'The Inbox: type filters with counts and message cards from across sessions.', 'Messages from every session — typed, searchable, linked back to their session.'),
  ]],
  ['Agent memory', [
    P('The cross-session lesson store. Lessons are ranked by usefulness, de-duplicated offline and recalled into future prompts. The Recall tab previews what a given task would pull.'),
    S(memory, 'The Memory page: categories and a grid of lesson cards.', '1,077 active memories — the store that survives regin init and feeds later sessions.'),
  ]],
  ['Rules', [
    P('Every rule the engines enforce, with engine, trigger, severity and deploy state. These gates fire on `PostToolUse` — the feedback half of the harness. Built in: GritQL, Radon and the bundle engine.'),
    S(rules, 'The Rules page with engine chips and a table of rules.', '43 rules across five engines, grouped by pattern.'),
  ]],
  ['Patterns & skills', [
    P('The feedforward half: procedure guides (**Patterns**), promoted to versioned **Skill** bundles surfaced only when their triggers match. Drop a skill folder to import; push to deploy.'),
    S(patterns, 'The Patterns page with import dropzone and pattern table.', 'Patterns synced from source repos, with skill deploy state and scope.'),
  ]],
  ['Topic wikis', [
    P('Each repo gets a graph of approved **topics**, each backed by a wiki page, plus proposal runs and a reference-health audit. At task time the relevant slices route to the agent by keyword. More in [Topic Wikis](/topics).'),
    S(topics, 'The Topics workspace: approved topics, proposal runs and broken refs.', '50 approved topics over one repo\'s wiki graph.'),
  ]],
]

export const GUIDANCE = [
  ['Patterns & skills', [
    P('Local procedure guides for recurring implementation shapes, deployed into the provider\'s skills directory. When one earns its keep, `regin pattern promote` packages it as a versioned skill. Dashboard: `/patterns`, `/skills`. [How it works](/architecture).'),
  ]],
  ['Rule engines', [
    P('GritQL, Radon and bundle checkers run in the agent\'s `PostToolUse` hook, so a violation is reported in the same turn as the edit that caused it — a rule is a hook that pushes back, not prose the agent must remember. Dashboard: `/rules`; CLI: `regin rules`, `regin hooks`.'),
    C('PostToolUse ▸ rule-check:\n2 violation(s) in SiteHeader.vue\n- focus_visible_styling_coverage (warn)\n- prefer_button_primitive (warn)\nFix these before claiming the edit is complete.'),
  ]],
  ['Agent memory', [
    P('Lessons from past sessions are ranked, consolidated and superseded as they go stale, then recalled when they match the task. Dashboard: `/memory`; CLI: `regin memory`.'),
    S(memory, 'The memory browser: lessons, gotchas, facts and procedures.', 'The live store on 2026-10-09: 1,077 active memories.'),
  ]],
  ['Topic wikis', [
    P('Per-repo knowledge as a reviewed graph: an agent drafts topics against real code, you approve them, the router hands an agent only the topic its task touches, and drift detection flags stale pages. CLI: `regin topics`, `regin wiki`. [Topic Wikis →](/topics)'),
  ]],
]

export const ARCHITECTURE = [
  ['How it works', [
    P('One loop — Spec → Run → Watch → Count. **Workbench** holds features and tasks, **Runs** launches and steers agents, **Observability** records what they did, **Guidance** sits under every run. Declared defects feed back into the Workbench and close the loop.'),
    C('              Workbench                      Runs                    Observability\n   feature row ─▶ spec ─▶ slices ─▶ tasks ─▶ armed run ─────────▶  session trace\n        ▲          (features.body)   (DAG)    Claude: Agent SDK      (spans, turns,\n        │                                     Kimi:   ACP / tmux      tokens, grades)\n        │                                        │  ▲                      │\n   defects ◀── done-claim ◀──────────────────────┘  │ steer / stop         │\n   (rounds-to-done)                    /live, agent bridge, inbox ◀────────┘\n                                                    │\n                  Guidance (underneath every run)   ▼\n   patterns & skills ─▶ provider skills dir     hook_manager ◀─ rule engines\n   agent memory, topic wikis ─▶ recalled into prompts on demand'),
    N('WARN', 'Observability, rule gates and memory injection run *inside* `hook_manager`, so they stay dormant until the dispatcher is installed — [Activate the hooks](/getting-started). Task runs from the dashboard are captured either way.'),
  ]],
  ['Modules by layer', [
    T(['LAYER', 'MODULE', 'PURPOSE'], '110px 190px minmax(240px,1fr)', [
      ['Workbench', 'lib/features/', 'Feature rows, spec, append-only event log, Spec/Build steps, the ledger.'],
      ['Workbench', 'lib/defects/', 'Defects, triage, escapes blamed on a ship, close-with-a-check.'],
      ['Workbench', 'lib/tasks/', 'Tasks, dependency edges, slice-check / tail / transcribe, arming, releasing dependents.'],
      ['Workbench', 'lib/pipeline/', 'Tab counts under one project scope, per-project rollups.'],
      ['Runs', 'lib/agent_sdk/', 'Claude task runs via the Agent SDK: launch, turns, steer, stop.'],
      ['Runs', 'lib/agent_acp/', 'Kimi task runs over ACP.'],
      ['Runs', 'lib/agent_bridge/', 'Delivers a message into a live session.'],
      ['Runs', 'lib/agent_messages/', 'send_to_user: Inbox, lessons into memory, push channels.'],
      ['Observability', 'lib/agent_events/', 'Producer-neutral events: hooks and SDK project onto one span shape.'],
      ['Observability', 'lib/trace/', 'Span ingest, serve-time merge, turn and token rollups, span gates.'],
      ['Observability', 'lib/grader/', 'Post-hoc session rubric grades.'],
      ['Guidance', 'lib/patterns/ + lib/skills/', 'Procedure guides promoted to deployed skills.'],
      ['Guidance', 'lib/rule_engines/', 'GritQL, Radon and bundle engines from PostToolUse.'],
      ['Guidance', 'lib/memory/', 'Lessons: capture, recall, reflect, supersede.'],
      ['Guidance', 'lib/topics/', 'Topic graph: routing, proposals, drift, wikis.'],
      ['Shared', 'hook_manager/', 'One entry point per hook event; runs every handler and merges answers.'],
      ['Shared', 'lib/providers/', 'Adapters (Claude, Kimi, Codex, generic).'],
      ['Shared', 'web/ + frontend/', 'Flask JSON API and the Vue 3 SPA.'],
    ], '580px', [1]),
  ]],
  ['One capture vocabulary', [
    P('Two producers feed the trace. The **hook path** observes a session you drive: `hook_manager` handlers turn each hook event into an agent event. The **SDK path** drives a session regin launched. Both project through one writer into spans, so a task run and a hand-driven session render in the same viewer.'),
    P('Spans are append-only. De-duplication, re-parenting and ordering happen on read, never by rewriting rows — a late or repeated event cannot corrupt what was stored.'),
  ]],
  ['Counts come from events', [
    P('A feature\'s stage and rounds-to-done are not counters. They are queries over its event log, written only by declarations: ship, reopen, abandon, a build starting, a defect blamed on a ship. Nothing is inferred from commits, branch names or timing. See [Pipeline](/pipeline).'),
  ]],
  ['The Guidance layer', [
    H('Patterns & skills — before the agent acts'),
    P('Advisory: they shape what the agent *tends* to do. `regin pattern promote` packages a pattern as a versioned skill, surfaced only when its triggers match.'),
    H('Rule engines — while it acts'),
    P('The enforced half, in `PostToolUse`. **GritQL** (structural queries), **Radon** (complexity) and the **bundle engine** (YAML rule packs, like the Vue/CSS conventions used on this website).'),
    H('Agent memory — across sessions'),
    P('Lessons live in their own SQLite database, outside any prompt, ranked by use, consolidated offline and superseded when stale.'),
  ]],
  ['Databases', [
    P('Three stores: local SQLite (`db/regin.db` — features, tasks, defects, repos, patterns, trace), auth/audit (same file standalone, MySQL shared), and agent memory (its own SQLite file, outside init/rebuild). Migrations ship via Alembic: `regin migrate`.'),
  ]],
]

export const CONFIGURATION = [
  ['Files & precedence', [
    N('WARN', 'Settings only matter once regin is wired into your agent. Empty dashboard? [Install the hook dispatcher](/getting-started) first.'),
    P('Two JSON files plus environment variables, merged highest-wins:'),
    C('REGIN_* environment variable\n  > config/settings.local.json   (machine-local, gitignored)\n  > config/settings.json          (shared, git-tracked)\n  > field default                 (REGIN_DATA_DIR / XDG_DATA_HOME / ~)'),
    P('Team-wide settings go in `config/settings.json`; machine paths, mode, provider and secrets in `config/settings.local.json`. The `/settings` page edits both and routes each key to the right file.'),
    N('TIP', 'Settings edited in the web UI take effect immediately — `save_settings()` refreshes the process-wide singleton in place.'),
  ]],
  ['Core settings', [
    T(['KEY', 'DEFAULT', 'EFFECT'], 'minmax(170px,1fr) minmax(120px,0.8fr) minmax(220px,2fr)', [
      ['web_port', '8321', 'Web dashboard port.'],
      ['mode', 'standalone', 'standalone (local SQLite) or shared (MySQL).'],
      ['database_url', '—', 'MySQL URL; only for shared mode.'],
      ['active_provider', 'claude', 'claude, codex, generic, or kimi.'],
      ['repo_paths', '[]', 'Registered repos. Managed by /repos or regin add-repo.'],
      ['skills_dir', '~/.claude/skills', 'Claude Code skills deploy directory.'],
      ['patterns_dir', '~/.local/share/regin/patterns', 'Where patterns are stored.'],
      ['rule_engines', '[]', 'Engines to load (grit | bundle | radon). Empty = no enforcement.'],
      ['bundle_autoload', 'true', 'Auto-discover regin-bundle.yaml packs.'],
      ['capture_assistant_response', 'true', 'Persist each assistant turn into the trace.'],
      ['experimental_providers', 'false', 'Show codex / generic providers in the UI.'],
    ], '600px', [0, 1]),
  ]],
  ['Environment variables', [
    P('Any key can be set as `REGIN_<KEY>`; these are the ones you\'ll reach for:'),
    T(['VARIABLE', 'EFFECT'], 'minmax(190px,1fr) minmax(240px,2fr)', [
      ['REGIN_DATA_DIR', 'Relocate the whole user-local data tree at once.'],
      ['REGIN_PATTERNS_DIR', 'Override the patterns directory alone.'],
      ['REGIN_GRIT_DIR', 'Override the GritQL rules directory.'],
      ['REGIN_TAGS_PATH', 'Override the tag definitions YAML path.'],
      ['REGIN_LOG_DIR', 'Override the activity-log directory.'],
      ['REGIN_DATABASE_URL', 'MySQL URL for shared mode.'],
      ['REGIN_BRIDGE', 'Per-shell opt-in for agent-bridge pane registration.'],
    ], '460px'),
  ]],
  ['Hook handlers', [
    P('Each handler the dispatcher routes — trace capture, rule gates, memory injection, the inbox — toggles per provider under **Settings → Hook Handlers**. These flags live in `~/.claude/hook-manager-config.json`, not `settings.json`.'),
    S(handlers, 'Settings → Hook Handlers: handlers with kind badges and Enabled toggles.', 'Each handler has a kind — gate, trace, enrich, notify — and its own toggle. Takes effect on the next hook fire.'),
  ]],
  ['Agent memory', [
    P('`agent_memory` — cross-session lesson store, its own SQLite DB. On by default.'),
    T(['KEY', 'DEFAULT', 'EFFECT'], '160px 140px minmax(220px,1fr)', [
      ['enabled', 'true', 'Master switch.'], ['auto_inject', 'true', 'Inject recalled lessons via UserPromptSubmit.'], ['inject_top_k', '3', 'Max memories per prompt.'], ['scope_policy', 'per-repo-tagged', 'global, per-repo, or per-repo-tagged.'], ['dense_enabled', 'true', 'Embedding recall; degrades to FTS.'], ['forget_after_days', '45', 'Retire never-recalled episodic memories. 0 disables.'],
    ], '560px', [0, 1]),
  ]],
  ['Agent bridge', [
    P('`agent_bridge` — HTTP → guarded tmux keystroke injection into a live session. Off by default.'),
    T(['KEY', 'DEFAULT', 'EFFECT'], '200px 80px minmax(220px,1fr)', [
      ['enabled', 'false', 'Also needs REGIN_BRIDGE=1, claude inside tmux, and an editor login.'], ['token', '""', 'Bearer token for headless /api/bridge/* callers only.'], ['rate_limit_per_minute', '30', 'Per-session delivery cap.'], ['max_text_len', '4000', 'Messages sanitized and capped at this length.'],
    ], '560px', [0, 1]),
    N('WARN', 'The bridge `token` is SSH-equivalent. Keep the whole `agent_bridge` block in the gitignored `config/settings.local.json`.'),
  ]],
  ['Other blocks', [
    T(['BLOCK', 'WHAT IT TUNES'], '170px minmax(260px,1fr)', [
      ['agent_messages', 'Inbox push channels — webhook, Telegram, Lark — each with its own severity gate; retention.'],
      ['grader', 'Post-hoc grades on two axes: correctness and process. Judge agent, auto-escalate, distill on fail.'],
      ['topic_evolution', 'Drift detection and refresh proposals. Everything defaults off.'],
      ['trace_retention', 'Opt-in background prune of superseded pending spans.'],
    ], '460px'),
    P('The exhaustive, always-current source is the `Settings` model in `lib/settings.py`.'),
  ]],
]

// The command-group rows stay in content/cli.js, which a test holds to the
// real `regin --help` (the prototype's own list is a snapshot of it).
export const CLI = [
  ['Invoking the CLI', [
    P('Run from the repo root through the project venv — the system interpreter lacks regin\'s dependencies:'),
    C('.venv/bin/python cli/regin.py <command>\n\n# examples\n.venv/bin/python cli/regin.py doctor\n.venv/bin/python cli/regin.py serve'),
    N('TIP', 'Listings below use the short `regin` form; substitute `.venv/bin/python cli/regin.py` unless you\'ve installed an alias.'),
  ]],
  ['Core commands', [
    T(['COMMAND', 'WHAT IT DOES'], 'minmax(190px,1fr) minmax(260px,2.2fr)', [
      ['regin init', 'Initialize DB and directories. --force does a true local reset.'],
      ['regin doctor', 'Check tools, provider capabilities, agent-bridge preconditions.'],
      ['regin discover', 'Register sibling source repositories.'],
      ['regin add-repo <path>', 'Register one git working tree.'],
      ['regin remove-repo <name>', 'Unregister a repository.'],
      ['regin prune-repos', 'Drop rows whose path is missing or under $TMPDIR.'],
      ['regin session-id', 'Print the current agent session id.'],
      ['regin search <keyword>', 'Search patterns.'],
      ['regin rebuild', 'Rebuild the local DB from git-tracked files.'],
      ['regin migrate', 'Sync an existing DB to the current schema.'],
      ['regin serve', 'Start the dashboard on :8321. --port, --host 0.0.0.0.'],
    ], '500px'),
  ]],
  ['Grouped subcommands', [
    P('Run any group with `--help` for the full list and flags.'),
    T(['GROUP', 'SUBCOMMANDS', 'PURPOSE'], '130px minmax(200px,1.3fr) minmax(200px,1.2fr)', COMMAND_GROUPS.map((r) => [r.group, r.sub, r.desc]), '640px', [0, 1]),
  ]],
  ['Troubleshooting', [
    T(['SYMPTOM', 'FIX'], 'minmax(200px,1fr) minmax(240px,1.4fr)', [
      ['“Database not initialized”', 'regin init, then regin rebuild.'],
      ['Login broken after a DB rebuild', 'Accounts are local — regin users create <name> <pass> --role admin.'],
      ['Forgot the admin password', 'regin users reset-password <name> <newpass>.'],
      ['Port already in use', 'regin serve --port 8322.'],
      ['Dashboard empty — no traces, rules never fire', 'The hook dispatcher isn\'t wired in. Settings → Hook Installers.'],
    ], '480px', []),
  ]],
]

const TOPIC_SUBPAGES = DOCS_PAGES.filter((page) => page.parent === '/topics')

export const TOPICS = [
  ['Why topics', [
    P('A coding agent starts every session knowing nothing about your repo beyond its prompt. Dumping documentation wastes context on the 95% that doesn\'t apply; writing nothing forces it to rediscover your architecture every time. Topics are the middle path: knowledge sized to a task, delivered only when the task touches it.'),
  ]],
  ['What a topic is', [
    C('{\n  "id": "session-trace-design",\n  "label": "Session trace design",\n  "aliases": ["trace spans", "span ingest"],\n  "intent": "How sessions become queryable spans",\n  "refs": [\n    { "path": "lib/trace/ingest.py", "role": "entrypoint" },\n    { "path": "lib/trace/merge.py",  "role": "implementation" },\n    { "path": "db/schema.sql",       "role": "schema" }\n  ],\n  "edges": [{ "target": "trace-span-capture", "type": "related" }]\n}\n# + .regin/topics/wiki/session-trace-design.md'),
    L(['**Refs with roles** — the files that matter, ordered the way a newcomer should read them.', '**A wiki page** — how the pieces fit, the invariants, the gotchas; checked against the code later.', '**Aliases and intent** — the vocabulary the router matches a task\'s keywords against.', '**Edges** — links to related topics, so context widens one deliberate hop at a time.']),
  ]],
  ['Where the graph lives', [
    L(['`.regin/topics/topic.json` — the approved graph, git-tracked. Nothing writes to it without review.', '`.regin/topics/topic.local.json` — a machine-local overlay for unpromoted topics.', '`.regin/topics/wiki/<id>.md` — one page per topic, plus a generated `index.md`.']),
    P('Commit the graph and a teammate\'s clone routes to the same knowledge; `regin topics install-hook` keeps snapshots in sync after pulls and merges.'),
  ]],
  ['The lifecycle', [
    L(['An agent explores the repo and **proposes** draft topics; you review, iterate and apply them one at a time.', 'At task time the **router** resolves 2–6 keywords to one approved topic — deterministically, no model call — and hands over refs, wiki and neighbours. A vague query honestly resolves to nothing.', 'As code moves on, **drift detection** flags pages whose files changed; refresh proposals bring them back in line.', 'Cross-session **memory lessons** file themselves under topic nodes, so the graph doubles as an index of what past sessions learned.'], true),
  ]],
  // G1: the prototype draws no sub-pages, so the section's own index is built
  // from the registry's one-line summaries.
  ['In this section', [
    L(TOPIC_SUBPAGES.map((page) => `[${page.code} ${page.title}](${page.path}) — ${page.summary}`)),
  ]],
]
