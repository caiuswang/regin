/**
 * Feature #148, slice S3 — a project's step stages, edited on its own page and
 * read back as the Feature page's step buttons. Against the suite's server, so
 * every assertion is about what the stages API actually stored.
 */
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'
import { registerFixtureRepo } from './helpers/fixtureRepo.js'

async function client(page) {
  const login = await page.request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })
  const { token } = await login.json()
  const headers = { Authorization: `Bearer ${token}` }
  const call = async (method, path, data) => {
    const res = await page.request.fetch(`${API_BASE}/api${path}`, { method, headers, data })
    expect(res.ok(), `${method} ${path}: ${await res.text()}`).toBeTruthy()
    return res.json()
  }
  return {
    get: path => call('GET', path),
    post: (path, data) => call('POST', path, data),
    put: (path, data) => call('PUT', path, data),
    del: path => call('DELETE', path),
  }
}

async function projectWithDesign(page, playwright, prefix) {
  const api = await client(page)
  const project = await registerFixtureRepo(playwright, prefix, () => {})
  await api.post(`/projects/${project}/stages`, {
    key: 'design', label: 'Design', role: 'spec', prompt: 'Design only, no code — {{stage_label}}',
  })
  await api.put(`/projects/${project}/stages/order`, { keys: ['spec', 'design', 'build'] })
  const feature = await api.post('/features', { title: `stages ${prefix}`, project_id: project })
  return { api, project, feature: feature.id }
}

const liveKeys = page => page.getByTestId('project-stages-live')
  .locator('[data-stage-key]').evaluateAll(rows => rows.map(r => r.dataset.stageKey))

