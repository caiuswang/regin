/**
 * F178 S5 — the two create dialogs write their body in the same editor the
 * pages do: rich by default, no Expand, images through the dialog's own held
 * list, and Esc spent on an open menu before it closes the dialog.
 *
 * The declare dialog is stubbed like `feature-declare.spec.js` (the assertion
 * is the POST it sends); the task composer opens against the e2e server and is
 * never submitted.
 */
import { test, expect } from './auth-fixture.js'

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

function ledger() {
  return {
    id: 301, title: 'rich declare', origin: 'native',
    external_ref: null, external_url: null, sync_error: null,
    stage: { stage: 'spec', blurb: 'specified, not started', rounds: 1,
             claimed_at: null, open_defects: 0, escapes: 0 },
    cost: { usd: null, gaps: { no_usage_captured: 0 } },
    work_time: {}, tokens: {},
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 0, all_sessions: 0 },
  }
}

async function stubFeatures(page) {
  const sent = { create: null, uploads: [] }
  // Registered first: Playwright tries the latest route first, and the upload
  // route below must win over this catch-all.
  await page.route(/\/api\/features\/301(\/.*)?$/, (route) => route.fulfill({ json: ledger() }))
  await page.route(/\/api\/features(\?.*)?$/, (route) => {
    const method = route.request().method()
    if (method === 'POST') {
      sent.create = route.request().postDataJSON()
      return route.fulfill({ status: 201, json: ledger() })
    }
    return route.fulfill({
      json: {
        items: [], total: 0,
        pagination: { strategy: 'offset', total: 0, page: 0, size: 50,
                      has_next: false, has_prev: false },
        coverage: { declared_sessions: 0, all_sessions: 0, declared_usd: 0, all_usd: 0 },
        unattributed_defects: 0,
      },
    })
  })
  await page.route(/\/api\/features\/301\/images$/, (route) => {
    sent.uploads.push(route.request().postDataJSON())
    return route.fulfill({ status: 201, json: { image: { id: 1, idx: 1, media_type: 'image/png', byte_size: 68 } } })
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

async function pasteText(locator, text) {
  await locator.evaluate((el, value) => {
    const dt = new DataTransfer()
    dt.setData('text/plain', value)
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }, text)
}

async function openComposer(page) {
  await page.goto('/tasks')
  await page.getByTestId('task-new').click()
  await expect(page.locator('#task-title')).toBeVisible()
}

test.use({ viewport: { width: 1280, height: 900 } })

test('test_declare_dialog_uses_rich_editor', async ({ page }) => {
  const sent = await stubFeatures(page)
  await page.goto('/features')
  await page.getByTestId('declare-feature').click()

  const dialog = page.getByTestId('new-feature')
  const face = dialog.getByTestId('declare-body-rich')
  await expect(face).toBeVisible()
  await expect(dialog.getByTestId('editor-mode-rich')).toHaveAttribute('aria-pressed', 'true')
  await expect(dialog.getByTestId('editor-mode-source')).toBeVisible()
  await expect(dialog.getByTestId('editor-expand')).toHaveCount(0)
  await expect(dialog.getByTestId('editor-head')).toHaveCSS('position', 'static')

  await page.getByTestId('declare-title').fill('rich declare')
  await face.click()
  await page.keyboard.type('## Intent')
  await page.keyboard.press('Enter')
  await page.keyboard.type('x')
  await page.keyboard.press('Enter')
  await pasteImage(face)
  await expect(face.locator('img[data-idx="1"]')).toHaveCount(1)

  // ⌘⇧F opens no focus layout inside a dialog.
  await page.keyboard.press('ControlOrMeta+Shift+F')
  await expect(dialog.getByTestId('markdown-editor')).toHaveAttribute('data-layout', 'inline')

  await page.getByTestId('declare-submit').click()
  await expect.poll(() => sent.create).not.toBeNull()
  expect(sent.create.body).toBe('## Intent\n\nx\n\n![](image:1)')
  await expect.poll(() => sent.uploads.length).toBe(1)
  expect(sent.uploads[0].idx).toBe(1)
})

test('test_task_composer_uses_rich_editor', async ({ page }) => {
  await openComposer(page)
  const dialog = page.getByTestId('task-composer')
  const face = dialog.getByTestId('task-body-compose-rich')
  await expect(face).toBeVisible()
  await expect(dialog.getByTestId('editor-expand')).toHaveCount(0)

  const box = page.locator('.ds-dialog')
  const before = await box.evaluate((el) => el.getBoundingClientRect().height)
  await face.click()
  await pasteText(face, Array.from({ length: 60 }, (_, i) => `line ${i}`).join('\n\n'))
  await expect(face.locator('p')).toHaveCount(60)

  const after = await box.evaluate((el) => ({
    height: el.getBoundingClientRect().height, scroll: el.scrollHeight, client: el.clientHeight,
  }))
  expect(after.height).toBeGreaterThan(before)
  expect(after.scroll).toBeGreaterThan(after.client)
  const own = await face.evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight }))
  expect(own.scroll).toBe(own.client)
})

