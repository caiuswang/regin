/**
 * F176 S3 — references, pictures and opaque blocks on the rich face.
 *
 * Stubbed the way `f174-images-refs.spec.js` is, `/api/refs` included: every
 * assertion is about what the face draws and what Save PATCHes, so the ledger,
 * the upload and the search answer are fixtures.
 */
import { test, expect } from './auth-fixture.js'

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

const FEATURE = 1763

const TASK = {
  ref: 'task:42', kind: 'task', key: '42', label: 'title', title: 'title',
  status: 'idea', project_id: null, project: null, feature_id: null,
  feature_title: null, url: '/tasks/42',
}

function summary(idx) {
  return { id: idx, idx, media_type: 'image/png', byte_size: 68 }
}

function ledger(body, images) {
  return {
    id: FEATURE, title: 'Refs and images', origin: 'native',
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
    coverage: { declared_sessions: 1, all_sessions: 2, declared_usd: 1, all_usd: 2 },
  }
}

async function stub(page, body, { upload, images = [summary(1)] } = {}) {
  const patches = []
  const uploads = []
  await page.route(new RegExp(`/api/features/${FEATURE}/images/\\d+$`), (route) => route.fulfill({
    contentType: 'image/png', body: Buffer.from(PNG_B64, 'base64'),
  }))
  await page.route(new RegExp(`/api/features/${FEATURE}/images$`), (route) => {
    uploads.push(route.request().method())
    return upload ? upload(route) : route.fulfill({ status: 201, json: { image: summary(3) } })
  })
  await page.route(new RegExp(`/api/features/${FEATURE}$`), (route) => {
    if (route.request().method() === 'PATCH') {
      const sent = route.request().postDataJSON()
      patches.push(sent.body)
      return route.fulfill({ json: ledger(sent.body, images) })
    }
    return route.fulfill({ json: ledger(body, images) })
  })
  await page.route(new RegExp(`/api/features/${FEATURE}/(tasks|defects)`), (route) =>
    route.fulfill({ json: { items: [] } }))
  await page.route((url) => url.pathname === '/api/refs', (route) => {
    const q = new URL(route.request().url()).searchParams.get('q') || ''
    return route.fulfill({
      json: { query: q, kind: null, context: [], groups: [{ kind: 'task', total: 1, items: [TASK] }] },
    })
  })
  await page.route((url) => url.pathname === '/api/refs/resolve', (route) =>
    route.fulfill({ json: { refs: { [TASK.ref]: TASK } } }))
  return { patches, uploads }
}

const face = (page) => page.getByTestId('feature-spec-rich')
const menu = (page) => page.getByTestId('ref-menu')
const chip = (page, target) => face(page).locator(`[data-testid="ref-chip"][data-ref="${target}"]`)
const opaque = (page) => face(page).getByTestId('rich-opaque')

async function openRich(page) {
  await page.goto(`/features/${FEATURE}`)
  await page.getByTestId('feature-spec-edit').click()
  await expect(face(page)).toBeVisible()
}

async function save(page, patches) {
  await page.getByTestId('feature-spec-save').click()
  await expect.poll(() => patches.length).toBe(1)
  return patches[0]
}

// Selections through the editor view (Tiptap sets `dom.editor`), with none of
// a pointer's guesswork about where a click lands in a line.
async function caretAt(locator, pos) {
  await locator.evaluate((el, at) => {
    el.editor.view.focus()
    el.editor.commands.setTextSelection(at)
  }, pos)
}

