/**
 * F133 S2 — the viewer shell: Monaco for code, native media, the binary card.
 *
 * Runs against the real /api/files backend: a throwaway git repo is written
 * under the suite's scratch dir and registered through POST /api/repos, so the
 * meta, the classification and the raw bytes are the server's, not mocks.
 */
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from './auth-fixture.js'
import { registerFixtureRepo } from './helpers/fixtureRepo.js'

const PY_LINES = Array.from({ length: 300 }, (_, i) => `value_${i + 1} = ${i + 1}`)
PY_LINES[0] = 'def greet(name):'
PY_LINES[1] = '    return f"hello {name}"  # a comment'

const SVG = `<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" onload="top.__svgOnload = 1">
  <script>top.__svgRan = 1; parent.__svgRan = 1;</script>
  <rect width="40" height="40" fill="teal"/>
</svg>`

function minimalPdf() {
  const objs = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] >>',
  ]
  let body = '%PDF-1.4\n'
  const offsets = []
  objs.forEach((o, i) => {
    offsets.push(body.length)
    body += `${i + 1} 0 obj\n${o}\nendobj\n`
  })
  const xref = body.length
  body += `xref\n0 ${objs.length + 1}\n0000000000 65535 f \n`
  for (const off of offsets) body += `${String(off).padStart(10, '0')} 00000 n \n`
  body += `trailer\n<< /Size ${objs.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return body
}

// One repo for the file: parallel workers each registering their own race on
// the `repo_paths` read-modify-write, and the reconcile drops the loser's row.
test.describe.configure({ mode: 'serial' })

let projectId

test.beforeAll(async ({ playwright }) => {
  projectId = await registerFixtureRepo(playwright, 'f133-viewer-', (root) => {
    writeFileSync(join(root, 'main.py'), PY_LINES.join('\n') + '\n')
    writeFileSync(join(root, 'pic.svg'), SVG)
    writeFileSync(join(root, 'doc.pdf'), minimalPdf())
    writeFileSync(join(root, 'blob.dat'), Buffer.from([0x00, 0x01, 0x02, 0xff, 0x00, 0x10, 0x20]))
  })
})

const fileUrl = (path, hash = '') => `/files?project=${projectId}&path=${encodeURIComponent(path)}${hash}`

async function codeReady(page) {
  const view = page.getByTestId('file-code-view')
  await expect(view).toHaveAttribute('data-ready', 'true', { timeout: 20000 })
  return view
}

test('test_code_opens_read_only_in_monaco_with_highlighting', async ({ page }) => {
  await page.goto(fileUrl('main.py'))
  const view = await codeReady(page)
  await expect(view).toHaveAttribute('data-language', 'python')

  // Monaco renders spaces as NBSP, hence `\s` in the text patterns below.
  const firstLine = view.locator('.view-line').filter({ hasText: 'def greet' })
  await expect(firstLine).toHaveCount(1)
  // Monarch tokenizes `def` as a keyword: the line splits into several token
  // classes (mtkN), where an untokenized buffer renders as one run.
  await expect.poll(async () => firstLine.evaluate((el) =>
    new Set([...el.querySelectorAll('span[class*="mtk"]')].map((s) => s.className)).size))
    .toBeGreaterThan(1)

  await view.locator('.view-lines').click()
  await page.keyboard.type('XYZZY')
  await page.keyboard.press('Enter')
  await expect(view.locator('.view-lines')).not.toContainText('XYZZY')
  await expect(firstLine).toHaveText(/^def\sgreet\(name\):$/)
})

test('test_monaco_is_not_loaded_until_a_code_view_mounts', async ({ page }) => {
  const monacoRequests = []
  page.on('request', (req) => {
    if (/monaco|editor\.(api|worker)/i.test(req.url())) monacoRequests.push(req.url())
  })

  await page.goto('/trace/sessions/f133-no-such-session')
  // The trace view polls, so it never goes network-idle; wait for the route's
  // own chunk to have rendered instead.
  await expect(page.locator('.content-scroll > *').last()).toBeVisible()
  await page.waitForLoadState('load')
  expect(monacoRequests).toEqual([])

  // No trace surface opens the drawer yet, so drive the drawer's own store —
  // the dev server serves it at the same URL the app imported it from, so it
  // is the same module instance.
  await page.evaluate(async (project) => {
    const mod = await import('/src/composables/useFilePreview.js')
    mod.openFilePreview({ project: String(project), path: 'main.py' })
  }, projectId)

  const drawer = page.getByTestId('file-preview-drawer')
  await expect(drawer).toBeVisible()
  await expect(drawer.getByTestId('file-code-view')).toHaveAttribute('data-ready', 'true', { timeout: 20000 })
  expect(monacoRequests.length).toBeGreaterThan(0)
})

test('test_line_anchor_reveals_and_highlights_lines', async ({ page }) => {
  await page.goto(fileUrl('main.py', '#L150-L152'))
  const view = await codeReady(page)
  // Monaco renders only the lines in its viewport, so a decoration that is in
  // the DOM is a line that was scrolled into view.
  await expect(view.locator('.view-overlays .fv-line-highlight')).toHaveCount(3)
  await expect(view.locator('.view-line').filter({ hasText: /^value_150\s=\s150$/ })).toBeInViewport()
  await expect(view.locator('.view-line').filter({ hasText: /^value_152\s=\s152$/ })).toBeInViewport()
  await expect(view.locator('.view-line').filter({ hasText: /^def greet/ })).toHaveCount(0)
})

test('test_svg_scripts_do_not_run', async ({ page }) => {
  await page.goto(fileUrl('pic.svg'))
  const media = page.getByTestId('file-media-view')
  await expect(media).toHaveAttribute('data-preview', 'svg')
  const img = media.getByTestId('file-media-img')
  await expect(img).toHaveAttribute('src', /^blob:/)
  await expect.poll(() => img.evaluate((el) => el.complete && el.naturalWidth)).toBeGreaterThan(0)
  await expect(media.locator('svg')).toHaveCount(0)
  expect(await page.evaluate(() => [window.__svgRan, window.__svgOnload])).toEqual([undefined, undefined])
})

test('test_pdf_renders_in_the_native_viewer', async ({ page }) => {
  await page.goto(fileUrl('doc.pdf'))
  const frame = page.getByTestId('file-media-pdf')
  await expect(frame).toHaveAttribute('src', /^blob:/)
  const type = await frame.evaluate(async (el) => (await (await fetch(el.src)).blob()).type)
  expect(type).toBe('application/pdf')
})

test('test_binary_file_offers_only_metadata_and_download', async ({ page }) => {
  await page.goto(fileUrl('blob.dat'))
  const card = page.getByTestId('file-binary-card')
  await expect(card).toHaveAttribute('data-preview', 'binary')
  await expect(card.getByTestId('file-binary-name')).toHaveText('blob.dat')
  await expect(card.getByTestId('file-binary-size')).toHaveText('7 B')
  await expect(card.getByTestId('file-binary-mime')).not.toBeEmpty()
  await expect(page.getByTestId('file-code-view')).toHaveCount(0)
  await expect(page.getByTestId('file-media-view')).toHaveCount(0)

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    card.getByTestId('file-binary-download').click(),
  ])
  expect(download.suggestedFilename()).toBe('blob.dat')
})
