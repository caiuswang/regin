/**
 * F176 S2 — the rich face: a body edited on its own rendering, saved as the
 * source bytes it came from wherever the writer did not touch it.
 *
 * Stubbed the way `f174-split-editor.spec.js` is: every assertion is about the
 * editor and what it PATCHes, so the ledger is a fixture.
 */
import { test, expect } from './auth-fixture.js'

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

const FEATURE = 1760

function ledger(body) {
  return {
    id: FEATURE, title: 'Rich editor', origin: 'native',
    external_ref: null, external_url: null, sync_error: null, body,
    stage: {
      stage: 'building', blurb: 'being built', rounds: 1,
      claimed_at: null, open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true,
    images: [{ id: 1, idx: 3, media_type: 'image/png', byte_size: 68 }],
    cost: { usd: 1, sessions: 1, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 1000, gaps: {} },
    tokens: { input: 1, output: 1 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: { declared_sessions: 1, all_sessions: 2, declared_usd: 1, all_usd: 2 },
  }
}

async function stub(page, body, patches = []) {
  await page.route(new RegExp(`/api/features/${FEATURE}/images/\\d+$`), (route) => route.fulfill({
    contentType: 'image/png', body: Buffer.from(PNG_B64, 'base64'),
  }))
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
const face = (page) => page.getByTestId('feature-spec-rich')
const input = (page) => page.getByTestId('feature-spec-input')
const tool = (page, kind) => page.getByTestId(`editor-tool-${kind}`)

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

async function pasteText(locator, text) {
  await locator.evaluate((el, value) => {
    const dt = new DataTransfer()
    dt.setData('text/plain', value)
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }, text)
}

// The editor view is reachable from its DOM node (Tiptap sets `dom.editor`);
// a selection made through it is the one a press acts on, with none of a
// pointer's guesswork about where a click lands in a line.
// `view.focus()` rather than the `focus` command, which defers to a frame.
async function selectText(locator, from, to = from) {
  await locator.evaluate((el, [a, b]) => {
    el.editor.view.focus()
    el.editor.commands.setTextSelection({ from: a, to: b })
  }, [from, to])
}

// The end of the last textblock: every body these tests end on is a paragraph.
async function caretAtEnd(locator) {
  await locator.evaluate((el) => {
    el.editor.view.focus()
    el.editor.commands.setTextSelection(el.editor.state.doc.content.size - 1)
  })
}

test.use({ viewport: { width: 1280, height: 900 } })

test.describe('test_rich_is_default', () => {
  for (const stored of [null, 'split', 'preview']) {
    test(`test_rich_is_default — stored ${stored ?? 'nothing'}`, async ({ page }) => {
      if (stored) await page.addInitScript((v) => localStorage.setItem('regin_editor_mode', v), stored)
      await stub(page, 'thin prose')
      await openRich(page)

      await expect(face(page)).toHaveAttribute('contenteditable', 'true')
      await expect(face(page)).toHaveText('thin prose')
      await expect(input(page)).toHaveCount(0)
      await expect(editor(page)).toHaveAttribute('data-mode', 'rich')
      await expect(editor(page).locator('[data-testid^="editor-mode-"]')).toHaveCount(2)
      await expect(page.getByTestId('editor-mode-rich')).toHaveAttribute('aria-pressed', 'true')
      await expect(page.getByTestId('editor-mode-source')).toHaveAttribute('aria-pressed', 'false')
    })
  }

  test('test_rich_is_default — a narrow viewport no longer forces source', async ({ page }) => {
    await page.setViewportSize({ width: 600, height: 900 })
    await stub(page, 'thin prose')
    await openRich(page)
    await expect(editor(page)).toHaveAttribute('data-mode', 'rich')

    await page.getByTestId('editor-mode-source').click()
    await expect(input(page)).toBeVisible()
    expect(await page.evaluate(() => localStorage.getItem('regin_editor_mode'))).toBe('source')
  })
})

test('test_open_then_save_is_byte_exact', async ({ page }) => {
  const body = [
    '## Intent  ',
    '',
    'See [t](task:42) and *this*  —  verbatim.',
    '',
    '![](image:3)',
    '',
    '| a | bb |',
    '|---|-|',
    '|ccc|   d |',
    '',
    '```mermaid',
    'graph TD',
    '  A --> B',
    '```',
    '',
    '<!-- kept -->',
    '* star list',
    '* second',
    '',
    '',
  ].join('\n')
  const patches = await stub(page, body)
  await openRich(page)
  await expect(face(page).locator('h2')).toHaveText('Intent')
  await expect(face(page).locator('table')).toHaveCount(1)
  await expect(face(page).locator('img[data-idx="3"]')).toHaveAttribute('src', /^blob:/)
  await face(page).locator('table td').first().click()

  expect(await save(page, patches)).toBe(body)
})

test('test_table_edit_serializes_table', async ({ page }) => {
  const patches = await stub(page, 'intro\n\n| a | b |\n|---|-|\n| 1 | 2 |\n\noutro')
  await openRich(page)
  await expect(tool(page, 'row_after')).toHaveCount(0)

  await face(page).locator('td', { hasText: '1' }).click()
  await tool(page, 'row_after').click()
  await page.keyboard.type('z')
  await expect(face(page).locator('tr')).toHaveCount(3)

  expect(await save(page, patches))
    .toBe('intro\n\n| a   | b   |\n| --- | --- |\n| 1   | 2   |\n| z   |     |\n\noutro')
})

test('test_markdown_input_rules', async ({ page }) => {
  const patches = await stub(page, '')
  await openRich(page)
  await face(page).click()

  await page.keyboard.type('## 目标')
  await page.keyboard.press('Enter')
  await page.keyboard.type('- 项')
  await page.keyboard.press('Enter')

  await expect(face(page).locator('h2')).toHaveText('目标')
  await expect(face(page).locator('ul > li').first()).toHaveText('项')
  expect(await save(page, patches)).toContain('## 目标\n\n- 项')
})

test('test_mode_switch_is_lossless', async ({ page }) => {
  const body = '# T\n\n| x |y|\n|--|:-:|\n| 1 | 2 |\n\n```mermaid\nA-->B\n```\n\n* a\n+ b\n'
  const patches = await stub(page, body)
  await openRich(page)

  await page.getByTestId('editor-mode-source').click()
  await expect(input(page)).toHaveValue(body)
  await page.getByTestId('editor-mode-rich').click()
  await expect(face(page).locator('table')).toHaveCount(1)

  expect(await save(page, patches)).toBe(body)
})

test('test_paste_markdown_text_parses', async ({ page }) => {
  await stub(page, '')
  await openRich(page)
  await face(page).click()

  await pasteText(face(page), '# T\n\n- a\n- b')

  await expect(face(page).locator('h1')).toHaveText('T')
  await expect(face(page).locator('ul > li')).toHaveText(['a', 'b'])
})

test('a pasted line of plain text joins the paragraph at the caret', async ({ page }) => {
  const patches = await stub(page, 'head')
  await openRich(page)
  await caretAtEnd(face(page))

  await pasteText(face(page), ' and **tail**')

  await expect(face(page).locator('p')).toHaveCount(1)
  await expect(face(page).locator('p strong')).toHaveText('tail')
  expect(await save(page, patches)).toBe('head and **tail**')
})

test('a toolbar mark on the rich face is one undo step', async ({ page }) => {
  const patches = await stub(page, 'word')
  await openRich(page)
  await selectText(face(page), 1, 5)

  await tool(page, 'bold').click()
  await expect(face(page).locator('strong')).toHaveText('word')
  await page.keyboard.press('ControlOrMeta+z')
  await expect(face(page).locator('strong')).toHaveCount(0)
  await tool(page, 'heading').click()
  await expect(face(page).locator('h2')).toHaveText('word')

  expect(await save(page, patches)).toBe('## word')
})

test('a toolbar press is its own undo step, even with typing right behind it', async ({ page }) => {
  const patches = await stub(page, '| a | b |\n|---|-|\n| 1 | 2 |')
  await openRich(page)
  await face(page).locator('td', { hasText: '1' }).click()

  await tool(page, 'row_after').click()
  await page.keyboard.type('z')
  await expect(face(page).locator('tr')).toHaveCount(3)
  await page.keyboard.press('ControlOrMeta+z')
  await expect(face(page).locator('tr')).toHaveCount(3)
  await expect(face(page)).not.toContainText('z')
  await page.keyboard.press('ControlOrMeta+z')
  await expect(face(page).locator('tr')).toHaveCount(2)
  await save(page, patches)
})

test('Mod-k on the rich face asks for a link, not the search palette', async ({ page }) => {
  const patches = await stub(page, 'word')
  await openRich(page)
  await selectText(face(page), 1, 5)

  await page.keyboard.press('ControlOrMeta+k')
  await expect(page.getByTestId('editor-link-url')).toBeFocused()
  await expect(page.locator('.search-backdrop')).toHaveCount(0)
  await page.getByTestId('editor-link-url').fill('https://x.dev')
  await page.getByTestId('editor-link-url').press('Enter')
  await expect(face(page).locator('a[href="https://x.dev"]')).toHaveText('word')

  expect(await save(page, patches)).toBe('[word](https://x.dev)')
})

test('Mod-Enter on the rich face saves', async ({ page }) => {
  const patches = await stub(page, 'x')
  await openRich(page)
  await caretAtEnd(face(page))
  await page.keyboard.type('y')
  await page.keyboard.press('ControlOrMeta+Enter')
  await expect.poll(() => patches).toEqual(['xy'])
})

test('the rich face opens and edits without a Tiptap or Vue warning', async ({ page }) => {
  const noise = []
  page.on('console', (msg) => {
    if (/tiptap|prosemirror|\[Vue warn\]/i.test(msg.text())) noise.push(msg.text())
  })
  page.on('pageerror', (err) => noise.push(err.message))
  const patches = await stub(page, '# T\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n- [ ] task\n')
  await openRich(page)
  await face(page).locator('td', { hasText: '1' }).click()
  await page.keyboard.type('0')
  await page.getByTestId('editor-mode-source').click()
  await page.getByTestId('editor-mode-rich').click()

  expect(await save(page, patches)).toContain('| 10  | 2   |')
  expect(noise).toEqual([])
})

test('test_legacy_specs_pass_in_source_mode', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('regin_editor_mode', 'source'))
  const patches = await stub(page, 'old')
  await page.goto(`/features/${FEATURE}`)
  await page.getByTestId('feature-spec-edit').click()

  await expect(editor(page)).toHaveAttribute('data-mode', 'source')
  await expect(face(page)).toHaveCount(0)
  await expect(input(page)).toHaveValue('old')
  await input(page).fill('## new  \n\n*kept*')
  await expect(tool(page, 'table')).toHaveCount(0)

  expect(await save(page, patches)).toBe('## new  \n\n*kept*')
})

test('test_rich_editor_is_lazy_chunk', async ({ page }) => {
  const requested = []
  page.on('request', (req) => requested.push(req.url()))
  const lazy = (url) => /@tiptap|prosemirror|RichEditor/.test(url)
  await stub(page, 'thin prose')
  await page.goto(`/features/${FEATURE}`)
  await expect(page.getByTestId('feature-spec-body')).toHaveText('thin prose')
  expect(requested.filter(lazy)).toEqual([])

  await page.getByTestId('feature-spec-edit').click()
  await expect(face(page)).toBeVisible()
  expect(requested.some((url) => /RichEditor/.test(url))).toBe(true)
  expect(requested.some((url) => /@tiptap/.test(url))).toBe(true)
})

const TASK_BODY = '## Goal\n\nShip **it**.'

async function stubTask(page, patches) {
  await page.route(/\/api\/tasks\/1761$/, (route) => {
    if (route.request().method() === 'PATCH') {
      patches.push(route.request().postDataJSON())
      return route.fulfill({ json: { ok: true } })
    }
    return route.fulfill({
      json: {
        task: {
          id: 1761, title: 'rich task', body: TASK_BODY, status: 'idea',
          source: 'web', tags: [], links: [], params: {},
          autorun: false, project_id: null, priority: 'none', priority_rank: 4,
          parent_id: null, assignee: null, assignee_name: null, rounds: 1,
          created_at: '2026-10-25', updated_at: '2026-10-25',
          external_id: null, created_by: null,
        },
        grain: { children: 0, runs: 0 },
      },
    })
  })
  await page.route(/\/api\/tasks\/1761\/comments$/, (route) => route.fulfill({ json: { threads: [] } }))
  await page.route(/\/api\/tasks\/1761\/links$/, (route) => route.fulfill({ json: { links: [] } }))
}

test('test_task_body_uses_rich', async ({ page }) => {
  const patches = []
  await stubTask(page, patches)
  await page.goto('/tasks/1761')
  await page.getByTestId('task-body-edit').click()

  const rich = page.getByTestId('task-body-rich')
  await expect(rich).toHaveAttribute('contenteditable', 'true')
  await expect(page.getByTestId('task-body-area')).toHaveCount(0)
  await expect(rich.locator('h2')).toHaveText('Goal')
  await expect(rich.locator('strong')).toHaveText('it')
  await expect(page.getByTestId('editor-mode-rich')).toHaveAttribute('aria-pressed', 'true')

  await caretAtEnd(rich)
  await page.keyboard.type('!')
  await page.getByTestId('task-body-save').click()
  await expect.poll(() => patches).toEqual([{ body: '## Goal\n\nShip **it**.!' }])
})

const SYMPTOM = 'folds when **x**'

test('test_defect_symptom_unchanged', async ({ page }) => {
  await page.route(/\/api\/defects\/\d+$/, (route) => route.fulfill({
    json: {
      defect: {
        id: 7, feature_id: 81, feature_title: 'The ledger',
        symptom: SYMPTOM, status: 'closed', round: 0,
        blamed_event_kind: 'started', missing_check: 'test_gutter', suspect_paths: [],
        fix_task_id: null, images: [],
        opened_at: '2026-09-14 12:00:00', closed_at: '2026-09-15 12:00:00',
      },
    },
  }))
  await page.goto('/defects/7')
  const shown = page.getByTestId('defect-symptom-7')
  await expect(shown).toContainText(SYMPTOM)
  await expect(shown.locator('strong')).toHaveCount(0)
  await shown.click()

  await expect(editor(page)).toHaveAttribute('data-mode', 'source')
  await expect(editor(page).locator('[data-testid^="editor-mode-"]')).toHaveCount(0)
  await expect(editor(page).getByRole('toolbar')).toHaveCount(0)
  await expect(editor(page).locator('[contenteditable="true"]')).toHaveCount(0)
  await expect(page.getByTestId('defect-edit-field-7')).toHaveValue(SYMPTOM)
})
