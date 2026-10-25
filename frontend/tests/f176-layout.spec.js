/**
 * F176 S4 — long-form layout: both faces grow with their text and the page
 * scrolls, the editor's head (toolbar + Save/Cancel) sticks to the top of
 * `.content-scroll`, Expand opens a focus layout that leaves the page to the
 * editor, and an edited draft is not thrown away without asking.
 *
 * Stubbed like `f176-rich.spec.js`: the ledger is a fixture, and every
 * assertion is about geometry, the layout, or what was PATCHed.
 */
import { test, expect } from './auth-fixture.js'
import { contentOverflow } from './helpers/overflow.js'

const FEATURE = 1761
const TITLE = 'Long spec layout'

function ledger(body) {
  return {
    id: FEATURE, title: TITLE, origin: 'native',
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

// 300 lines: a heading every 20 lines, the rest one-line paragraphs, each
// naming its own line so a test can find "line 201".
const LONG = Array.from({ length: 300 }, (_, i) => (i % 20 === 0 ? `## Part ${i / 20}` : `line ${i}`))
  .join('\n\n')

async function stub(page, body, patches = []) {
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
  return patches
}

const editor = (page) => page.getByTestId('markdown-editor')
const head = (page) => page.getByTestId('editor-head')
const face = (page) => page.getByTestId('feature-spec-rich')
const input = (page) => page.getByTestId('feature-spec-input')
const scroller = (page) => page.locator('.content-scroll')

async function open(page) {
  await page.goto(`/features/${FEATURE}`)
  await page.getByTestId('feature-spec-edit').click()
  // The rich face is a lazy chunk: wait for whichever face this mode draws.
  await expect(face(page).or(input(page))).toBeVisible()
}

function noInnerScroll(locator) {
  return locator.evaluate((el) => ({ scroll: el.scrollHeight, client: el.clientHeight }))
}

// Brings the middle of the editor to the middle of the scroller.
async function scrollToMiddle(page) {
  await scroller(page).evaluate((sc) => {
    const ed = sc.querySelector('[data-testid="markdown-editor"]')
    const box = ed.getBoundingClientRect()
    sc.scrollTop += box.top + box.height / 2 - sc.getBoundingClientRect().top - sc.clientHeight / 2
  })
}

async function box(locator) {
  return locator.evaluate((el) => {
    const r = el.getBoundingClientRect()
    return { top: r.top, bottom: r.bottom, left: r.left, width: r.width }
  })
}

async function inScroller(page, locator) {
  const pane = await box(scroller(page))
  const pane2 = await scroller(page).evaluate((sc) => sc.getBoundingClientRect().top + sc.clientHeight)
  const r = await box(locator)
  return r.top >= pane.top && r.bottom <= pane2
}

async function expectStuck(page) {
  const pane = await box(scroller(page))
  const bar = await box(head(page))
  expect(Math.abs(bar.top - pane.top)).toBeLessThanOrEqual(1)
  for (const id of ['feature-spec-save', 'feature-spec-cancel', 'editor-expand']) {
    const r = await box(page.getByTestId(id))
    expect(r.top).toBeGreaterThanOrEqual(bar.top)
    expect(r.bottom).toBeLessThanOrEqual(bar.bottom)
  }
  // The head is drawn over what scrolls beneath it, the section index included.
  const hit = await head(page).evaluate((el) => {
    const r = el.getBoundingClientRect()
    return el.contains(document.elementFromPoint(r.left + r.width / 2, r.top + 2))
  })
  expect(hit).toBe(true)
}

test.use({ viewport: { width: 1280, height: 800 } })

test('test_inline_grows_and_toolbar_sticks', async ({ page }) => {
  await stub(page, LONG)
  await open(page)
  await expect(face(page)).toBeVisible()
  await scrollToMiddle(page)

  const { scroll, client } = await noInnerScroll(face(page))
  expect(scroll).toBe(client)
  const paneHeight = await scroller(page).evaluate((sc) => sc.clientHeight)
  expect(client).toBeGreaterThan(paneHeight * 3)
  expect(await scroller(page).evaluate((sc) => sc.scrollTop)).toBeGreaterThan(0)
  await expect(page.getByTestId('editor-tool-bold')).toBeVisible()
  await expectStuck(page)
  expect(await inScroller(page, page.getByTestId('editor-tool-bold'))).toBe(true)
})

test('test_inline_grows_and_toolbar_sticks — a phone pane does not scroll sideways', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await stub(page, LONG)
  await open(page)
  await scrollToMiddle(page)
  expect((await contentOverflow(page)).offenders).toEqual([])
  await expectStuck(page)
  await page.getByTestId('editor-expand').click()
  expect((await contentOverflow(page)).offenders).toEqual([])
})

test('test_source_textarea_autogrows', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('regin_editor_mode', 'source'))
  const patches = await stub(page, LONG)
  await open(page)
  await expect(input(page)).toHaveValue(LONG)

  const first = await noInnerScroll(input(page))
  expect(first.scroll).toBe(first.client)
  expect(first.client).toBeGreaterThan(3000)
  expect(await input(page).evaluate((el) => getComputedStyle(el).resize)).toBe('none')

  // Typing grows it, from the input event, with no inner scroll appearing.
  await input(page).click()
  await input(page).evaluate((el) => el.setSelectionRange(el.value.length, el.value.length))
  await input(page).pressSequentially('\n\nmore\n\nand more')
  const grown = await noInnerScroll(input(page))
  expect(grown.scroll).toBe(grown.client)
  expect(grown.client).toBeGreaterThan(first.client)

  await scrollToMiddle(page)
  await expectStuck(page)

  // A value set from outside the field (the mode switch re-mounts it from the
  // draft) is measured as well.
  await page.getByTestId('editor-mode-rich').click()
  await page.getByTestId('editor-mode-source').click()
  const again = await noInnerScroll(input(page))
  expect(again.scroll).toBe(again.client)

  await page.getByTestId('feature-spec-save').click()
  await expect.poll(() => patches.length).toBe(1)
  expect(patches[0]).toBe(`${LONG}\n\nmore\n\nand more`)
})

