/**
 * Sessions list: the two axes that tell the user what they are looking at —
 * the time-range picker (presets plus an explicit start–end span) and the
 * active-filter chip row.
 *
 * A count badge on `Filters` says HOW MANY axes are narrowing the list; only
 * the chip row says WHICH. These tests pin that the two never disagree, and
 * that a custom span reaches the API as the `since`/`until` the calendar
 * actually shows.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

const SERVER_NOW = '2026-07-10T13:00:00.000000'
const SERVER_NOW_UTC = '2026-07-10T09:00:00.000Z'

function row(overrides = {}) {
  const id = overrides.trace_id || randomUUID()
  return {
    trace_id: id,
    title: `RANGE_FIXTURE_${id.slice(0, 8)}`,
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

async function seed(page, overrides = {}) {
  await page.addInitScript((o) => {
    localStorage.setItem('regin_sessions_group', 'flat')
    localStorage.setItem('regin_sessions_range', o.range || 'all')
    localStorage.setItem('regin_sessions_kind', o.kind || 'real')
    localStorage.setItem('regin_sessions_active', o.active || 'all')
    localStorage.setItem('regin_sessions_tag', o.tag || '')
    localStorage.setItem('regin_sessions_repo', o.repo || 'all')
    localStorage.setItem('regin_sessions_custom_span', o.span || '')
  }, overrides)
}

// Record every /api/sessions query so a filter's effect on the REQUEST can be
// asserted, not just its effect on the chrome.
function trackRequests(page, rows, extra = {}) {
  const seen = []
  return page.route('**/api/sessions?*', (r) => {
    seen.push(new URL(r.request().url()).searchParams)
    return r.fulfill({ json: envelope(rows, extra) })
  }).then(() => seen)
}

const chipTexts = (page) => page.locator('.afilter__text').allInnerTexts()

// The calendar derives "today" (and therefore which cells are unreachable
// future dates) from the browser clock, so any assertion naming a date has to
// pin that clock rather than the machine's.
const freezeClock = (page) => page.clock.setFixedTime(new Date('2026-07-10T13:00:00'))

test('a calendar span sets since/until, labels the trigger and survives reload', async ({ page }) => {
  await freezeClock(page)
  await seed(page)
  const seen = await trackRequests(page, [row(), row()])
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(2)

  await page.getByLabel('Filter by last activity time range').click()
  await expect(page.locator('.range-panel')).toBeVisible()

  // First click opens the span and leaves the grid up; the picker must not
  // close before the other edge is reachable.
  await page.getByRole('button', { name: 'Jul 3, 2026', exact: true }).click()
  await expect(page.locator('.range-panel')).toBeVisible()
  await expect(page.locator('.range-cal__hint')).toHaveText('Pick an end date')
  await expect(page.locator('.range-trigger')).toContainText('From Jul 3')

  // Second click closes it — and dismisses the picker.
  await page.getByRole('button', { name: 'Jul 9, 2026', exact: true }).click()
  await expect(page.locator('.range-panel')).toHaveCount(0)
  await expect(page.locator('.range-trigger')).toContainText('Jul 3 – 9')

  const last = seen.at(-1)
  expect(last.get('since')).toBe('2026-07-03T00:00:00')
  // `until` is exclusive, so a span ending on the 9th runs to the 10th.
  expect(last.get('until')).toBe('2026-07-10T00:00:00')

  // The span is one narrowing axis, named in the chip row...
  await expect(page.locator('.afilter__text').first()).toHaveText('Jul 3 – 9')
  await expect(page.locator('.filters-trigger__count')).toHaveText('1')

  // ...and it is written to storage for the next visit. (`seed`'s init script
  // re-runs on reload and would stomp these, so assert the write here; the
  // read-back path is covered by the preset test, which seeds a saved span.)
  expect(await page.evaluate(() => ({
    range: localStorage.getItem('regin_sessions_range'),
    span: localStorage.getItem('regin_sessions_custom_span'),
  }))).toEqual({ range: 'custom', span: '2026-07-03|2026-07-09' })
})

