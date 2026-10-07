/**
 * Feature #139, slice S10 — the pixel Overview scoped to one project: the 1-4
 * panel's breadcrumb head, stage cells, figure keys and feature table. The
 * overview read is stubbed so every number on screen is one this spec chose.
 */
import { test, expect } from './auth-fixture.js'

const part = (key, n = 0) => ({ key, label: key, n, highlighted: false })
const LOOP = {
  stages: [
    { label: 'Tasks', total: 0, parts: ['idea', 'armed', 'running', 'done', 'failed'].map(k => part(k)) },
    { label: 'Features', total: 0, parts: ['spec', 'building', 'shipped', 'escaped'].map(k => part(k)) },
    { label: 'Defects', total: 0, parts: ['triage', 'open', 'closed'].map(k => part(k)) },
  ],
  fix_tasks: 0,
}

const feature = (id, title, stage, extra = {}) => ({
  id, title, origin: 'local', external_ref: null, stage, blurb: '', rounds: 1, sessions: 2,
  tasks: 4, tasks_done: 2, cost_usd: 4.5, gaps: {}, defects: { total: 0, open: 0, escapes: 0 },
  last_activity: '2026-10-05T06:00:00', ...extra,
})

const ROWS = [
  feature(131, 'Live trace follow', 'building'),
  feature(129, 'Cost attribution in the ledger', 'building', { tasks_done: 3, defects: { total: 2, open: 1, escapes: 0 } }),
  feature(127, 'Conversation folding', 'building', { tasks: 5 }),
  feature(118, 'Triage rail chords', 'escaped', { tasks: 3, tasks_done: 3, defects: { total: 4, open: 1, escapes: 1 } }),
  feature(133, 'Pipeline tab memory', 'spec', { tasks: 2, tasks_done: 0 }),
]

function scoped(project, { head = {}, features } = {}) {
  return {
    scope: String(project), loop: LOOP,
    running: { total: 0, rows: [] },
    run_next: { total: 0, ready: 0, rows: [] },
    needs_decision: { waiting: 0, rows: [] },
    project_head: {
      id: project, name: 'regin-web', breadcrumb: ['All projects', 'regin-web'],
      stage_bar: { spec: 2, building: 4, shipped: 3, escaped: 1 }, shipped_pct: 40,
      open_tasks: 9, open_defects: 3, spent_usd: 1284.6, ...head,
    },
    project_features: features ?? { total: 10, rows: ROWS },
  }
}

const ALL = {
  scope: 'all', loop: LOOP,
  running: { total: 0, rows: [] },
  run_next: { total: 0, ready: 0, rows: [] },
  needs_decision: { waiting: 0, rows: [] },
  projects: { sort: 'attention', total: 1, active: 1, quiet: 0, rows: [
    { id: 2, scope: '2', name: 'regin-web', status: 'active', escaped: 1, last_activity: null,
      stage_bar: { spec: 2, building: 4, shipped: 3, escaped: 1 }, features: 10, open_tasks: 9, open_defects: 3 },
  ] },
}

async function stubOverview(page, body) {
  await page.route(/\/api\/pipeline\/overview/, route => route.fulfill({
    status: 200, contentType: 'application/json',
    body: JSON.stringify(/[?&]project=/.test(route.request().url()) ? body : ALL),
  }))
}

async function openScoped(page, theme, project = 2) {
  await page.addInitScript(id => localStorage.setItem('regin_pipeline_theme', id), theme)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto(`/overview?project=${project}`)
  await expect.poll(() => page.evaluate(() => document.documentElement.getAttribute('data-pl-theme'))).toBe(theme)
  // The shell hides tab content while a theme chunk loads; toHaveCount alone
  // would pass on the hidden panel.
  await expect(page.getByTestId('project-scope')).toBeVisible()
}

const inner = { useInnerText: true }

async function breadcrumbClears(page, theme) {
  await openScoped(page, theme)
  await page.getByTestId('project-head-all').click()
  await expect(page.getByTestId('projects-panel')).toBeVisible()
  const url = new URL(page.url())
  return `${url.pathname}${url.search}`
}

