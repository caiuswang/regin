/**
 * Sessions list: the grouping axis and the page-scoped-vs-server-scoped
 * counts around it.
 *
 * Grouping (Active first / By day / By repo / Flat) partitions the rows
 * ALREADY LOADED, while the header pill, the tab badge and the footer total
 * come from the server over the whole filter set. The two disagree the moment
 * the list is truncated, so the UI has to say which one it is showing — that
 * disclosure is what these tests pin.
 *
 * Everything is mocked with a fixed `server_now` pair so "active" is decided
 * by the fixture, not the machine clock.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

const SERVER_NOW = '2026-07-10T13:00:00.000000'
const SERVER_NOW_UTC = '2026-07-10T09:00:00.000Z'

function row(overrides = {}) {
  const id = overrides.trace_id || randomUUID()
  return {
    trace_id: id,
    title: `GROUP_FIXTURE_${id.slice(0, 8)}`,
    status: 'ended', ended_reason: null, is_test: 0,
    started_at: '2026-07-10T11:00:00.000000',
    last_seen: '2026-07-10T12:00:00.000000',
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

const withRepo = (name, extra = {}) => row({
  repos: [{ name, is_primary: true }], primary_repo: name, ...extra,
})

function envelope(rows, extra = {}) {
  return {
    items: rows, sessions: rows,
    pagination: { next_cursor: null, size: 50, has_next: false },
    tag_counts: { user: rows.length },
    builtin_tags: [{ slug: 'user', label: 'User' }],
    repo_counts: {},
    total_count: rows.length,
    active_count: rows.filter(r => r.status === 'active').length,
    server_now: SERVER_NOW,
    server_now_utc: SERVER_NOW_UTC,
    ...extra,
  }
}

// Seed the persisted axes so a previous run's localStorage can't steer these.
async function seed(page, group = 'active') {
  await page.addInitScript((g) => {
    localStorage.setItem('regin_sessions_group', g)
    localStorage.setItem('regin_sessions_range', 'all')
    localStorage.setItem('regin_sessions_kind', 'real')
    localStorage.setItem('regin_sessions_active', 'all')
    localStorage.setItem('regin_sessions_tag', '')
    localStorage.setItem('regin_sessions_repo', 'all')
  }, group)
}

// Read the rendered groups by walking the grid in document order, so a group's
// declared count is compared against the rows actually under it.
async function groupState(page) {
  return page.evaluate(() => {
    const grid = document.querySelector('.slist__grid')
    const out = []
    let cur = null
    for (const el of grid.children) {
      if (el.classList.contains('slist__group')) {
        cur = {
          label: el.querySelector('.slist__group-label').textContent.trim(),
          count: el.querySelector('.slist__group-count').textContent.trim(),
          rendered: 0,
        }
        out.push(cur)
      } else if (el.classList.contains('srow') && cur) {
        cur.rendered++
      }
    }
    return { groups: out, rows: grid.querySelectorAll('.srow').length }
  })
}

test('each grouping mode partitions the loaded rows and its count matches what it renders', async ({ page }) => {
  await seed(page)
  const rows = [
    withRepo('zeta', { status: 'active' }),
    withRepo('alpha', { status: 'active' }),
    withRepo('alpha'),
    withRepo('mid'),
    row(),                                              // no repo
  ]
  await page.route('**/api/sessions?*', (r) => r.fulfill({ json: envelope(rows) }))
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(5)

  // Active first — the two active rows lead, the rest fall to EARLIER.
  let st = await groupState(page)
  // Labels are uppercased by CSS, so textContent carries the source casing.
  expect(st.groups.map(g => [g.label, g.count, g.rendered]))
    .toEqual([['Active now', '2', 2], ['Earlier', '3', 3]])

  // By day — every fixture row shares one last_seen date.
  await page.getByRole('button', { name: 'By day', exact: true }).click()
  st = await groupState(page)
  expect(st.groups).toHaveLength(1)
  expect(st.groups[0].rendered).toBe(5)
  expect(st.groups[0].count).toBe('5')

  // By repo — alphabetical, with the unmatched bucket pinned last.
  await page.getByRole('button', { name: 'By repo', exact: true }).click()
  st = await groupState(page)
  expect(st.groups.map(g => g.label)).toEqual(['alpha', 'mid', 'zeta', 'No repo'])
  for (const g of st.groups) expect(Number(g.count)).toBe(g.rendered)

  // Flat — no headers at all, every row still rendered.
  await page.getByRole('button', { name: 'Flat', exact: true }).click()
  st = await groupState(page)
  expect(st.groups).toHaveLength(0)
  expect(st.rows).toBe(5)

  // The choice persists.
  expect(await page.evaluate(() => localStorage.getItem('regin_sessions_group'))).toBe('flat')
})

