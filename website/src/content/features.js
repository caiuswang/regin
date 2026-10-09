// Every artifact below is real output from regin running on its own repo —
// nothing invented. The Workbench artifact is the Pixel theme's feature (F139,
// shipped 2026-10-07); the Runs artifact is this website's own feature (F153,
// 2026-10-09); the trace numbers and the hook refusal are from
// the session that built the first version of the site (trace 3b3adbbc,
// 2026-07-16). The numbers are dated snapshots, copied from the prototype.

import taskgraphShot from '../assets/shots/pixel/taskgraph.webp'
import taskboardShot from '../assets/shots/pixel/taskboard.webp'
import traceShot from '../assets/shots/pixel/trace.webp'
import handlersShot from '../assets/shots/pixel/handlers.webp'

// The hero's loop tiles, one per layer, in the order you meet them.
export const LAYERS = [
  {
    name: 'Workbench',
    code: '1-1',
    verb: 'SPEC',
    anchor: 'workbench',
    to: '/pipeline',
    summary: 'Features with specs and acceptance tests, cut into tasks, held to a done-claim.',
  },
  {
    name: 'Runs',
    code: '1-2',
    verb: 'RUN',
    anchor: 'runs',
    to: '/runs',
    summary: 'Launch tasks on Claude or Kimi, follow them live, steer or stop.',
  },
  {
    name: 'Observability',
    code: '1-3',
    verb: 'WATCH',
    anchor: 'observability',
    to: '/dashboard',
    summary: 'Every session traced: tool calls, edits, tokens, time, cost.',
  },
  {
    name: 'Guidance',
    code: '1-4',
    verb: 'COUNT',
    anchor: 'guidance',
    to: '/guidance',
    summary: 'Hook-enforced rules and memory keep the agent on-spec.',
  },
]

