/**
 * F178 S2 — code blocks and mermaid diagrams: highlighted and drawn in the
 * read view, and on the rich face a language picker per block with a live
 * diagram preview under a mermaid one.
 *
 * Stubbed like `f178-slash.spec.js`: the ledger, uploads and `/api/refs` are
 * fixtures; every assertion is about what the page draws and what it PATCHes.
 */
import { test, expect } from './auth-fixture.js'

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

const FEATURE = 1781

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
    id: FEATURE, title: 'Code and diagrams', origin: 'native',
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

const body = (page) => page.getByTestId('feature-spec-body')
const preview = (page) => face(page).getByTestId('mermaid-preview')

test.use({ viewport: { width: 1280, height: 900 } })

const DIAGRAM = '```mermaid\nstateDiagram-v2\n[*] --> a\n```'

test('test_slash_diagram_live_preview', async ({ page }) => {
  const patches = await onEmptyLine(page, 'intro')
  await page.keyboard.type('/diagram')
  await page.keyboard.press('Enter')

  await expect(face(page).getByTestId('code-language')).toContainText('mermaid')
  await expect(preview(page)).toContainText('Type a mermaid diagram')

  await page.keyboard.type('stateDiagram-v2')
  await page.keyboard.press('Enter')
  await page.keyboard.type('[*] --> a')

  await expect(preview(page).locator('svg')).toBeVisible()
  await expect(preview(page)).toHaveAttribute('data-state', 'rendered')

  const saved = await save(page, patches)
  expect(saved).toContain(DIAGRAM)
  expect(saved.startsWith('intro\n\n')).toBe(true)
})

test('test_mermaid_error_shows_source', async ({ page }) => {
  const patches = await onEmptyLine(page, 'intro')
  await page.keyboard.type('/diagram')
  await page.keyboard.press('Enter')
  await page.keyboard.type('graph TD')
  await page.keyboard.press('Enter')
  await page.keyboard.type('A -->')

  await expect(preview(page)).toHaveAttribute('data-state', 'error')
  await expect(preview(page)).toContainText('Diagram did not render')
  await expect(preview(page).locator('svg')).toHaveCount(0)
  await expect(face(page).locator('pre')).toHaveText('graph TD\nA -->')

  // Still editable: the fix renders.
  await page.keyboard.type(' B')
  await expect(preview(page).locator('svg')).toBeVisible()
  await page.keyboard.press('Backspace')
  await page.keyboard.press('Backspace')
  await expect(preview(page)).toHaveAttribute('data-state', 'error')

  const saved = await save(page, patches)
  expect(saved).toBe('intro\n\n```mermaid\ngraph TD\nA -->\n```')
})

test('test_read_view_renders_mermaid', async ({ page }) => {
  await stub(page, `before\n\n${DIAGRAM}\n\nafter`)
  await page.goto(`/features/${FEATURE}`)

  const diagram = body(page).locator('.md-mermaid')
  await expect(diagram.locator('svg')).toBeVisible()
  await expect(body(page)).not.toContainText('stateDiagram-v2')
  await expect(body(page).locator('pre')).toHaveCount(0)
  // In place: between the paragraphs it was written between.
  const order = await body(page).evaluate((el) => [...el.querySelectorAll(':scope > *')].map((n) => (n.querySelector(':scope > .md-mermaid') ? 'diagram' : n.textContent)))
  expect(order).toEqual(['before', 'diagram', 'after'])

  await expect(diagram).toHaveAttribute('data-theme', 'default')
  const light = await diagram.locator('svg').innerHTML()
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'))
  await expect(diagram).toHaveAttribute('data-theme', 'dark')
  await expect.poll(() => diagram.locator('svg').innerHTML()).not.toBe(light)
  await expect(diagram).toHaveAttribute('data-state', 'rendered')
})

test('test_read_view_mermaid_keeps_labels', async ({ page }) => {
  await stub(page, '```mermaid\ngraph TD\nS[Open spec] --> B{Has code?}\n```')
  await page.goto(`/features/${FEATURE}`)
  const svg = body(page).locator('.md-mermaid svg')
  await expect(svg.getByText('Open spec')).toBeVisible()
  await expect(svg.getByText('Has code?')).toBeVisible()
})