test('pill, tab badge and footer report the SERVER totals, not the loaded page', async ({ page }) => {
  await seed(page)
  const rows = [row({ status: 'active' }), row()]
  await page.route('**/api/sessions?*', (r) => r.fulfill({
    json: envelope(rows, { total_count: 2186, active_count: 92 }),
  }))
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(2)

  await expect(page.locator('.live-pill')).toContainText('92 active now')
  await expect(page.locator('.segmented-item.is-active .segmented-item__count')).toHaveText('2186')
  await expect(page.locator('.slist')).toContainText('Showing 2 of 2186 sessions')
})

/**
 * Regression: the header pill counts every active session on the server while
 * ACTIVE NOW counts only the loaded ones. Shipping "92 active now" directly
 * above a bare "ACTIVE NOW · 1" reads as a bug, so the group must name both
 * scopes and the footer must say grouping is page-scoped.
 */
test('a truncated list says which rows its group counts cover', async ({ page }) => {
  await seed(page)
  const rows = [row({ status: 'active' }), row()]
  await page.route('**/api/sessions?*', (r) => r.fulfill({
    json: envelope(rows, {
      total_count: 2186,
      active_count: 92,
      pagination: { next_cursor: 'more', size: 50, has_next: true },
    }),
  }))
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(2)

  // Scope to the desktop grid: the mobile card list renders its own group
  // header into the same DOM, hidden by CSS rather than by v-if.
  await expect(page.locator('.slist__grid .slist__group--live')).toContainText('1 of 92')
  await expect(page.locator('.slist')).toContainText('groups cover the loaded rows')

  // Flat has no groups, so the qualifier must not be claimed.
  await page.getByRole('button', { name: 'Flat', exact: true }).click()
  await expect(page.locator('.slist')).not.toContainText('groups cover the loaded rows')
  await expect(page.locator('.slist')).toContainText('Showing 2 of 2186 sessions')
})

/**
 * Regression: `resetFilters` clears its `batching` guard around an
 * `await nextTick()`. The five facet watchers are `flush: 'pre'`, so they run
 * as microtasks AFTER the assigning function yields — clearing the flag in a
 * synchronous `finally` let every one of them fire its own reload (measured:
 * 3 identical requests per Reset, each re-running the two count aggregates
 * and the repo group-by).
 */
test('Reset all restores every facet in exactly one request', async ({ page }) => {
  await seed(page)
  const rows = [row(), row()]
  await page.route('**/api/sessions?*', (r) => r.fulfill({ json: envelope(rows) }))
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(2)

  // Dirty three facets so the reset genuinely changes more than one ref.
  const panel = page.locator('.filters-panel')
  await page.getByRole('button', { name: /Filters/ }).click()
  await panel.locator('.facet', { hasText: 'Status' }).locator('.chip', { hasText: 'Active' }).click()
  await expect(page.locator('.filters-trigger__count')).toBeVisible()
  await panel.locator('.facet', { hasText: 'Kind' }).locator('.chip', { hasText: 'Tests' }).click()
  await expect(page.locator('.filters-trigger__count')).toHaveText('3')  // status + kind + range(all)

  const requests = []
  page.on('request', (r) => { if (r.url().includes('/api/sessions?')) requests.push(r.url()) })
  await panel.getByRole('button', { name: 'Reset all' }).click()

  await expect(page.locator('.filters-trigger__count')).toBeHidden()
  await expect.poll(async () => page.evaluate(() => ({
    active: localStorage.getItem('regin_sessions_active'),
    kind: localStorage.getItem('regin_sessions_kind'),
    range: localStorage.getItem('regin_sessions_range'),
  }))).toEqual({ active: 'all', kind: 'real', range: 'today' })

  // The list refetched — once.
  await expect(page.locator('.srow')).toHaveCount(2)
  expect(requests, `Reset all fired ${requests.length} requests:\n${requests.join('\n')}`)
    .toHaveLength(1)
})
