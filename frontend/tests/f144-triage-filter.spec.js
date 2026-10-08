/**
 * Feature #144, slice S1 — triage rail folds by default and the Task filter's
 * feature axis is picked from existing features.
 */
import { test, expect } from './auth-fixture.js'

const FEATURE = {
  id: 144, title: 'Pipeline operation polish V', origin: 'local', stage: 'building',
  external_ref: 'GH-144', tasks: 4, tasks_done: 2, rounds: 2, last_activity: '2026-10-01T06:00:00',
  defects: { total: 0, open: 0, escapes: 0 }, cost_usd: 0,
}

const FEATURES = [
  FEATURE,
  { id: 73, title: 'Old backlog feature', origin: 'local', stage: 'spec',
    external_ref: 'GH-73', tasks: 0, tasks_done: 0, rounds: 1, last_activity: '2026-10-01T06:00:00',
    defects: { total: 0, open: 0, escapes: 0 }, cost_usd: null },
]

const defect = (id, extra = {}) => ({
  id, feature_id: null, symptom: `symptom ${id}`, feature_title: null, status: 'open', round: 1,
  blamed_event_kind: null, missing_check: null, suspect_paths: [], fix_task_id: null,
  fix_state: null, triage_state: null, images: [], opened_at: '2026-10-04T10:00:00', ...extra,
})

const TRIAGE_DEFECTS = [defect(97), defect(96, { round: 2 })]

const json = (route, body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })

async function stubShell(page) {
  await page.route('**/api/pipeline/counts*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json',
                    body: JSON.stringify({ counts: { tasks: 4, features: 2, defects: 2 } }) }))
}

async function stubTriage(page) {
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    const p = url.pathname
    if (route.request().method() !== 'GET') return route.fallback()
    if (p === '/api/features') {
      return json(route, {
        items: FEATURES, pagination: { total: FEATURES.length, page: 0, size: 50, has_next: false, has_prev: false },
        population: FEATURES.length, applied: { sort: url.searchParams.get('sort') || 'activity:desc' },
        extras: { stage_counts: { all: 2, spec: 1, building: 1, shipped: 0, escaped: 0 }, unattributed_defects: 2 },
      })
    }
    if (p === `/api/features/${FEATURE.id}`) {
      return json(route, { id: FEATURE.id, title: FEATURE.title, stage: FEATURE.stage, spec: '', synced_at: null })
    }
    if (p === '/api/defects') {
      if (url.searchParams.get('scope') === 'triage') {
        return json(route, { items: TRIAGE_DEFECTS, pagination: { total: TRIAGE_DEFECTS.length } })
      }
      if (url.searchParams.get('triage') === 'snoozed') return json(route, { items: [], pagination: { total: 0 } })
      return json(route, { items: [], pagination: { total: 0 } })
    }
    return route.fallback()
  })
}

const taskRows = (n) => Array.from({ length: n }, (_, i) => ({
  id: 1000 + i, title: `row ${i}`, status: 'idea', priority: 'none',
  created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
  project_id: null, feature_id: null, run_count: 0, images: [],
}))

async function stubTasks(page) {
  await page.route('**/api/auth/stream-ticket', () => {})
  await page.route('**/api/tasks/counts*', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json',
                    body: JSON.stringify({ total: 2, by_status: { idea: 2 }, by_priority: {}, by_project: {} }) }))
  await page.route('**/api/tasks?*', (route) => {
    const url = new URL(route.request().url())
    const rows = taskRows(2)
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        items: rows, pagination: { total: rows.length, page: 0, size: 50, has_next: false, has_prev: false },
        population: rows.length, applied: { sort: url.searchParams.get('sort') || 'created:desc', filters: {} },
        extras: { counts: { total: 2, by_status: { idea: 2 }, by_priority: {}, by_project: {} } },
      }),
    })
  })
}

async function openFilters(page) {
  await page.getByTestId('task-filter-button').click()
}

