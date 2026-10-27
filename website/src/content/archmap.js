// The Architecture diagram: regin's four layers as bands of parts, the agent
// they wrap, the server under all of them, and flows that light a path through
// them. Mirrors ARCHITECTURE.md "How It Works" and regin-plugin/README.md.

export const YOU = {
  id: 'you', name: 'You', sub: 'browser · phone',
  detail: 'The operator. The Pipeline overview is the home page, /live steers running sessions, /trace replays them, and the Inbox collects what agents report.',
}

export const AGENT = {
  id: 'agent', name: 'Your agent', sub: 'the coding agent you already use',
  providers: ['Claude Code', 'Kimi Code', 'CodeBuddy Code'],
  detail: 'The coding agent you already use, working on the slice’s branch: Claude Code, Kimi Code or CodeBuddy Code today, with WorkBuddy traced too. Each is a provider adapter in lib/providers, so the next agent is an adapter, not a rewrite. regin wraps the agent; it does not replace it.',
}

export const SERVE = {
  id: 'serve', name: 'regin serve', sub: 'Flask JSON API · SQLite db/regin.db · Vue SPA',
  detail: 'One API over one store — features, tasks, defects, repos, patterns, trace — rendered by the Vue SPA. Every layer reads and writes here; agent memory keeps a database of its own.',
}

export const BANDS = [
  { id: 'workbench', name: 'Workbench', role: 'holds the work', parts: [
    { id: 'features', name: 'Features', sub: 'spec on the row · event log',
      detail: 'A feature carries its spec on the row: intent, design, Gherkin acceptance, a slice table. Stage and rounds-to-done are queries over an append-only event log. A workspace scopes it all to one project.' },
    { id: 'dag', name: 'Task DAG', sub: 'slices → tasks · requires / after',
      detail: 'slice-check, tail and transcribe turn the slice table into tasks with dependency edges. A finished run releases the tasks waiting on it.' },
    { id: 'defects', name: 'Defects', sub: 'blamed on a ship · closed with a check',
      detail: 'Anything wrong is declared against the feature. One declared after a ship is an escape and costs a round; closing one names the check that would have caught it.' },
  ] },
  { id: 'runs', name: 'Runs', role: 'launch and steer', port: 'launch · steer', parts: [
    { id: 'runctl', name: 'Run control', sub: 'arm · Agent SDK · ACP · tmux',
      detail: 'Arming a ready task picks the provider, worktree and skills. Claude Code runs through the Agent SDK inside regin serve; Kimi Code and CodeBuddy Code over ACP; any of them can run in a tmux pane. A provider that declares no launch mode cannot be armed. A run settles on the agent’s own task finish.' },
    { id: 'bridge', name: 'Agent bridge', sub: '/live · mod channel · tmux',
      detail: 'Delivers your message into a live session — through the regin-bridge mod’s channel when it has one, else as guarded keystrokes into its tmux pane. Questions and permission asks are answered from /live.' },
    { id: 'mcp', name: 'regin MCP', sub: 'send_to_user · pipeline tools',
      detail: 'One MCP server for any agent: send_to_user for progress, lessons and blockers, plus the feature, task and defect tools — so the agent reads and writes the pipeline itself.' },
  ] },
  { id: 'observe', name: 'Observability', role: 'record what happened', port: 'hooks · mods', parts: [
    { id: 'hooks', name: 'hook_manager', sub: 'one dispatcher per hook event',
      detail: 'Every hook event the agent fires — Claude Code, Kimi, CodeBuddy, WorkBuddy — enters here; it runs each registered handler and merges their answers. Tracing, rule gates and memory injection all ride on it.' },
    { id: 'mods', name: 'Mods', sub: 'regin-plugin · lineage · verdicts',
      detail: 'Claude Code plugins regin ships, so Claude Code sessions only: bridge, permit, lineage, meter, band, compact, ask, attribution. They report what no hook carries — subagent lineage, permission verdicts, rate limits, the run’s phase.' },
    { id: 'trace', name: 'Trace', sub: 'spans · turns · cost · grades',
      detail: 'Hook events and SDK messages project onto one span shape. Spans are append-only; de-duplication and ordering happen on read. Turns, tokens, cost and post-hoc grades roll up from them.' },
  ] },
  { id: 'guide', name: 'Guidance', role: 'shape what it does', port: 'skills · rules · recall', parts: [
    { id: 'skills', name: 'Skills', sub: 'patterns → provider skills',
      detail: 'Patterns are procedure guides; promoted, they deploy into the provider’s skills directory and load only when their triggers match.' },
    { id: 'rules', name: 'Rule engines', sub: 'GritQL · Radon · bundles',
      detail: 'The enforced half, in PostToolUse: GritQL structural rules, Radon complexity, bundle packs such as this site’s Vue/CSS conventions. A violation goes back to the agent.' },
    { id: 'memory', name: 'Memory', sub: 'lessons · own DB · recall',
      detail: 'Lessons in their own SQLite database: captured from send_to_user, consolidated offline, superseded when stale, injected into matching prompts.' },
  ] },
]

