// Grounded in `regin --help` and each group's own --help. COMMAND_GROUPS lists
// every group command `regin --help` prints, in the four-layer order; a test
// holds it to the real CLI.

export const COMMAND_GROUPS = [
  { group: 'regin feature', sub: 'new · spec · stage · ledger · attribute · unattribute · image · ship · reopen · abandon · refresh', desc: 'Workbench: where a feature stands in the pipeline, and what it cost.' },
  { group: 'regin task', sub: 'add · list · show · comment · finish · delete · slice-check · tail · transcribe · commands', desc: 'Workbench: capture and inspect tasks; cut a spec’s slice table into tasks; report a run finished.' },
  { group: 'regin defects', sub: 'open · list · rounds · attribute · edit · merge · fix-item · image · close · reopen', desc: 'Workbench: the defect ledger — what a feature got wrong, and what escaped.' },
  { group: 'regin messages', sub: 'list · read · stats · prune', desc: 'Runs: inspect and prune the send_to_user inbox.' },
  { group: 'regin bridge', sub: 'listen', desc: 'Runs: the agent bridge — a session’s channel for steering messages.' },
  { group: 'regin session', sub: 'context', desc: 'Runs: what a session’s compaction must keep — its feature, task and finish command.' },
  { group: 'regin usage', sub: 'measure', desc: 'Runs: raise a rate-limit alert from a session’s usage measurement, once per window and threshold.' },
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
  { group: 'regin mcp', sub: 'register', desc: 'Guidance: register regin’s MCP servers (memory, regin) with agent providers.' },
  { group: 'regin schema', sub: 'list · bootstrap · validate · diff', desc: 'Guidance: PostToolUse payload schemas.' },
  { group: 'regin memory', sub: 'recall · read · list · remember · supersede · forget · reflect · index-root · index-expand · index-fetch · export-tree · …', desc: 'Guidance: inspect and curate the cross-session agent memory store.' },
  { group: 'regin topics', sub: 'bootstrap · scan · list · promote · drift · evolve · propose · proposal-* · wiki-debt · route · audit · …', desc: 'Guidance: the per-repo topic graph — proposals, drift detection, wiki debt.' },
  { group: 'regin wiki', sub: 'index', desc: 'Guidance: the dense index over accepted topic wiki pages.' },
  { group: 'regin users', sub: 'init-db · list · create · reset-password · set-role · delete', desc: 'Account management. The first user created gets the admin role automatically.' },
]
