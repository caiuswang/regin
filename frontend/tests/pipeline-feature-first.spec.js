/**
 * Feature #148, slice S2 — the Pipeline reads Feature → Task → Defect: the tab
 * strip, the Overview's default create, and a Features panel ahead of Run next.
 */
import { test, expect } from './auth-fixture.js'

const part = (key, label, n) => ({ key, label, n, highlighted: false })
const stage = (label, parts) => ({ label, total: parts.reduce((s, p) => s + p.n, 0), parts })

const LOOP = {
  stages: [
    stage('Features', [part('spec', 'Spec', 2), part('building', 'Building', 1),
      part('shipped', 'Shipped', 4), part('escaped', 'Escaped', 0)]),
    stage('Tasks', [part('idea', 'Ideas', 1), part('armed', 'Armed', 1), part('running', 'Running', 0),
      part('done', 'Done', 3), part('failed', 'Failed', 0)]),
    stage('Defects', [part('triage', 'Triage', 0), part('open', 'Open', 0), part('closed', 'Closed', 1)]),
  ],
  fix_tasks: 0,
}

const flightRow = (id, title, stageName, at) => ({
  id, title, stage: stageName, project_id: 3, last_activity_at: at,
})

function scopedOverview() {
  return {
    scope: '3',
    loop: LOOP,
    features_in_flight: { total: 3, rows: [
      flightRow(52, 'Stage prompts', 'building', '2026-10-07T09:00:00Z'),
      flightRow(51, 'Project page', 'spec', '2026-10-06T09:00:00Z'),
      flightRow(50, 'Tab order', 'spec', '2026-10-05T09:00:00Z'),
    ] },
    running: { total: 0, rows: [] },
    run_next: { total: 0, ready: 0, blocked: 0, rows: [] },
    recently_done: { week: 0, rows: [] },
    needs_decision: { waiting: 0, all_projects: true, rows: [] },
    project_head: { id: 3, name: 'regin-web', breadcrumb: ['All projects', 'regin-web'],
      stage_bar: { spec: 2, building: 1, shipped: 4, escaped: 0 }, shipped_pct: 57,
      open_tasks: 2, open_defects: 0, spent_usd: 12 },
    project_features: { total: 3, rows: [] },
  }
}

async function openOverview(page, path = '/overview') {
  await page.goto(path)
  await expect(page.getByTestId('features-in-flight')).toBeVisible()
}

test('test_pipeline_tabs_feature_first', async ({ page }) => {
  await openOverview(page)
  const tabs = page.getByTestId('pipeline-tabs').locator('a')
  await expect(tabs).toHaveCount(3)
  const testids = await tabs.evaluateAll(els => els.map(el => el.dataset.testid))
  expect(testids).toEqual(['pipeline-tab-overview', 'pipeline-tab-features', 'pipeline-tab-tasks'])
  await expect(tabs.nth(0)).toContainText('Overview')
  await expect(tabs.nth(1)).toContainText('Features')
  await expect(tabs.nth(2)).toContainText('Tasks')

  await page.goto('/defects')
  await expect(page.getByTestId('defects-list-page')).toBeVisible()
  await expect(page.getByTestId('pipeline-tab-features')).toHaveClass(/is-active/)
  await expect(page.getByTestId('pipeline-tab-tasks')).not.toHaveClass(/is-active/)
})

test('test_overview_create_defaults_to_feature', async ({ page }) => {
  await openOverview(page)
  const create = page.locator('#pipeline-actions [data-testid="overview-new"]')
  await expect(create).toContainText('New feature')
  await expect(page.locator('#pipeline-actions [data-testid="task-new"]')).toHaveCount(0)

  await page.locator('body').click({ position: { x: 5, y: 5 } })
  await page.keyboard.press('c')
  await expect(page.getByRole('dialog', { name: /Declare a feature/ })).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('dialog')).toHaveCount(0)

  await page.locator('#pipeline-actions [data-testid="pipeline-new"]').click()
  for (const kind of ['task', 'feature', 'defect']) {
    await expect(page.getByTestId(`pipeline-new-${kind}`)).toBeVisible()
  }
})

test('test_features_panel_above_run_next', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await openOverview(page)
  const features = page.getByTestId('features-in-flight')
  const decision = page.getByTestId('needs-decision')
  const runNext = page.getByTestId('run-next')
  await expect(runNext).toBeVisible()
  await expect(decision).toBeVisible()
  const [f, d, r] = await Promise.all([features, decision, runNext].map(el => el.boundingBox()))
  // One row: Features beside Needs decision, Run next full-width under both.
  expect(Math.abs(f.y - d.y)).toBeLessThan(2)
  expect(f.x).toBeLessThan(d.x)
  expect(r.y).toBeGreaterThanOrEqual(Math.max(f.y + f.height, d.y + d.height))
  expect(r.width).toBeGreaterThan(f.width + d.width)

  await page.setViewportSize({ width: 900, height: 900 })
  await expect(runNext).toBeVisible()
  const [f2, d2, r2] = await Promise.all([features, decision, runNext].map(el => el.boundingBox()))
  expect(f2.y + f2.height).toBeLessThanOrEqual(d2.y)
  expect(d2.y + d2.height).toBeLessThanOrEqual(r2.y)

  await page.setViewportSize({ width: 375, height: 800 })
  await expect(runNext).toBeVisible()
  const [f3, d3, r3] = await Promise.all([features, decision, runNext].map(el => el.boundingBox()))
  expect(f3.y + f3.height).toBeLessThanOrEqual(d3.y)
  expect(d3.y + d3.height).toBeLessThanOrEqual(r3.y)
})

test('test_project_scope_lists_features_once', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.route(/\/api\/pipeline\/overview/, route =>
    route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(scopedOverview()) }))
  await openOverview(page, '/overview?project=3')

  await expect(page.getByTestId('project-head')).toBeVisible()
  await expect(page.getByTestId('project-features')).toHaveCount(0)
  await expect(page.getByTestId('project-features-all')).toHaveCount(0)
  await expect(page.getByTestId('features-in-flight')).toHaveCount(1)
  const rows = page.getByTestId('features-in-flight').locator('[data-testid^="features-in-flight-row-"]')
  await expect(rows).toHaveCount(3)
  await expect(rows.first()).toContainText('Stage prompts')
  await expect(page.getByTestId('features-in-flight-count')).toHaveText('3')
  await expect(page.getByTestId('features-in-flight-spec')).toHaveAttribute('href', '/features?project=3&stage=spec')
  await expect(page.getByTestId('features-in-flight-all')).toHaveAttribute('href', '/features?project=3')
})