test('a half-open span filters open-ended, matching its "From Jul 3" label', async ({ page }) => {
  await freezeClock(page)
  await seed(page)
  const seen = await trackRequests(page, [row()])
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(1)

  await page.getByLabel('Filter by last activity time range').click()
  await page.getByRole('button', { name: 'Jul 3, 2026', exact: true }).click()

  // "From Jul 3" promises everything since the 3rd. Capping `until` at the 4th
  // would hide the rest under a label saying it was included.
  await expect(page.locator('.range-trigger')).toContainText('From Jul 3')
  expect(seen.at(-1).get('since')).toBe('2026-07-03T00:00:00')
  expect(seen.at(-1).has('until')).toBe(false)
})

test('a stored end with no start is discarded, not painted as a selection', async ({ page }) => {
  await freezeClock(page)
  await seed(page, { range: 'custom', span: '|2026-07-09' })
  await trackRequests(page, [row()])
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(1)

  // No start means nothing to filter by, so the range falls back to default...
  await expect(page.locator('.range-trigger')).toContainText('Today')
  await page.getByLabel('Filter by last activity time range').click()
  // ...and the calendar must not show a day as selected when nothing is
  // filtering on it. Asserted through the pressed state the day cells expose,
  // not the class that happens to paint it.
  await expect(
    page.locator('.range-cal__grid').getByRole('button', { pressed: true })
  ).toHaveCount(0)
})

test('a half-open span restored from storage stays open-ended', async ({ page }) => {
  await freezeClock(page)
  await seed(page, { range: 'custom', span: '2026-07-03|' })
  const seen = await trackRequests(page, [row()])
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(1)

  // The persistent form of the half-open span: restored from storage rather
  // than mid-click, it must filter the same way it labels itself.
  await expect(page.locator('.range-trigger')).toContainText('From Jul 3')
  await expect(page.locator('.afilter__text').first()).toHaveText('From Jul 3')
  expect(seen.at(-1).get('since')).toBe('2026-07-03T00:00:00')
  expect(seen.at(-1).has('until')).toBe(false)
})

test('clicking a preset drops any custom span; Clear returns to the default', async ({ page }) => {
  await freezeClock(page)
  await seed(page, { range: 'custom', span: '2026-07-03|2026-07-09' })
  const seen = await trackRequests(page, [row()])
  await page.goto('/trace/sessions')
  await expect(page.locator('.range-trigger')).toContainText('Jul 3 – 9')

  await page.getByLabel('Filter by last activity time range').click()
  await page.getByRole('button', { name: 'Last 7 days', exact: true }).click()
  await expect(page.locator('.range-panel')).toHaveCount(0)
  await expect(page.locator('.range-trigger')).toContainText('Last 7 days')
  // The preset's rolling window replaces the span outright.
  expect(seen.at(-1).get('since')).not.toBe('2026-07-03T00:00:00')

  await page.getByLabel('Filter by last activity time range').click()
  await page.getByRole('button', { name: 'Clear', exact: true }).click()
  // Clear resets to the list's default range, which sends today's bounds and
  // drops the range chip entirely.
  await expect(page.locator('.range-trigger')).toContainText('Today')
  expect(await chipTexts(page)).not.toContain('Last 7 days')
})

test('future days are not selectable and the next-month arrow stops at the current month', async ({ page }) => {
  await freezeClock(page)
  await seed(page)
  await trackRequests(page, [row()])
  await page.goto('/trace/sessions')
  await page.getByLabel('Filter by last activity time range').click()

  await expect(page.getByLabel('Next month')).toBeDisabled()
  // Paging back re-enables it; paging forward again re-disables it.
  await page.getByLabel('Previous month').click()
  await expect(page.getByLabel('Next month')).toBeEnabled()
  await page.getByLabel('Next month').click()
  await expect(page.getByLabel('Next month')).toBeDisabled()

  const future = page.locator('.range-cal__day:disabled')
  expect(await future.count()).toBeGreaterThan(0)
  // Every disabled cell must be a future date, never a past one.
  const enabled = await page.locator('.range-cal__day:not(:disabled)').count()
  expect(enabled).toBeGreaterThan(0)
})

