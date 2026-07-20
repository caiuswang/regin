// Grounded in docs/setup.md §CLI reference and CLAUDE.md §Commands.

export const CORE_COMMANDS = [
  { cmd: 'regin init', desc: 'Initialize the database and directories. --force does a true local reset (recreates the DB, clears local state, rotates the JWT secret).' },
  { cmd: 'regin doctor', desc: 'Check the environment: missing CLI tools, provider capabilities, agent-bridge preconditions.' },
  { cmd: 'regin discover', desc: 'Register sibling source repositories.' },
  { cmd: 'regin add-repo <path>', desc: 'Register one repository by path (must be a git working tree).' },
  { cmd: 'regin remove-repo <name>', desc: 'Unregister a repository.' },
  { cmd: 'regin search <keyword>', desc: 'Search patterns.' },
  { cmd: 'regin tags --index', desc: 'Generate tag / repo indexes.' },
  { cmd: 'regin rebuild', desc: 'Rebuild the local database from git-tracked files.' },
  { cmd: 'regin migrate', desc: 'Sync an existing database to the current schema (alembic upgrade head).' },
  { cmd: 'regin serve', desc: 'Start the web dashboard on port 8321. --port changes the port; --host 0.0.0.0 exposes it beyond localhost.' },
]

export const COMMAND_GROUPS = [
  { group: 'regin users', sub: 'init-db · list · create · reset-password · set-role · delete', desc: 'Account management. The first user created gets the admin role automatically.' },
  { group: 'regin skills', sub: 'list · check · pull · push · undeploy', desc: 'Sync packaged skills with the active provider’s skills directory.' },
  { group: 'regin pattern', sub: 'promote · import · import-dir · embed · route · enable-rules · rules-doctor', desc: 'Pattern lifecycle: import, promote to versioned skill bundles, route queries.' },
  { group: 'regin rules', sub: 'check · index · deploy · list-disabled · disable · enable · run · list', desc: 'Rule-engine management and one-off rule runs.' },
  { group: 'regin topics', sub: 'bootstrap · scan · list · promote · drift · evolve · propose · proposal-*', desc: 'Topic graph: proposals, drift detection, wiki debt.' },
  { group: 'regin trace', sub: 'backfill-tokens · resolve-repos · backfill-costs', desc: 'Trace maintenance and backfills.' },
  { group: 'regin memory', sub: 'list · recall · supersede · forget · consolidate-skills · link-topics', desc: 'Inspect and curate the cross-session agent memory store.' },
]

export const TROUBLESHOOTING = [
  { symptom: '“Database not initialized”', fix: 'Run regin init, then regin rebuild.' },
  { symptom: 'Login not working after a DB rebuild', fix: 'Accounts are local — re-create with regin users create <name> <pass> --role admin.' },
  { symptom: 'Forgot the admin password', fix: 'regin users reset-password <name> <newpass> (no old password needed).' },
  { symptom: 'Port already in use', fix: 'regin serve --port 8322.' },
  { symptom: 'Dashboard is empty — no traces, rules never fire', fix: 'The hook dispatcher isn’t wired into your agent (not a CLI step). Install it at Settings → Hook Installers — see Getting Started → Activate the hooks.' },
]
