/**
 * Feature #167 Rules A — every place that lists projects lets an editor add
 * one in place, through the one AddProjectDialog, and tells a viewer why
 * there are none.
 *
 * "Zero registered repos" is stubbed over the suite's real server: the scratch
 * DB is shared by every spec, so it cannot be emptied. The add and the probe
 * run for real against throwaway directories under the scratch dir.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync } from 'node:fs'
import { basename, join } from 'node:path'
import { test, expect } from './auth-fixture.js'
import { SCRATCH_DIR, TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

const NO_REPOS = '不是 git 仓库，其下也没有 git 仓库'

function scratchDir(prefix) {
  mkdirSync(SCRATCH_DIR, { recursive: true })
  return mkdtempSync(join(SCRATCH_DIR, prefix))
}

async function stubZeroProjects(page) {
  await page.route('**/api/repos', route => (route.request().method() === 'GET'
    ? route.fulfill({ json: { repos: [] } }) : route.fallback()))
  await page.route('**/api/pipeline/projects?*', route => route.fulfill({
    json: { items: [], pagination: { total: 0 }, population: 0 },
  }))
  await page.route('**/api/pipeline/overview*', async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    body.projects = { sort: 'attention', total: 0, active: 0, quiet: 0, rows: [] }
    await route.fulfill({ response: res, json: body })
  })
}

// The overview stub proxies a live read; one still in flight when a test ends
// would fail it on a closed page.
test.afterEach(async ({ page }) => { await page.unrouteAll({ behavior: 'ignoreErrors' }) })

async function asViewer(page) {
  await page.addInitScript(() => {
    const raw = localStorage.getItem('regin_auth_user')
    if (!raw) return
    const user = JSON.parse(raw)
    user.role = 'viewer'
    localStorage.setItem('regin_auth_user', JSON.stringify(user))
  })
}

async function openAddFromPicker(page) {
  await page.getByTestId('pipeline-project').click()
  const add = page.getByTestId('project-picker').getByTestId('project-picker-add')
  await expect(add).toHaveText('+ Add project…')
  await add.click()
  await expect(page.getByTestId('add-project-dialog')).toBeVisible()
}

test('test_project_picker_offers_add_project_to_editors', async ({ page }) => {
  await stubZeroProjects(page)
  await page.goto('/overview')
  // The overview read is proxied through the stub; let it land before the
  // test can end with the proxy mid-fetch.
  await expect(page.getByTestId('projects-first-run')).toBeVisible()

  await page.getByTestId('pipeline-project').click()
  const panel = page.getByTestId('project-picker')
  const rows = panel.locator('.pp-row')
  await expect(rows.last()).toHaveText('+ Add project…')
  await rows.last().click()

  await expect(panel).toHaveCount(0)
  await expect(page.getByTestId('add-project-dialog')).toBeVisible()
  await expect(page.getByTestId('add-project-path')).toBeFocused()
})

test('test_project_picker_hides_add_project_from_viewers', async ({ page }) => {
  await asViewer(page)
  await page.goto('/overview')

  await page.getByTestId('pipeline-project').click()
  await expect(page.getByTestId('project-picker-all')).toBeVisible()
  await expect(page.getByTestId('project-picker-add')).toHaveCount(0)
})

test('test_overview_empty_state_invites_adding_a_project', async ({ page }) => {
  await stubZeroProjects(page)
  await page.goto('/overview')

  const panel = page.getByTestId('projects-panel')
  await expect(panel.getByTestId('projects-first-run')).toContainText('还没有项目 — 添加一个 repo 或 workspace')
  await expect(panel.getByTestId('projects-empty')).toHaveCount(0)
  await panel.getByTestId('projects-first-run-add').click()
  await expect(page.getByTestId('add-project-dialog')).toBeVisible()
})

test('test_overview_empty_state_tells_viewers_who_can_add', async ({ page }) => {
  await stubZeroProjects(page)
  await asViewer(page)
  await page.goto('/overview')

  const card = page.getByTestId('projects-first-run')
  await expect(card).toHaveText('还没有项目，请 editor 在 Repos 添加')
  await expect(card.getByRole('button')).toHaveCount(0)
})

