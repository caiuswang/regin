// Grounded in lib/settings.py (SETTINGS_SCHEMA + the typed Settings model)
// and docs/setup.md. Keep in sync when either changes.

export const CORE_SETTINGS = [
  { key: 'web_port', def: '8321', desc: 'Web dashboard port.' },
  { key: 'mode', def: 'standalone', desc: 'Server mode: standalone (local SQLite for users/audit) or shared (MySQL).' },
  { key: 'database_url', def: '—', desc: 'MySQL URL; only needed when mode is shared. Also via REGIN_DATABASE_URL.' },
  { key: 'active_provider', def: 'claude', desc: 'Primary agent provider: claude, codex, generic, or kimi. Claude is the fully supported default.' },
  { key: 'repo_paths', def: '[]', desc: 'Registered source repositories. Managed by the /repos page or regin add-repo / remove-repo, not edited by hand.' },
  { key: 'skills_dir', def: '~/.claude/skills', desc: 'Claude Code skills deploy directory.' },
  { key: 'skillhub_url', def: 'http://127.0.0.1:8322', desc: 'Base URL of the optional regin-skillhub server (for pattern promote).' },
  { key: 'patterns_dir', def: '~/.local/share/regin/patterns', desc: 'Where procedure guides (patterns) are stored — user-local data.' },
  { key: 'grit_dir', def: '~/.local/share/regin/grit', desc: 'GritQL rule sources and generated indexes — user-local data.' },
  { key: 'tags_path', def: '~/.local/share/regin/config/tags.yaml', desc: 'User-curated tag definitions YAML.' },
  { key: 'rule_engines', def: '[]', desc: 'Rule engines to load (id, kind: grit | bundle | radon, per-engine options). Empty list = no rule enforcement.' },
  { key: 'bundle_autoload', def: 'true', desc: 'Auto-discover regin-bundle.yaml packs under patterns_dir as bundle engines.' },
  { key: 'capture_assistant_response', def: 'true', desc: 'Persist each assistant turn’s response text into the session trace.' },
  { key: 'diagnostics_enabled', def: 'false', desc: 'Maintainer diagnostics: hook payload schema validation and drift recording. Per-machine.' },
  { key: 'experimental_providers', def: 'false', desc: 'Surface the experimental codex / generic providers in the UI.' },
]

export const ENV_VARS = [
  { key: 'REGIN_DATA_DIR', desc: 'Relocate the whole user-local data tree (patterns, grit rules, tags, logs) at once.' },
  { key: 'REGIN_PATTERNS_DIR', desc: 'Override the patterns directory alone.' },
  { key: 'REGIN_GRIT_DIR', desc: 'Override the GritQL rules directory alone.' },
  { key: 'REGIN_TAGS_PATH', desc: 'Override the tag definitions YAML path.' },
  { key: 'REGIN_LOG_DIR', desc: 'Override the activity-log directory.' },
  { key: 'REGIN_DATABASE_URL', desc: 'MySQL URL for shared mode.' },
  { key: 'REGIN_BRIDGE', desc: 'Per-shell opt-in for agent-bridge pane registration (set to 1 in the shell that launches claude).' },
]

