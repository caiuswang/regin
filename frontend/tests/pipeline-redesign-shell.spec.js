/**
 * Feature #110 · S2 — the Pipeline shell, Overview and Features tab.
 *
 * Every API the pages read is stubbed, including the writes: a spec that let a
 * decline reach the scratch server would be a mutation, not an observation.
 */
import { existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test, expect } from './auth-fixture.js'

const SRC = join(dirname(fileURLToPath(import.meta.url)), '..', 'src')

const COUNTS = { tasks: 128, features: 24, defects: 40 }

const part = (key, label, n, highlighted = false) => ({ key, label, n, highlighted })
const LOOP = {
  stages: [
    { label: 'Tasks', total: 128, parts: [part('idea', 'Ideas', 42), part('armed', 'Armed', 9),
      part('running', 'Running', 2), part('done', 'Done', 71),
      part('failed', 'Failed', 4, true)] },
    { label: 'Features', total: 24, parts: [part('spec', 'Spec', 5), part('building', 'Building', 7),
      part('shipped', 'Shipped', 10), part('escaped', 'Escaped', 2, true)] },
    { label: 'Defects', total: 40, parts: [part('triage', 'Triage', 3, true), part('open', 'Open', 6),
      part('closed', 'Closed', 31)] },
  ],
  fix_tasks: 4,
}

const runRow = (id, title, action, extra = {}) => ({
  id, title, status: 'armed', priority: 'high', kind: 'task', feature_id: 38,
  feature_title: 'Span search facets', step: { n: 4, of: 7 }, fixes_defects: [], action, ...extra,
})

const minutesAgo = n => new Date(Date.now() - n * 60000).toISOString()
const RUNNING = {
  total: 2,
  rows: [
    { id: 414, title: 'Reconnect tail when the socket drops mid-turn', status: 'running', priority: 'urgent', kind: 'fix',
      feature_id: 31, feature_title: 'Live tail reconnect', step: null, fixes_defects: [89],
      live: { run_id: 7, trace_id: 'tr-414', attempt: 2, started_at: minutesAgo(3) }, turns: 14, cost_usd: 0.82 },
    { id: 408, title: 'Index tool-span arguments for search', status: 'running', priority: 'high', kind: 'task',
      feature_id: 38, feature_title: 'Span search facets', step: { n: 3, of: 7 }, fixes_defects: [],
      live: { run_id: 6, trace_id: null, attempt: 1, started_at: minutesAgo(12) }, turns: null, cost_usd: null },
  ],
}

const TRIAGE = [
  { id: 94, round: 2, opened_at: '2026-09-25T10:00:00', symptom: 'Search palette returns spans from a deleted repo after re-ingest; clicking one 404s.', paths: ['lib/search/index.py', 'SpanPreview.vue'] },
  { id: 93, round: 1, opened_at: '2026-09-25T08:00:00', symptom: 'Arming a task that depends on an archived task leaves it armed forever, with no warning.', paths: ['lib/tasks/deps.py'] },
  { id: 92, round: 1, opened_at: '2026-09-24T08:00:00', symptom: 'Feature cost reads $0.00 instead of "none declared" when every session is unpriced.', paths: ['lib/features/ledger.py'] },
]

const PANEL_ROW = (id, name, extra) => ({
  id, scope: String(id), name, status: 'active', escaped: 0, last_activity: '2026-09-27T08:00:00',
  stage_bar: { spec: 2, building: 5, shipped: 6, escaped: 1 }, features: 14, open_tasks: 38, open_defects: 6, ...extra,
})

function overview({ empty = false, project = null, loop = LOOP } = {}) {
  const body = {
    scope: project ? String(project) : 'all',
    loop,
    running: empty ? { total: 0, rows: [] } : RUNNING,
    run_next: empty ? { total: 0, ready: 0, rows: [] } : {
      total: 9, ready: 4, blocked: 5,
      rows: [
        runRow(415, 'Stale lease blocks re-arm after runner crash', { kind: 'run' }, { fixes_defects: [91], feature_id: 31, feature_title: 'Live tail reconnect', step: null, status: 'queued' }),
        runRow(412, 'Retry budget for runner restarts', { kind: 'run' }),
        runRow(409, 'Facet counts for tool spans', { kind: 'wait', on: 408, on_status: 'running' }, { step: { n: 5, of: 7 } }),
      ],
    },
    needs_decision: empty ? { waiting: 0, rows: [] } : { waiting: 3, rows: TRIAGE },
  }
  if (!project) {
    body.projects = empty
      ? { sort: 'attention', total: 32, active: 0, quiet: 32, rows: [] }
      : { sort: 'attention', total: 32, active: 2, quiet: 30,
          rows: [PANEL_ROW(3, 'regin-core', { escaped: 1 }), PANEL_ROW(4, 'regin-web', { open_defects: 3 })] }
  } else {
    body.project_head = { id: project, name: 'regin-core', breadcrumb: ['All projects', 'regin-core'],
      stage_bar: { spec: 2, building: 5, shipped: 6, escaped: 1 }, shipped_pct: 57,
      open_tasks: 38, open_defects: 6, spent_usd: 184 }
    body.project_features = { total: 14, rows: FEATURES.slice(0, 5) }
  }
  return body
}

const feature = (id, title, stage, extra = {}) => ({
  id, title, origin: 'local', external_ref: `CAI-${200 + id}`, stage, blurb: '', rounds: 2, sessions: 3,
  tasks: 6, tasks_done: 3, cost_usd: 12.5, gaps: {}, defects: { total: 1, open: 0, escapes: 0 },
  last_activity: '2026-09-27T06:00:00', ...extra,
})
const FEATURES = [
  feature(44, 'Project progress rollups', 'building'),
  feature(31, 'Live tail reconnect', 'escaped', { rounds: 5, tasks: 11, tasks_done: 9, defects: { total: 5, open: 3, escapes: 2 } }),
  feature(38, 'Span search facets', 'building'),
  feature(35, 'Declare-session dialog', 'shipped'),
  feature(46, 'Memory topic routing', 'spec', { external_ref: null, cost_usd: null }),
  feature(33, 'Payload log retention', 'shipped'),
]
const STAGE_COUNTS = { all: 24, spec: 5, building: 7, shipped: 10, escaped: 2 }

const defect = (id, extra = {}) => ({
  id, feature_id: null, symptom: `symptom ${id}`, feature_title: null, status: 'open', round: 1,
  blamed_event_kind: null, missing_check: null, suspect_paths: [], fix_task_id: null, fix_state: null,
  triage_state: null, images: [], opened_at: '2026-09-25T10:00:00', ...extra,
})

function json(route, body, status = 200) {
  return route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
}

/**
 * One handler for every API the three tabs read. `state` is mutable so a test
 * can make a write change what the next read returns.
 */
