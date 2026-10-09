// Grounded in `regin --help` and each group's own --help. COMMAND_GROUPS lists
// every group command `regin --help` prints, in the four-layer order.

export const CORE_COMMANDS = [
  { cmd: 'regin init', desc: 'Initialize the database and directories. --force does a true local reset (recreates the DB, clears local state, rotates the JWT secret).' },
  { cmd: 'regin doctor', desc: 'Check the environment: missing CLI tools, provider capabilities, agent-bridge preconditions.' },
  { cmd: 'regin discover', desc: 'Register sibling source repositories.' },
  { cmd: 'regin add-repo <path>', desc: 'Register one repository by path (must be a git working tree).' },
  { cmd: 'regin remove-repo <name>', desc: 'Unregister a repository.' },
  { cmd: 'regin prune-repos', desc: 'Drop repository rows whose path is missing or under $TMPDIR.' },
  { cmd: 'regin session-id', desc: 'Print the current agent session id ($REGIN_SESSION_ID, or the provider’s own variable).' },
  { cmd: 'regin search <keyword>', desc: 'Search patterns.' },
  { cmd: 'regin tags --index', desc: 'Generate tag / repo indexes.' },
  { cmd: 'regin rebuild', desc: 'Rebuild the local database from git-tracked files.' },
  { cmd: 'regin migrate', desc: 'Sync an existing database to the current schema (alembic upgrade head).' },
  { cmd: 'regin serve', desc: 'Start the web dashboard on port 8321. --port changes the port; --host 0.0.0.0 exposes it beyond localhost.' },
]

export const COMMAND_GROUPS = [
  { group: 'regin feature', sub: 'new · spec · stage · ledger · attribute · unattribute · image · ship · reopen · abandon · refresh', desc: 'Workbench: where a feature stands in the pipeline, and what it cost.' },
  { group: 'regin task', sub: 'add · list · show · comment · finish · delete · slice-check · tail · transcribe · commands', desc: 'Workbench: capture and inspect tasks; cut a spec’s slice table into tasks; report a run finished.' },
  { group: 'regin defects', sub: 'open · list · rounds · attribute · edit · merge · fix-item · image · close · reopen', desc: 'Workbench: the defect ledger — what a feature got wrong, and what escaped.' },
  { group: 'regin messages', sub: 'list · read · stats · prune', desc: 'Runs: inspect and prune the send_to_user inbox.' },
  { group: 'regin bridge', sub: 'listen', desc: 'Runs: the agent bridge — a session’s channel for steering messages.' },
  { group: 'regin events', sub: 'list', desc: 'Runs: inspect the declared notification event bus.' },
  { group: 'regin stats', sub: 'time · session · sessions · daily · by', desc: 'Observability: engineering-time analytics over real sessions — where build time goes, why one session was slow.' },
  { group: 'regin trace', sub: 'dump · span · prune · reap-pending · backfill-* · resolve-repos · ingest-workflows', desc: 'Observability: session trace maintenance and backfills.' },
  { group: 'regin grade', sub: 'run · show · list · reflect · pareto', desc: 'Observability: post-hoc session rubric grades.' },
  { group: 'regin logs', sub: 'list · tail · grep · prune · path', desc: 'Observability: view and manage regin’s own activity log.' },
  { group: 'regin gate', sub: 'recall-ran · task-recall-ran · pipeline-ran', desc: 'Observability: trace-derived pass/fail gates for steps nothing else enforces.' },
  { group: 'regin goal', sub: 'preflight · feedback', desc: 'Guidance: loop-engineering helpers for goal-verified runs.' },
  { group: 'regin pattern', sub: 'promote · import · import-dir · embed · route · enable-rules · rules-doctor', desc: 'Guidance: pattern lifecycle — import, promote to versioned skill bundles, route queries.' },
  { group: 'regin skills', sub: 'list · check · pull · push · undeploy', desc: 'Guidance: sync packaged skills with the active provider’s skills directory.' },
  { group: 'regin rules', sub: 'check · run · list · index · deploy · disable · enable · list-disabled · bundles · trust · untrust', desc: 'Guidance: rule-engine management and one-off rule runs.' },
  { group: 'regin hooks', sub: 'status · install · remove · adopt · repair', desc: 'Guidance: inspect, install and repair regin’s agent hook wiring.' },
  { group: 'regin schema', sub: 'list · bootstrap · validate · diff', desc: 'Guidance: PostToolUse payload schemas.' },
  { group: 'regin memory', sub: 'recall · read · list · remember · supersede · forget · reflect · index-root · index-expand · index-fetch · export-tree · …', desc: 'Guidance: inspect and curate the cross-session agent memory store.' },
  { group: 'regin topics', sub: 'bootstrap · scan · list · promote · drift · evolve · propose · proposal-* · wiki-debt · route · audit · …', desc: 'Guidance: the per-repo topic graph — proposals, drift detection, wiki debt.' },
  { group: 'regin wiki', sub: 'index', desc: 'Guidance: the dense index over accepted topic wiki pages.' },
  { group: 'regin users', sub: 'init-db · list · create · reset-password · set-role · delete', desc: 'Account management. The first user created gets the admin role automatically.' },
]

export const TROUBLESHOOTING = [
  { symptom: '“Database not initialized”', fix: 'Run regin init, then regin rebuild.' },
  { symptom: 'Login not working after a DB rebuild', fix: 'Accounts are local — re-create with regin users create <name> <pass> --role admin.' },
  { symptom: 'Forgot the admin password', fix: 'regin users reset-password <name> <newpass> (no old password needed).' },
  { symptom: 'Port already in use', fix: 'regin serve --port 8322.' },
  { symptom: 'Dashboard is empty — no traces, rules never fire', fix: 'The hook dispatcher isn’t wired into your agent (not a CLI step). Install it at Settings → Hook Installers — see Getting Started → Activate the hooks.' },
]
