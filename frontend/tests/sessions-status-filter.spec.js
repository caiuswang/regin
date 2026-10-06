/**
 * Sessions list: the Status facet speaks the row's own vocabulary (every key
 * of STATES, multi-select, OR-ed), the header pill toggles the active set,
 * and the selection bar can Close as well as Delete.
 *
 * Every response is mocked: the assertions are about what the page SENDS and
 * how it renders the counts it is given, and confirming a real batch close
 * would settle live sessions on the host.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

const SERVER_NOW = '2026-07-10T13:00:00.000000'
const SERVER_NOW_UTC = '2026-07-10T09:00:00.000Z'
const FRESH = '2026-07-10T12:58:00.000000'
const STALE = '2026-07-10T12:00:00.000000'
const DIALOG = '.fixed.inset-0.z-50'

const COUNTS = {
  awaiting: 2, retrying: 0, working: 3, idle: 1,
  starting: 0, inactive: 4, ended: 7, closed: 1,
}

function row(overrides = {}) {
  const id = overrides.trace_id || randomUUID()
  return {
    trace_id: id,
    title: `STATUS_FIXTURE_${id.slice(0, 8)}`,
    status: 'active', ended_reason: null, is_test: 0,
    started_at: '2026-07-10T11:00:00.000000',
    last_seen: STALE,
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

const inactive = () => row()
const ended = () => row({ status: 'ended', ended_reason: 'session_end' })
const closed = () => row({ status: 'ended', ended_reason: 'manual' })

function envelope(rows, extra = {}) {
  return {
    items: rows, sessions: rows,
    pagination: { next_cursor: null, size: 50, has_next: false },
    tag_counts: { user: rows.length },
    builtin_tags: [{ slug: 'user', label: 'User' }],
    repo_counts: {},
    total_count: rows.length,
    status_counts: COUNTS,
    active_count: COUNTS.working + COUNTS.retrying + COUNTS.awaiting + COUNTS.idle,
    server_now: SERVER_NOW,
    server_now_utc: SERVER_NOW_UTC,
    ...extra,
  }
}

// Seeds storage once per test, not on every navigation, so a reload reads
// back what the page itself wrote.
async function seed(page, status = []) {
  await page.addInitScript((s) => {
    if (sessionStorage.getItem('f136_seeded')) return
    sessionStorage.setItem('f136_seeded', '1')
    localStorage.setItem('regin_sessions_group', 'flat')
    localStorage.setItem('regin_sessions_range', 'all')
    localStorage.setItem('regin_sessions_kind', 'real')
    localStorage.setItem('regin_sessions_status', JSON.stringify(s))
    localStorage.setItem('regin_sessions_tag', '')
    localStorage.setItem('regin_sessions_repo', 'all')
  }, status)
}

function trackList(page, rows) {
  const seen = []
  return page.route('**/api/sessions?*', (r) => {
    seen.push(new URL(r.request().url()).searchParams)
    return r.fulfill({ json: envelope(typeof rows === 'function' ? rows() : rows) })
  }).then(() => seen)
}

const statusFacet = (page) => page.locator('.filters-panel .facet', { hasText: 'Status' })
const chip = (page, key) => statusFacet(page).locator(`.chip[data-value="${key}"]`)
const anyChip = (page) => statusFacet(page).locator('.chip', { hasText: /^\s*Any\s*$/ })

async function openFilters(page) {
  await page.getByRole('button', { name: /Filters/ }).click()
  await expect(page.locator('.filters-panel')).toBeVisible()
}

// The seeded `All time` range is a chip of its own; only the status one matters here.
const statusChipText = (page) => page.locator('.afilter__text', { hasText: /^Status:/ })

const checkboxOf = (page, s) => page.locator('.srow')
  .filter({ has: page.locator(`[aria-label="Copy session id ${s.trace_id}"]`) })
  .locator('input[type="checkbox"]')