test('test_focus_mode', async ({ page }) => {
  const patches = await stub(page, LONG)
  await open(page)
  await expect(face(page)).toBeVisible()

  // The caret in the block of line 201 (line 200 is a heading), scrolled into the middle of the pane.
  const block = face(page).getByText('line 201', { exact: true })
  await block.click()
  await block.evaluate((el) => {
    const sc = el.closest('.content-scroll')
    sc.scrollTop += el.getBoundingClientRect().top - sc.getBoundingClientRect().top - sc.clientHeight / 2
  })

  await page.getByTestId('editor-expand').click()
  await expect(editor(page)).toHaveAttribute('data-layout', 'focus')
  await expect(page.getByTestId('feature-section-nav')).toBeHidden()
  await expect(page.getByTestId('detail-back')).toBeHidden()
  await expect(page.locator('[data-testid="feature-spec"] ~ *').filter({ visible: true })).toHaveCount(0)
  await expect(page.getByTestId('editor-focus-title')).toHaveText(TITLE)
  await expect(page.getByTestId('editor-collapse')).toBeVisible()
  const width = await editor(page).evaluate((el) => {
    const cs = getComputedStyle(el)
    return { width: el.getBoundingClientRect().width, max: cs.maxWidth, font: parseFloat(cs.fontSize) }
  })
  expect(width.max).not.toBe('none')
  expect(width.width).toBeLessThanOrEqual(parseFloat(width.max) + 0.5)
  // 84ch is under 84 em-widths of the editor's font: centred, not card-wide.
  expect(width.width).toBeLessThan(84 * width.font)
  const ed = await box(editor(page))
  const pane = await box(scroller(page))
  const paneWidth = await scroller(page).evaluate((sc) => sc.clientWidth)
  const leftGap = ed.left - pane.left
  const rightGap = pane.left + paneWidth - (ed.left + ed.width)
  expect(Math.abs(leftGap - rightGap)).toBeLessThanOrEqual(2)
  expect(await inScroller(page, block)).toBe(true)
  const { scroll, client } = await noInnerScroll(face(page))
  expect(scroll).toBe(client)

  await page.keyboard.press('Escape')
  await expect(editor(page)).toHaveAttribute('data-layout', 'inline')
  await expect(page.getByTestId('feature-section-nav')).toBeVisible()
  await expect(page.getByTestId('editor-focus-title')).toHaveCount(0)
  expect(await inScroller(page, block)).toBe(true)

  // ⌘⇧F opens it from the keyboard as well; Collapse closes it.
  await page.keyboard.press('ControlOrMeta+Shift+F')
  await expect(editor(page)).toHaveAttribute('data-layout', 'focus')
  await page.getByTestId('editor-collapse').click()
  await expect(editor(page)).toHaveAttribute('data-layout', 'inline')

  // Nothing above touched the draft: it saves as the bytes it opened with.
  await page.getByTestId('feature-spec-save').click()
  await expect.poll(() => patches.length).toBe(1)
  expect(patches[0]).toBe(LONG)
})