async function caretAtEnd(locator) {
  await locator.evaluate((el) => {
    el.editor.view.focus()
    el.editor.commands.setTextSelection(el.editor.state.doc.content.size - 1)
  })
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

async function settled(page, query) {
  await expect(menu(page)).toHaveAttribute('data-query', query)
  await expect(menu(page)).not.toHaveClass(/is-stale/)
}

test.use({ viewport: { width: 1280, height: 900 } })

test.describe('test_mention_in_rich', () => {
  test('test_mention_in_rich — @ picks a task into a chip', async ({ page }) => {
    const { patches } = await stub(page, 'see')
    await openRich(page)
    await caretAtEnd(face(page))

    await page.keyboard.type(' @ti')
    await settled(page, 'ti')
    await page.keyboard.press('Enter')

    await expect(menu(page)).toHaveCount(0)
    await expect(chip(page, 'task:42')).toBeVisible()
    await expect(chip(page, 'task:42')).toContainText('title')
    // What follows the accept lands after the chip, in the same paragraph.
    await page.keyboard.type('ok')
    await expect(face(page).locator('p')).toHaveCount(1)

    expect(await save(page, patches)).toBe('see [title](task:42) ok')
  })

  test('test_mention_in_rich — a click on a row accepts it; Escape closes', async ({ page }) => {
    const { patches } = await stub(page, 'x')
    await openRich(page)
    await caretAtEnd(face(page))
    await page.keyboard.type(' @t')
    await settled(page, 't')
    await page.keyboard.press('Escape')
    await expect(menu(page)).toHaveCount(0)

    await page.keyboard.type('i')
    await settled(page, 'ti')
    await menu(page).locator('[data-testid="ref-menu-row"][data-ref="task:42"]').click()
    await expect(chip(page, 'task:42')).toBeVisible()
    expect(await save(page, patches)).toContain('x [title](task:42)')
  })

  test('test_mention_in_rich — no menu inside code or inside a word', async ({ page }) => {
    await stub(page, '```\ncode\n```\n\nmail')
    await openRich(page)
    await caretAt(face(page), 5)
    await page.keyboard.type(' @ti')
    await expect(menu(page)).toHaveCount(0)

    await caretAtEnd(face(page))
    await page.keyboard.type('@ti')
    await expect(menu(page)).toHaveCount(0)
    // The same keys after a space do open it: the two above were refused.
    await page.keyboard.type(' @ti')
    await settled(page, 'ti')
  })

  test('test_mention_in_rich — a chip does not navigate while editing', async ({ page }) => {
    await stub(page, 'see [title](task:42) here')
    await openRich(page)
    const before = page.url()
    await expect(chip(page, 'task:42')).toHaveAttribute('href', '/tasks/42')
    await chip(page, 'task:42').click()
    await expect(page).toHaveURL(before)
    await expect(face(page)).toBeVisible()
    await expect(chip(page, 'task:42')).toBeVisible()
  })

  test('test_mention_in_rich — a pasted app URL becomes a reference', async ({ page }) => {
    const { patches } = await stub(page, 'see')
    await openRich(page)
    await caretAtEnd(face(page))
    await page.keyboard.type(' ')
    const origin = new URL(page.url()).origin
    await pasteText(face(page), `${origin}/tasks/42`)

    await expect(chip(page, 'task:42')).toBeVisible()
    expect(await save(page, patches)).toBe('see [title](task:42) ')
  })
})

test.describe('test_paste_image_in_rich', () => {
  test('test_paste_image_in_rich — the attachment lands at the caret', async ({ page }) => {
    const { patches } = await stub(page, 'HEADTAIL')
    await openRich(page)
    await caretAt(face(page), 5)

    await pasteImage(face(page))

    const img = face(page).locator('img[data-idx="3"]')
    await expect(img).toHaveCount(1)
    const src = await img.getAttribute('src')
    expect(src).toMatch(/^blob:/)
    // The same object URL the Images card shows for the attachment.
    await expect(page.getByTestId('feature-images').locator('img[alt="pasted.png"]'))
      .toHaveAttribute('src', src)
    await expect(page.getByTestId('feature-spec-error')).toHaveCount(0)

    expect(await save(page, patches)).toBe('HEAD![](image:3)TAIL')
  })

  test('test_paste_image_in_rich — a drop lands where it was dropped', async ({ page }) => {
    const { patches } = await stub(page, 'abc\n\nlast')
    await openRich(page)
    await face(page).evaluate((el, b64) => {
      const bin = atob(b64)
      const bytes = new Uint8Array(bin.length)
      for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i)
      const dt = new DataTransfer()
      dt.items.add(new File([bytes], 'dropped.png', { type: 'image/png' }))
      const at = el.editor.view.coordsAtPos(3)
      el.dispatchEvent(new DragEvent('drop', {
        dataTransfer: dt, bubbles: true, cancelable: true, clientX: at.left, clientY: (at.top + at.bottom) / 2,
      }))
    }, PNG_B64)

    await expect(face(page).locator('img[data-idx="3"]')).toHaveCount(1)
    expect(await save(page, patches)).toBe('ab![](image:3)c\n\nlast')
  })

  test('test_paste_image_in_rich — an image no longer attached says so', async ({ page }) => {
    await stub(page, 'gone ![](image:9) here')
    await openRich(page)
    await expect(face(page).locator('.rich-image-missing')).toHaveText('(image:9 — no longer attached)')
  })
})

