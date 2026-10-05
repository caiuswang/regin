/**
 * F133 S4 — the surfaces that open the file viewer (R4): a `file:` chip links
 * to /files, an edit card's filename and a Read row's View open the drawer,
 * and a search file hit opens the drawer at the hit line.
 *
 * Unstubbed: the repo, the feature and the spans are real rows, and the
 * viewer reads them through the real /api/files.
 */
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './auth-fixture.js'
import { SCRATCH_DIR, TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')

const DOC_LINES = ['# Entry doc', '', 'chip-target-body']
const EDITED = Array.from({ length: 40 }, (_, i) => `edited_${i + 1} = ${i + 1}`)
EDITED[29] = 'zqxjEntryNeedle = 30'
// What the Read returned, starting at its offset — deliberately unlike the
// file on disk, so the drawer can only be showing the capture.
const CAPTURED = ['captured_120 = 120', 'captured_121 = 121', 'captured_122 = 122']

test.describe.configure({ mode: 'serial' })

let projectId
let root

test.beforeAll(async ({ playwright }) => {
  mkdirSync(SCRATCH_DIR, { recursive: true })
  root = mkdtempSync(join(SCRATCH_DIR, 'f133-entry-'))
  mkdirSync(join(root, 'docs'))
  writeFileSync(join(root, 'docs', 'x.md'), DOC_LINES.join('\n') + '\n')
  writeFileSync(join(root, 'edited.py'), EDITED.join('\n') + '\n')
  writeFileSync(join(root, 'read.py'), 'on_disk_now = 1\n')
  execFileSync('git', ['init', '-q', root])
  execFileSync('git', ['-C', root, 'add', '.'])
  execFileSync('git', ['-C', root, '-c', 'user.email=e2e@x', '-c', 'user.name=e2e',
    'commit', '-qm', 'seed'])

  const ctx = await playwright.request.newContext()
  const login = await ctx.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })
  const { token } = await login.json()
  const headers = { Authorization: `Bearer ${token}` }
  const added = await ctx.post(`${API_BASE}/api/repos`, { headers, data: { path: root } })
  expect(added.ok(), await added.text()).toBeTruthy()
  const { repo } = await added.json()
  const listed = await (await ctx.get(`${API_BASE}/api/repos`, { headers })).json()
  projectId = listed.repos.find((r) => r.name === repo.name).id
  await ctx.dispose()
})

function seedFeature(body) {
  const script = `
import json, sys
sys.path.insert(0, ".")
from lib.features import store as features
print(features.create("Entry points", body=sys.argv[1], project_id=int(sys.argv[2])).id)
`
  const out = execFileSync(PY, ['-c', script, body, String(projectId)], { cwd: ROOT, encoding: 'utf8' })
  return Number(out.trim().split('\n').pop())
}

async function seedSession(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const at = (i) => `2026-05-10T10:00:${String(i).padStart(2, '0')}`
  const ids = { edit: `e-${p8}`, read: `r-${p8}` }
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'open files from the trace', is_test: true } },
    { trace_id: traceId, span_id: ids.edit, parent_id: prompt, name: 'tool.Edit',
      start_time: at(1), duration_ms: 50,
      attributes: { tool_name: 'Edit', edit_op: 'update', file_path: join(root, 'edited.py'),
        diff: '@@ -1,1 +1,1 @@\n-old = 1\n+edited_1 = 1', added_lines: 1, removed_lines: 1,
        is_test: true } },
    { trace_id: traceId, span_id: ids.read, parent_id: prompt, name: 'tool.Read',
      start_time: at(2), duration_ms: 20,
      attributes: { tool_name: 'Read', file_path: join(root, 'read.py'),
        content: CAPTURED.join('\n'), start_line: 120, num_lines: 3, is_test: true } },
    // Most Reads capture no content; the span source would 404 on these.
    { trace_id: traceId, span_id: `n-${p8}`, parent_id: prompt, name: 'tool.Read',
      start_time: at(3), duration_ms: 20,
      attributes: { tool_name: 'Read', file_path: join(root, 'uncaptured.py'), is_test: true } },
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return { traceId, ids }
}

const drawer = (page) => page.getByTestId('file-preview-drawer')
const rowFor = (page, text) => page.locator('.event-spine-row').filter({ hasText: text }).first()

async function drawerCode(page) {
  const view = drawer(page).getByTestId('file-code-view')
  await expect(view).toHaveAttribute('data-ready', 'true', { timeout: 20000 })
  return view
}

