/**
 * Feature #172 — a project's output language, set on its page and read back
 * in the launch prompt a task under it would run with.
 */
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'
import { registerFixtureRepo } from './helpers/fixtureRepo.js'

async function client(page) {
  const login = await page.request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })
  const { token } = await login.json()
  const headers = { Authorization: `Bearer ${token}` }
  return async (method, path, data) => {
    const res = await page.request.fetch(`${API_BASE}/api${path}`, { method, headers, data })
    expect(res.ok(), `${method} ${path}: ${await res.text()}`).toBeTruthy()
    return res.json()
  }
}

test('test_project_language_reaches_the_task_preview', async ({ page, playwright }) => {
  const call = await client(page)
  const project = await registerFixtureRepo(playwright, 'f172-lang-', () => {})
  const created = await call('POST', '/tasks', { title: 'say hello', project_id: project })
  const taskId = (created.task ?? created).id

  await page.goto(`/projects/${project}`)
  const input = page.getByTestId('project-output-language-input')
  await expect(input).toHaveValue('')
  await expect(input).not.toHaveAttribute('placeholder', /[\u4e00-\u9fff]/)
  await input.fill('简体中文')
  await page.getByTestId('project-output-language-save').click()
  await expect(page.getByTestId('project-output-language-save')).toBeDisabled()
  expect((await call('GET', `/projects/${project}/output-language`)).output_language).toBe('简体中文')

  await page.goto(`/tasks/${taskId}`)
  const block = page.getByTestId('launch-prompt')
  await expect(block).not.toHaveAttribute('open', /.*/)
  await block.locator('summary').click()
  await expect(page.getByTestId('launch-prompt-source')).toHaveText('project')
  await expect(page.getByTestId('launch-prompt-language')).toHaveText('简体中文')
  const preview = await call('GET', `/tasks/${taskId}/prompt-preview`)
  await expect(page.getByTestId('launch-prompt-text')).toHaveText(preview.prompt)
  expect(preview.prompt).toContain('<language>')

  await page.goto(`/projects/${project}`)
  await expect(input).toHaveValue('简体中文')
  await input.fill('')
  await page.getByTestId('project-output-language-save').click()
  await expect(page.getByTestId('project-output-language-fallback')).toBeVisible()
  expect((await call('GET', `/projects/${project}/output-language`)).output_language).toBeNull()
})

test('test_preview_failure_is_shown_not_blank', async ({ page }) => {
  const call = await client(page)
  const created = await call('POST', '/tasks', { title: 'preview fails' })
  const taskId = (created.task ?? created).id
  await page.route(`**/api/tasks/${taskId}/prompt-preview`,
    route => route.fulfill({ status: 500, json: { error: 'boom' } }))

  await page.goto(`/tasks/${taskId}`)
  await page.getByTestId('launch-prompt').locator('summary').click()
  await expect(page.getByTestId('launch-prompt-error')).toContainText('boom')
})

test('test_default_page_edits_the_global_language', async ({ page }) => {
  const call = await client(page)
  const global = async () => (await call('GET', '/settings/tasks'))
    .fields.find(f => f.key === 'output_language').value

  await page.goto('/projects/default')
  const input = page.getByTestId('project-output-language-input')
  await expect(input).not.toHaveAttribute('placeholder', /[\u4e00-\u9fff]/)
  await input.fill('English')
  await page.getByTestId('project-output-language-save').click()
  await expect(page.getByTestId('project-output-language-save')).toBeDisabled()
  expect(await global()).toBe('English')

  const created = await call('POST', '/tasks', { title: 'no project' })
  const preview = await call('GET', `/tasks/${(created.task ?? created).id}/prompt-preview`)
  expect([preview.language, preview.language_source]).toEqual(['English', 'default'])

  await input.fill('')
  await page.getByTestId('project-output-language-save').click()
  await expect(page.getByTestId('project-output-language-save')).toBeDisabled()
  expect(await global()).toBe('')
})