// A HUD stat is a run of segments; a `[text, tone]` pair is a bold value.
// A pre line is a run of segments; a `[text, tone]` pair is coloured.
export const PILLARS = [
  {
    layer: 'Workbench',
    hud: 'WORKBENCH · SPEC',
    stats: [
      ['F139 ROUNDS ', ['1', 'green']],
      ['ESCAPES ', ['0', 'green']],
      ['COST ', ['$140.43', 'ink']],
      ['TIME ', ['7H17M', 'ink']],
    ],
    title: 'A feature is a row, and done is a claim.',
    body: 'The spec lives on the feature row, its acceptance items are named tests, and it\'s cut into slices that become tasks with dependencies. Anything found wrong after the done-claim is a defect declared against the feature. The score is **rounds-to-done** — counted from declarations, never inferred.',
    pre: [
      [['$ ', 'dim'], 'regin feature ledger 139'],
      ['#139  重新设计Pipeline操作界面-游戏像素风格'],
      [['shipped  rounds=1  open_defects=0  escapes=0', 'green']],
      ['  completed and never walked back'],
      [''],
      ['cost (upper bound)  $140.43 across 28 sessions'],
      ['work time  7h 17m'],
      ['human prompts  0 across 0 human sessions'],
    ],
    caption: 'The ledger of the feature that built the dashboard\'s Pixel theme: 15 slices and 12 defects later it shipped once and was never walked back.',
    link: { to: '/pipeline', label: 'HOW THE PIPELINE WORKS →' },
    shot: {
      src: taskgraphShot,
      alt: 'Task graph scoped to feature #139: slices S1 to S7 as tasks, all done, connected by dependency edges.',
      cap: 'QUEST LOG · F139 SLICES AS A TASK GRAPH',
    },
  },
  {
    layer: 'Runs',
    hud: 'RUNS · RUN',
    stats: [
      ['DONE ', ['1', 'green']],
      ['RUNNING ', ['2', 'ink']],
      ['ARMED ', ['2', 'gold']],
      ['AGENTS ', ['CLAUDE · KIMI', 'ink']],
    ],
    title: 'Launch the run, then steer it.',
    body: 'Arm a task with a provider, model and launch mode, then start it from the dashboard — Claude through the Agent SDK, Kimi over ACP or tmux. **/live** follows each session: what it\'s doing, the question it\'s blocked on, with steer and Stop. Agents report back to an inbox.',
    pre: [
      [['$ ', 'dim'], 'regin task list --status ""'],
      ['#473   ', ['armed  ', 'gold'], '   spec   S5 — integrate + verify'],
      ['#472   ', ['armed  ', 'gold'], '   spec   S4 — Website Pipeline + Runs pages'],
      ['#471   ', ['running', 'ink'], '   spec   S3 — Website copy + IA'],
      ['#470   ', ['running', 'ink'], '   spec   S2 — Setup + CLI reference'],
      ['#469   ', ['done   ', 'green'], '   spec   S1 — Positioning text'],
    ],
    caption: 'This website\'s own feature on 2026-10-09: S2 and S3 running in parallel, S4 and S5 waiting on their dependencies.',
    link: { to: '/runs', label: 'HOW RUNS ARE LAUNCHED →' },
    shot: {
      src: taskboardShot,
      alt: 'The Pipeline task board in the Pixel theme: Ideas, Armed, Running and Done columns, with two live runs that can be watched or stopped.',
      cap: 'QUEST LOG · IDEAS → ARMED → RUNNING → DONE',
    },
  },
  {
    layer: 'Observability',
    hud: 'OBSERVABILITY · WATCH',
    stats: [
      ['TRACE ', ['3B3ADBBC', 'ink'], ' · TURN ', ['8', 'ink']],
    ],
    title: 'A session viewer, not a scrollback.',
    body: 'Hooks and transcripts already produce everything you need — the terminal just flattens it. regin ingests the stream into spans you can filter by tool, agent and phase, replay turn by turn, and roll up into token, cost and time attribution.',
    tiles: [
      { label: 'SPANS', value: '552' },
      { label: 'TOOLS', value: '16' },
    ],
    meter: { label: 'TOOL-ATTRIBUTED COST', part: 5.59, whole: 55.14 },
    caption: 'The first website build session at turn 8. The split is the point: $5.59 traces to specific tool calls; the rest is shared context.',
    link: { to: '/dashboard', label: 'HOW CAPTURE WORKS →' },
    shot: {
      src: traceShot,
      alt: 'A session trace: spans, duration, context peak, tokens, cost and turns, with an activity timeline across eight prompts.',
      cap: 'SESSION TRACE · REPLAY TURN BY TURN',
    },
  },
  {
    layer: 'Guidance',
    hud: 'GUIDANCE · COUNT',
    stats: [
      ['VIOLATIONS ', ['2', 'gold']],
      ['FIXED ', ['SAME TURN', 'green']],
    ],
    title: 'Rules the agent can\'t drift past.',
    body: 'Conventions in prose get forgotten by the next session. Patterns and skills tell the agent how before it acts; rule engines live inside hooks, so every edit is checked in flight and pushed back on-spec in the same turn. Lessons from past sessions are recalled when a new one touches the same ground.',
    pre: [
      [['PostToolUse ▸ rule-check:', 'dim'], ['2 violation(s) in SiteHeader.vue', 'gold']],
      ['- focus_visible_styling_coverage (warn):'],
      ['  interactive element at line 15'],
      ['  lacks focus-visible styling'],
      ['- prefer_button_primitive (warn):'],
      ['  use the <Button> primitive, not a raw <button>'],
      [['Fix these before claiming the edit is complete.', 'ink']],
    ],
    caption: 'A real push-back from the session that built the first version of this site — the agent had to fix both before moving on.',
    link: { to: '/guidance', label: 'PATTERNS, RULES AND MEMORY →' },
    shot: {
      src: handlersShot,
      alt: 'Settings → Hook Handlers for Claude Code: 46 handlers with their events, kind (gate, trace, enrich) and enabled status.',
      cap: '46 HOOK HANDLERS · GATE · TRACE · ENRICH',
    },
  },
]