test('test_focus_mode — the draft typed in focus survives Esc', async ({ page }) => {
  const patches = await stub(page, 'alpha\n\nbeta')
  await open(page)
  await page.getByTestId('editor-expand').click()
  await face(page).getByText('beta', { exact: true }).click()
  await page.keyboard.press('End')
  await page.keyboard.type('!')
  await page.keyboard.press('Escape')
  await expect(editor(page)).toHaveAttribute('data-layout', 'inline')
  await page.getByTestId('feature-spec-save').click()
  await expect.poll(() => patches.length).toBe(1)
  expect(patches[0]).toBe('alpha\n\nbeta!')
})

test('test_focus_mode — the task page hides every other card too', async ({ page }) => {
  const task = {
    id: 1762, title: 'Task in focus', body: 'alpha\n\nbeta', status: 'idea',
    source: 'web', tags: [], links: [], params: {}, autorun: false, project_id: null,
    priority: 'none', priority_rank: 4, parent_id: null, assignee: null, assignee_name: null,
    rounds: 1, created_at: '2026-10-25', updated_at: '2026-10-25', external_id: null,
    created_by: null, images: [],
  }
  await page.route(/\/api\/tasks\/1762$/, (route) =>
    route.fulfill({ json: { task, grain: { children: 0, runs: 0 } } }))
  await page.route(/\/api\/tasks\/1762\/comments$/, (route) => route.fulfill({ json: { threads: [] } }))
  await page.route(/\/api\/tasks\/1762\/links$/, (route) => route.fulfill({ json: { links: [] } }))
  await page.goto('/tasks/1762')
  await page.getByTestId('task-body-edit').click()
  await expect(page.getByTestId('task-body-rich')).toBeVisible()

  await page.getByTestId('editor-expand').click()
  await expect(page.getByTestId('editor-focus-title')).toHaveText('Task in focus')
  const visible = await page.locator('.td-page').evaluate((root) => [...root.children]
    .filter((el) => el.getClientRects().length && !el.querySelector('.md-editor'))
    .map((el) => el.className))
  expect(visible).toEqual([])
  await expect(page.getByTestId('task-body-save')).toBeVisible()

  await page.getByTestId('task-body-cancel').click()
  await expect(page.locator('.td-page')).not.toHaveClass(/is-editor-focus/)
  await expect(page.locator('.td-header')).toBeVisible()
})

test.describe('test_cancel_confirms_when_dirty', () => {
  const discard = (page) => page.getByRole('button', { name: 'Discard changes?' })

  test('test_cancel_confirms_when_dirty — clean draft leaves at once', async ({ page }) => {
    const patches = await stub(page, 'alpha')
    await open(page)
    await page.getByTestId('feature-spec-cancel').click()
    await expect(editor(page)).toHaveCount(0)
    await expect(discard(page)).toHaveCount(0)
    expect(patches).toEqual([])
  })

  test('test_cancel_confirms_when_dirty — Cancel asks, keep editing or discard', async ({ page }) => {
    const patches = await stub(page, 'alpha')
    await open(page)
    await face(page).getByText('alpha').click()
    await page.keyboard.press('End')
    await page.keyboard.type(' beta')

    await page.getByTestId('feature-spec-cancel').click()
    await expect(discard(page)).toBeVisible()
    await page.getByRole('button', { name: 'Cancel', exact: true }).last().click()
    await expect(discard(page)).toHaveCount(0)
    await expect(face(page)).toHaveText('alpha beta')

    await page.getByTestId('feature-spec-cancel').click()
    await discard(page).click()
    await expect(editor(page)).toHaveCount(0)
    await expect(page.getByTestId('feature-spec-body')).toHaveText('alpha')
    expect(patches).toEqual([])
  })

  test('test_cancel_confirms_when_dirty — leaving the page asks too', async ({ page }) => {
    const patches = await stub(page, 'alpha')
    await open(page)
    await face(page).getByText('alpha').click()
    await page.keyboard.press('End')
    await page.keyboard.type(' beta')

    await page.getByTestId('detail-back').click()
    await expect(discard(page)).toBeVisible()
    await page.getByRole('button', { name: 'Cancel', exact: true }).last().click()
    await expect(page).toHaveURL(new RegExp(`/features/${FEATURE}$`))
    await expect(face(page)).toHaveText('alpha beta')

    await page.getByTestId('detail-back').click()
    await discard(page).click()
    await expect(page).not.toHaveURL(new RegExp(`/features/${FEATURE}$`))
    expect(patches).toEqual([])
  })
})
