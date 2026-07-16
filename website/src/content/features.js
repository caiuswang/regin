export const FEATURES = [
  {
    icon: 'book-open',
    title: 'Patterns & skills',
    body: 'Local procedure guides for recurring implementation shapes, tracked and deployed into the active provider’s skills directory. Proven patterns get promoted to versioned, shareable skill bundles surfaced only when their triggers match.',
    link: { to: '/architecture#feedforward', label: 'How the feedforward layer works' },
  },
  {
    icon: 'shield',
    title: 'Rule engines',
    body: 'Real hooks that intercept tool calls, edits, and prompts in flight and evaluate them against your invariants — pushing the agent back on-spec or blocking the action outright. Force, not suggestion.',
    link: { to: '/architecture#rule-engines', label: 'GritQL, Radon & bundle engines' },
  },
  {
    icon: 'activity',
    title: 'Session tracing',
    body: 'A real session viewer over the hook/transcript event stream: filterable by tool, agent, and phase; replayable; with token rollups so you can diagnose why a session went sideways.',
    link: { to: '/architecture#tracing', label: 'Trace internals' },
  },
  {
    icon: 'database',
    title: 'Agent memory',
    body: 'Lessons from past sessions — root causes, gotchas, decisions — distilled into a curated cross-session store and recalled on demand, matched to what the agent is actually doing.',
    link: { to: '/architecture#memory', label: 'Cross-session learning' },
  },
  {
    icon: 'git-branch',
    title: 'Topic wikis',
    body: 'Per-repo knowledge organized as a reviewable graph of topics. Drafted by a tool-using agent, approved by you, kept honest by content-drift detection, and routed into context on demand.',
    link: { to: '/architecture#topics', label: 'The topic graph' },
  },
  {
    icon: 'smartphone',
    title: 'Mobile remote control',
    body: 'A phone-sized /live card that tails a running session. With the agent bridge enabled, it doubles as a remote: answer permission prompts and steer the agent from your phone.',
    link: { to: '/configuration#agent-bridge', label: 'Enabling the agent bridge' },
    experimental: true,
  },
]
