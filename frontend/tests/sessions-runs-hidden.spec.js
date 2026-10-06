/**
 * Sessions list: non-interactive runs are folded away, not listed one row
 * per agent.
 *
 * A single fan-out (a captured Workflow run, a `memory-topic-classify` batch)
 * writes hundreds of `origin` in (workflow, llm-stage) sessions. Listing each
 * as its own row buries the handful a human actually opened, so the list asks
 * the server for `workflow=hide` and offers a "N hidden" pivot instead. These
 * tests pin the request the list makes — the axis is invisible in the DOM, so
 * only the query string proves it.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

const SERVER_NOW = '2026-07-10T13:00:00.000000'
const SERVER_NOW_UTC = '2026-07-10T09:00:00.000Z'

function row(overrides = {}) {
  const id = overrides.trace_id || randomUUID()
  return {
    trace_id: id,
    title: `RUNS_FIXTURE_${id.slice(0, 8)}`,
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

const runRow = () => row({
  origin: 'llm-stage', category: 'system', is_run: true,
  tags: [{ slug: 'system', source: 'auto', builtin: true }],
})

function envelope(rows, extra = {}) {
  return {
    items: rows, sessions: rows,
    pagination: { next_cursor: null, size: 50, has_next: false },
    // Counted WITHOUT the origin clause server-side, so the `system` facet
    // stays truthful while its rows are hidden.
    tag_counts: { user: 2, system: 133 },
    builtin_tags: [{ slug: 'system', label: 'System' }, { slug: 'user', label: 'User' }],
    repo_counts: {},
    total_count: rows.length,
    active_count: 0,
    server_now: SERVER_NOW,
    server_now_utc: SERVER_NOW_UTC,
    ...extra,
  }
}

async function seed(page, tag = '') {
  await page.addInitScript((t) => {
    localStorage.setItem('regin_sessions_group', 'flat')
    localStorage.setItem('regin_sessions_range', 'all')
    localStorage.setItem('regin_sessions_kind', 'real')
    localStorage.setItem('regin_sessions_status', '[]')
    localStorage.setItem('regin_sessions_tag', t)
    localStorage.setItem('regin_sessions_repo', 'all')
    localStorage.setItem('regin_sessions_runs', '0')
  }, tag)
}

// Every /api/sessions request the page made, as parsed query params.
async function recordQueries(page, reply) {
  const seen = []
  await page.route('**/api/sessions?*', (r) => {
    seen.push(new URL(r.request().url()).searchParams)
    r.fulfill({ json: reply(seen.length) })
  })
  return seen
}

test('the default list hides run rows, and the chip folds them back in', async ({ page }) => {
  await seed(page)
  const interactive = [row(), row()]
  const seen = await recordQueries(page, (n) => (n === 1
    ? envelope(interactive, { workflow_hidden_count: 133 })
    : envelope([...interactive, runRow(), runRow()])))

  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(2)

  // 1. The default request narrows the origin axis.
  expect(seen[0].get('workflow')).toBe('hide')
  expect(seen[0].get('tag')).toBeNull()

  // 2. Hiding is the DEFAULT, so it must not read as a user-applied filter:
  //    the only chip is the range this fixture widened.
  await expect(page.locator('.afilter__text')).toHaveText(['All time'])

  // 3. The chip names the count the server reported.
  const chip = page.getByRole('button', { name: /133 system runs hidden/ })
  await expect(chip).toBeVisible()

  // 4. Clicking drops `workflow=hide` — runs join the list rather than
  //    replacing it, so "sessions and runs interleaved" stays expressible —
  //    in EXACTLY one refetch, and the chip flips to the way back.
  await chip.click()
  await expect(page.locator('.srow')).toHaveCount(4)
  expect(seen).toHaveLength(2)
  expect(seen[1].get('workflow')).toBeNull()
  await expect(page.getByRole('button', { name: /Hide system runs/ })).toBeVisible()
})

test('a selected tag suppresses the hide, so tag=system is not self-cancelling',
  async ({ page }) => {
    await seed(page, 'system')
    const seen = await recordQueries(page, () => envelope([runRow(), runRow()]))

    await page.goto('/trace/sessions')
    await expect(page.locator('.srow')).toHaveCount(2)
    expect(seen[0].get('tag')).toBe('system')
    expect(seen[0].get('workflow')).toBeNull()
  })

test('a trace-id lookup is a deep link, so it never hides a run row', async ({ page }) => {
  await seed(page)
  const seen = await recordQueries(page, () => envelope([runRow()]))

  await page.goto('/trace/sessions')
  await expect(page.locator('.srow')).toHaveCount(1)

  await page.getByRole('button', { name: /Filters/ }).click()
  await page.locator('#filters-trace-id').fill('wf_7c224fdd')
  await page.locator('#filters-trace-id').press('Enter')

  await expect.poll(() => seen.length).toBeGreaterThan(1)
  const after = seen[seen.length - 1]
  expect(after.get('trace_id')).toBe('wf_7c224fdd')
  expect(after.get('workflow')).toBeNull()
})