export const NESTED_BLOCKS = [
  {
    id: 'agent-memory',
    key: 'agent_memory',
    title: 'Agent memory',
    summary: 'Cross-session lesson store (own SQLite DB, survives regin init/rebuild). On by default.',
    rows: [
      { key: 'enabled', def: 'true', desc: 'Master switch for the memory engine.' },
      { key: 'auto_inject', def: 'true', desc: 'Inject <recalled_experience> lessons into matching prompts via the UserPromptSubmit hook.' },
      { key: 'inject_top_k', def: '3', desc: 'Max memories injected per prompt (reranked surfaces).' },
      { key: 'scope_policy', def: 'per-repo-tagged', desc: 'Write scope: global, per-repo, or per-repo-tagged (repo-stamped writes, globally visible recall).' },
      { key: 'dense_enabled', def: 'true', desc: 'Semantic (embedding) recall on long-lived surfaces; degrades to FTS without torch/transformers.' },
      { key: 'forget_after_days', def: '45', desc: 'Retire episodic memories never deliberately recalled after this many days. 0 disables.' },
    ],
  },
  {
    id: 'agent-messages',
    key: 'agent_messages',
    title: 'Agent messages & push channels',
    summary: 'The send_to_user inbox is always on; outbound push channels are opt-in, each with its own severity gate.',
    rows: [
      { key: 'base_url', def: 'http://127.0.0.1:8321', desc: 'Woven into payloads so notifications link back to the originating session.' },
      { key: 'webhook_url', def: '—', desc: 'Generic webhook (ntfy, Slack incoming hook…). Gated by webhook_min_severity (default warning).' },
      { key: 'telegram_bot_token / telegram_chat_id', def: '—', desc: 'Telegram Bot API channel. Gated by telegram_min_severity (default warning).' },
      { key: 'lark_webhook_url', def: '—', desc: 'Lark / Feishu custom-bot webhook; optional lark_secret for signed requests.' },
      { key: 'retention_days', def: '—', desc: 'Auto-prune inbox messages older than this. Unset = keep forever.' },
    ],
  },
  {
    id: 'agent-bridge',
    key: 'agent_bridge',
    title: 'Agent bridge',
    summary: 'HTTP → guarded tmux keystroke injection into a live claude session. Off by default; the token is SSH-equivalent and belongs only in settings.local.json.',
    rows: [
      { key: 'enabled', def: 'false', desc: 'Master switch. Also requires REGIN_BRIDGE=1 in the launching shell, claude running inside tmux, and an editor-role login.' },
      { key: 'token', def: '""', desc: 'Bearer token for headless /api/bridge/* callers only — the web composer uses your normal login.' },
      { key: 'rate_limit_per_minute', def: '30', desc: 'Per-session delivery cap.' },
      { key: 'max_text_len', def: '4000', desc: 'Messages are sanitized and capped at this length before being typed into the pane.' },
    ],
  },
  {
    id: 'grader',
    key: 'grader',
    title: 'Session grader',
    summary: 'Post-hoc rubric grades on two never-fused axes: correctness (claim groundedness) and process (tool use, redundancy, cost).',
    rows: [
      { key: 'enabled', def: 'true', desc: 'Master switch.' },
      { key: 'external_agent', def: '—', desc: 'Judge agent for the deep tier (a key in topic_proposal_external_agents); unset = first configured agent.' },
      { key: 'auto_escalate', def: 'true', desc: 'Escalate screen → deep when the mechanical pass is borderline and a judge is configured.' },
      { key: 'distill_on_fail', def: 'true', desc: 'Distill flagged sessions into proposed memory lessons.' },
    ],
  },
  {
    id: 'topic-evolution',
    key: 'topic_evolution',
    title: 'Topic evolution',
    summary: 'Code-driven topic/memory co-evolution (drift detection, refresh proposals). Everything defaults off.',
    rows: [
      { key: 'evolution_enabled', def: 'false', desc: 'Unlock the drift/cascade machinery.' },
      { key: 'mechanical_autoapply', def: 'false', desc: 'Let ref renames write to the local overlay without review (never the approved topic.json).' },
      { key: 'auto_spawn_agents', def: 'false', desc: 'Launch the external drafting agent for refresh proposals automatically.' },
    ],
  },
  {
    id: 'trace-retention',
    key: 'trace_retention',
    title: 'Trace retention',
    summary: 'Opt-in background prune of superseded pending placeholder spans.',
    rows: [
      { key: 'auto_reap', def: 'false', desc: 'Run the reaper in a daemon thread while regin serve is up. Manual regin trace reap-pending always works.' },
      { key: 'interval_hours', def: '24', desc: 'Sweep cadence.' },
      { key: 'idle_minutes', def: '60', desc: 'Only touch sessions idle at least this long.' },
    ],
  },
]

export const STATE_LAYERS = [
  { layer: 'Git (shared)', what: 'Team settings', where: 'config/settings.json' },
  { layer: 'XDG data dir (user-local)', what: 'Patterns, rule-engine sources + indexes, tag definitions', where: '~/.local/share/regin/ (patterns/, grit/, config/tags.yaml)' },
  { layer: 'Auth / audit', what: 'User accounts, roles, audit log', where: 'MySQL when mode: shared; SQLite when standalone' },
  { layer: 'SQLite (local)', what: 'Pattern index, repo tracking, experiments, rule triggers, trace', where: 'db/*.db — rebuilt from on-disk files via regin rebuild' },
  { layer: 'Local files', what: 'Machine-specific paths, JWT secret', where: 'config/settings.local.json, config/jwt_secret.txt' },
]