test.describe('triage rail folding', () => {
  test.beforeEach(async ({ page }) => {
    await stubShell(page)
    await stubTriage(page)
  })

  test('Triage rail opens folded', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/features')
    await expect(page.getByTestId('feature-stage-tabs')).toBeVisible()

    // The folded strip shows the queue count.
    const foldCount = page.getByTestId('triage-fold-count')
    await expect(foldCount).toBeVisible()
    await expect(foldCount).toHaveText(String(TRIAGE_DEFECTS.length))

    // The rail is rendered as the 52px strip, not the full panel.
    await expect(page.getByTestId('triage-unfold')).toBeVisible()
    await expect(page.getByTestId('triage-fold')).toHaveCount(0)

    const layout = page.locator('.fv-layout')
    await expect.poll(() => layout.evaluate(el => getComputedStyle(el).gridTemplateColumns)).toMatch(/52px/)
  })

  test('A triage deep link unfolds the rail', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const target = TRIAGE_DEFECTS[0].id
    await page.goto(`/features?triage=${target}`)
    await expect(page.getByTestId('feature-stage-tabs')).toBeVisible()

    await expect(page.getByTestId('triage-fold')).toBeVisible()
    await expect(page.getByTestId('triage-unfold')).toHaveCount(0)
    await expect(page.getByTestId(`triage-card-${target}`)).toBeVisible()

    const card = page.getByTestId(`triage-card-${target}`)
    await expect.poll(() => card.evaluate(el => el === document.activeElement)).toBe(true)
  })
})

test.describe('feature filter picker', () => {
  test.beforeEach(async ({ page }) => {
    await stubShell(page)
    await stubTasks(page)
    await page.route('**/api/**', (route) => {
      const url = new URL(route.request().url())
      const p = url.pathname
      if (route.request().method() !== 'GET') return route.fallback()
      if (p === '/api/features') {
        return json(route, {
          items: FEATURES, pagination: { total: FEATURES.length, page: 0, size: 50, has_next: false, has_prev: false },
          population: FEATURES.length, applied: { sort: url.searchParams.get('sort') || 'activity:desc' },
          extras: { stage_counts: { all: 2, spec: 1, building: 1, shipped: 0, escaped: 0 }, unattributed_defects: 0 },
        })
      }
      if (p === `/api/features/${FEATURE.id}`) {
        return json(route, { id: FEATURE.id, title: FEATURE.title, stage: FEATURE.stage, spec: '', synced_at: null })
      }
      return route.fallback()
    })
  })

  test('Feature filter is picked not typed', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/tasks?group=none')
    await expect(page.getByTestId('task-list')).toBeVisible()

    await openFilters(page)
    const picker = page.getByTestId('task-filter-feature')
    await picker.click()

    // Focusing without typing lists the existing features.
    const list = page.getByTestId('task-filter-feature-list')
    await expect(list).toBeVisible()
    for (const f of FEATURES) {
      await expect(page.getByTestId(`task-filter-feature-option-${f.id}`)).toContainText(f.title)
    }

    await page.keyboard.press('Enter')
    await expect(page.getByTestId('task-filter-feature-chosen')).toBeVisible()
    await page.getByTestId('task-more-apply').click()

    await expect(page).toHaveURL(new RegExp(`feature_id=${FEATURE.id}`))
    const chip = page.getByTestId('task-chip-feature')
    await expect(chip).toContainText(`Feature`)
    await expect(chip).toContainText(FEATURE.title)
  })

  test('Clicking a feature option fills the Feature field', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/tasks?group=none')
    await expect(page.getByTestId('task-list')).toBeVisible()

    await openFilters(page)
    await page.getByTestId('task-filter-feature').click()
    await page.getByTestId(`task-filter-feature-option-${FEATURE.id}`).click()

    await expect(page.getByTestId('task-filter-feature-chosen')).toContainText(FEATURE.title)
    await page.getByTestId('task-more-apply').click()
    await expect(page).toHaveURL(new RegExp(`feature_id=${FEATURE.id}`))
  })

  test('Arriving with feature_id shows the title in the chip', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto(`/tasks?feature_id=${FEATURE.id}&group=none`)
    await expect(page.getByTestId('task-list')).toBeVisible()

    const chip = page.getByTestId('task-chip-feature')
    await expect(chip).toContainText('Feature')
    await expect(chip).toContainText(FEATURE.title)
  })
})
