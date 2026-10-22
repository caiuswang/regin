/**
 * Feature #167 Rules C — a workspace is a selectable scope in the ProjectPicker,
 * its member repos indented under it, and `?project=ws:<id>` narrows the
 * Pipeline to them. The workspace is created for real over a scratch folder
 * of two git trees.
 */
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync } from 'node:fs'
import { join } from 'node:path'
import { test, expect } from './auth-fixture.js'
import { SCRATCH_DIR, TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

function folderOfRepos() {
  mkdirSync(SCRATCH_DIR, { recursive: true })
  const root = mkdtempSync(join(SCRATCH_DIR, 'ws-scope-'))
  for (const name of ['alpha', 'beta']) {
    const dir = join(root, `${name}-${Date.now()}`)
    execFileSync('git', ['init', '-q', dir])
  }
  return root
}

async function createWorkspace(request) {
  const { token } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const headers = { Authorization: `Bearer ${token}` }
  const root = folderOfRepos()
  const res = await request.post(`${API_BASE}/api/workspaces`, { headers, data: { path: root } })
  expect(res.status()).toBe(201)
  const { workspace } = await res.json()
  return { workspace, headers }
}

test('test_workspace_scope_filters_the_pipeline_to_member_repos', async ({ page, request }) => {
  const { workspace, headers } = await createWorkspace(request)
  expect(workspace.members).toHaveLength(2)
  try {
    await page.goto('/overview')
    await page.getByTestId('pipeline-project').click()
    const panel = page.getByTestId('project-picker')
    const header = panel.getByTestId(`project-picker-ws-${workspace.id}`)
    await expect(header).toContainText(workspace.name)
    await expect(header).toContainText('2 repos')

    // Members are indented under their header, not flat among the loose rows.
    const headerBox = await header.boundingBox()
    for (const m of workspace.members) {
      const row = panel.getByTestId(`project-picker-row-${m.id}`)
      await expect(row).toHaveClass(/pp-row--member/)
      const box = await row.boundingBox()
      expect(box.y).toBeGreaterThan(headerBox.y)
    }

    const overview = page.waitForResponse(r => r.url().includes(
      `/api/pipeline/overview?project=ws%3A${workspace.id}`) && r.ok())
    await header.click()
    await expect(page).toHaveURL(new RegExp(`project=ws(%3A|:)${workspace.id}`))
    await overview
    await expect(page.getByTestId('pipeline-project')).toContainText(workspace.name)
    await expect(page.getByTestId('overview-error')).toHaveCount(0)

    const features = page.waitForResponse(r => r.url().includes('/api/features?')
      && r.url().includes(`project_id=ws%3A${workspace.id}`))
    await page.getByRole('link', { name: /^Features/ }).first().click()
    expect((await features).status()).toBe(200)
  } finally {
    await request.delete(`${API_BASE}/api/workspaces/${workspace.id}`, { headers })
  }
})

test('test_unknown_workspace_scope_reads_as_all_projects', async ({ page }) => {
  const read = page.waitForResponse(r => r.url().includes('/api/pipeline/overview?project=ws%3A999999'))
  await page.goto('/overview?project=ws:999999')
  expect((await read).status()).toBe(200)
  await expect(page.getByTestId('overview-error')).toHaveCount(0)
})
