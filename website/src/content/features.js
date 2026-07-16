// Every artifact below is real output from regin running on its own repo —
// nothing invented. The hook refusal, trace numbers, and recalled lesson are
// from the session that built this website (trace 3b3adbbc, 2026-07-16).

export const PILLARS = [
  {
    icon: 'shield',
    title: 'Rules the agent can’t drift past',
    body: 'Conventions written in prose get forgotten by the next session. regin’s rule engines live inside hooks, so every edit is checked in flight — the agent gets pushed back on-spec in the same turn, or the action is refused outright. Force, not suggestion.',
    link: { to: '/architecture#rule-engines', label: 'How the rule engines work' },
    artifact: 'code',
    code: 'PostToolUse ▸ rule-check:\n2 violation(s) in SiteHeader.vue\n- focus_visible_styling_coverage (warn):\n  interactive element at line 15\n  lacks focus-visible styling\n- prefer_button_primitive (warn):\n  use the <Button> primitive,\n  not a raw <button>\nFix these before claiming\nthe edit is complete.',
    codeCaption: 'A real push-back from the session that built this page — the hook flagged both problems and the agent had to fix them before moving on.',
  },
  {
    icon: 'activity',
    title: 'A session viewer, not a scrollback',
    body: 'Hooks and transcripts already produce everything you need to understand a session — the terminal just flattens it. regin ingests the stream into spans you can filter by tool, agent, and phase, replay turn by turn, and roll up into token and cost attribution.',
    link: { to: '/architecture#tracing', label: 'Trace internals' },
    artifact: 'stats',
    stats: ['552 spans', '8 turns', '16 tools', '$5.59 of $55.14 tool-attributed'],
    statsCaption: 'This page’s own build session, captured at turn 8 — the same moment as the trace above. The split is the point: $5.59 was attributable to specific tool calls; the rest is shared context. A live session keeps counting, so captures taken moments apart drift upward.',
  },
  {
    icon: 'database',
    title: 'Lessons that survive the session',
    body: 'When a session learns something the hard way — a gotcha, a root cause, a decision that mattered — regin distills it into a lesson and recalls it when a future session touches the same ground. The store is curated: lessons are reinforced when they help and retired when they stop.',
    link: { to: '/architecture#memory', label: 'How memory compounds' },
    artifact: 'shot',
    shotAlt: 'The regin memory browser: cross-session memories filterable by lessons, gotchas, facts and procedures — the newest lesson written by this website’s own build session.',
    shotCaption: 'The live memory store — the top lesson was written by the session that built this website.',
  },
]

export const SECONDARY = [
  {
    title: 'Patterns & skills',
    body: 'Local procedure guides, promoted to versioned skill bundles when they earn it.',
    to: '/architecture#feedforward',
  },
  {
    title: 'Topic wikis',
    body: 'Per-repo knowledge as a reviewable graph, kept honest by drift detection.',
    to: '/architecture#topics',
  },
  {
    title: 'Mobile remote control',
    body: 'Tail a running session from your phone; answer its prompts over the agent bridge.',
    to: '/configuration#agent-bridge',
    experimental: true,
  },
]
