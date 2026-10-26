/**
 * F180 #6 — a dialog's editor face is remembered under its own key
 * (`regin_editor_mode:compose`), apart from the page editors'
 * (`regin_editor_mode`), and the feature step dialog writes its Instructions
 * in the same rich editor the other create dialogs use.
 *
 * Stubbed: every assertion is about what the page renders, stores and sends.
 */
import { test, expect } from './auth-fixture.js'

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

const FEATURE = 242
const STEP_TASK = 90
const REF = {
  ref: 'task:42', kind: 'task', key: '42', label: 'title', title: 'title',
  status: 'idea', project_id: null, project: null, feature_id: null,
  feature_title: null, url: '/tasks/42',
}

function ledger() {
  return {
    id: FEATURE, title: 'Feature workbench', origin: 'native',
    external_ref: null, external_url: null, sync_error: null, body: '## Intent\n\nspec',
    stage: { stage: 'spec', blurb: '', rounds: 1, claimed_at: null,
             open_defects: 0, escapes: 0 },
    synced_at: null, stage_live: true,
    cost: { usd: 0, sessions: 0, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 0, gaps: {} },
    tokens: { input: 0, output: 0 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 },
  }
}

function stepTask() {
  return {
    id: STEP_TASK, title: 'Spec · Feature workbench', body: '', status: 'idea', kind: 'spec',
    source: 'web', tags: [], links: [], priority: 'none', priority_rank: 4,
    project_id: null, feature_id: FEATURE, feature_title: 'Feature workbench',
    assignee: null, assignee_name: null, external_id: `feature:${FEATURE}:spec`, created_by: 1,
    created_at: '2026-10-26 10:00:00', updated_at: '2026-10-26 10:00:00',
    images: [], run_count: 0,
  }
}

async function stubFeatureList(page) {
  await page.route(/\/api\/features(\?.*)?$/, (route) => route.fulfill({
    json: {
      items: [], total: 0,
      pagination: { strategy: 'offset', total: 0, page: 0, size: 50,
                    has_next: false, has_prev: false },
      coverage: { declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 },
      unattributed_defects: 0,
    },
  }))
}

async function stubFeaturePage(page) {
  const sent = { steps: [], uploads: [] }
  await page.route(new RegExp(`/api/features/${FEATURE}$`), (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: ledger() }) : route.fallback())
  await page.route('**/api/tasks?**', (route) =>
    route.request().method() === 'GET'
      ? route.fulfill({ json: { items: [], next_cursor: null } })
      : route.fallback())
  await page.route('**/api/defects?feature_id=*', (route) =>
    route.fulfill({ json: { items: [], scope: 'feature' } }))
  await page.route(`**/api/features/${FEATURE}/steps`, (route) => {
    sent.steps.push(route.request().postDataJSON())
    return route.fulfill({ status: 201, json: { task: stepTask(), created: true } })
  })
  await page.route(new RegExp(`/api/tasks/${STEP_TASK}/images$`), (route) => {
    sent.uploads.push(route.request().postDataJSON())
    return route.fulfill({ status: 201, json: { image: { id: 1, idx: 1, media_type: 'image/png', byte_size: 68 } } })
  })
  await page.route((url) => url.pathname === '/api/refs', (route) => {
    const q = new URL(route.request().url()).searchParams.get('q') || ''
    return route.fulfill({
      json: { query: q, kind: null, context: [], groups: [{ kind: 'task', total: 1, items: [REF] }] },
    })
  })
  return sent
}

async function pasteImage(locator) {
  await locator.evaluate((el, b64) => {
    const bin = atob(b64)
    const bytes = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i)
    const dt = new DataTransfer()
    dt.items.add(new File([bytes], 'pasted.png', { type: 'image/png' }))
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }, PNG_B64)
}

const stored = (page) => page.evaluate(() => ({
  document: localStorage.getItem('regin_editor_mode'),
  compose: localStorage.getItem('regin_editor_mode:compose'),
}))

