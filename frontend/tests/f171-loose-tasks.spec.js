/**
 * Feature #171 — tasks under no feature reach the Overview: a "No feature"
 * panel, and a "No project" row in the Projects panel.
 */
import { test, expect } from './auth-fixture.js'

const EMPTY_LOOP = {
  stages: ['Features', 'Tasks', 'Defects'].map(label => ({ label, total: 0, parts: [] })),
  fix_tasks: 0,
}

const loose = (id, status = 'idea') => ({
  id, title: `loose task ${id}`, status, priority: 'none', kind: 'task', project_id: null,
})

function overview({ project, looseTasks, noProject }) {
  const body = {
    scope: project || 'all',
    loop: EMPTY_LOOP,
    features_in_flight: { total: 0, rows: [] },
    running: { total: 0, rows: [] },
    run_next: { total: 0, ready: 0, blocked: 0, rows: [] },
    recently_done: { week: 0, rows: [] },
    needs_decision: { waiting: 0, all_projects: false, rows: [] },
    loose_tasks: looseTasks,
  }
  if (!project) {
    body.projects = { sort: 'attention', total: 1, active: 0, quiet: 1, rows: [] }
    if (noProject) body.projects.no_project = noProject
  }
  return body
}

async function stub(page, opts) {
  const errors = []
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })
  await page.route('**/api/**', (route) => {
    const url = new URL(route.request().url())
    const p = url.pathname
    const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) })
    if (p === '/api/pipeline/overview') {
      return json(overview({ ...opts, project: url.searchParams.get('project') }))
    }
    if (p === '/api/pipeline/counts') return json({ counts: { tasks: 0, features: 0, defects: 0 } })
    if (p === '/api/repos') return json({ repos: [{ id: 7, name: 'core', path: '/c' }] })
    if (p.startsWith('/api/pipeline') || p.startsWith('/api/tasks') || p.startsWith('/api/features')
        || p.startsWith('/api/defects') || p.startsWith('/api/workspaces')) {
      return json({ items: [], pagination: { total: 0, page: 0, size: 50 }, groups: [], open: 0 })
    }
    return route.fallback()
  })
  return errors
}

test('the Overview lists open loose tasks, capped, with View all', async ({ page }) => {
  const rows = [1, 2, 3, 4, 5].map(id => loose(id))
  await stub(page, { looseTasks: { total: 7, rows } })
  await page.goto('/overview')

  const panel = page.getByTestId('loose-tasks')
  await expect(panel).toBeVisible()
  await expect(panel.getByTestId('loose-tasks-count')).toHaveText('7 open')
  await expect(panel.locator('[data-testid^="loose-tasks-row-"]')).toHaveCount(5)
  await expect(panel.getByTestId('loose-tasks-row-3').getByRole('link')).toHaveAttribute('href', '/tasks/3')

  const all = panel.getByTestId('loose-tasks-all')
  await expect(all).toHaveText('View all 7 →')
  await all.click()
  await expect(page).toHaveURL(/\/tasks\?.*unfeatured=1/)
})

test('View all is absent while every loose task fits', async ({ page }) => {
  await stub(page, { looseTasks: { total: 2, rows: [loose(1), loose(2, 'failed')] } })
  await page.goto('/overview')

  await expect(page.getByTestId('loose-tasks')).toBeVisible()
  await expect(page.getByTestId('loose-tasks-all')).toHaveCount(0)
})

test('an empty loose-tasks panel is omitted, without console errors', async ({ page }) => {
  const errors = await stub(page, { looseTasks: { total: 0, rows: [] } })
  await page.goto('/overview')

  await expect(page.getByTestId('projects-panel')).toBeVisible()
  await expect(page.getByTestId('loose-tasks')).toHaveCount(0)
  expect(errors).toEqual([])
})

test('the Projects panel offers No project and picks scope none', async ({ page }) => {
  await stub(page, { looseTasks: { total: 0, rows: [] },
                     noProject: { open_tasks: 4, open_defects: 1, scope: 'none' } })
  await page.goto('/overview')

  const row = page.getByTestId('projects-row-none')
  await expect(row).toContainText('No project')
  await expect(row.getByTestId('projects-defects-none')).toHaveText('1 open defect')
  await row.getByTestId('projects-pick-none').click()
  await expect(page).toHaveURL(/project=none/)
})

test('no No project row when the bucket is empty', async ({ page }) => {
  await stub(page, { looseTasks: { total: 0, rows: [] } })
  await page.goto('/overview')

  await expect(page.getByTestId('projects-panel')).toBeVisible()
  await expect(page.getByTestId('projects-row-none')).toHaveCount(0)
})

test('View all ignores the reader\'s stored narrowings', async ({ page }) => {
  await stub(page, { looseTasks: { total: 7, rows: [1, 2, 3, 4, 5].map(id => loose(id)) } })
  await page.addInitScript(() => localStorage.setItem('regin.tasks.status', 'running'))
  await page.goto('/overview')

  const feed = page.waitForRequest(r => {
    const u = new URL(r.url())
    return u.pathname === '/api/tasks' && u.searchParams.get('root_only') === '1'
  })
  await page.getByTestId('loose-tasks-all').click()
  const asked = new URL((await feed).url()).searchParams

  expect(asked.get('status') || '').toBe('')
  expect(new URL(page.url()).searchParams.get('status')).toBeNull()
})