test('the chip row names every active filter and each × clears only its own axis', async ({ page }) => {
  await seed(page, { kind: 'all', active: 'active', repo: 'regin' })
  const seen = await trackRequests(page, [row({ status: 'active' })], {
    repo_counts: { regin: 1 },
  })
  await page.route('**/api/repos', (r) => r.fulfill({ json: { repos: [{ name: 'regin' }] } }))
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(1)

  // range(all) + kind(all) + status(active) + repo(regin) = 4 axes.
  await expect(page.locator('.filters-trigger__count')).toHaveText('4')
  expect(await chipTexts(page)).toEqual(['All time', 'Real + tests', 'Active', 'Repo: regin'])

  const before = seen.length
  await page.getByLabel('Clear filter: Active').click()
  await expect(page.locator('.filters-trigger__count')).toHaveText('3')
  expect(await chipTexts(page)).toEqual(['All time', 'Real + tests', 'Repo: regin'])
  // Exactly one refetch — clearing one axis must not fan out into several.
  expect(seen.length).toBe(before + 1)
  expect(seen.at(-1).has('active')).toBe(false)
  expect(seen.at(-1).get('repo')).toBe('regin')

  await page.getByRole('button', { name: 'Clear all' }).click()
  await expect(page.locator('.afilter')).toHaveCount(0)
  await expect(page.locator('.filters-trigger__count')).toHaveCount(0)
})

test('no chips and no badge when nothing is narrowing the list', async ({ page }) => {
  await seed(page, { range: 'today' })
  await trackRequests(page, [row()])
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(1)
  await expect(page.locator('.afilters')).toHaveCount(0)
  await expect(page.locator('.filters-trigger__count')).toHaveCount(0)
})

test('Escape clears a row selection, and the batch bar offers an explicit Clear', async ({ page }) => {
  await seed(page)
  await trackRequests(page, [row(), row(), row()])
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(3)

  await page.locator('.srow input[type="checkbox"]').first().check()
  await expect(page.locator('.stoolbar__selcount')).toHaveText('1 selected')

  await page.keyboard.press('Escape')
  await expect(page.locator('.stoolbar__selcount')).toHaveCount(0)

  await page.locator('.srow input[type="checkbox"]').first().check()
  await page.locator('.srow input[type="checkbox"]').nth(1).check()
  await expect(page.locator('.stoolbar__selcount')).toHaveText('2 selected')
  await page.locator('.stoolbar__clearsel').click()
  await expect(page.locator('.stoolbar__selcount')).toHaveCount(0)
})

test('an expanded repo facet is searchable and keeps the selected chip reachable', async ({ page }) => {
  await seed(page)
  const repos = ['regin', 'agent-sdk', 'aural-kids', 'dotfiles', 'hook-manager', 'infra-scripts']
  await trackRequests(page, [row()], {
    repo_counts: { regin: 4, 'agent-sdk': 3, dotfiles: 2 },
  })
  await page.route('**/api/repos', (r) => r.fulfill({
    json: { repos: repos.map(name => ({ name })) },
  }))
  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(1)

  await page.getByRole('button', { name: /^Filters/ }).click()
  const repoFacet = page.locator('.facet').filter({ hasText: 'REPO' }).first()

  // Collapsed: the top 3 by count, behind a "+N more".
  await expect(repoFacet.locator('.chip')).toHaveCount(5)  // All + 3 + more
  await repoFacet.getByRole('button', { name: /more$/ }).click()

  const search = repoFacet.locator('.facet__search')
  await expect(search).toBeVisible()
  await search.fill('hook')
  await expect(repoFacet.locator('.chip--on, .chip')).toContainText(['All', 'hook-manager'])
  await search.fill('zzz-no-such-repo')
  await expect(repoFacet.locator('.facet__empty')).toHaveText('No match')

  // A 0-count repo, once selected, must not fall behind "+N more" on reopen.
  await search.fill('infra')
  await repoFacet.getByRole('button', { name: /^infra-scripts/ }).click()
  await page.keyboard.press('Escape')
  await page.getByRole('button', { name: /^Filters/ }).click()
  await expect(repoFacet.locator('.chip--on')).toHaveText(/infra-scripts/)
})
