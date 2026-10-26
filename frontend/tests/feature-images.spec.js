/**
 * The images a feature carries, on the ledger page.
 *
 * Stubbed rather than seeded, for the reason `feature-ledger.spec.js` states:
 * every assertion here is about the strip surviving a render, and binding it
 * to live data would make a rendering regression look like an empty database.
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

function ledger(images, body = 'thin prose') {
  return {
    id: 242, title: 'A feature with a mockup', origin: 'native',
    external_ref: null, external_url: null, sync_error: null, body,
    stage: {
      stage: 'building', blurb: 'being built', rounds: 1,
      claimed_at: null, open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true, images,
    cost: { usd: 1, sessions: 1, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 1000, gaps: {} },
    tokens: { input: 1, output: 1 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: {
      declared_sessions: 1, all_sessions: 2,
      declared_usd: 1, all_usd: 2,
    },
  }
}

function summary(idx) {
  return { id: idx, idx, media_type: 'image/png', byte_size: 68 }
}

async function stub(page, images, { body, onPatch } = {}) {
  await page.route(/\/api\/features\/242\/images\/\d+$/, (route) => {
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ json: { removed: true } })
    }
    return route.fulfill({
      contentType: 'image/png', body: Buffer.from(PNG_B64, 'base64'),
    })
  })
  // Never reused, like the real `idx` discipline — a two-image paste has to
  // be able to tell its two references apart.
  let nextIdx = 3
  await page.route(/\/api\/features\/242\/images$/, (route) => {
    const image = summary(nextIdx)
    nextIdx += 1
    return route.fulfill({ status: 201, json: { image } })
  })
  await page.route(/\/api\/features\/242$/, (route) => {
    if (route.request().method() === 'PATCH') {
      const sent = JSON.parse(route.request().postData() || '{}')
      onPatch?.(sent)
      return route.fulfill({ json: ledger(images, sent.body) })
    }
    return route.fulfill({ json: ledger(images, body) })
  })
  await page.route(/\/api\/features\/242\/(tasks|defects)/, (route) =>
    route.fulfill({ json: { items: [] } }))
}

// The strip's file input is `.sr-only` — `position: absolute` with no insets.
// Sitting deep in `.content-scroll`, it escaped the shell's `overflow: hidden`
// until `.content` was given `position: relative`, grew the document past
// 100vh, and left the window scrolling onto a blank slab below the layout.
test('the image strip does not make the window scroll past the shell',
  async ({ page }) => {
    await stub(page, [summary(1), summary(2)], {
      body: Array.from({ length: 120 }, (_, i) => `spec line ${i}`).join('\n\n'),
    })
    await page.goto('/features/242')
    await expect(page.getByTestId('feature-images').locator('.task-image-thumb'))
      .toHaveCount(2)

    const m = await page.evaluate(() => {
      const d = document.documentElement
      const pane = document.querySelector('.content-scroll')
      return {
        over: d.scrollHeight - d.clientHeight,
        paneScrolls: !!pane && pane.scrollHeight > pane.clientHeight,
      }
    })
    expect(m.paneScrolls, 'the page must be long enough to be a real probe').toBe(true)
    expect(m.over, `the window scrolls ${m.over}px past the shell`).toBeLessThanOrEqual(1)
  })

test('the feature renders every image it carries', async ({ page }) => {
  await stub(page, [summary(1), summary(2)])
  await page.goto('/features/242')

  const card = page.getByTestId('feature-images')
  await expect(card).toContainText('delivered with every run cut from this feature')
  await expect(card.locator('.task-image-thumb')).toHaveCount(2)
})

test('a feature with no images says what the absence costs', async ({ page }) => {
  await stub(page, [])
  await page.goto('/features/242')

  await expect(page.getByTestId('feature-images')
    .locator('.task-image-thumb')).toHaveCount(0)
  await expect(page.getByTestId('feature-images'))
    .toContainText('leaves the agent nothing to match')
})

test('attaching one posts it to the feature and shows it at once',
  async ({ page }) => {
    await stub(page, [summary(1)])
    await page.goto('/features/242')

    const card = page.getByTestId('feature-images')
    const posted = page.waitForRequest((req) =>
      req.url().endsWith('/api/features/242/images') && req.method() === 'POST')

    await card.getByTestId('task-image-file').setInputFiles({
      name: 'mockup.png', mimeType: 'image/png',
      buffer: Buffer.from(PNG_B64, 'base64'),
    })

    await posted
    await expect(card.locator('.task-image-thumb')).toHaveCount(2)
  })

test('removing one is immediate, and hits the feature route', async ({ page }) => {
  await stub(page, [summary(1), summary(2)])
  await page.goto('/features/242')

  const card = page.getByTestId('feature-images')
  await expect(card.locator('.task-image-thumb')).toHaveCount(2)
  const deleted = page.waitForRequest((req) =>
    /\/api\/features\/242\/images\/1$/.test(req.url())
      && req.method() === 'DELETE')

  await card.locator('.task-image-x').first().click()

  await deleted
  await expect(card.locator('.task-image-thumb')).toHaveCount(1)
})

/**
 * A real paste of image bytes. `setInputFiles` exercises the attach BUTTON;
 * the defect this covers is the gesture the operator actually uses, and only
 * a ClipboardEvent carrying a DataTransfer reaches the handler that was
 * missing.
 */