test('test_new_defect_dialog_uses_rich_editor', async ({ page }) => {
  let sent = null
  await page.route(/\/api\/defects(\?.*)?$/, (route) => {
    if (route.request().method() === 'POST') {
      sent = route.request().postDataJSON()
      return route.fulfill({ status: 201, json: { ok: true, defect: { id: 9001 } } })
    }
    return route.fulfill({ json: { items: [], population: 0,
      pagination: { total: 0, page: 0, size: 50, has_next: false, has_prev: false } } })
  })
  await page.goto('/defects')
  await page.locator('#pipeline-actions [data-testid="defect-new"]').click()

  const dialog = page.getByTestId('new-defect')
  const face = dialog.getByTestId('new-defect-symptom')
  await expect(face).toHaveAttribute('contenteditable', 'true')
  await expect(dialog.getByTestId('editor-mode-rich')).toHaveAttribute('aria-pressed', 'true')
  await expect(dialog.getByTestId('editor-expand')).toHaveCount(0)
  await expect(dialog.getByTestId('editor-head')).toHaveCSS('position', 'static')
  // No image item: a defect's screenshots go to the dialog's strip, not the text.
  await face.click()
  await page.keyboard.type('/imag')
  await expect(page.getByTestId('slash-menu')).toHaveCount(0)
  await page.keyboard.press('ControlOrMeta+a')
  await page.keyboard.press('Backspace')

  await page.keyboard.type('## Steps')
  await page.keyboard.press('Enter')
  await page.keyboard.type('click **Save**')
  await expect(face.locator('h2')).toHaveText('Steps')
  await expect(face.locator('strong')).toHaveText('Save')

  await page.keyboard.press('ControlOrMeta+Enter')
  await expect.poll(() => sent).not.toBeNull()
  expect(sent.symptom).toBe('## Steps\n\nclick **Save**')
})

test('test_dialog_esc_closes_overlay_first', async ({ page }) => {
  await openComposer(page)
  const dialog = page.getByTestId('task-composer')
  const face = dialog.getByTestId('task-body-compose-rich')
  await face.click()
  await page.keyboard.type('/')
  await expect(page.getByTestId('slash-menu')).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(page.getByTestId('slash-menu')).toHaveCount(0)
  await expect(dialog).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
})

test('test_dialog_esc_closes_overlay_first — a block source box keeps its Esc', async ({ page }) => {
  await openComposer(page)
  const dialog = page.getByTestId('task-composer')
  const face = dialog.getByTestId('task-body-compose-rich')
  await face.click()
  await pasteText(face, 'intro\n\n<details><summary>x</summary>y</details>')
  const opaque = face.getByTestId('rich-opaque')
  await opaque.dblclick()
  const box = opaque.getByTestId('rich-opaque-source')
  await expect(box).toBeFocused()

  await page.keyboard.press('Escape')
  await expect(box).toHaveCount(0)
  await expect(dialog).toBeVisible()
})

test('test_legacy_dialog_specs_pass_in_source_mode', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('regin_editor_mode:compose', 'source'))
  await stubFeatures(page)
  await page.goto('/features')
  await page.getByTestId('declare-feature').click()
  await page.getByTestId('declare-body').fill('## Intent\nthin prose')
  await expect(page.getByTestId('declare-body')).toHaveValue('## Intent\nthin prose')
  await expect(page.getByTestId('declare-body-rich')).toHaveCount(0)
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('new-feature')).toHaveCount(0)

  await openComposer(page)
  await page.fill('#task-body', 'source body')
  await expect(page.locator('#task-body')).toHaveValue('source body')
})