async function openStepDialog(page) {
  await page.goto(`/features/${FEATURE}`)
  await page.getByTestId('feature-verb-spec').click()
  const dialog = page.getByTestId('feature-step-dialog')
  await expect(dialog).toBeVisible()
  return dialog
}

test.use({ viewport: { width: 1280, height: 900 } })

test('test_declare_dialog_opens_rich_despite_page_source_mode', async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('regin_editor_mode', 'source')
    localStorage.removeItem('regin_editor_mode:compose')
  })
  await stubFeatureList(page)
  await page.goto('/features')
  await page.getByTestId('declare-feature').click()

  const dialog = page.getByTestId('new-feature')
  await expect(dialog.getByTestId('markdown-editor')).toHaveAttribute('data-mode', 'rich')
  await expect(dialog.getByTestId('declare-body-rich')).toBeVisible()
  expect(await stored(page)).toEqual({ document: 'source', compose: null })
})

test('test_compose_mode_choice_does_not_leak_to_document', async ({ page }) => {
  await page.addInitScript(() => {
    if (sessionStorage.getItem('f180-seeded')) return
    sessionStorage.setItem('f180-seeded', '1')
    localStorage.removeItem('regin_editor_mode')
    localStorage.removeItem('regin_editor_mode:compose')
  })
  await stubFeatureList(page)
  await stubFeaturePage(page)
  await page.goto('/features')
  await page.getByTestId('declare-feature').click()
  const dialog = page.getByTestId('new-feature')
  await dialog.getByTestId('editor-mode-source').click()
  await expect(dialog.getByTestId('markdown-editor')).toHaveAttribute('data-mode', 'source')
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)

  await page.goto(`/features/${FEATURE}`)
  await page.getByTestId('feature-spec-edit').click()
  await expect(page.getByTestId('feature-spec').getByTestId('markdown-editor'))
    .toHaveAttribute('data-mode', 'rich')
  expect(await stored(page)).toEqual({ document: null, compose: 'source' })
})

test('test_feature_step_dialog_uses_rich_editor', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('regin_editor_mode', 'source'))
  const sent = await stubFeaturePage(page)
  const dialog = await openStepDialog(page)

  await expect(dialog.getByTestId('markdown-editor')).toHaveAttribute('data-mode', 'rich')
  await expect(dialog.getByTestId('editor-expand')).toHaveCount(0)
  const face = dialog.getByTestId('feature-step-body-rich')
  await face.click()
  await page.keyboard.type('## Shape')
  await page.keyboard.press('Enter')
  await page.keyboard.type('key ')
  await page.keyboard.type('@')
  await expect(page.getByTestId('ref-menu')).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('ref-menu')).toHaveCount(0)
  await expect(dialog).toBeVisible()
  await page.keyboard.press('Backspace')
  await page.keyboard.type('**shape**')

  await dialog.getByTestId('feature-step-submit').click()
  await expect(page).toHaveURL(new RegExp(`/tasks/${STEP_TASK}$`))
  expect(sent.steps).toEqual([{ stage: 'spec', body: '## Shape\n\nkey **shape**' }])
  expect(sent.uploads).toEqual([])
})

test('test_feature_step_dialog_pasted_image_lands_on_step_task', async ({ page }) => {
  const sent = await stubFeaturePage(page)
  const dialog = await openStepDialog(page)

  const face = dialog.getByTestId('feature-step-body-rich')
  await face.click()
  await page.keyboard.type('see')
  await page.keyboard.press('Enter')
  await pasteImage(face)
  await expect(face.locator('img[data-idx="1"]')).toHaveCount(1)
  // Held in the browser until the step task exists.
  expect(sent.uploads).toEqual([])

  await dialog.getByTestId('feature-step-submit').click()
  await expect(page).toHaveURL(new RegExp(`/tasks/${STEP_TASK}$`))
  expect(sent.steps).toHaveLength(1)
  expect(sent.steps[0].body).toContain('![](image:1)')
  expect(sent.uploads).toHaveLength(1)
  expect(sent.uploads[0].idx).toBe(1)
})
