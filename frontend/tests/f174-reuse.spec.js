/**
 * F174 S4 — the editor reused for a task's body and a defect's symptom.
 *
 * Each host keeps its own field (test id, label, keys). A task body has the
 * rich face beside the source one; a symptom, quoted as typed in its saved
 * view, is source only.
 */
import { test, expect } from './auth-fixture.js'

// The rich face is the default editor now; these specs drive the source one.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('regin_editor_mode', 'source')
    localStorage.setItem('regin_editor_mode:compose', 'source')
  })
})

const BODY = '## Goal\n\nShip **it**.'

function task() {
  return {
    id: 80, title: 'a specced feature', body: BODY, status: 'idea',
    source: 'web', tags: [], links: [], params: {},
    autorun: false, project_id: null, priority: 'none', priority_rank: 4,
    parent_id: null, assignee: null, assignee_name: null, rounds: 1,
    created_at: '2026-09-14', updated_at: '2026-09-14',
    external_id: null, created_by: null,
  }
}

const SYMPTOM = 'folds when **not bold**\n## not a heading'

const DEFECT = {
  id: 7, feature_id: 81, feature_title: 'The ledger',
  symptom: SYMPTOM, status: 'closed', round: 0,
  blamed_event_kind: 'started', missing_check: 'test_gutter', suspect_paths: [],
  fix_task_id: null, images: [],
  opened_at: '2026-09-14 12:00:00', closed_at: '2026-09-15 12:00:00',
}

async function stubTask(page, patches) {
  await page.route(/\/api\/tasks\/80$/, (route) => {
    if (route.request().method() === 'PATCH') {
      patches.push(route.request().postDataJSON())
      return route.fulfill({ json: { ok: true } })
    }
    return route.fulfill({ json: { task: task(), grain: { children: 0, runs: 0 } } })
  })
  await page.route(/\/api\/tasks\/80\/comments$/, (route) =>
    route.fulfill({ json: { threads: [] } }))
  await page.route(/\/api\/tasks\/80\/links$/, (route) =>
    route.fulfill({ json: { links: [] } }))
}

async function stubDefect(page) {
  await page.route(/\/api\/defects\/\d+$/, (route) =>
    route.fulfill({ json: { defect: DEFECT } }))
}

test.use({ viewport: { width: 1280, height: 900 } })

test('test_component_reused_in_task_and_defect_editors: a task body edits in source',
  async ({ page }) => {
    const patches = []
    await stubTask(page, patches)
    await page.goto('/tasks/80')
    await page.getByTestId('task-body-edit').click()

    const editor = page.getByTestId('markdown-editor')
    const input = page.getByTestId('task-body-area')
    await expect(editor).toHaveAttribute('data-mode', 'source')
    await expect(input).toHaveValue(BODY)
    await expect(input).toHaveAttribute('aria-label', 'Task details markdown')
    await expect(page.getByTestId('task-body-preview')).toHaveCount(0)
    await expect(editor.getByRole('toolbar')).toBeVisible()

    await input.fill('## Next\n\n**now**')

    await page.getByTestId('task-body-save').click()
    await expect.poll(() => patches).toEqual([{ body: '## Next\n\n**now**' }])
  })

test('test_component_reused_in_task_and_defect_editors: a symptom edits as source only',
  async ({ page }) => {
    await stubDefect(page)
    await page.goto('/defects/7')
    await page.getByTestId('defect-symptom-7').click()

    const editor = page.getByTestId('markdown-editor')
    const field = page.getByTestId('defect-edit-field-7')
    await expect(editor).toHaveAttribute('data-mode', 'source')
    await expect(field).toBeFocused()
    await expect(field).toHaveValue(SYMPTOM)
    await expect(field).toHaveAttribute('id', 'ds-7')
    // A mark the toolbar wrote would show as typed, so there is no toolbar.
    await expect(editor.getByRole('toolbar')).toHaveCount(0)

    await expect(editor.locator('[data-testid^="editor-mode-"]')).toHaveCount(0)
    await field.fill('the **gutter**')
    await expect(field).toHaveValue('the **gutter**')
    await expect(page.getByTestId('defect-edit-submit-7')).toBeVisible()
  })
