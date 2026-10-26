/**
 * F174 S2 — the markdown toolbar over the feature spec's source field.
 *
 * Every mark is one `replaceRange` through the browser's editing stack, so a
 * single undo takes the source and its selection back to before the press.
 */
import { test, expect } from './auth-fixture.js'

// The rich face is the default editor now; these specs drive the source one.
test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem('regin_editor_mode', 'source')
    localStorage.setItem('regin_editor_mode:compose', 'source')
  })
})

function ledger(body) {
  return {
    id: 243, title: 'Toolbar', origin: 'native',
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

async function stub(page) {
  await page.route(/\/api\/features\/243$/, (route) => route.fulfill({ json: ledger('') }))
  await page.route(/\/api\/features\/243\/(tasks|defects)/, (route) =>
    route.fulfill({ json: { items: [] } }))
}

const input = (page) => page.getByTestId('feature-spec-input')
const tool = (page, kind) => page.getByTestId(`editor-tool-${kind}`)
const selection = (page) => input(page).evaluate((el) => [el.selectionStart, el.selectionEnd])

async function openWith(page, text) {
  await page.goto('/features/243')
  await page.getByTestId('feature-spec-edit').click()
  await input(page).fill(text)
}

async function select(page, start, end) {
  await input(page).evaluate((el, [s, e]) => {
    el.focus()
    el.setSelectionRange(s, e)
  }, [start, end])
}

test.use({ viewport: { width: 1280, height: 900 } })

test('test_toolbar_bold_is_one_undo_step', async ({ page }) => {
  await stub(page)
  await openWith(page, '重点')
  await expect(page.getByTestId('markdown-editor')).toHaveAttribute('data-mode', 'source')
  await expect(tool(page, 'bold')).toHaveAttribute('aria-label', 'Bold')
  await expect(tool(page, 'bold')).toHaveAttribute('title', 'Bold')
  await select(page, 0, 2)

  await tool(page, 'bold').click()
  await expect(input(page)).toHaveValue('**重点**')
  await expect(input(page)).toBeFocused()

  await page.keyboard.press('ControlOrMeta+z')
  await expect(input(page)).toHaveValue('重点')
  expect(await selection(page)).toEqual([0, 2])
})

test('bold without a selection leaves the caret between the markers', async ({ page }) => {
  await stub(page)
  await openWith(page, 'ab')
  await select(page, 1, 1)
  await tool(page, 'bold').click()
  await expect(input(page)).toHaveValue('a****b')
  expect(await selection(page)).toEqual([3, 3])
})

test('test_toolbar_line_marks_apply_per_line', async ({ page }) => {
  await stub(page)
  await openWith(page, 'a\nb\nc')
  await select(page, 0, 3)

  await tool(page, 'bullet_list').click()
  await expect(input(page)).toHaveValue('- a\n- b\nc')

  await select(page, 8, 8)
  await tool(page, 'checkbox').click()
  await expect(input(page)).toHaveValue('- a\n- b\n- [ ] c')

  await page.keyboard.press('ControlOrMeta+z')
  await expect(input(page)).toHaveValue('- a\n- b\nc')
  expect(await selection(page)).toEqual([8, 8])
})

test('undoing a line mark puts back the selection it was made from', async ({ page }) => {
  await stub(page)
  await openWith(page, 'ab\ncd')
  await select(page, 1, 4)
  await tool(page, 'numbered_list').click()
  await expect(input(page)).toHaveValue('1. ab\n2. cd')
  await page.keyboard.press('ControlOrMeta+z')
  await expect(input(page)).toHaveValue('ab\ncd')
  expect(await selection(page)).toEqual([1, 4])

  await select(page, 1, 1)
  await tool(page, 'heading').click()
  await expect(input(page)).toHaveValue('## ab\ncd')
  await page.keyboard.type('x')
  await page.keyboard.press('ControlOrMeta+z')
  await expect(input(page)).toHaveValue('## ab\ncd')
  await page.keyboard.press('ControlOrMeta+z')
  await expect(input(page)).toHaveValue('ab\ncd')
  expect(await selection(page)).toEqual([1, 1])
})

test('a mark further back in history still undoes to its own selection', async ({ page }) => {
  await stub(page)
  await openWith(page, 'ab')
  await select(page, 1, 1)
  await tool(page, 'heading').click()
  await tool(page, 'bullet_list').click()
  await expect(input(page)).toHaveValue('- ## ab')
  await page.keyboard.press('ControlOrMeta+z')
  await page.keyboard.press('ControlOrMeta+z')
  await expect(input(page)).toHaveValue('ab')
  expect(await selection(page)).toEqual([1, 1])

  await page.keyboard.press('ControlOrMeta+Shift+z')
  await page.keyboard.press('ControlOrMeta+z')
  await expect(input(page)).toHaveValue('ab')
  expect(await selection(page)).toEqual([1, 1])
})

test('numbered list counts the selected lines; headings deepen to ####', async ({ page }) => {
  await stub(page)
  await openWith(page, 'x\ny')
  await select(page, 0, 3)
  await tool(page, 'numbered_list').click()
  await expect(input(page)).toHaveValue('1. x\n2. y')

  await input(page).fill('t')
  await select(page, 1, 1)
  for (const want of ['## t', '### t', '#### t', '#### t']) {
    await tool(page, 'heading').click()
    await expect(input(page)).toHaveValue(want)
  }
})

test('code block fences the selection on lines of its own', async ({ page }) => {
  await stub(page)
  await openWith(page, 'run go now')
  await select(page, 4, 6)
  await tool(page, 'code_block').click()
  await expect(input(page)).toHaveValue('run \n```\ngo\n```\n now')
  expect(await selection(page)).toEqual([9, 11])

  await input(page).fill('')
  await select(page, 0, 0)
  await tool(page, 'code_block').click()
  await expect(input(page)).toHaveValue('```\n\n```')
  expect(await selection(page)).toEqual([4, 4])
})

test('test_toolbar_link_prompts_for_url', async ({ page }) => {
  await stub(page)
  let prompted = false
  page.on('dialog', (dialog) => {
    prompted = true
    dialog.dismiss()
  })
  await openWith(page, '看文档')
  await select(page, 1, 3)

  await tool(page, 'link').click()
  const url = page.getByTestId('editor-link-url')
  await expect(url).toBeFocused()
  await url.fill('https://example.com')
  await url.press('Enter')

  await expect(url).toBeHidden()
  await expect(input(page)).toHaveValue('看[文档](https://example.com)')
  await expect(input(page)).toBeFocused()
  expect(prompted).toBe(false)

  await page.keyboard.press('ControlOrMeta+z')
  await expect(input(page)).toHaveValue('看文档')
})

test('the link field takes a relative path', async ({ page }) => {
  await stub(page)
  await openWith(page, '文档')
  await select(page, 0, 2)
  await tool(page, 'link').click()
  await page.getByTestId('editor-link-url').fill('docs/a.md')
  await page.getByTestId('editor-link-url').press('Enter')
  await expect(input(page)).toHaveValue('[文档](docs/a.md)')
})

test('Escape in the link popover leaves the source untouched', async ({ page }) => {
  await stub(page)
  await openWith(page, '文档')
  await select(page, 0, 2)
  await tool(page, 'link').click()
  const url = page.getByTestId('editor-link-url')
  await url.fill('https://example.com')
  await url.press('Escape')

  await expect(url).toBeHidden()
  await expect(page.getByTestId('feature-spec-input')).toBeVisible()
  await expect(input(page)).toHaveValue('文档')
  expect(await selection(page)).toEqual([0, 2])
})

test('a link with no selection leaves the caret inside the brackets', async ({ page }) => {
  await stub(page)
  await openWith(page, '')
  await tool(page, 'link').click()
  await page.getByTestId('editor-link-url').fill('https://x.dev')
  await page.getByTestId('editor-link-url').press('Enter')
  await expect(input(page)).toHaveValue('[](https://x.dev)')
  expect(await selection(page)).toEqual([1, 1])
})

test('the toolbar serves both faces; the table group only the rich one', async ({ page }) => {
  await stub(page)
  await openWith(page, 'x')
  await expect(page.getByTestId('editor-toolbar')).toBeVisible()
  await expect(tool(page, 'table')).toHaveCount(0)
  await page.getByTestId('editor-mode-rich').click()
  await expect(page.getByTestId('editor-toolbar')).toBeVisible()
  await expect(tool(page, 'table')).toBeVisible()
  await page.getByTestId('editor-mode-source').click()
  await expect(input(page)).toHaveValue('x')
})
