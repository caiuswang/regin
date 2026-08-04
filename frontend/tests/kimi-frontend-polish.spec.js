/**
 * Kimi/Claude parity on the frontend surfaces a human actually reads.
 *
 * Kimi trace ids are literally `session_<uuid>`, so a raw `slice(0, 8)`
 * renders the constant string "session_" for every Kimi row — every such id
 * looks identical. These tests RENDER the real components (sessions table,
 * /live switcher, grades list, span detail panel) against mocked API
 * responses and assert the short id actually discriminates. A pure
 * unit-assert on the helper would not have caught the original defect, whose
 * whole shape was "helper written, call sites never switched over".
 *
 * Everything is `page.route`-mocked: no session is posted, so these tests
 * never write to the dev DB. The sessions-list envelope is templated off a
 * live response so each row keeps every field SessionListRow reads.
 *
 * The trace ids below are real ids sampled from the dev DB — two Kimi
 * sessions sharing the `session_` prefix, one of them titleless (the case
 * LiveSessionPicker's fallback hits).
 */
import { test, expect } from './auth-fixture.js'
import { API_BASE } from './helpers/api-base.js'

// playwright.config.js pins baseURL to :5173 with `reuseExistingServer`, so a
// dev server started from a DIFFERENT checkout that already owns :5173 makes
// every spec silently exercise that other tree's source. These tests assert on
// exact rendered strings, so point them at the checkout under test:
//   REGIN_E2E_ORIGIN=http://localhost:5175 npx playwright test kimi-frontend-polish
// Unset, it falls back to the config default — which fails loudly (never
// false-passes) when that server is serving code without the fix.
// Relative, so Playwright resolves against the config's `baseURL`. A pinned
// origin sent these three tests at the dev stack's vite rather than the one the
// suite started, and every locator then missed.
const url = (path) => path

const KIMI_A = 'session_113dcdf9-f20c-488a-a53a-619671b96ad2'
const KIMI_B = 'session_0526b6b6-2af5-4c09-908e-b288aad59588'
const KIMI_TITLELESS = 'session_ea496f20-ae0c-4a21-b79b-e8871873ba8e'
const CLAUDE_A = '33cc3a2d-ec89-4d0a-9f4d-1f2b3c4d5e6f'

// What a raw slice would have rendered — the defect's fingerprint.
const PREFIX = 'session_'

async function authToken(page) {
  const res = await page.request.post(`${API_BASE}/api/auth/login`, {
    data: { username: 'claude-admin', password: 'claude-admin-2026' },
  })
  expect(res.ok()).toBeTruthy()
  return (await res.json()).token
}

