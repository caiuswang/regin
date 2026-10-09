// Every artifact below is real output from regin running on its own repo —
// nothing invented. The Workbench artifact is the Pixel theme's feature (F139,
// shipped 2026-10-07); the Runs artifact is this website's own feature (F153,
// 2026-10-09); the trace numbers and the hook refusal are from
// the session that built the first version of the site (trace 3b3adbbc,
// 2026-07-16).

export const LAYERS = [
  {
    icon: 'layers',
    name: 'Workbench',
    summary: 'A feature carries its spec and acceptance tests, is cut into tasks, and is held to a done-claim. Defects are declared against it.',
    to: '/pipeline',
  },
  {
    icon: 'activity',
    name: 'Runs',
    summary: 'Tasks are launched as agent runs on Claude or Kimi, followed live, steered, stopped — and report back to an inbox.',
    to: '/runs',
  },
  {
    icon: 'database',
    name: 'Observability',
    summary: 'Every session is traced: tool calls, edits, tokens, time, grades. Find out why a run went sideways.',
    to: '/dashboard',
  },
  {
    icon: 'shield',
    name: 'Guidance',
    summary: 'Patterns, skills, hook-enforced rules and cross-session memory that keep the agent on-spec while it works.',
    to: '/guidance',
  },
]

export const LOOP = ['Spec', 'Run', 'Watch', 'Count']

export const PILLARS = [
  {
    layer: 'Workbench',
    icon: 'layers',
    title: 'A feature is a row, and done is a claim',
    body: 'The spec lives on the feature row, its acceptance items are named tests, and the spec is cut into slices that become tasks with dependencies. Calling it done is a done-claim; anything found wrong afterwards is a defect declared against the feature. The number it is held to is rounds-to-done — counted from declarations, never inferred.',
    link: { to: '/pipeline', label: 'How the pipeline works' },
    artifact: 'code',
    code: '$ regin feature ledger 139\n#139  重新设计Pipeline操作界面-游戏像素风格\nshipped  rounds=1  open_defects=0  escapes=0\n  completed and never walked back\n\ncost (upper bound)  $140.43 across 28 sessions\nwork time  7h 17m\nhuman prompts  0 across 0 human sessions\n\nsessions\n  85fcf73e  S1 — Seam                $6.20\n  cc2e7fcb  S2 — Pixel foundation    $4.38\n  fad49391  S4 — Tasks              $11.44\n  ff040c3d  S3 — Overview            $8.46\n…',
    codeCaption: 'The ledger of the feature that built the dashboard’s Pixel theme (date, kind and round columns trimmed; 4 of its 28 sessions shown, the first four slices): 15 slices and 12 defects later it shipped once and was never walked back. The Pipeline page walks through it step by step.',
  },
  {
    layer: 'Runs',
    icon: 'activity',
    title: 'Launch the run, then steer it',
    body: 'A task is armed with a provider, a model and a launch mode, then started from the dashboard — Claude through the Agent SDK, Kimi over ACP or tmux. /live follows each running session: what it is doing, the question it is blocked on, with steer and Stop. Agents report progress and results back through the inbox.',
    link: { to: '/runs', label: 'How runs are launched' },
    artifact: 'code',
    code: '$ regin task list --status ""\n#473   armed     spec   S5 — integrate + verify\n#472   armed     spec   S4 — Website Pipeline + Runs pages\n#471   running   spec   S3 — Website copy + IA\n#470   running   spec   S2 — Setup + CLI reference\n#469   done      spec   S1 — Positioning text',
    codeCaption: 'The same feature’s slices on 2026-10-09: S1 done, S2 and S3 running in parallel, S4 and S5 armed and waiting on their dependencies.',
  },
  {
    layer: 'Observability',
    icon: 'database',
    title: 'A session viewer, not a scrollback',
    body: 'Hooks and transcripts already produce everything you need to understand a session — the terminal just flattens it. regin ingests the stream into spans you can filter by tool, agent, and phase, replay turn by turn, and roll up into token, cost and time attribution.',
    link: { to: '/architecture#tracing', label: 'How capture works' },
    artifact: 'stats',
    stats: ['552 spans', '8 turns', '16 tools', '$5.59 of $55.14 tool-attributed'],
    statsCaption: 'The first website build session, captured at turn 8. The split is the point: $5.59 was attributable to specific tool calls; the rest is shared context. A live session keeps counting, so captures taken moments apart drift upward.',
  },
  {
    layer: 'Guidance',
    icon: 'shield',
    title: 'Rules the agent can’t drift past',
    body: 'Conventions written in prose get forgotten by the next session. Patterns and skills tell the agent how before it acts; rule engines live inside hooks, so every edit is checked in flight and the agent is pushed back on-spec in the same turn. Lessons from past sessions are recalled when a new one touches the same ground.',
    link: { to: '/guidance', label: 'Patterns, rules and memory' },
    artifact: 'code',
    code: 'PostToolUse ▸ rule-check:\n2 violation(s) in SiteHeader.vue\n- focus_visible_styling_coverage (warn):\n  interactive element at line 15\n  lacks focus-visible styling\n- prefer_button_primitive (warn):\n  use the <Button> primitive,\n  not a raw <button>\nFix these before claiming\nthe edit is complete.',
    codeCaption: 'A real push-back from the session that built the first version of this site — the hook flagged both problems and the agent had to fix them before moving on.',
  },
]

export const SECONDARY = [
  {
    title: 'Topic wikis',
    body: 'Per-repo knowledge as a reviewable graph, kept honest by drift detection.',
    to: '/topics',
  },
  {
    title: 'Mobile remote control',
    body: 'Tail a running session from your phone; answer its prompts over the agent bridge.',
    to: '/configuration#agent-bridge',
    experimental: true,
  },
]
