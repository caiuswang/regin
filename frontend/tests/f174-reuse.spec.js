/**
 * F174 S4 — the split editor reused for a task's body and a defect's symptom.
 *
 * Each host keeps its own field (test id, label, keys) and names its own
 * preview pane; the preview draws what that host's saved view draws — a task
 * body renders, a symptom is quoted as typed.
 */
import { test, expect } from './auth-fixture.js'

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

test('test_component_reused_in_task_and_defect_editors: a task body edits in split',
  async ({ page }) => {
    const patches = []
    await stubTask(page, patches)
    await page.goto('/tasks/80')
    await page.getByTestId('task-body-edit').click()

    const editor = page.getByTestId('markdown-editor')
    const input = page.getByTestId('task-body-area')
    const pane = page.getByTestId('task-body-preview-pane')
    await expect(editor).toHaveAttribute('data-mode', 'split')
    await expect(input).toHaveValue(BODY)
    await expect(input).toHaveAttribute('aria-label', 'Task details markdown')
    await expect(page.getByTestId('task-body-preview')).toHaveCount(0)
    await expect(editor.getByRole('toolbar')).toBeVisible()

    await expect(pane.locator('h2')).toHaveText('Goal')
    await input.fill('## Next\n\n**now**')
    await expect(pane.locator('h2')).toHaveText('Next')
    await expect(pane.locator('strong')).toHaveText('now')

    await page.getByTestId('task-body-save').click()
    await expect.poll(() => patches).toEqual([{ body: '## Next\n\n**now**' }])
  })

test('test_component_reused_in_task_and_defect_editors: a symptom previews as typed',
  async ({ page }) => {
    await stubDefect(page)
    await page.goto('/defects/7')
    await page.getByTestId('defect-symptom-7').click()

    const editor = page.getByTestId('markdown-editor')
    const field = page.getByTestId('defect-edit-field-7')
    const pane = page.getByTestId('defect-symptom-preview-pane')
    await expect(editor).toHaveAttribute('data-mode', 'split')
    await expect(field).toBeFocused()
    await expect(field).toHaveValue(SYMPTOM)
    await expect(field).toHaveAttribute('id', 'ds-7')
    // A mark the toolbar wrote would show as typed, so there is no toolbar.
    await expect(editor.getByRole('toolbar')).toHaveCount(0)

    await expect(pane).toHaveText(SYMPTOM)
    await expect(pane.locator('strong, h2')).toHaveCount(0)
    const whiteSpace = await pane.locator('p').evaluate((el) => getComputedStyle(el).whiteSpace)
    expect(whiteSpace).toMatch(/^pre/)

    await field.fill('the **gutter**')
    await expect(pane).toHaveText('the **gutter**')

    await page.getByTestId('editor-mode-preview').click()
    await expect(field).toBeHidden()
    await expect(page.getByTestId('defect-edit-submit-7')).toBeVisible()
  })
