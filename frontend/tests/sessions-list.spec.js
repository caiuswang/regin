/**
 * Sessions list (`frontend/src/views/SessionsView.vue` + `SessionListRow.vue`
 * / `SessionCard.vue`): the Live row action, the workflow tag facet chip,
 * surfaced tag-mutation errors, and timezone-safe "Last seen" ages.
 *
 * The list request is mocked (`**\/api/sessions?*`) with a deterministic
 * envelope — including a FIXED `server_now` / `server_now_utc` pair — so the
 * age assertions are exact and independent of the machine clock. The browser
 * runs under a spoofed `timezoneId` that differs from the host: the old
 * client-clock arithmetic would read the naive host-local stamp as future
 * ("just now"); the server-anchored age must still say "1h ago".
 *
 * The Live-link navigation targets a REAL synthetic session posted via
 * /api/session-spans (its trace_id substituted into the mocked list), so
 * /live/<id> resolves and renders the live card.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

// Any zone well away from the host's; the naive-stamp regression reproduces
// whenever browser TZ ≠ host TZ.
test.use({ timezoneId: 'America/New_York' })

const SERVER_NOW = '2026-07-10T13:00:00.000000'       // naive host-local
const SERVER_NOW_UTC = '2026-07-10T09:00:00.000Z'     // same instant, zoned

function row(traceId, overrides = {}) {
  return {
    trace_id: traceId,
    title: `SESSIONS_LIST_FIXTURE_${traceId.slice(0, 8)}`,
    status: 'ended', ended_reason: null, is_test: 0,
    started_at: '2026-07-10T11:00:00.000000',
    last_seen: '2026-07-10T12:00:00.000000',           // 1h before SERVER_NOW
    span_count: 3, file_edits: 1, tool_calls: 2, skill_reads: 0,
    rule_checks: 0, plans: 0, prompts: 1,
    agent_type: 'claude', agent_kind: 'claude', origin: 'session',
    category: 'user', is_workflow: false, is_run: false,
    model: null, cwd: null, repos: [], primary_repo: null, is_multi_repo: false,
    context_pct: null, context_pct_all: null, active_work_ms: null,
    active_pct: null, idle_ms: null,
    tags: [{ slug: 'user', source: 'auto', builtin: true }],
    ...overrides,
  }
}

function envelope(rows) {
  return {
    items: rows, sessions: rows,
    pagination: { next_cursor: null, size: 50, has_next: false },
    tag_counts: { user: rows.length, workflow: 57 },
    builtin_tags: [
      { slug: 'topic-proposal', label: 'Topic proposal' },
      { slug: 'system', label: 'System' },
      { slug: 'user', label: 'User' },
    ],
    server_now: SERVER_NOW,
    server_now_utc: SERVER_NOW_UTC,
  }
}

async function mockList(page, rows) {
  await page.route('**/api/sessions?*', (route) =>
    route.fulfill({ json: envelope(rows) }))
  await page.route('**/api/session-tags', (route) =>
    route.fulfill({ json: { tags: [{ slug: 'workflow', count: 57 }] } }))
}

async function postLiveTarget(page) {
  const traceId = randomUUID()
  const now = new Date().toISOString()
  const res = await page.request.post('/api/session-spans', {
    data: [
      { trace_id: traceId, span_id: `p-${traceId.slice(0, 8)}`, parent_id: null,
        name: 'prompt', start_time: now,
        attributes: { text: 'live target fixture', is_test: true } },
    ],
  })
  expect(res.ok()).toBeTruthy()
  return traceId
}

test('Live row action navigates to /live/<id> and renders the live card', async ({ page }) => {
  const liveId = await postLiveTarget(page)
  await mockList(page, [row(liveId)])
  await page.goto('/trace/sessions')

  // Row actions are revealed by hover (and :focus-within), so hover the row
  // before clicking — at rest the bar is opacity:0 / pointer-events:none.
  const listRow = page.locator('.srow', { hasText: `SESSIONS_LIST_FIXTURE_${liveId.slice(0, 8)}` })
  await listRow.hover()
  const link = listRow.locator(`a[href="/live/${liveId}"]`, { hasText: 'Live' })
  await expect(link).toBeVisible()
  await link.click()
  await expect(page).toHaveURL(new RegExp(`/live/${liveId}`))
  await expect(page.locator('.live-card')).toBeVisible()
})

test('mobile card also carries the Live link', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 667 })
  const id = randomUUID()
  await mockList(page, [row(id)])
  await page.goto('/trace/sessions')
  await expect(
    page.locator(`.scard a[href="/live/${id}"]`, { hasText: 'Live' })).toBeVisible()
})

// The tag facet is a chip row inside the Filters popover (portalled to <body>
// by Reka). Counts come from the envelope's `tag_counts`, never from the
// global per-tag totals in /api/session-tags — a tag absent from the current
// filter set must read 0, not its all-time count.
test('tag facet lists the workflow custom tag with its count', async ({ page }) => {
  await mockList(page, [row(randomUUID())])
  await page.goto('/trace/sessions')
  await page.getByRole('button', { name: /Filters/ }).click()
  const chip = page.locator('.filters-panel .facet', { hasText: 'Tag' })
    .locator('.chip', { hasText: '#workflow' })
  await expect(chip).toHaveCount(1)
  await expect(chip).toContainText('57')
})