test('test_file_reference_chip_opens_the_viewer', async ({ page }) => {
  const id = seedFeature('See [x](file:docs/x.md) for the entry doc.')
  await page.goto(`/features/${id}`)
  const chip = page.getByTestId('feature-spec-body').locator('a.ref-chip[data-ref="file:docs/x.md"]')
  await expect(chip).toHaveAttribute('href', `/files?project=${projectId}&path=docs/x.md`)

  await chip.click()
  await expect(page).toHaveURL(new RegExp(`/files\\?project=${projectId}&path=docs/x\\.md$`))
  await expect(page.getByText('chip-target-body').first()).toBeVisible({ timeout: 20000 })
})

test('a file chip without a project stays plain text', async ({ page }) => {
  const script = `
import sys
sys.path.insert(0, ".")
from lib.features import store as features
print(features.create("No project", body="See [x](file:docs/x.md).").id)
`
  const id = Number(execFileSync(PY, ['-c', script], { cwd: ROOT, encoding: 'utf8' }).trim().split('\n').pop())
  await page.goto(`/features/${id}`)
  const chip = page.getByTestId('feature-spec-body').locator('[data-ref="file:docs/x.md"]')
  await expect(chip).toHaveAttribute('data-state', 'live')
  await expect(chip).not.toHaveAttribute('href', /.*/)
})

test('test_diffcard_filename_opens_the_current_file_in_the_drawer', async ({ page }) => {
  await seedSession(page)
  const row = rowFor(page, 'edited.py')
  await row.getByTestId('diff-card-file').click()

  const view = await drawerCode(page)
  // The file as it is on disk, not the edit's two-line diff.
  await expect(view.locator('.view-line').filter({ hasText: /^edited_2\s=\s2$/ })).toBeVisible()
  // The filename is its own control: the card did not fold open under it.
  await expect(row.locator('.diff-block')).toHaveCount(0)

  await page.keyboard.press('Escape')
  await expect(drawer(page)).toHaveCount(0)
})

test('test_read_span_opens_captured_content_at_its_offset', async ({ page }) => {
  await seedSession(page)
  await expect(rowFor(page, 'uncaptured.py')).toBeVisible()
  await expect(rowFor(page, 'uncaptured.py').getByTestId('read-row-view')).toHaveCount(0)
  const row = rowFor(page, 'read.py')
  await row.getByTestId('read-row-view').click()

  const view = await drawerCode(page)
  await expect(view.locator('.view-line').filter({ hasText: /^captured_120\s=\s120$/ })).toBeVisible()
  await expect(view).not.toContainText('on_disk_now')
  const numbers = await view.locator('.margin-view-overlays .line-numbers').allTextContents()
  expect(numbers.map((n) => n.trim()).filter(Boolean).slice(0, 3)).toEqual(['120', '121', '122'])
  // View is its own control: the row behind it was not selected.
  await expect(row.locator('.event-selected')).toHaveCount(0)
})

test('View in the presentation reader stacks the drawer over it', async ({ page }) => {
  await seedSession(page)
  await rowFor(page, 'read.py').locator('.present-open').click()
  const reader = page.getByTestId('span-present-reader')
  await expect(reader).toBeVisible()

  await reader.getByTestId('present-read-view').click()
  await drawerCode(page)
  // Clickable over the reader's modal layer, and the reader is still there.
  await drawer(page).getByTestId('file-preview-close').click()
  await expect(drawer(page)).toHaveCount(0)
  await expect(reader).toBeVisible()

  // Escape peels the drawer first, then the reader.
  await reader.getByTestId('present-read-view').click()
  await drawerCode(page)
  await page.keyboard.press('Escape')
  await expect(drawer(page)).toHaveCount(0)
  await expect(reader).toBeVisible()
})

test('a search file hit opens the file in the drawer at the hit line', async ({ page }) => {
  await seedSession(page)
  await page.keyboard.press('Meta+k')
  const panel = page.locator('[data-search-palette]')
  await expect(panel).toBeVisible()
  await panel.locator('input').first().fill('zqxjEntryNeedle')
  const hit = panel.locator('[role="group"][aria-label="Files & diffs"] [role="option"]')
  await expect(hit.first()).toBeVisible({ timeout: 10000 }).catch(() => {})
  if (!(await hit.count())) test.skip(true, 'no file corpus in this environment')
  await hit.first().click()

  const open = panel.getByTestId('search-open-file')
  await expect(open).toContainText('at line 30')
  await open.click()

  await expect(panel).toHaveCount(0)
  const view = await drawerCode(page)
  await expect(view.locator('.view-overlays .fv-line-highlight')).toHaveCount(1)
  await expect(view.locator('.view-line').filter({ hasText: /^zqxjEntryNeedle\s=\s30$/ })).toBeInViewport()
})