// Real list envelope with its rows swapped for our fixtures. Templating off a
// live row keeps the ~40 fields SessionListRow reads (tokens, repos, phase, …)
// realistic without hand-maintaining them here.
async function mockSessionList(page, rows) {
  const token = await authToken(page)
  const res = await page.request.get(`${API_BASE}/api/sessions?size=1`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  expect(res.ok()).toBeTruthy()
  const envelope = await res.json()
  const template = envelope.items?.[0] || {}
  const items = rows.map(r => ({ ...template, ended_at: null, ...r }))
  await page.route('**/api/sessions?*', route => route.fulfill({
    json: {
      ...envelope,
      items,
      sessions: items,
      pagination: { ...(envelope.pagination || {}), next_cursor: null, has_next: false },
    },
  }))
}

const LIST_ROWS = [
  { trace_id: KIMI_A, title: null, agent_kind: 'kimi' },
  { trace_id: KIMI_B, title: null, agent_kind: 'kimi' },
  { trace_id: CLAUDE_A, title: null, agent_kind: 'claude' },
]

test.describe('session ids stay distinguishable across providers', () => {
  test('desktop sessions list renders discriminating Kimi ids', async ({ page }) => {
    await mockSessionList(page, LIST_ROWS)
    await page.goto(url('/trace/sessions'))

    // `.srow__id`, not `table .cell-code`: b93ff013 rebuilt the list as a
    // grouped grid and removed the table wrapper, but left this spec behind.
    const cells = page.locator('.srow .srow__id')
    await expect(cells.first()).toBeVisible({ timeout: 15_000 })
    // innerText, not textContent: the row also carries a wide-viewport-only
    // span holding the FULL id, which is correct and must stay untruncated.
    const shown = await cells.evaluateAll(els => els.map(e => e.innerText.trim()))

    for (const text of shown) expect(text).not.toContain(PREFIX)
    expect(shown.some(t => t.startsWith('113dcdf9'))).toBe(true)
    expect(shown.some(t => t.startsWith('0526b6b6'))).toBe(true)
    // Claude is the primary product: its id must render exactly as before —
    // the unmodified first 12 characters.
    expect(shown.some(t => t.startsWith(CLAUDE_A.slice(0, 12)))).toBe(true)
  })

  test('sessions-list checkbox labels discriminate Kimi rows', async ({ page }) => {
    await mockSessionList(page, LIST_ROWS)
    await page.goto(url('/trace/sessions'))

    // Scoped to the desktop row: the phone card layout renders in the same DOM
    // (hidden by CSS) and its checkboxes carry identical labels.
    const boxes = page.locator('.srow [aria-label^="Select session "]')
    await expect(boxes.first()).toBeVisible({ timeout: 15_000 })
    const labels = await boxes.evaluateAll(els => els.map(e => e.getAttribute('aria-label')))

    for (const label of labels) expect(label).not.toContain(PREFIX)
    expect(labels).toContain(`Select session ${CLAUDE_A.slice(0, 8)}`)
    expect(new Set(labels).size).toBe(labels.length)
  })

  test('sessions-list row actions name the session without the prefix', async ({ page }) => {
    await mockSessionList(page, LIST_ROWS)
    await page.goto(url('/trace/sessions'))

    const live = page.locator('.srow [title^="Watch session "]')
    await expect(live.first()).toBeVisible({ timeout: 15_000 })
    const titles = await live.evaluateAll(els => els.map(e => e.getAttribute('title')))

    for (const t of titles) expect(t).not.toContain(PREFIX)
    expect(titles).toContain(`Watch session ${CLAUDE_A.slice(0, 12)}… in the live view`)
  })

  test('/live switcher names a titleless Kimi session by a real id fragment', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 })
    await mockSessionList(page, [
      { trace_id: KIMI_TITLELESS, title: null, status: 'active', agent_kind: 'kimi' },
      { trace_id: KIMI_B, title: null, status: 'ended', agent_kind: 'kimi' },
    ])
    const now = new Date().toISOString()
    await page.route(`**/api/sessions/${KIMI_TITLELESS}/map*`, route => route.fulfill({
      json: {
        trace_id: KIMI_TITLELESS, title: null, spans: [], span_count: 0,
        started_at: now, last_seen: now, server_now: now,
        phase: 'inactive-stale', agent_phase: {},
      },
    }))

    await page.goto(url(`/live/${KIMI_TITLELESS}`))
    const switchBtn = page.locator('[data-testid="live-switch"]')
    await expect(switchBtn).toBeVisible({ timeout: 15_000 })
    await switchBtn.click()

    const rows = page.locator('[data-testid="live-picker-row"]')
    await expect(rows.first()).toBeVisible({ timeout: 10_000 })
    const texts = await rows.evaluateAll(els => els.map(e => e.textContent))

    for (const t of texts) expect(t).not.toContain(PREFIX)
    expect(texts.join(' ')).toContain('ea496f20')
    expect(texts.join(' ')).toContain('0526b6b6')
  })

  test('grades list renders discriminating Kimi ids', async ({ page }) => {
    const grade = (traceId) => ({
      trace_id: traceId, axis: 'correctness', verdict: 'satisfied',
      tier: 'quick', judge: 'claude', report: 'fixture', rubric_version: 'v1',
      session: {},
    })
    await page.route('**/api/grades?*', route => route.fulfill({
      json: { grades: [grade(KIMI_A), grade(KIMI_B), grade(CLAUDE_A)] },
    }))
    await page.route('**/api/grades/pareto*', route => route.fulfill({
      json: { summary: {}, points: [] },
    }))

    await page.goto(url('/grades'))
    await expect(page.locator('a[href*="/trace/sessions/"]').first())
      .toBeVisible({ timeout: 15_000 })

    const body = await page.locator('body').textContent()
    expect(body).not.toContain(PREFIX)
    // Both the id line (8 chars) and the titleless title fallback (12 chars).
    expect(body).toContain('113dcdf9')
    expect(body).toContain('0526b6b6')
    expect(body).toContain(CLAUDE_A.slice(0, 12))
  })
})

