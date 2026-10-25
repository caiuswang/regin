/**
 * F178 S1 — the `/` menu on the rich face: where it opens, what each item
 * writes, and that it and the `@` menu are never open together.
 *
 * Stubbed the way `f176-refs-images.spec.js` is: every assertion is about the
 * editor and what it PATCHes, so the ledger, the uploads and `/api/refs` are
 * fixtures.
 */
import { test, expect } from './auth-fixture.js'

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

const FEATURE = 1780

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
    id: FEATURE, title: 'Slash menu', origin: 'native',
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

async function stub(page, body, { upload } = {}) {
  const patches = []
  let images = []
  await page.route(new RegExp(`/api/features/${FEATURE}/images/\\d+$`), (route) => route.fulfill({
    contentType: 'image/png', body: Buffer.from(PNG_B64, 'base64'),
  }))
  await page.route(new RegExp(`/api/features/${FEATURE}/images$`), (route) => {
    if (upload) return upload(route)
    images = [summary(3)]
    return route.fulfill({ status: 201, json: { image: summary(3) } })
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
  return patches
}

const face = (page) => page.getByTestId('feature-spec-rich')
const slash = (page) => page.getByTestId('slash-menu')
const items = (page) => slash(page).getByTestId('slash-menu-item')
const refMenu = (page) => page.getByTestId('ref-menu')

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

// Through the editor view Tiptap hangs on its DOM node: a caret placed there
// is exactly where the next keystroke lands.
async function caretAt(page, pos) {
  await face(page).evaluate((el, at) => {
    el.editor.view.focus()
    el.editor.commands.setTextSelection(at)
  }, pos)
}

async function caretAtEnd(page) {
  await face(page).evaluate((el) => {
    el.editor.view.focus()
    el.editor.commands.setTextSelection(el.editor.state.doc.content.size - 1)
  })
}

// Opens the rich face on `body` with the caret in a fresh empty paragraph
// after it.
async function onEmptyLine(page, body) {
  const patches = await stub(page, body)
  await openRich(page)
  await caretAtEnd(page)
  await page.keyboard.press('Enter')
  return patches
}

test.use({ viewport: { width: 1280, height: 900 } })

const INTRO = 'intro  *kept*  verbatim'

test('test_slash_opens_on_empty_paragraph', async ({ page }) => {
  const patches = await onEmptyLine(page, INTRO)
  await page.keyboard.type('/')

  await expect(slash(page)).toBeVisible()
  for (const group of ['Text', 'Lists', 'Blocks', 'Insert']) {
    await expect(slash(page).getByTestId(`slash-menu-group-${group}`)).toBeVisible()
  }

  await page.keyboard.type('tab')
  await expect(items(page)).toHaveCount(1)
  await expect(items(page)).toHaveAttribute('data-id', 'table')

  await page.keyboard.press('Enter')
  await expect(slash(page)).toHaveCount(0)
  const table = face(page).locator('table')
  await expect(table.locator('tr')).toHaveCount(2)
  await expect(table.locator('tr').first().locator('th')).toHaveCount(3)
  await expect(face(page)).not.toContainText('/tab')

  await page.keyboard.type('x')
  await expect(table.locator('th').first()).toHaveText('x')

  const body = await save(page, patches)
  expect(body.startsWith(`${INTRO}\n\n| x`)).toBe(true)
  expect(body).toMatch(/^\| x +\| +\| +\|\n\| -+ \| -+ \| -+ \|\n\| +\| +\| +\|$/m)
  expect(body).not.toContain('/tab')
})

test('test_slash_inside_word_does_not_open', async ({ page }) => {
  await stub(page, 'lib')
  await openRich(page)
  await caretAtEnd(page)
  await page.keyboard.type('/orm')

  await expect(slash(page)).toHaveCount(0)
  await expect(face(page).locator('p').first()).toHaveText('lib/orm')
})

test('test_slash_silent_in_code_block', async ({ page }) => {
  await stub(page, '```\ncode\n```')
  await openRich(page)
  await caretAt(page, 5)
  await page.keyboard.type(' /')

  await expect(slash(page)).toHaveCount(0)
  await expect(face(page).locator('pre')).toHaveText('code /')
})

test.describe('test_slash_trigger_table', () => {
  test('after whitespace mid-paragraph it opens', async ({ page }) => {
    await stub(page, 'see')
    await openRich(page)
    await caretAtEnd(page)
    await page.keyboard.type(' /')
    await expect(slash(page)).toBeVisible()
  })

  test('inside inline code it stays shut', async ({ page }) => {
    await stub(page, 'a `x y` b')
    await openRich(page)
    await caretAt(page, 5)
    await page.keyboard.type(' /')
    await expect(slash(page)).toHaveCount(0)
  })

  test('in a table cell it opens without the table item', async ({ page }) => {
    await stub(page, '| a | b |\n|---|---|\n| 1 | 2 |')
    await openRich(page)
    await face(page).locator('td', { hasText: '1' }).click()
    await page.keyboard.press('End')
    await page.keyboard.type(' /')
    await expect(slash(page)).toBeVisible()
    await expect(items(page)).not.toHaveCount(0)
    await expect(slash(page).locator('[data-id="table"]')).toHaveCount(0)
  })

  test('while the @ menu is open it stays shut', async ({ page }) => {
    await onEmptyLine(page, 'intro')
    await page.keyboard.type('@')
    await expect(refMenu(page)).toBeVisible()
    await page.keyboard.type('/')
    await expect(slash(page)).toHaveCount(0)
  })

  test('while it is open the @ menu stays shut', async ({ page }) => {
    await onEmptyLine(page, 'intro')
    await page.keyboard.type('/')
    await expect(slash(page)).toBeVisible()
    await page.keyboard.type('@')
    await expect(refMenu(page)).toHaveCount(0)
  })

  test('Escape closes it, and a second unmatched character does too', async ({ page }) => {
    await onEmptyLine(page, 'intro')
    await page.keyboard.type('/')
    await expect(slash(page)).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(slash(page)).toHaveCount(0)

    await page.keyboard.press('Enter')
    await page.keyboard.type('/z')
    await expect(slash(page).getByTestId('slash-menu-empty')).toBeVisible()
    await page.keyboard.type('q')
    await expect(slash(page)).toHaveCount(0)
  })
})

const WRITES = [
  { query: 'h1', typed: 'T', saved: '# T' },
  { query: 'h4', typed: 'T', saved: '#### T' },
  { query: 'bullet', typed: 'a', saved: '- a' },
  { query: 'numbered', typed: 'a', saved: '1. a' },
  { query: 'todo', typed: 'a', saved: '- [ ] a' },
  { query: 'quote', typed: 'a', saved: '> a' },
  { query: 'divider', typed: 'a', saved: '---\n\na' },
  { query: 'code', typed: 'a', saved: '```\na\n```' },
  { query: 'mermaid', typed: 'graph TD', saved: '```mermaid\ngraph TD\n```' },
]

test.describe('test_slash_items_write_their_block', () => {
  for (const w of WRITES) {
    test(`/${w.query}`, async ({ page }) => {
      const patches = await onEmptyLine(page, INTRO)
      await page.keyboard.type(`/${w.query}`)
      await expect(items(page).first()).toBeVisible()
      await page.keyboard.press('Enter')
      await expect(slash(page)).toHaveCount(0)
      await page.keyboard.type(w.typed)
      expect(await save(page, patches)).toBe(`${INTRO}\n\n${w.saved}`)
    })
  }
})

test('test_slash_escape_in_focus_layout_closes_menu_only', async ({ page }) => {
  await onEmptyLine(page, 'intro')
  await page.getByTestId('editor-expand').click()
  await expect(page.getByTestId('markdown-editor')).toHaveAttribute('data-layout', 'focus')
  await caretAtEnd(page)
  await page.keyboard.type('/')
  await expect(slash(page)).toBeVisible()

  await page.keyboard.press('Escape')
  await expect(slash(page)).toHaveCount(0)
  await expect(page.getByTestId('markdown-editor')).toHaveAttribute('data-layout', 'focus')
})

test('test_slash_item_is_one_undo_step', async ({ page }) => {
  await onEmptyLine(page, 'intro')
  await page.keyboard.type('/quote')
  await page.keyboard.press('Enter')
  await expect(face(page).locator('blockquote')).toHaveCount(1)

  await page.keyboard.press('ControlOrMeta+z')
  await expect(face(page).locator('blockquote')).toHaveCount(0)
  await expect(face(page).locator('p').nth(1)).toHaveText('/quote')
})

test('test_slash_mouse_pick', async ({ page }) => {
  await onEmptyLine(page, 'intro')
  await page.keyboard.type('/')
  await slash(page).locator('[data-id="h2"]').click()
  await expect(slash(page)).toHaveCount(0)
  await expect(face(page).locator('h2')).toHaveCount(1)
})

test('test_slash_reference_opens_ref_menu', async ({ page }) => {
  const patches = await onEmptyLine(page, 'intro')
  await page.keyboard.type('/reference')
  await page.keyboard.press('Enter')

  await expect(slash(page)).toHaveCount(0)
  await expect(refMenu(page)).toBeVisible()
  await refMenu(page).locator('[data-testid="ref-menu-row"][data-ref="task:42"]').click()
  await expect(face(page).locator('[data-ref="task:42"]')).toHaveCount(1)
  expect(await save(page, patches)).toBe('intro\n\n[title](task:42) ')
})

test('test_slash_image_goes_through_host_attach', async ({ page }) => {
  const patches = await onEmptyLine(page, 'intro')
  await page.keyboard.type('/image')
  const chooser = page.waitForEvent('filechooser')
  await page.keyboard.press('Enter')
  await (await chooser).setFiles({ name: 'shot.png', mimeType: 'image/png', buffer: Buffer.from(PNG_B64, 'base64') })

  await expect(face(page).locator('img[data-idx="3"]')).toHaveCount(1)
  expect(await save(page, patches)).toBe('intro\n\n![](image:3)')
})

test('test_slash_image_failure_leaves_doc', async ({ page }) => {
  await stub(page, 'intro', {
    upload: (route) => route.fulfill({ status: 500, json: { error: 'disk full' } }),
  })
  await openRich(page)
  await caretAtEnd(page)
  await page.keyboard.press('Enter')
  await page.keyboard.type('/image')
  const chooser = page.waitForEvent('filechooser')
  await page.keyboard.press('Enter')
  await (await chooser).setFiles({ name: 'shot.png', mimeType: 'image/png', buffer: Buffer.from(PNG_B64, 'base64') })

  await expect(page.getByTestId('feature-spec-error')).toBeVisible()
  await expect(face(page).locator('img')).toHaveCount(0)
})
