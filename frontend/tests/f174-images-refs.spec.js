/**
 * F174 S3 — images and `@` references through the source editor.
 *
 * Stubbed the way `feature-images.spec.js` is, `/api/refs` included: every
 * assertion is about what the editor writes and what the rich face draws, so
 * the ledger and the search answer are fixtures rather than seeded data.
 */
import { test, expect } from './auth-fixture.js'

// The rich face is the default editor now; these specs drive the source one.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('regin_editor_mode', 'source')
    localStorage.setItem('regin_editor_mode:compose', 'source')
  })
})

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

const TASK = {
  ref: 'task:42', kind: 'task', key: '42', label: 'title', title: 'title',
  status: 'idea', project_id: null, project: null, feature_id: null,
  feature_title: null, url: '/tasks/42',
}

function summary(idx) {
  return { id: idx, idx, media_type: 'image/png', byte_size: 68 }
}

function ledger(body) {
  return {
    id: 242, title: 'Images and refs', origin: 'native',
    external_ref: null, external_url: null, sync_error: null, body,
    stage: {
      stage: 'building', blurb: 'being built', rounds: 1,
      claimed_at: null, open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true, images: [summary(1)],
    cost: { usd: 1, sessions: 1, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 1000, gaps: {} },
    tokens: { input: 1, output: 1 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 1, all_sessions: 2, declared_usd: 1, all_usd: 2 },
  }
}

async function stub(page, body, { upload } = {}) {
  await page.route(/\/api\/features\/242\/images\/\d+$/, (route) => route.fulfill({
    contentType: 'image/png', body: Buffer.from(PNG_B64, 'base64'),
  }))
  await page.route(/\/api\/features\/242\/images$/, (route) =>
    upload ? upload(route) : route.fulfill({ status: 201, json: { image: summary(3) } }))
  await page.route(/\/api\/features\/242$/, (route) => route.fulfill({ json: ledger(body) }))
  await page.route(/\/api\/features\/242\/(tasks|defects)/, (route) =>
    route.fulfill({ json: { items: [] } }))
  await page.route((url) => url.pathname === '/api/refs', (route) => {
    const q = new URL(route.request().url()).searchParams.get('q') || ''
    return route.fulfill({
      json: { query: q, kind: null, context: [],
              groups: [{ kind: 'task', total: 1, items: [TASK] }] },
    })
  })
  await page.route((url) => url.pathname === '/api/refs/resolve', (route) =>
    route.fulfill({ json: { refs: { [TASK.ref]: TASK } } }))
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
const face = (page) => page.getByTestId('feature-spec-rich')
const menu = (page) => page.getByTestId('ref-menu')

async function settled(page, query) {
  await expect(menu(page)).toHaveAttribute('data-query', query)
  await expect(menu(page)).not.toHaveClass(/is-stale/)
}

async function openSource(page, body) {
  await page.goto('/features/242')
  await page.getByTestId('feature-spec-edit').click()
  await expect(page.getByTestId('markdown-editor')).toHaveAttribute('data-mode', 'source')
  await expect(input(page)).toHaveValue(body)
}

test.use({ viewport: { width: 1280, height: 900 } })

test('test_pasted_image_writes_reference_and_previews', async ({ page }) => {
  await stub(page, 'HEADTAIL')
  await openSource(page, 'HEADTAIL')
  await input(page).evaluate((el) => { el.focus(); el.setSelectionRange(4, 4) })

  await pasteImage(input(page))

  await expect(input(page)).toHaveValue('HEAD\n![](image:3)\nTAIL')
  // The rendered picture is the attachment just taken, not a re-fetch of
  // something else: the same object URL the Images card shows for idx 3.
  await page.getByTestId('editor-mode-rich').click()
  const img = face(page).locator('img[data-idx="3"]')
  await expect(img).toHaveCount(1)
  const src = await img.getAttribute('src')
  expect(src).toMatch(/^blob:/)
  await expect(page.getByTestId('feature-images').locator('img')).toHaveCount(2)
  await expect(page.getByTestId('feature-images').locator('img[alt="pasted.png"]'))
    .toHaveAttribute('src', src)
  await expect(page.getByTestId('feature-spec-error')).toHaveCount(0)
})

test('test_failed_upload_leaves_source_untouched', async ({ page }) => {
  await stub(page, 'abc', {
    upload: (route) => route.fulfill({ status: 500, json: { error: 'disk full' } }),
  })
  await openSource(page, 'abc')
  await input(page).evaluate((el) => { el.focus(); el.setSelectionRange(1, 1) })

  const posted = page.waitForResponse((res) =>
    res.url().endsWith('/api/features/242/images') && res.request().method() === 'POST')
  await pasteImage(input(page))
  await posted

  await expect(page.getByTestId('feature-spec-error')).toHaveText('disk full')
  await expect(input(page)).toHaveValue('abc')
})

async function pasteImages(locator, names) {
  await locator.evaluate((el, [b64, files]) => {
    const bin = atob(b64)
    const bytes = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i)
    const dt = new DataTransfer()
    for (const name of files) dt.items.add(new File([bytes], name, { type: 'image/png' }))
    el.dispatchEvent(new ClipboardEvent('paste', {
      clipboardData: dt, bubbles: true, cancelable: true,
    }))
  }, [PNG_B64, names])
}

test('test_partial_upload_failure_shows_error_on_spec_card', async ({ page }) => {
  let posts = 0
  await stub(page, 'abc', {
    upload: (route) => {
      posts += 1
      return posts === 1
        ? route.fulfill({ status: 201, json: { image: summary(3) } })
        : route.fulfill({ status: 500, json: { error: 'disk full' } })
    },
  })
  await openSource(page, 'abc')
  await input(page).evaluate((el) => { el.focus(); el.setSelectionRange(1, 1) })

  await pasteImages(input(page), ['one.png', 'two.png'])

  await expect(page.getByTestId('feature-spec-error')).toHaveText('disk full')
  await expect(input(page)).toHaveValue('a\n![](image:3)\nbc')
  expect(posts).toBe(2)
})

// Wide enough that the menu, at its full list + preview width, is not pushed
// back from the right edge: a clamped menu sits in the same place whatever
// the caret does, and would prove nothing about the anchor.
test.describe('mentions', () => {
  test.use({ viewport: { width: 1920, height: 1000 } })

  test('test_mention_contract_preserved', async ({ page }) => {
    // A caret well into the line: a menu pinned to the textarea's edge, rather
    // than to the caret, lands somewhere else.
    const line = `${'w'.repeat(30)} see `
    await stub(page, line)
    await openSource(page, line)
    const field = input(page)
    await field.evaluate((el) => { el.focus(); el.setSelectionRange(el.value.length, el.value.length) })
    await field.pressSequentially('@ti')
    await settled(page, 'ti')
    // Pinned to the caret, well into the line — not to the textarea's edge.
    const [at, area] = await Promise.all([menu(page).boundingBox(), field.boundingBox()])
    expect(at.x).toBeGreaterThan(area.x + 150)

    await field.press('Enter')
    await expect(field).toHaveValue(`${line}[title](task:42) `)
    await expect(menu(page)).toHaveCount(0)

    await page.getByTestId('editor-mode-rich').click()
    await expect(face(page).locator('[data-testid="ref-chip"][data-ref="task:42"]')).toContainText('title')
  })
})
