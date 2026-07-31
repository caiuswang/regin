// Shared /inbox fixtures. Every inbox test mocks the API: a click on a row is
// a write (`POST /agent-messages/read`), so a spec pointed at the real feed
// permanently marks the operator's messages read.

export const isoMinutesAgo = (min) => {
  const d = new Date(Date.now() - min * 60_000)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T` +
    `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

export function msg(over = {}) {
  return {
    id: 1, span_id: null, trace_id: 'trace-dead', msg_type: 'result',
    msg_key: null, title: 'A result', body: 'Body text.', links: null,
    created_at: isoMinutesAgo(5), read_at: isoMinutesAgo(1), dismissed_at: null,
    pinned: false, version: 1, agent_type: null, session_title: 'Some session',
    is_test: false, webhook_status: null, acked_at: null,
    updated_at: isoMinutesAgo(1), ...over,
  }
}

// A card the agent is genuinely parked on: decision key + unread + a session
// seen inside the liveness window.
export const liveDecision = (id, over = {}) => msg({
  id, trace_id: 'trace-live', msg_type: 'blocker', msg_key: 'permission-pending',
  title: 'The agent is asking you a question', read_at: null,
  session_title: 'Clarify session intentions and goals',
  body: 'Which way should this go?\n• Option one\n• Option two',
  ...over,
})

// Same shape, but its session died long ago — `events.resolve` never fired, so
// the card is still un-dismissed. This must NOT read as a live decision.
export const staleDecision = (id, over = {}) => msg({
  id, trace_id: 'trace-dead', msg_type: 'blocker', msg_key: 'permission-pending',
  title: 'The agent is asking you a question', read_at: null,
  body: 'Long-abandoned question?\n• Yes\n• No', ...over,
})

// `_format_permission` (not `_format_question`): prose plus an italic option
// count, and NO bullets at all. The panel must still render it readably.
export const bulletlessPermission = (id, over = {}) => msg({
  id, trace_id: 'trace-live', msg_type: 'blocker', msg_key: 'permission-pending',
  title: 'Permission needed: Bash', read_at: null,
  body: 'The agent needs approval to run **Bash**.\n_2 option(s) — approve or deny in your session._',
  ...over,
})

const TYPE_SAMPLES = [
  ['progress', 'Rebuilding the topic index', 'Walked 3 of 7 buckets. **Next:** re-rank the leaves.\n\n```\nbucket: webui   42 memories\nbucket: trace   61 memories\n```'],
  ['note', 'Left the flag off by default', 'The new sweep is gated behind `topics.evolution_enabled` until the judge is calibrated.'],
  ['lesson', 'Restart vite after editing vite.config proxy rules', 'A proxy edit is read once at boot. Without a restart the dev server keeps forwarding `/api` to the old target and every call 404s while the config on disk looks right.'],
  ['result', 'Test suites 14x / 2.3x faster', '**Committed** `4cc5cb21`.\n\nPython 506s → 35s (14×), E2E 3.2m → 1.4m (2.3×). Profiling said the cost was an autouse fixture, not any single hot test.'],
  ['summary', 'CAI-122 done — merged via cherry-pick', 'Three commits landed on master. Linear moved to Done; the superseded PR is closed.'],
  ['warning', 'Schema drift: 372 columns unaccounted for', 'The baseline in `db/schema.sql` and the Alembic head disagree. Fresh installs and migrated installs will diverge until both are edited.'],
  ['blocker', 'Grading thresholds need sign-off', 'Both wiring paths are already implemented behind a flag, so this is purely a product call — no extra engineering either way.'],
]

// A feed with every message type represented, newest first.
export function inboxFixture({ withDecisions = false } = {}) {
  const rows = TYPE_SAMPLES.map(([type, title, body], i) => msg({
    id: 100 + i, trace_id: `trace-${i}`, msg_type: type, title, body,
    created_at: isoMinutesAgo(20 + i * 7), agent_type: i % 3 ? null : 'general-purpose',
    session_title: 'Understand agent-sdk branches',
    read_at: i < 2 ? null : isoMinutesAgo(2),
  }))
  if (!withDecisions) return rows
  return [
    liveDecision(1, {
      title: 'How should the subprocess launch be verified?',
      body: 'Acceptance item 3 needs the real subprocess launch exercised at least once '
        + 'before I can close this out. How should I handle it?\n'
        + '• Run the Haiku smoke test\n• Skip it — ship stub-tested\n• I\'ll run it myself',
    }),
    liveDecision(2, {
      title: 'Grading thresholds need sign-off',
      session_title: 'Rebase from main and fix conflicts',
      body: 'Both wiring paths are already implemented behind a flag, so this is purely a '
        + 'product call.\n• Ship at 0.8\n• Hold for review',
    }),
    ...rows,
  ]
}

// Mocks the two GETs the view reads plus every write it can issue, and returns
// a `writes` array so a test can assert what would have been sent.
export async function mockInbox(page, { messages = [], liveTraces = ['trace-live'] } = {}) {
  const writes = []
  await page.route('**/api/agent-messages/inbox*', (route) => route.fulfill({
    json: { messages, unread_count: messages.filter(m => !m.read_at).length },
  }))
  await page.route('**/api/sessions?active=active*', (route) => route.fulfill({
    json: {
      sessions: [
        ...liveTraces.map(t => ({ trace_id: t, status: 'active', last_seen: isoMinutesAgo(1) })),
        // Present and still flagged `active`, but long unseen — the case a
        // status-only gate gets wrong.
        { trace_id: 'trace-dead', status: 'active', last_seen: isoMinutesAgo(600) },
      ],
    },
  }))
  for (const p of ['**/api/agent-messages/read', '**/api/agent-messages/read-all',
    '**/api/agent-messages/*/dismiss', '**/api/agent-messages/unread-count']) {
    await page.route(p, (route) => {
      const req = route.request()
      if (req.method() !== 'GET') writes.push({ url: req.url(), body: req.postData() })
      return route.fulfill({ json: { marked: 1, dismissed: true, count: 0 } })
    })
  }
  return writes
}