test('test_project_stages_page_edits_stages', async ({ page, playwright }) => {
  const api = await client(page)
  const project = await registerFixtureRepo(playwright, 'stages-page-', () => {})
  await page.goto(`/projects/${project}`)

  await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
  await expect(page.locator('.pl-tab.is-active')).toHaveCount(0)
  await expect(page.getByTestId('project-info-repo')).toHaveAttribute('href', /^\/repos\//)
  await expect(page.getByTestId('project-stages-inherited')).toBeVisible()
  await expect.poll(() => liveKeys(page)).toEqual(['spec', 'build'])

  await page.getByTestId('stage-new-label').fill('Design')
  await expect(page.getByTestId('stage-new-key')).toHaveValue('design')
  await page.getByTestId('stage-new-submit').click()
  await expect.poll(() => liveKeys(page)).toEqual(['spec', 'build', 'design'])

  await page.getByTestId('stage-grip-design').dragTo(page.getByTestId('stage-row-build'))
  await expect.poll(() => liveKeys(page)).toEqual(['spec', 'design', 'build'])
  expect(await page.getByTestId('project-stages-live').locator('input[data-testid^="stage-label-"]')
    .evaluateAll(inputs => inputs.map(i => i.value))).toEqual(['Spec', 'Design', 'Build'])

  await page.getByTestId('stage-expand-design').click()
  await page.getByTestId('stage-prompt-input-design').fill('Settle the design for {{feature_named}}')
  await expect(page.getByTestId('stage-prompt-preview-design')).toContainText('#80 (Agent SDK runs)')
  await page.getByTestId('stage-prompt-save-design').click()
  await expect(page.getByTestId('stage-prompt-save-design')).toBeDisabled()

  const stored = await api.get(`/projects/${project}/stages`)
  expect(stored.inherited).toBe(false)
  expect(stored.stages.filter(s => !s.archived_at).map(s => s.key)).toEqual(['spec', 'design', 'build'])
  expect(stored.stages.find(s => s.key === 'design').prompt_body)
    .toBe('Settle the design for {{feature_named}}')
})

test('test_feature_page_renders_stage_buttons', async ({ page, playwright }) => {
  const { feature } = await projectWithDesign(page, playwright, 'stages-buttons-')
  await page.goto(`/features/${feature}`)

  const verbs = page.getByTestId('feature-tasks').locator('[data-testid^="feature-verb-"]')
  await expect(verbs).toHaveText(['Spec', 'Design', 'Build'])

  await page.getByTestId('feature-verb-design').click()
  const dialog = page.getByTestId('feature-step-dialog')
  await expect(dialog.getByRole('heading', { name: 'Design' })).toBeVisible()
  await expect(dialog.getByTestId('feature-step-prompt')).toContainText('Design only, no code — {{stage_label}}')
  const posted = page.waitForRequest(req => req.method() === 'POST'
    && req.url().endsWith(`/api/features/${feature}/steps`))
  await page.getByTestId('feature-step-submit').click()
  expect((await posted).postDataJSON()).toEqual({ stage: 'design', body: null })
  await expect(page).toHaveURL(/\/tasks\/\d+$/)
})

test('test_archived_stage_hides_button', async ({ page, playwright }) => {
  const { api, project, feature } = await projectWithDesign(page, playwright, 'stages-archived-')
  const { task } = await api.post(`/features/${feature}/steps`, { stage: 'design' })
  const removed = await api.del(`/projects/${project}/stages/design`)
  expect(removed).toMatchObject({ archived: true, deleted: false })

  await page.goto(`/features/${feature}`)
  await expect(page.getByTestId('feature-verb-spec')).toBeVisible()
  await expect(page.getByTestId('feature-verb-build')).toBeVisible()
  await expect(page.getByTestId('feature-verb-design')).toHaveCount(0)
  await expect(page.getByTestId('feature-tasks-list').locator(`a[href="/tasks/${task.id}"]`)).toBeVisible()

  await page.goto(`/projects/${project}`)
  await expect(page.getByTestId('project-stages-archived').getByTestId('stage-archived-design')).toBeVisible()
  await expect.poll(() => liveKeys(page)).toEqual(['spec', 'build'])
})

test('rename, re-role, nudge and remove a stage from its row', async ({ page, playwright }) => {
  const { api, project } = await projectWithDesign(page, playwright, 'stages-row-')
  await page.goto(`/projects/${project}`)
  await expect.poll(() => liveKeys(page)).toEqual(['spec', 'design', 'build'])

  await expect(page.getByTestId('stage-remove-spec')).toHaveCount(0)
  await expect(page.getByTestId('stage-role-spec')).toHaveText('spec')

  const label = page.getByTestId('stage-label-design')
  await label.fill('Design review')
  await label.press('Enter')
  await page.getByTestId('stage-role-design').click()
  await page.getByRole('option', { name: 'build' }).click()
  await page.getByTestId('stage-down-design').click()
  await expect.poll(() => liveKeys(page)).toEqual(['spec', 'build', 'design'])

  let stored = (await api.get(`/projects/${project}/stages`)).stages
  expect(stored.find(s => s.key === 'design')).toMatchObject({ label: 'Design review', role: 'build' })

  await page.getByTestId('stage-remove-design').click()
  await page.getByRole('button', { name: 'Remove Design review?' }).click()
  await expect.poll(() => liveKeys(page)).toEqual(['spec', 'build'])
  stored = (await api.get(`/projects/${project}/stages`)).stages
  expect(stored.map(s => s.key)).toEqual(['spec', 'build'])
})

test('the scope panel and the picker both open the project page', async ({ page, playwright }) => {
  const project = await registerFixtureRepo(playwright, 'stages-entry-', () => {})
  await page.goto(`/overview?project=${project}`)

  await page.getByTestId('project-head-manage').click()
  await expect(page).toHaveURL(new RegExp(`/projects/${project}$`))

  await page.getByTestId('pipeline-project').click()
  await expect(page.getByTestId('project-picker-manage')).toBeVisible()
  await page.getByTestId('project-picker-all').click()
  await page.getByTestId('pipeline-project').click()
  await expect(page.getByTestId('project-picker-manage')).toHaveCount(0)
})

test('a viewer reads the stages and their prompts but edits nothing', async ({ page }) => {
  await page.addInitScript(() => {
    const user = JSON.parse(localStorage.getItem('regin_auth_user') || '{}')
    localStorage.setItem('regin_auth_user', JSON.stringify({ ...user, role: 'viewer' }))
  })
  await page.goto('/projects/default')

  await expect.poll(() => liveKeys(page)).toEqual(['spec', 'build'])
  await expect(page.getByTestId('stage-new')).toHaveCount(0)
  await expect(page.locator('[data-testid^="stage-grip-"]')).toHaveCount(0)
  await expect(page.locator('[data-testid^="stage-remove-"]')).toHaveCount(0)
  await expect(page.getByTestId('stage-label-spec')).toHaveText('Spec')
  await page.getByTestId('stage-expand-spec').click()
  await expect(page.getByTestId('stage-prompt-preview-spec')).toBeVisible()
  await expect(page.getByTestId('stage-prompt-input-spec')).toHaveCount(0)
  await expect(page.getByTestId('stage-prompt-save-spec')).toHaveCount(0)
})
