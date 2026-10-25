/**
 * F178 S3 — the bar over a text selection on the rich face, and the head's
 * Shortcuts list.
 *
 * Stubbed the way `f178-slash.spec.js` is: every assertion is about the editor
 * and what it PATCHes, so the ledger and `/api/refs` are fixtures.
 */
import { test, expect } from './auth-fixture.js'

const FEATURE = 1781

const TASK = {
  ref: 'task:42', kind: 'task', key: '42', label: 'title', title: 'title',
  status: 'idea', project_id: null, project: null, feature_id: null,
  feature_title: null, url: '/tasks/42',
}

function ledger(body) {
  return {
    id: FEATURE, title: 'Bubble bar', origin: 'native',
    external_ref: null, external_url: null, sync_error: null, body,
    stage: {
      stage: 'building', blurb: 'being built', rounds: 1,
      claimed_at: null, open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true, images: [],
    cost: { usd: 1, sessions: 1, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 1000, gaps: {} },
    tokens: { input: 1, output: 1 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 1, all_sessions: 2, declared_usd: 1, all_usd: 2 },
  }
}

async function stub(page, body) {
  const patches = []
  await page.route(new RegExp(`/api/features/${FEATURE}$`), (route) => {
    if (route.request().method() === 'PATCH') {
      const sent = route.request().postDataJSON()
      patches.push(sent.body)
      return route.fulfill({ json: ledger(sent.body) })
    }
    return route.fulfill({ json: ledger(body) })
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
const bubble = (page) => page.getByTestId('bubble-menu')

async function openRich(page, body) {
  const patches = await stub(page, body)
  await page.goto(`/features/${FEATURE}`)
  await page.getByTestId('feature-spec-edit').click()
  await expect(face(page)).toBeVisible()
  return patches
}

// Drags the mouse across the first occurrence of `text` in the face.
async function mouseSelect(page, text) {
  const box = await face(page).evaluate((el, needle) => {
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      const at = node.textContent.indexOf(needle)
      if (at < 0) continue
      node.parentElement.scrollIntoView({ block: 'center' })
      const range = document.createRange()
      range.setStart(node, at)
      range.setEnd(node, at + needle.length)
      const r = range.getBoundingClientRect()
      return { left: r.left, right: r.right, y: r.top + r.height / 2 }
    }
    return null
  }, text)
  expect(box).not.toBeNull()
  await page.mouse.move(box.left + 1, box.y)
  await page.mouse.down()
  await page.mouse.move(box.right - 1, box.y, { steps: 4 })
  await page.mouse.up()
}

const selected = (page) => face(page).evaluate((el) => {
  const { from, to } = el.editor.state.selection
  return el.editor.state.doc.textBetween(from, to)
})

test.use({ viewport: { width: 1280, height: 900 } })

test('test_bubble_menu_on_selection', async ({ page }) => {
  const patches = await openRich(page, 'one abc two\n')
  await mouseSelect(page, 'abc')
  expect(await selected(page)).toBe('abc')
  await expect(bubble(page)).toBeVisible()
  for (const label of ['Bold', 'Italic', 'Strike', 'Code', 'Link', 'Reference']) {
    await expect(bubble(page).getByRole('button', { name: label, exact: true })).toBeVisible()
  }
  // Clear of the selection and of the sticky head: a first line sits right
  // under the head, so there the bar flips below it.
  const place = await page.evaluate(() => {
    const bar = document.querySelector('[data-testid="bubble-menu"]').getBoundingClientRect()
    const sel = window.getSelection().getRangeAt(0).getBoundingClientRect()
    const head = document.querySelector('[data-testid="editor-head"]').getBoundingClientRect()
    return { clear: bar.bottom <= sel.top || bar.top >= sel.bottom, underHead: bar.top >= head.bottom }
  })
  expect(place).toEqual({ clear: true, underHead: true })

  await bubble(page).getByTestId('bubble-bold').click()
  await expect(face(page).locator('strong')).toHaveText('abc')
  await expect(bubble(page)).toBeVisible()
  await expect(bubble(page).getByTestId('bubble-bold')).toHaveAttribute('aria-pressed', 'true')
  expect(await selected(page)).toBe('abc')

  await page.keyboard.press('ControlOrMeta+z')
  await expect(face(page).locator('strong')).toHaveCount(0)
  await expect(face(page)).toContainText('one abc two')
  await page.keyboard.press('ControlOrMeta+Shift+z')
  await expect(face(page).locator('strong')).toHaveText('abc')

  await page.getByTestId('feature-spec-save').click()
  await expect.poll(() => patches.length).toBe(1)
  expect(patches[0]).toBe('one **abc** two\n')
})

test('test_bubble_menu_hidden_in_code_block', async ({ page }) => {
  await openRich(page, 'intro\n\n```\nconst abc = 1\n```\n')
  await mouseSelect(page, 'abc')
  expect(await selected(page)).toBe('abc')
  await expect(bubble(page)).toHaveCount(0)
  // The same gesture over prose does raise it: the bar is not just absent.
  await mouseSelect(page, 'intro')
  await expect(bubble(page)).toBeVisible()
})

test.describe('the bar keeps out of the way', () => {
  test('a collapsed caret shows no bar', async ({ page }) => {
    await openRich(page, 'one abc two\n')
    await mouseSelect(page, 'abc')
    await expect(bubble(page)).toBeVisible()
    const at = await face(page).locator('p').boundingBox()
    await page.mouse.click(at.x + at.width - 2, at.y + at.height / 2)
    await expect.poll(() => selected(page)).toBe('')
    await expect(bubble(page)).toHaveCount(0)
  })

  test('with room above, the bar sits over the selection', async ({ page }) => {
    const lead = Array.from({ length: 12 }, (_, i) => `line ${i}\n`).join('\n')
    await openRich(page, `${lead}\none abc two\n`)
    await mouseSelect(page, 'abc')
    await expect(bubble(page)).toBeVisible()
    const above = await page.evaluate(() => {
      const bar = document.querySelector('[data-testid="bubble-menu"]').getBoundingClientRect()
      const sel = window.getSelection().getRangeAt(0).getBoundingClientRect()
      return bar.bottom <= sel.top && Math.abs((bar.left + bar.right) / 2 - (sel.left + sel.right) / 2) < 2
    })
    expect(above).toBe(true)
  })

  test('a selection holding a reference shows no bar', async ({ page }) => {
    await openRich(page, 'see [title](task:42) now\n')
    await expect(face(page).locator('.rich-ref')).toHaveCount(1)
    await face(page).evaluate((el) => {
      el.editor.view.focus()
      el.editor.commands.setTextSelection({ from: 1, to: el.editor.state.doc.content.size - 1 })
    })
    await expect(bubble(page)).toHaveCount(0)
  })

  test('Reference hands over to the @ menu and the bar goes', async ({ page }) => {
    await openRich(page, 'one abc two\n')
    await mouseSelect(page, 'abc')
    await bubble(page).getByTestId('bubble-reference').click()
    await expect(page.getByTestId('ref-menu')).toBeVisible()
    await expect(bubble(page)).toHaveCount(0)
    await expect(face(page)).toContainText('one abc @ two')
  })

  test('Link opens the link popover and the bar goes', async ({ page }) => {
    const patches = await openRich(page, 'one abc two\n')
    await mouseSelect(page, 'abc')
    await bubble(page).getByTestId('bubble-link').click()
    await expect(page.getByTestId('editor-link-url')).toBeFocused()
    await expect(bubble(page)).toHaveCount(0)
    await page.getByTestId('editor-link-url').fill('https://example.com')
    await page.keyboard.press('Enter')
    await expect(face(page).locator('a')).toHaveText('abc')
    await page.getByTestId('feature-spec-save').click()
    await expect.poll(() => patches.length).toBe(1)
    expect(patches[0]).toBe('one [abc](https://example.com) two\n')
  })

  test('Esc in the focus layout drops the selection, not the layout', async ({ page }) => {
    await openRich(page, 'one abc two\n')
    await page.getByTestId('editor-expand').click()
    await expect(page.getByTestId('markdown-editor')).toHaveAttribute('data-layout', 'focus')
    await mouseSelect(page, 'abc')
    await expect(bubble(page)).toBeVisible()
    await page.keyboard.press('Escape')
    await expect(bubble(page)).toHaveCount(0)
    await expect(page.getByTestId('markdown-editor')).toHaveAttribute('data-layout', 'focus')
    await page.keyboard.press('Escape')
    await expect(page.getByTestId('markdown-editor')).toHaveAttribute('data-layout', 'inline')
  })
})

test('test_shortcuts_popover', async ({ page }) => {
  const patches = await openRich(page, 'one abc two\n')
  await face(page).evaluate((el) => {
    el.editor.view.focus()
    el.editor.commands.setTextSelection(4)
  })
  const mac = await page.evaluate(() => /mac|iphone|ipad|ipod/i.test(navigator.platform))
  const mod = mac ? '⌘' : 'Ctrl+'

  await page.getByTestId('editor-shortcuts').click()
  const list = page.getByTestId('editor-shortcuts-list')
  await expect(list).toBeVisible()
  const row = (action) => list.locator('tr', { has: page.locator('th', { hasText: action }) })
  await expect(row('Bold / Italic').locator('td')).toContainText(`${mod}B`)
  await expect(row('Save').locator('td')).toHaveText(`${mod}Enter`)
  await expect(row('Slash menu').locator('td')).toContainText('/')

  await page.keyboard.press('Escape')
  await expect(list).toHaveCount(0)
  const inFace = await face(page).evaluate((el) => el.contains(document.activeElement))
  expect(inFace).toBe(true)
  await expect(face(page)).toHaveText('one abc two')

  await page.keyboard.type('X')
  await expect(face(page)).toHaveText('oneX abc two')
  await page.getByTestId('feature-spec-save').click()
  await expect.poll(() => patches.length).toBe(1)
  expect(patches[0]).toBe('oneX abc two\n')
})