test('test_adding_a_repo_from_the_picker_scopes_the_pipeline_to_it', async ({ page, request }) => {
  const root = scratchDir('f167-add-')
  execFileSync('git', ['init', '-q', root])
  await page.goto('/overview')

  await openAddFromPicker(page)
  await page.getByTestId('add-project-path').fill(root)
  const submit = page.getByTestId('add-project-submit')
  await submit.click()
  await expect(page.getByTestId('add-project-kind')).toHaveText('Repository')
  await expect(submit).toHaveText('Add repository')
  const posted = page.waitForResponse(r => r.url().endsWith('/api/repos')
    && r.request().method() === 'POST')
  await submit.click()
  expect((await posted).ok()).toBeTruthy()

  const { token } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const listed = await (await request.get(`${API_BASE}/api/repos`, {
    headers: { Authorization: `Bearer ${token}` },
  })).json()
  const repo = listed.repos.find(r => r.name === basename(root))
  expect(repo, `${basename(root)} missing from /api/repos`).toBeTruthy()

  await expect(page.getByTestId('add-project-dialog')).toHaveCount(0)
  await expect(page).toHaveURL(new RegExp(`[?&]project=${repo.id}(&|$)`))
  await expect(page.getByTestId('pipeline-project')).toContainText(repo.name)
})

test('test_probe_rejects_a_folder_with_no_repos', async ({ page }) => {
  const root = scratchDir('f167-empty-')
  await page.goto('/repos')

  await page.getByTestId('repos-add').click()
  await page.getByTestId('add-project-path').fill(root)
  const submit = page.getByTestId('add-project-submit')
  await submit.click()

  await expect(page.getByTestId('add-project-blocked')).toHaveText(NO_REPOS)
  await expect(submit).toBeDisabled()
})

test('test_arm_sheet_adds_a_project_as_its_working_directory', async ({ page }) => {
  const added = '/tmp/f167-arm-added'
  const TASK = {
    id: 64, title: 'arm add', status: 'idea', priority: 'none', body: '', kind: 'task',
    created_at: '2026-09-01T00:00:00Z', updated_at: '2026-09-01T00:00:00Z', tags: [],
    project_id: null, feature_id: null, run_count: 0, images: [], comments: [], deps: [],
    action: 'arm', waits_on: null, menu: ['arm', 'edit'],
  }
  const skillCwds = []
  let cwds = []
  await page.route('**/api/tasks/64/runs', route => route.fulfill({ json: { runs: [] } }))
  await page.route('**/api/tasks/64/arm-proposal', route => route.fulfill({ status: 404, json: { error: 'not a slice' } }))
  await page.route(/\/api\/tasks\/64(\?|$)/, route => route.fulfill({ json: { task: TASK, feature: null, armed: null } }))
  await page.route('**/api/agent-runs/skill-options*', (route) => {
    skillCwds.push(new URL(route.request().url()).searchParams.get('cwd'))
    return route.fulfill({ json: { skills: [] } })
  })
  await page.route('**/api/agent-runs/launch-options', route => route.fulfill({ json: {
    enabled: true, cwds, models: [], permission_modes: ['default'], efforts: [],
    default_permission_mode: 'default', detached_detail: '', providers: [],
  } }))
  await page.route('**/api/repos/probe?*', route => route.fulfill({ json: {
    path: added, exists: true, is_git: true, child_repos: [], workspace_id: null,
  } }))
  await page.route('**/api/repos', (route) => {
    if (route.request().method() !== 'POST') return route.fallback()
    cwds = [added]
    return route.fulfill({ json: { ok: true, repo: { name: 'f167-arm-added', path: added } } })
  })

  await page.goto('/tasks/64')
  await page.getByTestId('task-detail-actions').getByTestId('task-menu').click()
  await page.getByTestId('task-verb-arm').click()
  await page.getByTestId('task-arm-cwd').click()
  await page.getByRole('option', { name: '+ Add project…', exact: true }).click()

  await expect(page.getByTestId('add-project-dialog')).toBeVisible()
  await expect(page.getByTestId('task-arm-form')).toBeVisible()
  await page.getByTestId('add-project-path').fill(added)
  await page.getByTestId('add-project-submit').click()
  await page.getByTestId('add-project-submit').click()

  await expect(page.getByTestId('add-project-dialog')).toHaveCount(0)
  await expect(page.getByTestId('task-arm-cwd')).toHaveText(added)
  expect(skillCwds.some(c => c && c.includes('add-project'))).toBe(false)
})
