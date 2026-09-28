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
    { label: 'Tasks', total: 128, parts: [part('idea', 'Ideas', 42), part('armed', 'Armed', 6),
      part('queued', 'Queued', 3), part('running', 'Running', 2), part('done', 'Done', 71),
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

function overview({ empty = false, project = null } = {}) {
  const body = {
    scope: project ? String(project) : 'all',
    loop: LOOP,
    running: empty ? { total: 0, rows: [] } : RUNNING,
    run_next: empty ? { total: 0, ready: 0, rows: [] } : {
      total: 9, ready: 4,
      rows: [
        runRow(415, 'Stale lease blocks re-arm after runner crash', { kind: 'run' }, { fixes_defects: [91], feature_id: 31, feature_title: 'Live tail reconnect', step: null, status: 'queued' }),
        runRow(412, 'Retry budget for runner restarts', { kind: 'run' }),
        runRow(409, 'Facet counts for tool spans', { kind: 'wait', on: 408, on_status: 'running' }, { step: { n: 5, of: 7 } }),
      ],
    },
    needs_decision: empty ? { waiting: 0, rows: [] } : { waiting: 3, rows: TRIAGE },
    escaped: empty ? [] : [
      { feature_id: 31, title: 'Live tail reconnect', rounds: 4, defects_open: 3, defects_total: 5, escapes: 2, last_escape_at: '2026-09-25' },
    ],
  }
  if (!project) {
    body.projects = { sort: 'attention', total: 32, more: 0, quiet: 19, completed_hidden: 8,
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
      return json(route, overview({ empty: state.empty, project: project ? Number(project) : null }))
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
      return json(route, { open: 5, done_hidden: 9, groups: [
        { key: 'ready', n: 1, more: 0, rows: [task(416, 'Backoff jitter for reconnect attempts', 'armed')] },
        { key: 'running', n: 1, more: 0, rows: [task(414, 'Reconnect tail when the socket drops mid-turn', 'running',
          { live: { run_id: 7, trace_id: 'tr-414', attempt: 2, started_at: minutesAgo(3) } })] },
        { key: 'blocked', n: 1, more: 0, rows: [task(410, 'Drop the orphaned worktree on cancel', 'armed',
          { waits_on: { id: 415, status: 'queued' } })] },
        { key: 'ideas', n: 2, more: 0, rows: [task(420, 'An idea', 'idea')] },
      ] })
    }
    if (p === '/api/tasks') {
      return json(route, { items: [
        { id: 415, title: 'Stale lease blocks re-arm after runner crash', status: 'queued' },
        { id: 414, title: 'Reconnect tail when the socket drops mid-turn', status: 'running' },
      ], pagination: { total: 2 } })
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
  for (const path of ['/defects', '/projects', '/projects/3']) {
    await page.goto(path)
    await expect(page.locator('.page-title')).toHaveText('Page not found')
  }
  for (const file of ['views/DefectsView.vue', 'views/ProjectsView.vue', 'views/ProjectPanelView.vue', 'components/projects']) {
    expect(existsSync(join(SRC, file)), file).toBe(false)
  }
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/features')
  await page.getByTestId('triage-permalink-94').click()
  await expect(page).toHaveURL(/\/defects\/94$/)
  await expect(page.getByTestId('defect-detail')).toBeVisible()
  await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
  await expect(page.getByTestId('pipeline-tab-features')).toHaveClass(/is-active/)
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

  await page.keyboard.press('/')
  await expect(page.getByTestId('projects-search')).toBeFocused()
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

  // Loop totals: the parts sum to the total, and Tasks/Features match the tabs.
  for (const [stage, tab] of [['tasks', 128], ['features', 24], ['defects', null]]) {
    const card = page.getByTestId(`loop-${stage}`)
    const total = Number(await card.getByTestId('loop-total').innerText())
    const parts = await card.locator('dd').allInnerTexts()
    expect(parts.map(Number).reduce((a, b) => a + b, 0)).toBe(total)
    if (tab != null) {
      await expect(page.getByTestId(`pipeline-count-${stage}`)).toHaveText(String(total))
      expect(total).toBe(tab)
    }
  }
  await expect(page.getByTestId('loop-note')).toContainText('4 fix tasks carry open defects back into the queue')

  // Running now heads the Run next card: newest attempt first, one Watch per row.
  const runningNow = page.getByTestId('running-now')
  await expect(page.getByTestId('running-now-count')).toHaveText('2 agents working')
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
  await expect(waiting).toHaveClass(/is-waiting/)
  expect(Number(await waiting.evaluate(el => getComputedStyle(el).opacity))).toBeLessThan(1)
  await expect(waiting.getByRole('button')).toHaveCount(0)
  await expect(page.getByTestId('run-next-run-412')).toBeVisible()
  await expect(page.getByTestId('run-next-all')).toHaveText('All 9 in Tasks →')
  await expect(page.getByTestId('needs-decision-row-94')).toContainText('D-94')
  await expect(page.getByTestId('needs-decision-attribute-94')).toBeVisible()
  await expect(page.getByTestId('needs-decision-decline-94')).toBeVisible()
  await expect(page.getByTestId('escaped-row-31')).toContainText('3 open · 5')
  await expect(page.getByTestId('projects-foot')).toContainText('Showing 2 of 32')
  await expect(page.getByTestId('projects-foot')).toContainText('19 quiet')

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
  await expect(page.getByTestId('escaped-empty')).toBeVisible()
})

test('test_features_stage_tabs_expand_and_triage_keys', async ({ page }) => {
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
  const stageOf = id => page.getByTestId(`feature-row-${id}`).getByTestId('stage-segments')
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
  const pips = page.getByTestId('feature-row-31').getByTestId('feature-rounds').locator('.ftt-pip')
  await expect(pips).toHaveCount(5)
  const pipColours = new Set(await pips.evaluateAll(els => els.map(e => getComputedStyle(e).backgroundColor)))
  expect(pipColours.size).toBe(1)

  // Stage tabs are one neutral surface; only the active tab is filled.
  const tabBg = key => page.getByTestId(`feature-stage-${key}`).evaluate(e => getComputedStyle(e).backgroundColor)
  const neutral = await tabBg('spec')
  for (const key of ['building', 'shipped', 'escaped']) expect(await tabBg(key)).toBe(neutral)
  expect(await tabBg('all')).not.toBe(neutral)

  // Expand escaped: its active tasks, blamed defects and current-round fix
  // tasks, stacked in that order.
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
  const fixBox = await expansion.getByTestId('feature-fix-415').boundingBox()
  expect(defectBox.y).toBeGreaterThan(activeBox.y + activeBox.height - 1)
  expect(fixBox.y).toBeGreaterThan(defectBox.y + defectBox.height)
  expect(Math.abs(fixBox.x - defectBox.x)).toBeLessThan(2)
  await expect(expansion).toContainText('Blamed defects · 3 open')
  await expect(expansion.getByTestId('feature-defect-91')).toContainText('open · fix queued')
  await expect(expansion.getByTestId('feature-defect-89')).toContainText('open · fix running')
  await expect(expansion).toContainText('Fix tasks · round 5')
  await expect(expansion.getByTestId('feature-fix-415')).toContainText('Stale lease blocks re-arm')
  await expect(expansion.getByTestId('feature-fix-414')).toBeVisible()

  // Triage keys: A is refused with no feature blamed; X + confirm declines.
  await expect(page.getByTestId('triage-count')).toHaveText('3')
  await page.getByTestId('triage-card-94').click()
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
  await page.getByTestId('triage-card-93').click()
  await page.keyboard.press('d')
  await page.getByTestId('triage-prompt-duplicate').getByRole('textbox').fill('a')
  await expect(page.getByTestId('triage-refusal-93')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await page.getByTestId('triage-blame').click()
  await page.getByRole('option', { name: '#44 Project progress rollups' }).click()
  await page.getByTestId('triage-card-93').click()
  await page.keyboard.press('a')
  await expect(page.getByTestId('triage-count')).toHaveText('1')
  expect(state.posts.at(-1)).toEqual({ path: '/api/defects/93/attribute', body: { feature_id: 44 } })
})

test('test_features_triage_rail_folds', async ({ page }) => {
  const state = await stubPipeline(page)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/features')
  await expect(page.getByTestId('triage-count')).toHaveText('3')
  const table = page.getByTestId('feature-table')
  const railWidth = async () => (await page.getByTestId('triage-rail').boundingBox()).width
  const open = (await table.boundingBox()).width

  // The header button folds the rail to a strip that still counts the queue,
  // and the table takes the column it gave up. A prompt left open is dropped.
  await page.getByTestId('triage-card-94').click()
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

test('test_pipeline_mobile_overview_features_triage', async ({ page }) => {
  await stubPipeline(page)
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/overview')

  // 2a: stacked loop, ready-only run next (≤3), one decision card.
  await expect(page.getByTestId('overview-loop')).toHaveClass(/ol--compact/)
  const rows = page.getByTestId('run-next').locator('li')
  await expect(rows).toHaveCount(2)
  await expect(page.getByTestId('run-next-row-409')).toHaveCount(0)
  await expect(page.getByTestId('running-now')).toHaveCount(0)
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
    await expect(page.locator('.ftt-head .num').last()).toBeVisible()
  }
})