test('test_failed_upload_in_rich_leaves_doc_untouched', async ({ page }) => {
  const { patches, uploads } = await stub(page, 'abc\n\n* kept  as is', {
    upload: (route) => route.fulfill({ status: 500, json: { error: 'disk full' } }),
  })
  await openRich(page)
  await caretAt(face(page), 2)
  const html = await face(page).innerHTML()

  const posted = page.waitForResponse((res) =>
    res.url().endsWith(`/api/features/${FEATURE}/images`) && res.request().method() === 'POST')
  await pasteImage(face(page))
  await posted

  await expect(page.getByTestId('feature-spec-error')).toHaveText('disk full')
  expect(uploads).toEqual(['POST'])
  await expect(face(page).locator('img')).toHaveCount(0)
  expect(await face(page).innerHTML()).toBe(html)
  expect(await save(page, patches)).toBe('abc\n\n* kept  as is')
})

test.describe('test_opaque_block_roundtrip_and_edit', () => {
  const body = 'intro\n\n<details><summary>x</summary>y</details>\n\n| a |b|\n|-|-|\n| 1 | 2 |'

  test('test_opaque_block_roundtrip_and_edit — double-click, edit, leave, save', async ({ page }) => {
    const { patches } = await stub(page, body)
    await openRich(page)

    await expect(opaque(page)).toHaveCount(1)
    await expect(opaque(page)).toHaveAttribute('contenteditable', 'false')
    await expect(opaque(page).locator('details summary')).toHaveText('x')
    await expect(opaque(page).getByTestId('rich-opaque-source')).toHaveCount(0)

    await opaque(page).dblclick()
    const box = opaque(page).getByTestId('rich-opaque-source')
    await expect(box).toBeFocused()
    await expect(box).toHaveValue('<details><summary>x</summary>y</details>')
    await box.fill('<details><summary>z</summary>y</details>')
    await box.blur()

    await expect(box).toHaveCount(0)
    await expect(opaque(page).locator('details summary')).toHaveText('z')
    expect(await save(page, patches))
      .toBe('intro\n\n<details><summary>z</summary>y</details>\n\n| a |b|\n|-|-|\n| 1 | 2 |')
  })

  test('test_opaque_block_roundtrip_and_edit — opened and left unchanged is byte-exact', async ({ page }) => {
    const { patches } = await stub(page, body)
    await openRich(page)
    await opaque(page).dblclick()
    await opaque(page).getByTestId('rich-opaque-source').blur()
    await expect(opaque(page).getByTestId('rich-opaque-source')).toHaveCount(0)
    expect(await save(page, patches)).toBe(body)
  })

  test('test_opaque_block_roundtrip_and_edit — an image pasted into the box joins its source', async ({ page }) => {
    const { patches } = await stub(page, body)
    await openRich(page)
    await opaque(page).dblclick()
    const box = opaque(page).getByTestId('rich-opaque-source')
    await box.evaluate((el) => el.setSelectionRange(el.value.length, el.value.length))

    await pasteImage(box)

    await expect(box).toHaveValue('<details><summary>x</summary>y</details>\n![](image:3)\n')
    await box.blur()
    await expect(opaque(page)).toHaveCount(1)
    expect(await save(page, patches))
      .toBe('intro\n\n<details><summary>x</summary>y</details>\n![](image:3)\n\n| a |b|\n|-|-|\n| 1 | 2 |')
  })

  test('test_opaque_block_roundtrip_and_edit — a pasted image does not replace a selected card or chip', async ({ page }) => {
    const { patches } = await stub(page, 'see [title](task:42)\n\n<!-- note -->\n\nend')
    await openRich(page)
    await chip(page, 'task:42').click()
    await pasteImage(face(page))
    await expect(face(page).locator('img[data-idx="3"]')).toHaveCount(1)
    await expect(chip(page, 'task:42')).toBeVisible()

    await opaque(page).click()
    await pasteImage(face(page))
    await expect(face(page).locator('img[data-idx="3"]')).toHaveCount(2)
    await expect(opaque(page)).toHaveCount(1)
    expect(await save(page, patches))
      .toBe('see [title](task:42)![](image:3)\n\n<!-- note -->\n\n![](image:3)\n\nend')
  })

  test('test_opaque_block_roundtrip_and_edit — Escape abandons the edit', async ({ page }) => {
    const { patches } = await stub(page, body)
    await openRich(page)
    await opaque(page).dblclick()
    const box = opaque(page).getByTestId('rich-opaque-source')
    await box.fill('<p>other</p>')
    await box.press('Escape')
    await expect(box).toHaveCount(0)
    await expect(face(page)).toBeVisible()
    expect(await save(page, patches)).toBe(body)
  })
})