test('the Status facet offers Any plus every row status, with server counts', async ({ page }) => {
  await seed(page)
  await trackList(page, [inactive()])
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(1)
  await openFilters(page)

  const chips = statusFacet(page).locator('.chip')
  await expect(chips).toHaveCount(9)
  await expect(anyChip(page)).toHaveAttribute('aria-pressed', 'true')
  const texts = (await chips.allInnerTexts()).map(t => t.replace(/\s+/g, ' ').trim())
  expect(texts).toEqual([
    'Any', '◆ needs you · 2', '◌ reconnecting · 0', '● working · 3', '◐ idle · 1',
    '○ starting · 0', '■ inactive · 4', '✓ ended · 7', '✓ closed · 1',
  ])
  // A 0-count chip is dimmed, never hidden.
  await expect(chip(page, 'retrying')).toHaveClass(/chip--zero/)
  await expect(chip(page, 'working')).not.toHaveClass(/chip--zero/)
})

test('two statuses are OR-ed into one request and named in the chip row', async ({ page }) => {
  await seed(page)
  const seen = await trackList(page, [inactive()])
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(1)
  await openFilters(page)

  await chip(page, 'inactive').click()
  await chip(page, 'awaiting').click()
  await expect(chip(page, 'awaiting')).toHaveAttribute('aria-pressed', 'true')
  await expect(chip(page, 'inactive')).toHaveAttribute('aria-pressed', 'true')
  await expect(anyChip(page)).toHaveAttribute('aria-pressed', 'false')
  await expect.poll(() => seen.at(-1).get('status')).toBe('awaiting,inactive')
  await expect(statusChipText(page)).toHaveText(['Status: needs you, inactive'])

  // A third status collapses the chip text to a count; the title keeps the words.
  await chip(page, 'ended').click()
  await expect.poll(() => seen.at(-1).get('status')).toBe('awaiting,inactive,ended')
  const text = statusChipText(page)
  await expect(text).toHaveText(['Status: 3 states'])
  await expect(text).toHaveAttribute('title', 'needs you, inactive, ended')
})

test('unpressing the last status returns to Any', async ({ page }) => {
  await seed(page, ['inactive'])
  const seen = await trackList(page, [inactive()])
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(1)
  expect(seen.at(-1).get('status')).toBe('inactive')
  await openFilters(page)

  await chip(page, 'inactive').click()
  await expect(anyChip(page)).toHaveAttribute('aria-pressed', 'true')
  await expect.poll(() => seen.at(-1).has('status')).toBe(false)
  await expect(statusChipText(page)).toHaveCount(0)
})

test('the status set survives a reload', async ({ page }) => {
  await seed(page)
  const seen = await trackList(page, [ended()])
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(1)
  await openFilters(page)
  await chip(page, 'closed').click()
  await chip(page, 'ended').click()
  await expect.poll(() => seen.at(-1).get('status')).toBe('ended,closed')

  await page.reload()
  await expect(page.locator('.srow')).toHaveCount(1)
  expect(seen.at(-1).get('status')).toBe('ended,closed')
  await openFilters(page)
  await expect(chip(page, 'ended')).toHaveAttribute('aria-pressed', 'true')
  await expect(chip(page, 'closed')).toHaveAttribute('aria-pressed', 'true')
})

test('the active-now pill toggles exactly the active statuses', async ({ page }) => {
  await seed(page)
  const seen = await trackList(page, [inactive()])
  await page.goto('/trace/sessions')
  const pill = page.locator('.live-pill')
  await expect(pill).toContainText('6 active now')
  await expect(pill).toHaveAttribute('aria-pressed', 'false')

  await pill.click()
  await expect.poll(() => seen.at(-1).get('status')).toBe('awaiting,retrying,working,idle')
  await expect(pill).toHaveAttribute('aria-pressed', 'true')
  await expect(statusChipText(page)).toHaveText(['Status: 4 states'])

  await pill.click()
  await expect.poll(() => seen.at(-1).has('status')).toBe(false)
  await expect(pill).toHaveAttribute('aria-pressed', 'false')
})