test.describe('deny badge reads the same inline and in the detail panel', () => {
  const TRACE = 'deny0000-0000-4000-8000-000000000001'
  const DENY_SPAN = 'tooldeny-tu-parity'
  // Verbatim Claude Code hard-deny sentinel text, per
  // hook_manager/handlers/turn_trace/deny_detection.py — so this fixture
  // exercises the CLAUDE path, where deny_kind === 'deny' means the user
  // denied at the permission prompt (which is what "Denied" claims).
  const REASON = "The user doesn't want to proceed with this tool use."
    + ' The tool use was rejected.'

  async function mockDenyTrace(page, denyKind) {
    const now = new Date().toISOString()
    const attributes = {
      tool_name: 'Bash', tool_use_id: 'tu-parity', denied: true,
      command_preview: 'rm -rf /tmp/nope', denial_reason: REASON,
    }
    if (denyKind) attributes.deny_kind = denyKind
    await page.route(`**/api/sessions/${TRACE}/map*`, route => route.fulfill({
      json: {
        trace_id: TRACE, title: 'deny parity fixture', title_source: 'first_prompt',
        started_at: now, last_seen: now, server_now: now,
        phase: 'inactive-stale', agent_phase: {}, span_count: 2,
        spans: [
          {
            trace_id: TRACE, span_id: 'prompt-parity', parent_id: null, name: 'prompt',
            start_time: now, kind: 'internal', source: 'transcript', status_code: 'UNSET',
            attributes: { text: 'run the thing' },
          },
          {
            trace_id: TRACE, span_id: DENY_SPAN, parent_id: 'prompt-parity',
            name: 'tool.Bash', start_time: now, kind: 'internal', source: 'hook',
            status_code: 'ERROR', attributes,
          },
        ],
      },
    }))
  }

  // Both badges, isolated from surrounding copy. The inline badge lives in the
  // Conversation feed; the detail panel's rail is opt-in there but always on in
  // Timeline, so the panel is read after switching tabs.
  async function badgeTexts(page, denyKind) {
    await mockDenyTrace(page, denyKind)
    await page.goto(url(`/trace/sessions/${TRACE}`))

    const row = page.locator(`[data-span-id="${DENY_SPAN}"]`).first()
    await expect(row).toBeVisible({ timeout: 15_000 })
    const inlineBadge = row.locator('span.bg-amber-100').first()
    await expect(inlineBadge).toBeVisible({ timeout: 10_000 })
    const inline = (await inlineBadge.textContent()).trim()

    await page.getByRole('button', { name: 'Timeline', exact: true }).first().click()
    await page.getByText('rm -rf /tmp/nope').first().click()
    const header = page.locator('div.flex.items-center.gap-2.mb-1')
      .filter({ hasText: 'agent injected prompt' }).first()
    await expect(header).toBeVisible({ timeout: 10_000 })
    const panel = (await header.locator('span').last().textContent()).trim()
    return { inline, panel }
  }

  test('a Claude hard deny says "Denied" in both places', async ({ page }) => {
    const { inline, panel } = await badgeTexts(page, 'deny')
    expect(inline).toBe('Denied')
    expect(panel).toBe(inline)
  })

  test('an unclassified deny still reads "Interrupted" in both places', async ({ page }) => {
    const { inline, panel } = await badgeTexts(page, null)
    expect(inline).toBe('Interrupted')
    expect(panel).toBe(inline)
  })

  test('a "chat instead" deny keeps its own wording in both places', async ({ page }) => {
    const { inline, panel } = await badgeTexts(page, 'chat')
    expect(inline).toBe('chat instead')
    expect(panel).toBe(inline)
  })
})