test('invalid tag add surfaces the server reason, not a silent no-op', async ({ page }) => {
  await mockList(page, [row(randomUUID())])
  await page.goto('/trace/sessions')
  await page.locator('button[title="Add a tag"]').first().click()
  await page.locator('input[aria-label="New tag slug"]').fill('bad!!slug')
  await page.keyboard.press('Enter')
  const flash = page.locator('[role="status"].alert-error')
  await expect(flash).toBeVisible()
  await expect(flash).toContainText('slug')
})

test('last-seen ages are server-anchored: no "just now" across timezones', async ({ page }) => {
  const naive = row(randomUUID())                       // 12:00 naive vs 13:00 server_now
  const zoned = row(randomUUID(), {
    last_seen: '2026-07-10T07:00:00.000Z',              // 2h before SERVER_NOW_UTC
  })
  await mockList(page, [naive, zoned])
  await page.goto('/trace/sessions')

  const cells = page.locator('.srow .srow__seen[title*="Last seen"]')
  await expect(cells).toHaveCount(2)
  await expect(cells.nth(0)).toHaveText(/^1h ago$/)
  await expect(cells.nth(1)).toHaveText(/^2h ago$/)
  await expect(page.locator('.slist__grid')).not.toContainText('just now')
})

test('copying a session id acknowledges on the icon, not in a page-level toast', async ({ page }) => {
  const traceId = randomUUID()
  await page.addInitScript(() => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (text) => { window.__copied = text } },
    })
  })
  await mockList(page, [row(traceId)])
  await page.goto('/trace/sessions')

  const copy = page.locator(`[aria-label="Copy session id ${traceId}"]`)
  await expect(copy).toHaveCount(1)
  await page.locator('.srow').first().hover()
  const before = await copy.evaluate(el => getComputedStyle(el).color)

  await copy.click()
  await expect(copy).toHaveClass(/srow__copy--done/)
  expect(await page.evaluate(() => window.__copied)).toBe(traceId)

  // The success colour is read back through the token, so the assertion holds
  // in either theme rather than pinning one palette step.
  const success = await page.evaluate(() => {
    const probe = document.createElement('div')
    probe.style.color = 'var(--color-success)'
    document.body.appendChild(probe)
    const c = getComputedStyle(probe).color
    probe.remove()
    return c
  })
  // Polled, not read once: `color` is transitioned, so an immediate read
  // catches an interpolated oklab step rather than the settled token.
  await expect
    .poll(() => copy.evaluate(el => getComputedStyle(el).color), { timeout: 1000 })
    .toBe(success)
  expect(success).not.toBe(before)
  await expect(page.getByText('Copied!')).toHaveCount(0)

  // Transient: the row returns to its resting glyph on its own.
  await expect(copy).not.toHaveClass(/srow__copy--done/, { timeout: 3000 })
})

// A run regin LAUNCHED reports its own phase (`runner_phase`, served only for
// SDK-owned rows). The list must prefer it over the recency heuristic: an
// agent parked at the prompt emits no spans, so recency alone ages it out to
// "inactive" while regin is holding the runner and knows it is alive.
test('an SDK-owned run parked at the prompt reads idle, not inactive', async ({ page }) => {
  const idleId = randomUUID()
  const tracedId = randomUUID()
  // Both rows are equally silent — 1h since last_seen, well past the 10min
  // stale window. The ONLY difference is who can answer for them.
  await mockList(page, [
    row(idleId, { status: 'active', runner_phase: 'idle' }),
    row(tracedId, { status: 'active', runner_phase: null }),
  ])
  await page.goto('/trace/sessions')

  const statusOf = (id) => page
    .locator('.srow', { hasText: `SESSIONS_LIST_FIXTURE_${id.slice(0, 8)}` })
    .locator('.srow__status')

  await expect(statusOf(idleId)).toHaveText('idle')
  // The heuristic is untouched for a session regin merely traces.
  await expect(statusOf(tracedId)).toHaveText('inactive')
})

test('a working runner outranks a stale last_seen in the list', async ({ page }) => {
  const workingId = randomUUID()
  await mockList(page, [row(workingId, { status: 'active', runner_phase: 'working' })])
  await page.goto('/trace/sessions')
  await expect(page
    .locator('.srow', { hasText: `SESSIONS_LIST_FIXTURE_${workingId.slice(0, 8)}` })
    .locator('.srow__status')).toHaveText('working')
})

// `_aliases` is pruned only on run teardown, so a child that `/clear` retired
// still resolves through `owning_run` and the runner answers for it. An ended
// row must not render green on the strength of a runner that has moved on —
// the server keeps the same guard in `_session_phase`.
test('an ended row ignores a runner that still answers for it', async ({ page }) => {
  const retiredId = randomUUID()
  await mockList(page, [
    row(retiredId, { status: 'ended', ended_reason: 'clear', runner_phase: 'working' }),
  ])
  await page.goto('/trace/sessions')
  await expect(page
    .locator('.srow', { hasText: `SESSIONS_LIST_FIXTURE_${retiredId.slice(0, 8)}` })
    .locator('.srow__status')).toHaveText('ended')
})