test('test_two_diagrams_have_their_own_ids', async ({ page }) => {
  await stub(page, `${DIAGRAM}\n\n\`\`\`mermaid\ngraph TD\nX --> Y\n\`\`\``)
  await page.goto(`/features/${FEATURE}`)
  const svgs = body(page).locator('.md-mermaid svg')
  await expect(svgs).toHaveCount(2)
  const ids = await svgs.evaluateAll((els) => els.map((el) => el.id))
  expect(new Set(ids).size).toBe(2)
  await page.evaluate(() => document.documentElement.setAttribute('data-theme', 'dark'))
  await expect(body(page).locator('.md-mermaid[data-theme="dark"][data-state="rendered"] svg')).toHaveCount(2)
})

test('test_read_view_highlights_gherkin', async ({ page }) => {
  await stub(page, '```gherkin\nScenario: x\n```\n\n```unknownlang\nScenario: <b>y</b>\n```')
  await page.goto(`/features/${FEATURE}`)

  const gherkin = body(page).locator('code.hljs.language-gherkin')
  await expect(gherkin.locator('.hljs-keyword').first()).toBeVisible()
  await expect(gherkin).toHaveText('Scenario: x')

  const unknown = body(page).locator('code.language-unknownlang')
  await expect(unknown).toHaveText('Scenario: <b>y</b>')
  await expect(unknown.locator('[class*="hljs-"]')).toHaveCount(0)
  await expect(unknown).not.toHaveClass(/\bhljs\b/)
})

test('test_read_view_numbers_headings', async ({ page }) => {
  await stub(page, '# A\n\n## B\n\n### C\n\n#### D\n\n##### E\n\n## B')
  await page.goto(`/features/${FEATURE}`)
  const ids = await body(page).evaluate((el) =>
    [...el.querySelectorAll('h1, h2, h3, h4, h5')].map((h) => `${h.tagName}:${h.id}`))
  expect(ids).toEqual(['H1:md-h-0', 'H2:md-h-1', 'H3:md-h-2', 'H4:md-h-3', 'H5:', 'H2:md-h-4'])
})

test('test_language_select_rewrites_only_that_block', async ({ page }) => {
  const src = 'intro\n\n```\nx\n```\n\n| a |\n|---|\n| 1 |'
  const patches = await stub(page, src)
  await openRich(page)

  const picker = face(page).getByTestId('code-language')
  await expect(picker).toContainText('plain')
  await picker.click()
  await page.getByRole('option', { name: 'bash', exact: true }).click()
  await expect(picker).toContainText('bash')

  expect(await save(page, patches)).toBe('intro\n\n```bash\nx\n```\n\n| a |\n|---|\n| 1 |')
})

test('test_language_select_is_one_undo_step', async ({ page }) => {
  await stub(page, '```ts\nconst a = 1\n```')
  await openRich(page)
  const picker = face(page).getByTestId('code-language')
  await expect(picker).toContainText('ts')
  await expect(face(page).locator('pre .hljs-keyword').first()).toHaveText('const')

  await picker.click()
  await page.getByRole('option', { name: 'python', exact: true }).click()
  await expect(picker).toContainText('python')
  await face(page).evaluate((el) => el.editor.commands.undo())
  await expect(picker).toContainText('ts')
})

test('test_mermaid_and_lowlight_not_in_entry', async ({ page }) => {
  const asked = []
  page.on('request', (req) => asked.push(req.url()))
  const lib = (name) => asked.some((url) => new RegExp(`node_modules/(\\.vite/deps/)?${name}`).test(url))

  await stub(page, '## Plain\n\nno code here')
  await page.goto(`/features/${FEATURE}`)
  await expect(body(page)).toContainText('no code here')
  expect(lib('mermaid')).toBe(false)
  expect(lib('lowlight')).toBe(false)

  await page.unrouteAll({ behavior: 'ignoreErrors' })
  await stub(page, DIAGRAM)
  await page.reload()
  await expect(body(page).locator('.md-mermaid svg')).toBeVisible()
  expect(lib('mermaid')).toBe(true)
})
