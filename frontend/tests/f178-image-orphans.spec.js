/**
 * D334 — a picture pasted into the spec editor is uploaded at once, so an edit
 * that never cites it in saved text must take it back off the feature.
 */
import { test, expect } from './auth-fixture.js'

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('regin_editor_mode', 'source')
    localStorage.setItem('regin_editor_mode:compose', 'source')
  })
})

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

const summary = (idx) => ({ id: idx, idx, media_type: 'image/png', byte_size: 68 })

function ledger(body, images) {
  return {
    id: 242, title: 'Orphans', origin: 'native',
    external_ref: null, external_url: null, sync_error: null, body,
    stage: {
      stage: 'building', blurb: 'being built', rounds: 1,
      claimed_at: null, open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true, images: images.map(summary),
    cost: { usd: 1, sessions: 1, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 1000, gaps: {} },
    tokens: { input: 1, output: 1 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 1, all_sessions: 2, declared_usd: 1, all_usd: 2 },
  }
}

// Images 1 (cited by the opened body) and 2 (attached on the Images card,
// never cited) exist before the edit; a paste mints 3.
async function stub(page, body) {
  const deleted = []
  await page.route(/\/api\/features\/242\/images\/\d+$/, (route) => {
    if (route.request().method() === 'DELETE') {
      deleted.push(Number(route.request().url().split('/').pop()))
      return route.fulfill({ json: { ok: true } })
    }
    return route.fulfill({ contentType: 'image/png', body: Buffer.from(PNG_B64, 'base64') })
  })
  await page.route(/\/api\/features\/242\/images$/, (route) =>
    route.fulfill({ status: 201, json: { image: summary(3) } }))
  await page.route(/\/api\/features\/242$/, (route) => {
    if (route.request().method() === 'PATCH') {
      const saved = route.request().postDataJSON().body
      return route.fulfill({ json: ledger(saved, [1, 2, 3]) })
    }
    return route.fulfill({ json: ledger(body, [1, 2]) })
  })
  await page.route(/\/api\/features\/242\/(tasks|defects)/, (route) =>
    route.fulfill({ json: { items: [] } }))
  return deleted
}

async function pasteImage(locator) {
  await locator.evaluate((el, b64) => {
    const bin = atob(b64)
    const bytes = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i)
    const dt = new DataTransfer()
    dt.items.add(new File([bytes], 'pasted.png', { type: 'image/png' }))
    el.dispatchEvent(new ClipboardEvent('paste', {
      clipboardData: dt, bubbles: true, cancelable: true,
    }))
  }, PNG_B64)
}

const input = (page) => page.getByTestId('feature-spec-input')
const thumbs = (page) => page.getByTestId('feature-images').locator('img')
const BODY = 'A\n![](image:1)\nB'

async function openAndPaste(page) {
  await page.goto('/features/242')
  await expect(thumbs(page)).toHaveCount(2)
  await page.getByTestId('feature-spec-edit').click()
  await expect(input(page)).toHaveValue(BODY)
  await input(page).evaluate((el) => { el.focus(); el.setSelectionRange(1, 1) })
  await pasteImage(input(page))
  await expect(input(page)).toHaveValue('A\n![](image:3)\n\n![](image:1)\nB')
  await expect(thumbs(page)).toHaveCount(3)
}

test.use({ viewport: { width: 1280, height: 900 } })

test('test_cancel_detaches_what_the_edit_pasted', async ({ page }) => {
  const deleted = await stub(page, BODY)
  await openAndPaste(page)

  await page.getByTestId('feature-spec-cancel').click()
  await page.getByRole('button', { name: 'Discard changes?' }).click()

  await expect(thumbs(page)).toHaveCount(2)
  expect(deleted).toEqual([3])
})

test('test_save_detaches_pasted_and_cited_images_the_text_dropped', async ({ page }) => {
  const deleted = await stub(page, BODY)
  await openAndPaste(page)

  await input(page).fill('A only')
  await page.getByTestId('feature-spec-save').click()

  await expect(page.getByTestId('feature-spec-body')).toContainText('A only')
  await expect(thumbs(page)).toHaveCount(1)
  expect(deleted.sort()).toEqual([1, 3])
})

test('test_save_keeps_a_pasted_image_the_text_still_cites', async ({ page }) => {
  const deleted = await stub(page, BODY)
  await openAndPaste(page)

  await page.getByTestId('feature-spec-save').click()

  await expect(page.getByTestId('feature-spec-body')).toBeVisible()
  await expect(thumbs(page)).toHaveCount(3)
  expect(deleted).toEqual([])
})

test('test_leaving_the_page_mid_edit_detaches_what_was_pasted', async ({ page }) => {
  const deleted = await stub(page, BODY)
  await openAndPaste(page)

  await page.locator('a[href="/tasks"]').first().click()
  await page.getByRole('button', { name: 'Discard changes?' }).click()
  await expect.poll(() => deleted).toEqual([3])
})