export const PARTS = Object.fromEntries([YOU, AGENT, SERVE, ...BANDS.flatMap((b) => b.parts.map((p) => ({ ...p, band: b.id })))]
  .map((p) => [p.id, p]))

export const FLOWS = [
  { id: 'launch', name: 'Launch a task', steps: [
    ['you', 'You arm a ready task on the Pipeline overview.'],
    ['dag', 'Everything it requires is done, so it leaves the DAG.'],
    ['runctl', 'Provider, worktree and skills are set; the run launches.'],
    ['agent', 'The agent starts on the slice’s branch.'],
  ] },
  { id: 'watch', name: 'Watch it work', steps: [
    ['agent', 'Every tool call fires a hook event.'],
    ['hooks', 'hook_manager runs each handler and merges the answers.'],
    ['trace', 'The event lands as a span — append-only, merged on read.'],
  ] },
  { id: 'mods', name: 'Mods report', steps: [
    ['agent', 'Some facts never reach a hook: who spawned a subagent, why a call was allowed.'],
    ['mods', 'regin’s mods hand them over.'],
    ['trace', 'They land on the same spans.'],
  ] },
  { id: 'steer', name: 'Steer from your phone', steps: [
    ['you', 'You type a follow-up on /live.'],
    ['bridge', 'The bridge delivers it: mod channel first, tmux keystrokes otherwise.'],
    ['agent', 'The agent takes it as its next prompt.'],
  ] },
  { id: 'guard', name: 'Guard an edit', steps: [
    ['agent', 'The agent edits a file.'],
    ['hooks', 'PostToolUse fires.'],
    ['rules', 'GritQL, Radon and bundle packs check the edit.'],
    ['agent', 'A violation comes back as feedback, and the agent fixes it.'],
  ] },
  { id: 'recall', name: 'Recall a lesson', steps: [
    ['agent', 'You send the agent a prompt.'],
    ['hooks', 'UserPromptSubmit fires.'],
    ['memory', 'Matching lessons are ranked and picked.'],
    ['agent', 'They ride in as recalled experience.'],
  ] },
  { id: 'skills', name: 'Load a skill', steps: [
    ['skills', 'A promoted pattern sits in the provider’s skills directory.'],
    ['agent', 'The agent loads it when its triggers match the task.'],
  ] },
  { id: 'report', name: 'Report back', steps: [
    ['agent', 'The agent calls send_to_user, then task finish.'],
    ['mcp', 'The regin MCP server takes both.'],
    ['dag', 'The run settles; tasks waiting on it are released.'],
    ['you', 'The message reaches your Inbox — and Telegram, Lark or a webhook if it’s urgent.'],
  ] },
  { id: 'ship', name: 'Ship and count', steps: [
    ['you', 'You run feature ship: the done-claim.'],
    ['features', 'shipped is appended to the feature’s event log.'],
    ['defects', 'A defect declared afterwards is an escape: one more round.'],
    ['dag', 'Its fix task joins the DAG, and the loop runs again.'],
  ] },
]