test.describe('pixel scoped overview', () => {
  test('test_scoped_overview_shows_the_project_panel', async ({ page }) => {
    await stubOverview(page, scoped(2))
    await openScoped(page, 'pixel')

    const panel = page.getByTestId('project-scope')
    const isAfterLoop = await page.evaluate(() => {
      const loop = document.querySelector('[data-testid="overview-loop"]')
      const scope = document.querySelector('[data-testid="project-scope"]')
      return Boolean(loop.compareDocumentPosition(scope) & Node.DOCUMENT_POSITION_FOLLOWING)
    })
    expect(isAfterLoop).toBe(true)

    await expect(panel.locator('[data-pl-part="panel-head"]')).not.toHaveAttribute('data-pl-chapter')
    const head = await panel.locator('[data-pl-part="panel-head"]').innerText()
    expect(head.replace(/\s+/g, ' ')).toMatch(/ALL PROJECTS \/ REGIN-WEB/)
    await expect(page.getByTestId('project-head-count')).toHaveText('10 FEATURES', inner)
    await expect(page.getByTestId('project-head-pct')).toHaveText('40% SHIPPED', inner)

    const tones = await page.getByTestId('project-head-cells').locator('[data-pl-part="meter-cell"]')
      .evaluateAll(els => els.map(e => e.dataset.tone))
    const tally = tones.reduce((n, t) => ({ ...n, [t]: (n[t] || 0) + 1 }), {})
    expect(tally).toEqual({ spec: 2, building: 4, shipped: 3, escaped: 1 })
    await expect(page.getByTestId('project-head-legend').locator('li'))
      .toHaveText(['2 SPEC', '4 BUILDING', '3 SHIPPED', '1 ESCAPED'], inner)

    await expect(page.getByTestId('project-head-tasks')).toHaveText(/OPEN TASKS\s*9/, inner)
    await expect(page.getByTestId('project-head-tasks')).toHaveAttribute('href', '/tasks?project=2')
    const defects = page.getByTestId('project-head-defects')
    await expect(defects).toHaveText(/OPEN DEFECTS\s*3/, inner)
    await expect(defects).toHaveAttribute('href', '/defects?project=2&status=open')
    await expect(defects).toHaveAttribute('data-tone', 'warn')
    const [border, warn] = await defects.evaluate((el) => {
      const probe = document.createElement('span')
      probe.style.color = 'var(--pl-warn)'
      el.appendChild(probe)
      const want = getComputedStyle(probe).color
      probe.remove()
      return [getComputedStyle(el).borderTopColor, want]
    })
    expect(border).toBe(warn)
    await expect(page.getByTestId('project-head-spent')).toHaveText(/SPENT\s*\$1,284\.60/, inner)

    const rows = page.getByTestId('project-features').locator('tbody tr')
    await expect(rows).toHaveCount(5)
    await expect(rows.first()).toContainText('#131')
    await expect(rows.first().getByRole('link', { name: 'Live trace follow' })).toHaveAttribute('href', '/features/131')
    const escaped = page.getByTestId('project-feature-118')
    await expect(escaped.locator('td').nth(1)).toHaveText('ESCAPED', inner)
    await expect(escaped.locator('td').nth(2)).toContainText('3 / 3')
    await expect(escaped.locator('td').nth(3)).toHaveText('1 / 4')
    await expect(escaped.locator('td').nth(3)).toHaveClass(/is-warn/)
    await expect(page.getByTestId('project-feature-131').locator('td').nth(3)).not.toHaveClass(/is-warn/)
    await expect(page.getByTestId('project-feature-131').locator('[data-pl-part="meter-cell"][data-tone="settled"]'))
      .toHaveCount(2)

    await expect(page.getByTestId('project-features-showing')).toHaveText('SHOWING 5 OF 10', inner)
    await expect(page.getByTestId('project-features-all')).toHaveText('ALL 10 IN FEATURES →', inner)
    await expect(page.getByTestId('project-features-all')).toHaveAttribute('href', '/features?project=2')

    const stageWord = el => getComputedStyle(el.querySelector('.px-ps__stage')).color
    const tokenColour = (el, token) => {
      const probe = document.createElement('span')
      probe.style.color = `var(${token})`
      el.appendChild(probe)
      const c = getComputedStyle(probe).color
      probe.remove()
      return c
    }
    expect(await escaped.evaluate(stageWord))
      .toBe(await escaped.evaluate(tokenColour, '--pl-warn'))

    // Between md and lg the panel stays, but Tasks and Active drop out.
    await page.setViewportSize({ width: 800, height: 1000 })
    await expect(page.getByTestId('project-scope')).toBeVisible()
    await expect(escaped.locator('td').nth(2)).toBeHidden()
    const overflow = await page.evaluate(() => {
      const el = document.querySelector('.content-scroll') || document.documentElement
      return el.scrollWidth - el.clientWidth
    })
    expect(overflow).toBeLessThanOrEqual(0)
    await page.setViewportSize({ width: 1440, height: 1000 })

    const pixelCleared = await breadcrumbClears(page, 'pixel')
    const fresh = await page.context().newPage()
    await stubOverview(fresh, scoped(2))
    const classicCleared = await breadcrumbClears(fresh, 'classic')
    expect(pixelCleared).toBe(classicCleared)
    expect(pixelCleared).not.toMatch(/project=/)
  })

  test('test_scoped_overview_with_no_features', async ({ page }) => {
    await stubOverview(page, scoped(5, {
      head: { stage_bar: { spec: 0, building: 0, shipped: 0, escaped: 0 }, shipped_pct: null,
        open_tasks: 0, open_defects: 0, spent_usd: null },
      features: { total: 0, rows: [] },
    }))
    await openScoped(page, 'pixel', 5)

    await expect(page.getByTestId('project-features-empty')).toHaveText('No feature is filed under this project yet.')
    await expect(page.getByTestId('project-features').locator('tbody tr')).toHaveCount(0)
    await expect(page.getByTestId('project-head-cells').locator('[data-pl-part="meter-cell"]')).toHaveCount(0)
    await expect(page.getByTestId('project-head-count')).toHaveText('0 FEATURES', inner)
    await expect(page.getByTestId('project-head-pct')).toHaveText('NOTHING LIVE', inner)
    await expect(page.getByTestId('project-head-defects')).not.toHaveAttribute('data-tone', /.+/)
    await expect(page.getByTestId('project-head-spent')).toHaveText(/SPENT\s*—/, inner)
    await expect(page.getByTestId('project-features-showing')).toHaveText('SHOWING 0 OF 0', inner)
  })
})