test('batch close sends only the closable rows and clears the selection', async ({ page }) => {
  await seed(page)
  const rows = [inactive(), inactive(), inactive(), ended()]
  let current = rows
  await trackList(page, () => current)
  const posted = []
  await page.route('**/api/sessions/batch-close', (r) => {
    const ids = r.request().postDataJSON().trace_ids
    posted.push(ids)
    current = rows.map(s => (ids.includes(s.trace_id)
      ? { ...s, status: 'ended', ended_reason: 'manual' } : s))
    return r.fulfill({ json: { ok: true, processed: ids.length, closed: ids, skipped: [], missing: [] } })
  })
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(4)

  for (const s of rows) await checkboxOf(page, s).check()
  const close = page.locator('.stoolbar__batch').getByRole('button', { name: 'Close 3' })
  await expect(close).toBeVisible()
  await close.click()

  const dialog = page.locator(DIALOG).first()
  await expect(dialog).toContainText('1 already ended will be skipped')
  await dialog.getByRole('button', { name: 'Close 3 sessions' }).click()

  await expect.poll(() => posted.length).toBe(1)
  expect(new Set(posted[0])).toEqual(new Set(rows.slice(0, 3).map(s => s.trace_id)))
  await expect(page.getByTestId('flash')).toContainText('Closed 3 sessions, 1 already ended')
  await expect(page.locator('.stoolbar__batch')).toHaveCount(0)
  for (const s of rows.slice(0, 3)) {
    await expect(page.locator('.srow').filter({ has: page.locator(`[aria-label="Copy session id ${s.trace_id}"]`) }))
      .toContainText('closed')
  }
})

test('Close is not offered when every selected row has already ended', async ({ page }) => {
  await seed(page)
  const rows = [ended(), closed()]
  await trackList(page, rows)
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(2)
  for (const s of rows) await checkboxOf(page, s).check()
  const bar = page.locator('.stoolbar__batch')
  await expect(bar.getByRole('button', { name: 'Delete selected' })).toBeVisible()
  await expect(bar.getByRole('button', { name: /^Close/ })).toHaveCount(0)
})

test('a failed batch close keeps the selection for a retry', async ({ page }) => {
  await seed(page)
  const rows = [inactive(), inactive()]
  await trackList(page, rows)
  await page.route('**/api/sessions/batch-close', (r) => r.fulfill({
    status: 500, json: { ok: false, error: 'RuntimeError: disk full' },
  }))
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(2)
  for (const s of rows) await checkboxOf(page, s).check()
  await page.locator('.stoolbar__batch').getByRole('button', { name: 'Close 2' }).click()
  await page.locator(DIALOG).first().getByRole('button', { name: 'Close 2 sessions' }).click()

  await expect(page.getByTestId('flash')).toContainText('Batch close failed')
  await expect(page.locator('.stoolbar__selcount')).toHaveText('2 selected')
})

test('batch close warns about and stops a live process before closing', async ({ page }) => {
  await seed(page)
  const live = row({ last_seen: FRESH })
  const rows = [live, inactive()]
  await trackList(page, rows)
  const order = []
  await page.route('**/api/sessions/*/live-state', (r) => r.fulfill({
    json: { tier: 'sdk', live: true },
  }))
  await page.route('**/api/sessions/*/shutdown', (r) => {
    order.push('shutdown')
    return r.fulfill({ json: { closed: true, tier: 'sdk' } })
  })
  await page.route('**/api/sessions/batch-close', (r) => {
    order.push('batch-close')
    const ids = r.request().postDataJSON().trace_ids
    return r.fulfill({ json: { ok: true, processed: ids.length, closed: ids, skipped: [], missing: [] } })
  })
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(2)
  for (const s of rows) await checkboxOf(page, s).check()
  await page.locator('.stoolbar__batch').getByRole('button', { name: 'Close 2' }).click()

  const dialog = page.locator(DIALOG).first()
  await expect(dialog).toContainText('still LIVE')
  const confirmBtn = dialog.getByRole('button', { name: 'Close 2 sessions' })
  await expect(confirmBtn).toHaveClass(/bg-danger-soft/)
  await confirmBtn.click()
  await expect.poll(() => order).toEqual(['shutdown', 'batch-close'])
})

test('shift-click selects the range from the last-clicked row', async ({ page }) => {
  await seed(page)
  const rows = Array.from({ length: 6 }, () => inactive())
  await trackList(page, rows)
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(6)

  await checkboxOf(page, rows[1]).click()
  await checkboxOf(page, rows[4]).click({ modifiers: ['Shift'] })
  for (const [i, s] of rows.entries()) {
    const want = i >= 1 && i <= 4
    await expect(checkboxOf(page, s), `row ${i + 1}`).toBeChecked({ checked: want })
  }
  await expect(page.locator('.stoolbar__selcount')).toHaveText('4 selected')
})