async function pasteImage(locator, count = 1) {
  await locator.evaluate((el, { b64, count: n }) => {
    const bin = atob(b64)
    const bytes = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i)
    const dt = new DataTransfer()
    for (let i = 0; i < n; i += 1) {
      dt.items.add(new File([bytes], `pasted-${i}.png`, { type: 'image/png' }))
    }
    el.dispatchEvent(new ClipboardEvent('paste', {
      clipboardData: dt, bubbles: true, cancelable: true,
    }))
  }, { b64: PNG_B64, count })
}

test('pasting into the open spec attaches the image AND writes its reference',
  async ({ page }) => {
    await stub(page, [summary(1)], { body: 'thin prose' })
    await page.goto('/features/242')

    await page.getByTestId('feature-spec-edit').click()
    const area = page.getByTestId('feature-spec-input')
    await expect(area).toHaveValue('thin prose')

    const posted = page.waitForRequest((req) =>
      req.url().endsWith('/api/features/242/images') && req.method() === 'POST')
    await pasteImage(area)
    await posted

    // The reference, not just the upload: an attachment the prose cannot name
    // is a picture beside the spec rather than a picture in it.
    await expect(area).toHaveValue('thin prose\n![](image:3)\n')
    // One list, two cards: the Images card sees it without a reload.
    await expect(page.getByTestId('feature-images')
      .locator('.task-image-thumb')).toHaveCount(2)
  })

test('the pasted reference lands at the caret, not at the end', async ({ page }) => {
  await stub(page, [summary(1)], { body: 'HEADTAIL' })
  await page.goto('/features/242')

  await page.getByTestId('feature-spec-edit').click()
  const area = page.getByTestId('feature-spec-input')
  await area.evaluate((el) => { el.focus(); el.setSelectionRange(4, 4) })

  await pasteImage(area)

  await expect(area).toHaveValue('HEAD\n![](image:3)\nTAIL')
})

test('the reference survives the save, verbatim', async ({ page }) => {
  let patched = null
  await stub(page, [summary(1)], { body: 'thin prose', onPatch: (b) => { patched = b } })
  await page.goto('/features/242')

  await page.getByTestId('feature-spec-edit').click()
  const area = page.getByTestId('feature-spec-input')
  await pasteImage(area)
  await expect(area).toHaveValue(/image:3/)

  await page.getByTestId('feature-spec-save').click()

  await expect.poll(() => patched?.body).toBe('thin prose\n![](image:3)\n')
})

test('`![](image:N)` in a saved spec renders the attachment inline',
  async ({ page }) => {
    await stub(page, [summary(1)], { body: 'Before.\n\n![a mockup](image:1)\n\nAfter.' })
    await page.goto('/features/242')

    const img = page.locator('.fs-body img')
    await expect(img).toHaveCount(1)
    // An object URL, not the authenticated bytes route: an <img> tag cannot
    // attach the Authorization header.
    await expect(img).toHaveAttribute('src', /^blob:/)
    await expect(page.locator('.fs-body')).not.toContainText('image:1)')
  })

test('a spec reference to a detached image reads as text, not a broken image',
  async ({ page }) => {
    await stub(page, [], { body: '![gone](image:7)' })
    await page.goto('/features/242')

    await expect(page.locator('.fs-body img')).toHaveCount(0)
    await expect(page.locator('.fs-body'))
      .toContainText('image:7 — no longer attached')
  })

test('the spec editor names the syntax, and each thumbnail names its index',
  async ({ page }) => {
    await stub(page, [summary(1)])
    await page.goto('/features/242')

    // Unguessable otherwise, and the hint points at the thumbnails — so the
    // index has to actually be on one.
    await expect(page.getByTestId('feature-images')).toContainText('image:1')

    await page.getByTestId('feature-spec-edit').click()
    await expect(page.getByTestId('feature-spec')).toContainText('![](image:0)')
    await expect(page.getByTestId('feature-spec')).toContainText('appends no event')
  })

test('a viewer still SEES the mockups, but has nothing to attach or edit with',
  async ({ page }) => {
    // The split guards write-accidents, not read-secrets: the picture the spec
    // is checked against has to stay readable.
    await page.addInitScript(() => {
      const raw = localStorage.getItem('regin_auth_user')
      if (raw) {
        const user = JSON.parse(raw)
        user.role = 'viewer'
        localStorage.setItem('regin_auth_user', JSON.stringify(user))
      }
    })
    await stub(page, [summary(1)])
    await page.goto('/features/242')

    await expect(page.getByTestId('feature-images').locator('.fi-thumb'))
      .toHaveCount(1)
    await expect(page.getByTestId('feature-images')
      .getByTestId('task-image-attach')).toHaveCount(0)
    await expect(page.getByTestId('feature-spec-edit')).toHaveCount(0)
  })

test('two images pasted at once keep their order', async ({ page }) => {
  // One insert per image read the caret off a textarea Vue had not flushed
  // yet, so the second reference landed at the first one's caret and the
  // pair came out reversed.
  await stub(page, [summary(1)], { body: 'HEADTAIL' })
  await page.goto('/features/242')

  await page.getByTestId('feature-spec-edit').click()
  const area = page.getByTestId('feature-spec-input')
  await area.evaluate((el) => { el.focus(); el.setSelectionRange(4, 4) })

  await pasteImage(area, 2)

  await expect(area).toHaveValue('HEAD\n![](image:3)\n\n![](image:4)\nTAIL')
})