async function stubPipeline(page, opts = {}) {
  const state = { triage: TRIAGE.map(t => defect(t.id, { round: t.round, symptom: t.symptom, suspect_paths: t.paths, opened_at: t.opened_at })), posts: [], ...opts }
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    const p = url.pathname
    const method = route.request().method()
    if (method === 'POST' && /^\/api\/(defects|tasks)\//.test(p)) {
      state.posts.push({ path: p, body: route.request().postDataJSON() })
      const m = p.match(/^\/api\/defects\/(\d+)\/(decline|snooze|attribute|merge)$/)
      if (m) state.triage = state.triage.filter(d => d.id !== Number(m[1]))
      return json(route, { ok: true })
    }
    if (p === '/api/pipeline/counts') return json(route, { counts: COUNTS })
    if (p === '/api/pipeline/overview') {
      const project = url.searchParams.get('project')
      return json(route, overview({ empty: state.empty, loop: state.loop, project: project ? Number(project) : null }))
    }
    if (p === '/api/pipeline/projects') {
      return json(route, { items: [
        { id: 3, scope: '3', name: 'regin-core', features: { by_stage: { spec: 1, building: 2, shipped: 3, escaped: 1 } }, defects: { open: 6, total: 9 }, tasks: { by_status: {} }, last_activity: '2026-09-27' },
        { id: 7, scope: '7', name: 'docs-site', features: { by_stage: { spec: 1, building: 1 } }, defects: { open: 0, total: 0 }, tasks: { by_status: {} }, last_activity: '2026-09-20' },
        { id: null, scope: 'none', name: null, features: { by_stage: { spec: 1 } }, defects: { open: 0, total: 0 }, tasks: { by_status: {} } },
      ], pagination: { total: 3, page: 0, size: 100 }, population: 33 })
    }
    if (p === '/api/repos') return json(route, { repos: [{ id: 3, name: 'regin-core', path: '/r' }, { id: 7, name: 'docs-site', path: '/d' }] })
    if (p === '/api/features') {
      const stage = url.searchParams.get('stage')
      const rows = stage ? FEATURES.filter(f => f.stage === stage) : FEATURES
      return json(route, { items: rows, pagination: { total: stage ? STAGE_COUNTS[stage] : 24, page: 0, size: 50, has_next: false, has_prev: false },
        population: 24, applied: { sort: url.searchParams.get('sort') || 'activity:desc' },
        extras: { stage_counts: STAGE_COUNTS, unattributed_defects: state.triage.length + 5,
          attribution: { sessions_pct: 61, declared_sessions: 61, all_sessions: 100, declared_usd: 412.2, all_usd: 655 } } })
    }
    if (p === '/api/defects') {
      if (url.searchParams.get('triage') === 'snoozed') {
        return json(route, { items: [], pagination: { total: state.snoozed ?? 2 } })
      }
      if (url.searchParams.get('status') === 'open' && !url.searchParams.get('feature_id')) {
        // Open outside the queue (LOOP's Defects → Open is 6); without `queue=out` the queue joins them.
        const out = url.searchParams.get('queue') === 'out'
        const rows = Array.from({ length: out ? 6 : 9 }, (_, i) => defect(300 + i, { queue_position: out ? null : 'queue' }))
        return json(route, { items: rows, pagination: { total: rows.length, page: 0, size: 50 }, population: 40 })
      }
      if (url.searchParams.get('scope') === 'triage') {
        return json(route, { items: state.triage, pagination: { total: state.triage.length } })
      }
      if (url.searchParams.get('feature_id') === '31') {
        return json(route, { items: [
          defect(91, { feature_id: 31, symptom: 'Tail stalls after laptop sleep; no reconnect', fix_task_id: 415, fix_state: 'queued' }),
          defect(89, { feature_id: 31, symptom: 'Socket drop mid-turn loses the last span', fix_task_id: 414, fix_state: 'running' }),
          defect(86, { feature_id: 31, symptom: 'Duplicate spans after reconnect' }),
        ], pagination: { total: 3 } })
      }
      return json(route, { items: [], pagination: { total: 0 } })
    }
    const one = p.match(/^\/api\/defects\/(\d+)$/)
    if (one) return json(route, { defect: defect(Number(one[1]), { symptom: 'Search palette returns spans from a deleted repo' }) })
    if (p === '/api/tasks/counts' && url.searchParams.get('feature_id') === '31') {
      return json(route, { by_status: { idea: 2, armed: 2, queued: 0, running: 1, done: 9, failed: 0 } })
    }
    if (p === '/api/tasks' && url.searchParams.get('group') === 'next_step') {
      if (url.searchParams.get('feature_id') !== '31') {
        return json(route, { groups: ['ready', 'running', 'blocked', 'ideas'].map(key => ({ key, n: 0, rows: [], more: 0 })) })
      }
      const task = (id, title, status, extra = {}) => ({ id, title, status, waits_on: null, live: null, ...extra })
      return json(route, { open: 5, groups: [
        { key: 'ready', n: 1, more: 0, rows: [task(416, 'Backoff jitter for reconnect attempts', 'armed')] },
        { key: 'running', n: 1, more: 0, rows: [task(414, 'Reconnect tail when the socket drops mid-turn', 'running',
          { live: { run_id: 7, trace_id: 'tr-414', attempt: 2, started_at: minutesAgo(3) } })] },
        { key: 'blocked', n: 1, more: 0, rows: [task(410, 'Drop the orphaned worktree on cancel', 'armed',
          { waits_on: { id: 415, status: 'queued' } })] },
        { key: 'ideas', n: 2, more: 0, rows: [task(420, 'An idea', 'idea')] },
      ] })
    }
    return route.fallback()
  })
  return state
}

test('test_pipeline_shell_three_tabs_one_line_header', async ({ page }) => {
  await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/pipeline')
  await expect(page).toHaveURL(/\/overview$/)

  const header = page.getByTestId('pipeline-header')
  const title = header.locator('.page-title')
  const chip = page.getByTestId('pipeline-project')
  const updated = page.getByTestId('pipeline-updated')
  const create = page.locator('#pipeline-actions [data-testid="task-new"]')
  await expect(title).toHaveText('Pipeline')
  await expect(chip).toContainText('All projects')
  await expect(updated).toHaveText(/^Updated /)
  await expect(create).toContainText('New task')
  await expect(create.locator('kbd')).toHaveText('c')
  // One row: every piece shares a vertical band with the title.
  const band = await title.boundingBox()
  for (const el of [chip, updated, create]) {
    const box = await el.boundingBox()
    expect(box.y).toBeLessThan(band.y + band.height)
    expect(box.y + box.height).toBeGreaterThan(band.y)
  }
  await expect(header.locator('.page-eyebrow, .page-subtitle')).toHaveCount(0)

  const tabs = page.getByTestId('pipeline-tabs').locator('a')
  await expect(tabs).toHaveText(['Overview', 'Tasks 128', 'Features 24'])
  await expect(page.getByTestId('pipeline-tab-overview')).toHaveClass(/is-active/)
})

test('test_pipeline_retired_tabs_are_gone', async ({ page }) => {
  await stubPipeline(page)
  for (const path of ['/projects', '/projects/3']) {
    await page.goto(path)
    await expect(page.locator('.page-title')).toHaveText('Page not found')
  }
  for (const file of ['views/ProjectsView.vue', 'views/ProjectPanelView.vue', 'components/projects']) {
    expect(existsSync(join(SRC, file)), file).toBe(false)
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/features')
  await page.getByTestId('triage-permalink-94').click()
  await expect(page).toHaveURL(/\/defects\/94$/)
  await expect(page.getByTestId('defect-detail')).toBeVisible()
  await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
  await expect(page.getByTestId('pipeline-tab-features')).toHaveClass(/is-active/)
  // #119 D2: the defects list is back as a child of the shell, not a tab.
  await page.goto('/defects')
  await expect(page.getByTestId('defects-list-page')).toBeVisible()
  await expect(page.getByTestId('pipeline-tab-features')).toHaveClass(/is-active/)
  await expect(page.getByTestId('pipeline-tabs').locator('a')).toHaveCount(3)
})

test('test_pipeline_hotkeys', async ({ page }) => {
  await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/overview')
  await expect(page.getByTestId('overview-loop')).toBeVisible()

  await page.keyboard.press('c')
  await expect(page.getByRole('dialog', { name: 'New task' })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await page.keyboard.press('p')
  await expect(page.getByTestId('project-picker')).toBeVisible()
  await expect(page.getByTestId('project-picker-search')).toBeFocused()
  await expect(page.getByTestId('project-picker-search')).toHaveAttribute('placeholder', 'Find among 32 projects')
  await page.keyboard.press('Escape')

  // The Overview has no search of its own: "/" opens the chip's picker.
  await page.keyboard.press('/')
  await expect(page.getByTestId('project-picker-search')).toBeFocused()
  await page.keyboard.press('Escape')
  await page.locator('body').click({ position: { x: 5, y: 5 } })

  await page.getByTestId('pipeline-tab-features').click()
  await expect(page.locator('#pipeline-actions [data-testid="declare-feature"]')).toContainText('New feature')
  await expect(page.locator('#pipeline-actions [data-testid="task-new"]')).toHaveCount(0)
  await page.keyboard.press('/')
  await expect(page.getByTestId('list-search')).toBeFocused()
  await page.locator('body').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('c')
  await expect(page.getByRole('dialog', { name: /Declare a feature/ })).toBeVisible()
})

test('test_overview_panels_and_empty_states', async ({ page }) => {
  const state = await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/overview')

  // Loop totals: the parts (read off the bar's accessible name) sum to the
  // total, and Tasks/Features match the tabs.
  for (const [stage, tab] of [['tasks', 128], ['features', 24], ['defects', null]]) {
    const card = page.getByTestId(`loop-${stage}`)
    const total = Number(await card.getByTestId('loop-total').innerText())
    const name = await card.getByTestId('loop-bar').getAttribute('aria-label')
    expect(name.split(', ').map(p => Number(p.split(' ').pop())).reduce((a, b) => a + b, 0)).toBe(total)
    if (tab != null) {
      await expect(page.getByTestId(`pipeline-count-${stage}`)).toHaveText(String(total))
      expect(total).toBe(tab)
    }
  }
  await expect(page.getByTestId('loop-note')).toContainText('4 fix tasks carry open defects back into the queue')

  // Running now heads the Run next card: newest attempt first, one Watch per row.
  const runningNow = page.getByTestId('running-now')
  await expect(page.getByTestId('running-now-count')).toHaveText('2')
  await expect(runningNow.getByRole('link', { name: 'Open Live →' })).toHaveAttribute('href', '/live')
  await expect(runningNow.locator('li')).toHaveCount(2)
  const retry = page.getByTestId('running-now-row-414')
  await expect(retry).toContainText('Fix for D-89 · #31 Live tail reconnect · attempt 2')
  await expect(retry).toContainText('live · 3m')
  await expect(retry).toContainText('14 turns · $0.82')
  await expect(page.getByTestId('running-now-watch-414')).toHaveAttribute('href', '/trace/sessions/tr-414')
  await expect(page.getByTestId('running-now-row-408')).toContainText('#38 Span search facets · step 3 of 7')
  await expect(page.getByTestId('running-now-row-408')).not.toContainText('attempt')
  await expect(page.getByTestId('running-now-watch-408')).toHaveAttribute('href', '/tasks/408')
  // R4a: a running row offers Stop beside Watch (feature #119).
  await expect(page.getByTestId('running-now-stop-414')).toBeVisible()
  const runningBox = await runningNow.boundingBox()
  const runNextHead = await page.getByTestId('run-next').locator('#rn-title').boundingBox()
  expect(runningBox.y + runningBox.height).toBeLessThanOrEqual(runNextHead.y)
  // .pl-panel clips, so the page-level overflow check cannot see a row that overflows its card.
  for (const row of await runningNow.locator('li').all()) {
    expect(await row.evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0)
  }

  // Run next: a waiting row is dimmed, names what it waits on, has no Run.
  const waiting = page.getByTestId('run-next-row-409')
  await expect(page.getByTestId('run-next-wait-409')).toHaveText(/waits on #408 \(running\)/)
  await expect(page.getByTestId('run-next-wait-409')).toHaveAttribute('href', '/tasks/408')
  await expect(waiting).toHaveClass(/is-waiting/)
  expect(Number(await waiting.evaluate(el => getComputedStyle(el).opacity))).toBeLessThan(1)
  await expect(waiting.getByRole('button')).toHaveCount(0)
  await expect(page.getByTestId('run-next-run-412')).toBeVisible()
  await expect(page.getByTestId('run-next-count')).toHaveText('4')
  await expect(page.getByTestId('run-next-all')).toHaveText('All in Tasks →')
  await expect(page.getByTestId('needs-decision-row-94')).toContainText('D-94')
  await expect(page.getByTestId('needs-decision-attribute-94')).toBeVisible()
  await expect(page.getByTestId('projects-panel').locator('li')).toHaveCount(2)

  // Pick a project: the Projects panel becomes the breadcrumb head + ≤5 features.
  await page.getByTestId('pipeline-project').click()
  await page.getByTestId('project-picker-row-3').click()
  await expect(page).toHaveURL(/project=3/)
  await expect(page.getByTestId('projects-panel')).toHaveCount(0)
  await expect(page.getByTestId('project-head')).toContainText('regin-core')
  await expect(page.getByTestId('project-head')).toContainText('57% shipped')
  const rows = page.getByTestId('project-features').locator('tbody tr')
  expect(await rows.count()).toBeLessThanOrEqual(5)
  await expect(page.getByTestId('project-features-all')).toHaveText('All 14 in Features →')
  await expect(page.getByTestId('pipeline-project')).toContainText('regin-core')
  await page.getByTestId('pipeline-project-clear').click()
  await expect(page.getByTestId('projects-panel')).toBeVisible()

  // Empty: nothing armed or queued leaves a line, not a missing panel.
  state.empty = true
  await page.reload()
  await expect(page.getByTestId('run-next')).toBeVisible()
  await expect(page.getByTestId('run-next-empty')).toBeVisible()
  await expect(page.getByTestId('running-now')).toHaveCount(0)
  await expect(page.getByTestId('needs-decision-empty')).toBeVisible()
  await expect(page.getByTestId('projects-empty')).toHaveText('All 32 projects quiet')
  await expect(page.getByTestId('projects-counts')).toHaveText('0 active · 32 quiet')
  await expect(page.getByTestId('loop-note')).toBeVisible()
})

// Feature #139: under the `pixel` project these assertions are skipped because
// they pin classic's pixels, not behaviour — test_overview_colour_budget whole
// (pixel's budget is pipeline-theme-overview.spec.js), the text-transform, token
// colours, bar fills, note colour and button looks in
// test_overview_quiet_loop_facts_and_links, and the `ol--compact` class in
// test_pipeline_mobile_overview_features_triage.
test('test_overview_quiet_loop_facts_and_links', async ({ page, pipelineTheme }) => {
  const classic = pipelineTheme !== 'pixel'
  const state = await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/overview')

  // One card, three columns: no arrows, no legend, no per-stage cards.
  const loop = page.getByTestId('overview-loop')
  await expect(loop.locator('article')).toHaveCount(3)
  await expect(loop.locator('svg.lucide-arrow-right, dl, dt, .ol-dot')).toHaveCount(0)
  const tops = await loop.locator('article').evaluateAll(els => els.map(e => Math.round(e.getBoundingClientRect().top)))
  expect(new Set(tops).size).toBe(1)
  if (classic) await expect(page.getByTestId('loop-tasks').locator('h2')).toHaveCSS('text-transform', 'none')

  // Facts: only what moves or asks for a decision; zero facts dropped.
  const facts = stage => page.getByTestId(`loop-${stage}`).locator('[data-testid^="loop-fact-"]')
  await expect(facts('tasks')).toHaveText(['2 running', '4 ready', '4 failed'])
  await expect(facts('features')).toHaveText(['7 building', '5 in spec', '2 escaped'])
  await expect(facts('defects')).toHaveText(['6 open', '3 to triage'])
  const colourOf = id => page.getByTestId(id).evaluate(el => getComputedStyle(el).color)
  const token = name => page.evaluate((n) => {
    const probe = document.createElement('span')
    probe.style.color = `var(${n})`
    document.body.appendChild(probe)
    const c = getComputedStyle(probe).color
    probe.remove()
    return c
  }, name)
  if (classic) {
    expect(await colourOf('loop-fact-failed')).toBe(await token('--color-danger'))
    expect(await colourOf('loop-fact-escaped')).toBe(await token('--color-warning-strong'))
    expect(await colourOf('loop-fact-triage')).toBe(await token('--color-warning-strong'))
    expect(await colourOf('loop-fact-running')).toBe(await token('--color-fg-muted'))
  }

  // I11: every part is still in the bar's accessible name.
  await expect(page.getByTestId('loop-tasks').getByTestId('loop-bar'))
    .toHaveAttribute('aria-label', 'Ideas 42, Armed 9, Running 2, Done 71, Failed 4')
  // Bars are neutral except the parts that need a human or failed.
  if (classic) {
    const fills = await page.getByTestId('loop-tasks').locator('[data-part]').evaluateAll(els =>
      Object.fromEntries(els.map(e => [e.dataset.part, getComputedStyle(e).backgroundColor])))
    expect(fills.failed).toBe(await page.evaluate(() => {
      const p = document.createElement('span'); p.style.backgroundColor = 'var(--color-danger)'
      document.body.appendChild(p); const c = getComputedStyle(p).backgroundColor; p.remove(); return c
    }))
    expect(fills.done).not.toBe(fills.failed)

    // Loop note: the card's footer row, subtle text, no fill.
    const note = page.getByTestId('loop-note')
    expect(await note.evaluate(el => getComputedStyle(el).color)).toBe(await token('--color-fg-subtle'))
    expect(await note.evaluate(el => getComputedStyle(el).backgroundColor)).toBe('rgba(0, 0, 0, 0)')
  }

  // No caption sentence under any panel heading.
  await expect(page.getByText('Armed and queued · dependency-clear first')).toHaveCount(0)
  await expect(page.getByText('Reports nobody has routed')).toHaveCount(0)
  await expect(page.getByText(/Needs attention first/)).toHaveCount(0)

  // Decision rows: one action, Attribute; a one-line symptom; "Triage all →".
  const row = page.getByTestId('needs-decision-row-94')
  await expect(row.getByRole('button')).toHaveCount(1)
  await expect(row.getByRole('button')).toHaveText('Attribute')
  const symptom = row.getByTestId('needs-decision-symptom')
  expect(await symptom.evaluate(el => getComputedStyle(el).whiteSpace)).toBe('nowrap')
  await expect(page.getByTestId('needs-decision-count')).toHaveText('3')
  await expect(page.getByTestId('needs-decision-triage')).toHaveText('Triage all →')

  // Projects panel: counts in the header, no search/sort/legend/footer.
  await expect(page.getByTestId('projects-counts')).toHaveText('2 active · 30 quiet')
  await expect(page.getByTestId('projects-all')).toHaveText('All 32 projects →')
  const projects = page.getByTestId('projects-panel')
  await expect(projects.locator('input, [role="group"], footer')).toHaveCount(0)
  await expect(page.getByTestId('projects-attention-3')).toHaveText('1 escaped · 6 open defects')
  if (classic) expect(await colourOf('projects-attention-3')).toBe(await token('--color-warning-strong'))
  await page.getByTestId('projects-all').click()
  await expect(page.getByTestId('project-picker')).toBeVisible()
  await page.keyboard.press('Escape')

  // Run and Watch are bordered neutral buttons with primary text.
  if (classic) {
    const primary = await token('--color-primary')
    const border = await page.evaluate(() => {
      const p = document.createElement('span'); p.style.color = 'var(--color-border)'
      document.body.appendChild(p); const c = getComputedStyle(p).color; p.remove(); return c
    })
    for (const id of ['run-next-run-412', 'running-now-watch-414', 'needs-decision-attribute-94']) {
      const look = await page.getByTestId(id).evaluate(el => {
        const cs = getComputedStyle(el)
        return { color: cs.color, width: cs.borderTopWidth, border: cs.borderTopColor }
      })
      if (id !== 'needs-decision-attribute-94') expect(look.color, id).toBe(primary)
      expect(look.width, id).not.toBe('0px')
      expect(look.border, id).toBe(border)
    }
  }

  // "N escaped" opens Features on the Escaped stage; "N to triage" the rail.
  await page.getByTestId('loop-fact-escaped').click()
  await expect(page).toHaveURL(/\/features\?stage=escaped/)
  await expect(page.getByTestId('feature-stage-tabs').getByRole('tab', { selected: true })).toContainText('Escaped')
  await page.goBack()
  await page.getByTestId('loop-fact-triage').click()
  await expect(page).toHaveURL(/\/features$/)

  // A stage with nothing failed drops the failed fact.
  state.loop = { ...LOOP, stages: [{ ...LOOP.stages[0], total: 124, parts: LOOP.stages[0].parts.map(p =>
    (p.key === 'failed' ? { ...p, n: 0 } : p)) }, LOOP.stages[1], LOOP.stages[2]] }
  await page.goto('/overview')
  await expect(facts('tasks')).toHaveText(['2 running', '4 ready'])
  await expect(page.getByTestId('loop-fact-failed')).toHaveCount(0)
})

// I10: /overview draws only blue (links, action text), violet (the live
// marker), amber (a human must decide) and red (failed) — measured against the
// resolved tokens, not by hue: warning-strong and orange sit ~1° apart.
async function colourBudgetViolations(page) {
  return page.evaluate(() => {
    const resolve = (prop, value) => {
      const probe = document.createElement('span')
      probe.style[prop] = value
      document.body.appendChild(probe)
      const c = getComputedStyle(probe)[prop]
      probe.remove()
      return c
    }
    const allowed = new Set(['--color-primary', '--color-agent', '--color-warning', '--color-warning-strong',
      '--color-danger'].map(t => resolve('color', `var(${t})`)))
    // A tint is a *-soft token or a light ramp step: at chroma ~0.014 blue-50
    // is indistinguishable from slate-200 by measurement, so name them.
    const RAMPS = ['amber', 'orange', 'yellow', 'red', 'rose', 'pink', 'fuchsia', 'purple', 'violet', 'indigo',
      'blue', 'sky', 'cyan', 'teal', 'emerald', 'green', 'lime']
    const tints = new Set([
      ...['info', 'success', 'warning', 'danger', 'agent', 'orange', 'primary'].map(t => `--color-${t}-soft`),
      ...RAMPS.flatMap(r => [50, 100, 200, 300].map(n => `--color-${r}-${n}`)),
    ].map(t => resolve('backgroundColor', `var(${t})`)).filter(c => c !== 'rgba(0, 0, 0, 0)'))
    // Tailwind v4 tokens compute to oklch(); the slate neutrals carry a little
    // chroma (≤0.05), so "chromatic" means clearly more than that.
    const chromatic = (c) => {
      if (!c.includes('(') || c.startsWith('url(')) return false   // none, transparent, a paint server
      const num = x => Number(x)
      let m = c.match(/^rgba?\(([\d.]+),? ([\d.]+),? ([\d.]+)(?:,? \/? ?([\d.]+))?\)$/)
      if (m) {
        if (m[4] !== undefined && num(m[4]) === 0) return false
        const [r, g, b] = [m[1], m[2], m[3]].map(num)
        return Math.max(r, g, b) - Math.min(r, g, b) > 60
      }
      m = c.match(/^oklch\(([\d.]+) ([\d.]+) ([\d.]+|none)(?: \/ ([\d.]+))?\)$/)
      if (m) return !(m[4] !== undefined && num(m[4]) === 0) && num(m[2]) > 0.08
      m = c.match(/^oklab\(([\d.]+) (-?[\d.]+) (-?[\d.]+)(?: \/ ([\d.]+))?\)$/)
      if (m) return !(m[4] !== undefined && num(m[4]) === 0) && Math.hypot(num(m[2]), num(m[3])) > 0.08
      m = c.match(/^color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)(?: \/ ([\d.]+))?\)$/)
      if (m) {
        if (m[4] !== undefined && num(m[4]) === 0) return false
        const [r, g, b] = [m[1], m[2], m[3]].map(num)
        return Math.max(r, g, b) - Math.min(r, g, b) > 0.235
      }
      return true
    }
    const root = document.querySelector('.content-scroll') || document.body
    const bad = []
    // Blue is a family, not one token: the shell's primary button paints a
    // blue-800→500 gradient. Blue sits far from every other hue, so it can be
    // judged by hue; amber, orange and red cannot, so they match tokens exactly.
    const blue = (c) => {
      const lch = c.match(/^oklch\([\d.]+ [\d.]+ ([\d.]+)/)
      if (lch) return Number(lch[1]) >= 250 && Number(lch[1]) <= 275
      const m = c.match(/^rgba?\(([\d.]+),? ([\d.]+),? ([\d.]+)/)
      if (!m) return false
      const [r, g, b] = [m[1], m[2], m[3]].map(Number)
      const max = Math.max(r, g, b), min = Math.min(r, g, b)
      if (max !== b || max === min) return false
      const hue = 60 * ((r - g) / (max - min) + 4)
      return hue >= 205 && hue <= 240
    }
    const ok = c => allowed.has(c) || blue(c)
    const COLOUR_FN = /(?:rgba?|oklch|oklab|color)\([^()]*\)/g
    const check = (name, cs, isSvg) => {
      const props = ['color', 'backgroundColor', 'borderTopColor', 'borderRightColor', 'borderBottomColor',
        'borderLeftColor', 'textDecorationColor']
      if (isSvg) props.push('fill', 'stroke')
      for (const prop of props) {
        const value = cs[prop]
        if (chromatic(value) && !ok(value)) bad.push(`${name} ${prop}=${value}`)
      }
      for (const prop of ['boxShadow', 'backgroundImage']) {
        for (const value of cs[prop].match(COLOUR_FN) || []) {
          if (chromatic(value) && !ok(value)) bad.push(`${name} ${prop} has ${value}`)
        }
      }
      if (tints.has(cs.backgroundColor)) bad.push(`${name} tinted background ${cs.backgroundColor}`)
    }
    for (const el of root.querySelectorAll('*')) {
      if (!el.checkVisibility()) continue
      const name = `${el.tagName.toLowerCase()}${el.dataset.testid ? `[${el.dataset.testid}]` : ''}.${[...el.classList].slice(-2).join('.')}`
      check(name, getComputedStyle(el), el instanceof SVGElement)
      for (const pseudo of ['::before', '::after']) {
        const cs = getComputedStyle(el, pseudo)
        if (cs.content !== 'none' && cs.content !== 'normal') check(`${name}${pseudo}`, cs, false)
      }
    }
    return [...new Set(bad)]
  })
}

test('test_overview_colour_budget', async ({ page, pipelineTheme }) => {
  test.skip(pipelineTheme === 'pixel', 'pins the classic palette')
  await stubPipeline(page)
  const cases = [
    [1440, '/overview', 'projects-panel'],
    [1440, '/overview?project=3', 'project-head'],
    [390, '/overview', 'projects-panel'],
  ]
  for (const theme of ['light', 'dark']) {
    await page.addInitScript((t) => localStorage.setItem('regin_theme', t), theme)
    for (const [width, path, ready] of cases) {
      await page.setViewportSize({ width, height: 900 })
      await page.goto(path)
      await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
      await expect(page.getByTestId(ready)).toBeVisible()
      expect(await colourBudgetViolations(page), `${path} @ ${width} ${theme}`).toEqual([])
    }
  }
  // The live marker's ring is the one soft tint on the page.
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/overview')
  const ring = await page.locator('.rnw-dot').first().evaluate(el => getComputedStyle(el).boxShadow)
  const agentSoft = await page.evaluate(() => {
    const p = document.createElement('span'); p.style.color = 'var(--color-agent-soft)'
    document.body.appendChild(p); const c = getComputedStyle(p).color; p.remove(); return c
  })
  expect(ring).toContain(agentSoft)
})

test('test_features_stage_tabs_expand_and_triage_keys', async ({ page, pipelineTheme }) => {
  const state = await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/features')

  // Stage tabs: server counts, and All is the sum of the four.
  const counts = {}
  for (const key of ['all', 'spec', 'building', 'shipped', 'escaped']) {
    counts[key] = Number(await page.getByTestId(`feature-stage-count-${key}`).innerText())
  }
  expect(counts).toEqual(STAGE_COUNTS)
  expect(counts.spec + counts.building + counts.shipped + counts.escaped).toBe(counts.all)
  await expect(page.getByTestId('pipeline-subline')).toHaveText('61% of sessions attributed · $412 of $655')

  // Stage cell: label, step and one segment per step reached; escaped reads
  // "Shipped · escaped" with a warning last segment. Round pips carry no tone.
  // Classic pixels: the pixel theme draws this cell as its level track, which
  // pipeline-theme-features.spec.js checks instead.
  const stageOf = id => page.getByTestId(`feature-row-${id}`).getByTestId('stage-segments')
  if (pipelineTheme !== 'pixel') {
    await expect(stageOf(44)).toContainText('Building')
    await expect(stageOf(44).getByTestId('stage-step')).toHaveText('2 of 3')
    await expect(stageOf(44).locator('.ss-seg')).toHaveCount(2)
    await expect(stageOf(46).getByTestId('stage-step')).toHaveText('1 of 3')
    await expect(stageOf(46).locator('.ss-seg')).toHaveCount(1)
    await expect(stageOf(35).locator('.ss-seg--success')).toHaveCount(3)
    await expect(stageOf(31)).toContainText('Shipped')
    await expect(stageOf(31).getByTestId('stage-step')).toHaveText('escaped')
    await expect(stageOf(31).locator('.ss-seg--success')).toHaveCount(2)
    await expect(stageOf(31).locator('.ss-seg--warning')).toHaveCount(1)
  }
  // Pixel folds rounds into its STATS cell as "5 rounds · <ago>".
  const rounds = page.getByTestId('feature-row-31').getByTestId('feature-rounds')
  await expect(rounds).toHaveAttribute('aria-label', '5 rounds')
  if (pipelineTheme !== 'pixel') {
    const pips = rounds.locator('.ftt-pip')
    await expect(pips).toHaveCount(5)
    const pipColours = new Set(await pips.evaluateAll(els => els.map(e => getComputedStyle(e).backgroundColor)))
    expect(pipColours.size).toBe(1)
  }

  // Stage tabs are one neutral surface; only the active tab is filled. Read
  // once the theme chunk is in, or the first tab is measured on a shell the
  // theme has not yet styled.
  await expect(page.getByTestId('feature-stage-spec')).toBeVisible()
  const tabBg = key => page.getByTestId(`feature-stage-${key}`).evaluate(e => getComputedStyle(e).backgroundColor)
  const neutral = await tabBg('spec')
  for (const key of ['building', 'shipped', 'escaped']) expect(await tabBg(key)).toBe(neutral)
  expect(await tabBg('all')).not.toBe(neutral)

  // Expand escaped: its active tasks, then its blamed defects. A fix task has
  // no section of its own — the running one (#414) shows once, as an active task.
  await page.getByTestId('feature-toggle-31').click()
  const expansion = page.getByTestId('feature-expansion-31')
  const active = expansion.getByTestId('feature-active-31')
  await expect(active.getByTestId('feature-active-heading')).toHaveText('Active tasks · 1 running · 2 armed')
  await expect(active.locator('[data-testid^="feature-active-task-"]')).toHaveCount(3)
  await expect(active.locator('[data-testid^="feature-active-task-"]').first()).toHaveAttribute('data-testid', 'feature-active-task-414')
  await expect(active.getByTestId('feature-active-task-414')).toContainText('running · 3m')
  await expect(active.getByTestId('feature-active-task-410')).toContainText('armed · waits #415')
  await expect(active.getByTestId('feature-active-task-416')).toContainText(/armed$/)
  await expect(active.getByTestId('feature-active-task-420')).toHaveCount(0)
  await expect(active.getByTestId('feature-active-more')).toHaveCount(0)
  const activeBox = await active.boundingBox()
  const defectBox = await expansion.getByTestId('feature-defect-91').boundingBox()
  expect(defectBox.y).toBeGreaterThan(activeBox.y + activeBox.height - 1)
  await expect(expansion).toContainText('Blamed defects · 3 open')
  await expect(expansion.getByTestId('feature-defect-91')).toContainText('open · fix queued')
  await expect(expansion.getByTestId('feature-defect-89')).toContainText('open · fix running')
  await expect(expansion).not.toContainText('Fix tasks')
  await expect(expansion.locator('[data-testid^="feature-fix-"]')).toHaveCount(0)
  await expect(expansion.locator('a[href="/tasks/414"]')).toHaveCount(1)

  // Triage keys: A is refused with no feature blamed; X + confirm declines.
  await expect(page.getByTestId('triage-count')).toHaveText('3')
  await page.getByTestId('triage-card-94').click({ position: { x: 8, y: 8 } })
  await page.keyboard.press('a')
  await expect(page.getByTestId('triage-refusal-94')).toContainText('Blame a feature first')
  expect(state.posts).toEqual([])

  await page.keyboard.press('x')
  await expect(page.getByTestId('triage-prompt-decline')).toBeVisible()
  await page.getByTestId('triage-submit-decline').click()
  await expect(page.getByTestId('triage-count')).toHaveText('2')
  expect(state.posts.map(p => p.path)).toEqual(['/api/defects/94/decline'])
  await expect(page.getByTestId('triage-card-94')).toHaveCount(0)

  // Blamed, Accept goes through with the picked feature — and a chord typed
  // into the Dup field is text, not a verb.
  await page.getByTestId('triage-card-93').click({ position: { x: 8, y: 8 } })
  await page.keyboard.press('d')
  await page.getByTestId('triage-prompt-duplicate').getByRole('textbox').fill('a')
  await expect(page.getByTestId('triage-refusal-93')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await page.getByTestId('triage-blame').click()
  await page.getByRole('option', { name: '#44 Project progress rollups' }).click()
  await page.getByTestId('triage-card-93').click({ position: { x: 8, y: 8 } })
  await page.keyboard.press('a')
  await expect(page.getByTestId('triage-count')).toHaveText('1')
  // D6: Accept attributes and authors the fix task.
  expect(state.posts.slice(-2)).toEqual([
    { path: '/api/defects/93/attribute', body: { feature_id: 44 } },
    { path: '/api/defects/93/fix-item', body: { create: true } },
  ])
})

test('test_features_triage_rail_folds', async ({ page }) => {
  const state = await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/features')
  await expect(page.getByTestId('triage-count')).toHaveText('3')
  const table = page.getByTestId('feature-table')
  const railWidth = async () => (await page.getByTestId('triage-rail').boundingBox()).width
  // The count's text is in the DOM while the shell is still hidden behind a
  // loading theme chunk; a hidden table has no box.
  await expect(table).toBeVisible()
  const open = (await table.boundingBox()).width

  // The header button folds the rail to a strip that still counts the queue,
  // and the table takes the column it gave up. A prompt left open is dropped.
  await page.getByTestId('triage-card-94').click({ position: { x: 8, y: 8 } })
  await page.keyboard.press('x')
  await expect(page.getByTestId('triage-prompt-decline')).toBeVisible()
  await page.getByTestId('triage-fold').click()
  const strip = page.getByTestId('triage-unfold')
  await expect(strip).toBeFocused()
  await expect(page.getByTestId('triage-fold-count')).toHaveText('3')
  await expect(strip).toContainText('3 waiting')
  await expect(page.getByTestId('triage-card-94')).toHaveCount(0)
  await expect.poll(railWidth).toBeLessThanOrEqual(52)
  await expect.poll(async () => (await table.boundingBox()).width).toBeGreaterThan(open + 250)

  // A folded rail answers no verb: X from the strip opens no prompt.
  await page.keyboard.press('x')
  await expect(page.getByTestId('triage-prompt-decline')).toHaveCount(0)
  expect(state.posts).toEqual([])

  // ] toggles it from anywhere on the tab — but not out of a text field.
  await page.keyboard.press(']')
  await expect(page.getByTestId('triage-card-94')).toBeVisible()
  await page.getByTestId('list-search').focus()
  await page.keyboard.press(']')
  await expect(page.getByTestId('triage-card-94')).toBeVisible()
  await expect(page.getByTestId('list-search')).toHaveValue(']')
  await page.getByTestId('list-search').blur()
  await page.keyboard.press(']')
  await expect(strip).toBeVisible()

  // The strip itself unfolds, handing focus back to the fold button.
  await strip.click()
  await expect(page.getByTestId('triage-fold')).toBeFocused()
  await expect.poll(railWidth).toBeGreaterThan(300)
  await expect(page.getByTestId('triage-prompt-decline')).toHaveCount(0)
  expect(state.posts).toEqual([])
})

test('test_pipeline_mobile_overview_features_triage', async ({ page, pipelineTheme }) => {
  await stubPipeline(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/overview')

  // 2a: stacked loop; Running now + Run next show the same rows as desktop (defect #215); one decision card.
  if (pipelineTheme !== 'pixel') await expect(page.getByTestId('overview-loop')).toHaveClass(/ol--compact/)
  const runningNow = page.getByTestId('running-now')
  // The tab body is v-show-hidden until a non-classic theme module lands, and a
  // hidden element counts but has no box.
  await expect(runningNow).toBeVisible()
  await expect(runningNow.locator('li')).toHaveCount(2)
  await expect(page.getByTestId('running-now-count')).toHaveText('2')
  for (const id of ['running-now-watch-414', 'running-now-stop-414']) {
    expect((await page.getByTestId(id).boundingBox()).height).toBeGreaterThanOrEqual(44)
  }
  await expect(page.getByTestId('run-next').locator('.rn-rows > li')).toHaveCount(3)
  await expect(page.getByTestId('run-next-wait-409')).toHaveText(/waits on #408 \(running\)/)
  for (const row of await page.getByTestId('run-next').locator('li').all()) {
    expect(await row.evaluate(el => el.scrollWidth - el.clientWidth)).toBeLessThanOrEqual(0)
  }
  await expect(page.getByTestId('needs-decision')).toContainText('3 defects need a decision')
  await expect(page.getByTestId('needs-decision')).toContainText('Newest: D-94')
  for (const tab of await page.getByTestId('pipeline-tabs').locator('a').all()) {
    expect((await tab.boundingBox()).height).toBeGreaterThanOrEqual(44)
  }

  // 2d: the Triage button opens a bottom sheet at "1 of N" with four 44px verbs.
  await page.getByTestId('needs-decision-triage').click()
  const sheet = page.getByTestId('triage-sheet')
  await expect(sheet).toBeVisible()
  await expect(page.getByTestId('triage-sheet-position')).toHaveText('1 of 3')
  for (const id of ['accept', 'duplicate', 'snooze', 'decline']) {
    const box = await page.getByTestId(`triage-sheet-${id}`).boundingBox()
    expect(box.height).toBeGreaterThanOrEqual(44)
  }
  await page.getByRole('button', { name: 'Next report' }).click()
  await expect(page.getByTestId('triage-sheet-position')).toHaveText('2 of 3')
  await page.keyboard.press('Escape')
  await expect(sheet).toHaveCount(0)

  // 2c: stage chips + cards, escaped first and showing its blamed defects.
  await page.getByTestId('pipeline-tab-features').click()
  await expect(page.getByTestId('feature-stage-tabs').getByRole('tab').first()).toHaveText(/All/)
  const first = page.locator('[data-testid^="feature-card-"]').first()
  await expect(first).toHaveAttribute('data-testid', 'feature-card-31')
  await expect(first).toHaveClass(/is-escaped/)
  await expect(first.getByTestId('feature-defect-91')).toBeVisible()

  for (const [path, theme] of [['/overview', 'light'], ['/features', 'light'], ['/overview', 'dark'], ['/features', 'dark']]) {
    await page.evaluate((t) => localStorage.setItem('regin_theme', t), theme)
    await page.goto(path)
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
    await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
    const overflow = await page.evaluate(() => {
      const el = document.querySelector('.content-scroll') || document.documentElement
      return el.scrollWidth - el.clientWidth
    })
    if (overflow > 0) {
      console.log(await page.evaluate(() => [...document.querySelectorAll('.content-scroll *')]
        .filter(el => el.getBoundingClientRect().right > window.innerWidth + 1)
        .slice(0, 12).map(el => `${el.tagName}.${[...el.classList].join('.')} r=${Math.round(el.getBoundingClientRect().right)} w=${Math.round(el.getBoundingClientRect().width)}`).join('\n')))
    }
    expect(overflow, path).toBeLessThanOrEqual(0)
  }
})

for (const width of [1024, 1440]) {
  for (const theme of ['light', 'dark']) {
    test(`no horizontal overflow at ${width}px in ${theme}`, async ({ page }) => {
      await stubPipeline(page)
      await page.addInitScript((t) => localStorage.setItem('regin_theme', t), theme)
      await page.setViewportSize({ width, height: 900 })
      const ready = { '/overview': 'projects-panel', '/overview?project=3': 'project-head', '/features': 'feature-table' }
      for (const [path, testid] of Object.entries(ready)) {
        await page.goto(path)
        await expect(page.getByTestId(testid)).toBeVisible()
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme)
        const overflow = await page.evaluate(() => {
          const el = document.querySelector('.content-scroll') || document.documentElement
          return el.scrollWidth - el.clientWidth
        })
        expect(overflow, `${path} @ ${width} ${theme}`).toBeLessThanOrEqual(0)
      }
    })
  }
}

test('test_features_table_rows_fit_their_own_width', async ({ page }) => {
  await stubPipeline(page)
  for (const width of [800, 1024, 1280, 1440, 1920]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto('/features')
    await expect(page.locator('.ftt-row').first()).toBeVisible()
    const clipped = await page.$$eval('.ftt-head, .ftt-row', els =>
      els.filter(e => e.scrollWidth > e.clientWidth + 1).map(e => `${e.scrollWidth}>${e.clientWidth}`))
    expect(clipped, `viewport ${width}`).toEqual([])
    await expect(page.locator('.ftt-head [role="columnheader"]').last()).toBeVisible()
  }
})

// ---------------------------------------------------------------------------
// Feature #119 · S7 (R6) — creation and navigation from the shell.
// ---------------------------------------------------------------------------

/** Answer one method+path with `body`, leaving every other request alone. */
async function answer(page, method, pattern, reply) {
  await page.route(pattern, (route) => {
    if (route.request().method() !== method) return route.fallback()
    const body = typeof reply === 'function' ? reply(route.request()) : reply
    return json(route, body, body?.__status || 200)
  })
}

test('test_new_defect_dialog_unattributed_lands_in_triage', async ({ page }) => {
  const state = await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  const posted = []
  await answer(page, 'POST', '**/api/defects', (req) => {
    const body = req.postDataJSON()
    posted.push(body)
    const made = defect(97, { symptom: body.symptom, suspect_paths: body.suspect_paths })
    state.triage = [made, ...state.triage]
    return { outcome: 'opened', defect: made, __status: 201 }
  })
  await page.goto('/features')
  await expect(page.getByTestId('triage-card-94')).toBeVisible()

  await page.getByTestId('pipeline-new').click()
  await page.getByTestId('pipeline-new-defect').click()
  await page.getByTestId('new-defect-symptom').fill('Picker loses focus after a search')
  await page.getByTestId('new-defect-paths').fill('frontend/src/a.vue\n\n  lib/b.py  ')
  await page.getByTestId('new-defect-submit').click()

  await expect.poll(() => posted.length).toBe(1)
  expect(posted[0]).toEqual({ symptom: 'Picker loses focus after a search',
                              suspect_paths: ['frontend/src/a.vue', 'lib/b.py'] })
  // No feature named → it is a triage report, and the rail re-reads to show it.
  await expect(page.getByTestId('triage-card-97')).toContainText('Picker loses focus after a search')
  await expect(page.getByTestId('flash')).toContainText('Defect #97 reported in triage')
  await expect(page.getByTestId('flash-link')).toHaveAttribute('href', '/defects/97')
})

test('test_pipeline_create_menu_prefills_scope', async ({ page }) => {
  await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/tasks?project=3')

  // The tab's default is still one click and `c`; the ▾ offers every kind.
  await page.getByTestId('pipeline-new').click()
  await expect(page.getByTestId('pipeline-new-task')).toBeVisible()
  await expect(page.getByTestId('pipeline-new-feature')).toBeVisible()
  await expect(page.getByTestId('pipeline-new-defect')).toBeVisible()
  await page.getByTestId('pipeline-new-feature').click()
  await expect(page.getByTestId('declare-project')).toContainText('regin-core')
  await page.keyboard.press('Escape')

  await page.getByTestId('pipeline-new').click()
  await page.getByTestId('pipeline-new-task').click()
  await expect(page.getByTestId('task-composer-project')).toContainText('regin-core')
  await page.keyboard.press('Escape')

  // `c` keeps each tab's own default.
  await page.locator('body').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('c')
  await expect(page.getByTestId('task-title')).toBeVisible()
  await page.keyboard.press('Escape')
  await page.getByTestId('pipeline-tab-features').click()
  await expect(page.locator('#pipeline-actions [data-testid="declare-feature"]')).toBeVisible()
  await page.locator('body').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('c')
  await expect(page.getByTestId('declare-title')).toBeVisible()
  await expect(page.getByTestId('declare-project')).toContainText('regin-core')
})

test('test_quick_capture_carries_scope_and_refreshes_counts', async ({ page }) => {
  await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  let countReads = 0
  await page.route('**/api/pipeline/counts*', (route) => {
    countReads += 1
    return json(route, { counts: COUNTS })
  })
  const posted = []
  await answer(page, 'POST', '**/api/tasks', (req) => {
    posted.push(req.postDataJSON())
    return { task: { id: 901, title: 'captured', status: 'idea' } }
  })
  await page.goto('/overview?project=3')
  await expect(page.getByTestId('overview-loop')).toBeVisible()
  await expect.poll(() => countReads).toBeGreaterThan(0)
  const before = countReads

  await page.getByTestId('quick-capture-fab').click()
  await expect(page.getByTestId('quick-capture-scope')).toContainText('regin-core')
  await page.getByLabel('Idea title').fill('captured')
  await page.getByTestId('quick-capture-save').click()

  await expect.poll(() => posted.length).toBe(1)
  expect(posted[0]).toMatchObject({ title: 'captured', project_id: 3 })
  await expect.poll(() => countReads).toBeGreaterThan(before)
  await expect(page.getByTestId('flash-link')).toHaveAttribute('href', '/tasks/901')
})

test('test_overview_every_fact_links_to_filtered_tab', async ({ page }) => {
  await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/overview')

  const href = id => page.getByTestId(id).getAttribute('href')
  expect(await href('loop-fact-running')).toBe('/tasks?view=list&group=running')
  expect(await href('loop-fact-ready')).toBe('/tasks?view=list&group=ready')
  expect(await href('loop-fact-failed')).toBe('/tasks?view=list&group=failed')
  expect(await href('loop-fact-building')).toBe('/features?stage=building')
  expect(await href('loop-fact-spec')).toBe('/features?stage=spec')
  expect(await href('loop-fact-escaped')).toBe('/features?stage=escaped')
  expect(await href('loop-fact-open')).toBe('/defects?status=open&queue=out')
  expect(await href('loop-fact-triage')).toBe('/features')
  // Every printed fact is an anchor — none is left as plain text.
  await expect(page.locator('[data-testid^="loop-fact-"]:not(a)')).toHaveCount(0)

  // Projects panel counts, and Run next.
  expect(await href('projects-features-3')).toBe('/features?project=3')
  expect(await href('projects-tasks-3')).toBe('/tasks?project=3')
  expect(await href('projects-escaped-3')).toBe('/features?project=3&stage=escaped')
  expect(await href('projects-defects-3')).toBe('/defects?project=3&status=open')
  expect(await href('run-next-all')).toBe('/tasks?view=list&group=ready')
  expect(await href('run-next-feature-412')).toBe('/features/38')
  expect(await href('run-next-wait-409')).toBe('/tasks/408')

  // `group=` is an address: the next-step list, opened and scrolled to it.
  await page.route('**/api/tasks?*', (route) => {
    const url = new URL(route.request().url())
    if (url.searchParams.get('group') !== 'next_step') return route.fallback()
    const rows = n => Array.from({ length: n }, (_, i) => ({ id: 600 + i, title: `row ${i}`, status: 'idea', waits_on: null, live: null }))
    return json(route, { open: 41, groups: [
      { key: 'ready', n: 20, more: 0, rows: rows(20) },
      { key: 'ideas', n: 20, more: 0, rows: rows(20).map(r => ({ ...r, id: r.id + 50 })) },
      { key: 'failed', n: 1, more: 0, rows: [{ id: 700, title: 'broke', status: 'failed', waits_on: null, live: null }] },
    ] })
  })
  await page.getByTestId('loop-fact-failed').click()
  await expect(page).toHaveURL(/\/tasks\?view=list&group=failed$/)
  const failed = page.getByTestId('next-step-group-failed')
  await expect(failed).toHaveAttribute('data-anchored', 'true')
  await expect(page.getByTestId('next-step-toggle-failed')).toHaveAttribute('aria-expanded', 'true')
  await expect(failed).toBeInViewport()
  await expect(page.getByTestId('task-group')).toContainText('Next step')
  // An arrival is not a choice: filter changes keep the anchor and never
  // overwrite the grouping the reader stored.
  await page.evaluate(() => localStorage.setItem('regin.tasks.group', 'status'))
  const search = page.getByPlaceholder('Search tasks')
  await search.fill('b')
  await search.press('Enter')
  await search.fill('br')
  await search.press('Enter')
  await expect(page).toHaveURL(/q=br/)
  await expect(page).toHaveURL(/group=failed/)
  expect(await page.evaluate(() => localStorage.getItem('regin.tasks.group'))).toBe('status')

  // Under a project, the 1d head's counts link to the same tabs, scoped.
  await page.goto('/overview?project=3')
  expect(await href('project-head-tasks')).toBe('/tasks?project=3')
  expect(await href('project-head-defects')).toBe('/defects?project=3&status=open')
  expect(await href('loop-fact-running')).toBe('/tasks?project=3&view=list&group=running')
})

async function stubTaskDetail(page) {
  await page.route(/\/api\/tasks\/\d+(\?|$)/, (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const id = Number(route.request().url().match(/\/tasks\/(\d+)/)[1])
    return json(route, { id, title: `task ${id}`, status: 'armed', priority: 'none', body: '',
      created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z', tags: [],
      project_id: null, feature_id: null, run_count: 0, images: [], comments: [], deps: [] })
  })
}

test('test_detail_back_link_returns_to_origin', async ({ page }) => {
  await stubPipeline(page)
  await stubTaskDetail(page)
  await page.setViewportSize({ width: 1440, height: 900 })

  // Opened from the Overview → back to the Overview, not to /tasks.
  await page.goto('/overview?project=3')
  await page.getByTestId('run-next-row-412').getByRole('link', { name: /Retry budget/ }).click()
  await expect(page).toHaveURL(/\/tasks\/412$/)
  const back = page.getByTestId('detail-back')
  await expect(back).toContainText('Overview')
  await back.click()
  await expect(page).toHaveURL(/\/overview\?project=3$/)

  // Opened from a filtered list → back to that list, query and all, and Back
  // is a real history step, so no forward entry is left behind.
  await page.goto('/features?stage=escaped')
  await page.getByTestId('triage-permalink-94').click()
  await expect(page).toHaveURL(/\/defects\/94$/)
  const defectBack = page.getByTestId('defect-back')
  await expect(defectBack).toContainText('Features')
  await defectBack.click()
  await expect(page).toHaveURL(/\/features\?stage=escaped$/)
  expect(await page.evaluate(() => window.history.state.forward)).toBe('/defects/94')

  // An in-page anchor on the detail is its own history entry; Back must still
  // leave the page rather than return to the same one.
  await page.getByTestId('triage-permalink-94').click()
  await expect(page).toHaveURL(/\/defects\/94$/)
  await page.evaluate(() => document.querySelector('#app').__vue_app__
    .config.globalProperties.$router.push({ path: '/defects/94', hash: '#defect-images' }))
  await expect(page).toHaveURL(/\/defects\/94#defect-images$/)
  await expect(defectBack).not.toContainText('Defect #94')
  await defectBack.click()
  await expect(page).not.toHaveURL(/\/defects\/94/)

  await page.goto('/tasks/412')
  // A deep link has no Pipeline route behind it: the parent tab.
  await expect(back).toContainText('Tasks')
  await expect(back).toHaveAttribute('href', /^\/tasks/)
  await back.click()
  await expect(page).toHaveURL(/\/tasks(\?|$)/)
})

test('test_project_change_on_detail_goes_to_parent_tab', async ({ page }) => {
  await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })

  await page.goto('/defects/94')
  await page.getByTestId('pipeline-project').click()
  await page.getByTestId('project-picker-row-7').click()
  await expect(page).toHaveURL(/\/defects\?project=7$/)

  await page.goto('/features/44')
  await page.getByTestId('pipeline-project').click()
  await page.getByTestId('project-picker-row-3').click()
  await expect(page).toHaveURL(/\/features\?project=3$/)
  await expect(page.getByTestId('pipeline-project')).toContainText('regin-core')
})

test('test_project_picker_keyboard_and_server_search', async ({ page }) => {
  const asked = []
  await stubPipeline(page)
  await page.route('**/api/pipeline/projects*', (route) => {
    const url = new URL(route.request().url())
    asked.push(Object.fromEntries(url.searchParams))
    const q = url.searchParams.get('q') || ''
    const pageNo = Number(url.searchParams.get('page') || 0)
    const all = [
      { id: 3, scope: '3', name: 'regin-core', features: { by_stage: { escaped: 1 } }, defects: { open: 6 } },
      { id: 7, scope: '7', name: 'docs-site', features: { by_stage: {} }, defects: { open: 0 } },
      ...Array.from({ length: 118 }, (_, i) => ({ id: 100 + i, scope: String(100 + i), name: `repo-${100 + i}`,
                                                  features: { by_stage: {} }, defects: { open: 0 } })),
    ].filter(r => r.name.includes(q))
    const size = Number(url.searchParams.get('size') || 50)
    return json(route, { items: all.slice(pageNo * size, (pageNo + 1) * size),
                         pagination: { total: all.length, page: pageNo, size }, population: 121 })
  })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/overview')
  await expect(page.getByTestId('overview-loop')).toBeVisible()

  // No 100-row cap: the rest of the population is one "more" away.
  await page.keyboard.press('p')
  await expect(page.getByTestId('project-picker-search')).toBeFocused()
  const more = page.getByTestId('project-picker-more')
  await expect(more).toHaveText('70 more')
  await more.click()
  await expect(more).toHaveText('20 more')
  expect(asked.at(-1).page).toBe('1')

  // Server-side search: the typed text is the request's `q`.
  await page.getByTestId('project-picker-search').fill('docs')
  await expect.poll(() => asked.at(-1).q).toBe('docs')
  await expect(page.locator('.pp-panel .pp-row:not(.pp-row--all)')).toHaveCount(1)

  // ↓ moves off "All projects" onto the row; Enter picks it.
  await page.getByTestId('project-picker-search').press('ArrowDown')
  await expect(page.getByTestId('project-picker-row-7')).toHaveClass(/is-active/)
  await page.getByTestId('project-picker-search').press('ArrowUp')
  await expect(page.getByTestId('project-picker-all')).toHaveClass(/is-active/)
  await page.getByTestId('project-picker-search').press('ArrowDown')
  await page.getByTestId('project-picker-search').press('Enter')
  await expect(page).toHaveURL(/\/overview\?project=7$/)
})

test('test_new_feature_dialog_keeps_draft', async ({ page }) => {
  await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/features')

  await page.getByTestId('declare-feature').click()
  await page.getByTestId('declare-title').fill('half-typed feature')
  await page.getByTestId('declare-body').fill('## Intent\nstill thinking')
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('declare-title')).toHaveCount(0)

  // Reopened — even from another tab's menu — the words are still there.
  await page.getByTestId('pipeline-tab-tasks').click()
  await expect(page.locator('#pipeline-actions [data-testid="task-new"]')).toBeVisible()
  await page.getByTestId('pipeline-new').click()
  await page.getByTestId('pipeline-new-feature').click()
  await expect(page.getByTestId('declare-title')).toHaveValue('half-typed feature')
  await expect(page.getByTestId('declare-body')).toHaveValue('## Intent\nstill thinking')

  // Only a discard ends the draft.
  await page.getByTestId('declare-discard').click()
  await expect(page.getByTestId('declare-title')).toHaveCount(0)
  await page.getByTestId('pipeline-new').click()
  await page.getByTestId('pipeline-new-feature').click()
  await expect(page.getByTestId('declare-title')).toHaveValue('')
  await expect(page.getByTestId('declare-discard')).toHaveCount(0)
})

test('test_action_toasts_link_the_object', async ({ page }) => {
  await stubPipeline(page)
  await answer(page, 'POST', '**/api/tasks/412/run',
    { ok: true, run: { id: 9, attempt: 2, trace_id: 'tr-9' } })
  await answer(page, 'POST', '**/api/tasks/414/stop', { ok: true, delivered: true })
  await answer(page, 'POST', '**/api/tasks', { task: { id: 902, title: 'made', status: 'idea' } })
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/overview')

  const flash = page.getByTestId('flash')
  const link = page.getByTestId('flash-link')

  // Run → "Attempt 2 … started · Watch", linking the run's session.
  await page.getByTestId('run-next-run-412').click()
  await expect(flash).toContainText('Attempt 2 of #412 started')
  await expect(link).toHaveText('Watch')
  await expect(link).toHaveAttribute('href', '/trace/sessions/tr-9')

  // Stop → names and links the task.
  await page.getByTestId('running-now-stop-414').click()
  await page.getByRole('button', { name: /^Stop/ }).last().click()
  await expect(flash).toContainText('Stopped #414')
  await expect(link).toHaveAttribute('href', '/tasks/414')

  // Create → links the new task.
  await page.getByTestId('task-new').click()
  await page.getByTestId('task-title').fill('made')
  await page.getByTestId('task-submit').click()
  await expect(flash).toContainText('Task #902 created')
  await expect(link).toHaveAttribute('href', '/tasks/902')

  // On a phone too, where Running now is not drawn.
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/overview')
  await page.getByTestId('run-next-run-412').click()
  await expect(flash).toBeInViewport()
  await expect(link).toHaveAttribute('href', '/trace/sessions/tr-9')
})

test('test_overview_loop_open_link_matches_count', async ({ page }) => {
  await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/overview')
  const open = page.getByTestId('loop-fact-open')
  await expect(open).toHaveText('6 open')
  await open.click()
  await expect(page).toHaveURL(/\/defects\?status=open&queue=out$/)
  await expect(page.getByTestId('defects-list').locator('> li')).toHaveCount(6)
  await expect(page.getByTestId('defects-queue-chip')).toBeVisible()
  // Clearing the chip widens the list to the queue as well.
  await page.getByTestId('defects-queue-clear').click()
  await expect(page).not.toHaveURL(/queue=out/)
  await expect(page.getByTestId('defects-list').locator('> li')).toHaveCount(9)
})

test('test_run_next_count_is_ready_and_blocked_link', async ({ page }) => {
  await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/overview')
  await expect(page.getByTestId('run-next-count')).toHaveText('4')
  await expect(page.getByTestId('run-next-all')).toHaveAttribute('href', '/tasks?view=list&group=ready')
  const blocked = page.getByTestId('run-next-blocked')
  await expect(blocked).toHaveText('· 5 blocked')
  await expect(blocked).toHaveAttribute('href', '/tasks?view=list&group=blocked')
})

test('test_run_next_without_blocked_prints_no_blocked_link', async ({ page }) => {
  await stubPipeline(page, { empty: true })
  await page.goto('/overview')
  await expect(page.getByTestId('run-next-count')).toHaveText('0')
  await expect(page.getByTestId('run-next-blocked')).toHaveCount(0)
})

test('test_triage_rail_snoozed_count_from_server', async ({ page }) => {
  const state = await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/features')
  const rail = page.getByTestId('triage-rail')
  await expect(rail.getByTestId('triage-card-94')).toBeVisible()
  // `unattributed_defects − queue` would print 5; the server says 2 are snoozed.
  const snoozed = rail.getByTestId('triage-snoozed')
  await expect(snoozed).toHaveText('2 snoozed')
  await expect(snoozed).toHaveAttribute('href', '/defects?triage=snoozed')
  await expect(rail).not.toContainText('older')

  state.snoozed = 0
  await page.reload()
  await expect(rail.getByTestId('triage-card-94')).toBeVisible()
  await expect(rail.getByTestId('triage-snoozed')).toHaveCount(0)
})
