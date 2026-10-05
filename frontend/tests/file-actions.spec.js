/**
 * F133 S5 — the viewer's actions (R3) and its URL-addressed state (I8).
 *
 * Same setup as file-viewer.spec.js, except the fixture repo has a commit, so
 * HEAD exists and a worktree edit makes a file `diffable`.
 */
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { VITE_PORT } from '../e2e-env.js'
import { expect, test } from './auth-fixture.js'
import { registerFixtureRepo } from './helpers/fixtureRepo.js'

const lines = (edit = {}) => Array.from({ length: 120 }, (_, i) => edit[i + 1] ?? `value_${i + 1} = ${i + 1}`)
  .join('\n') + '\n'

function commitAll(root) {
  const git = (...args) => execFileSync('git', [
    '-C', root, '-c', 'user.name=e2e', '-c', 'user.email=e2e@example.com',
    '-c', 'commit.gpgsign=false', ...args,
  ])
  git('init', '-q')
  git('add', '-A')
  git('commit', '-q', '-m', 'fixture')
}

test.describe.configure({ mode: 'serial' })

let projectId

test.beforeAll(async ({ playwright }) => {
  projectId = await registerFixtureRepo(playwright, 'f133-actions-', (root) => {
    writeFileSync(join(root, 'main.py'), lines())
    writeFileSync(join(root, 'clean.py'), lines())
    writeFileSync(join(root, 'notes.md'), '# Notes\n\nSome *text*.\n')
    commitAll(root)
    writeFileSync(join(root, 'main.py'), lines({ 40: 'value_40 = 4000', 41: 'value_41 = 4100' }))
    writeFileSync(join(root, 'new.py'), 'fresh = 1\n')
  })
})

const query = (path, extra = '') => `project=${projectId}&path=${encodeURIComponent(path)}${extra}`
const fileUrl = (path, extra = '', hash = '') => `/files?${query(path, extra)}${hash}`

async function diffReady(page) {
  const view = page.getByTestId('file-diff-view')
  await expect(view).toHaveAttribute('data-ready', 'true', { timeout: 20000 })
  return view
}

test('test_changes_vs_head_shows_a_monaco_diff', async ({ page }) => {
  // Monaco renders only the lines in view; the anchor brings the change there.
  await page.goto(fileUrl('main.py', '', '#L40'))
  await expect(page.getByTestId('file-code-view')).toHaveAttribute('data-ready', 'true', { timeout: 20000 })
  await page.getByTestId('file-action-diff').click()

  const view = await diffReady(page)
  await expect(page).toHaveURL(/[?&]view=diff(&|#|$)/)
  const original = view.locator('.editor.original')
  const modified = view.locator('.editor.modified')
  // Monaco renders spaces as NBSP, hence `\s`.
  await expect(original.locator('.view-line').filter({ hasText: /^value_40\s=\s40$/ })).toHaveCount(1)
  await expect(modified.locator('.view-line').filter({ hasText: /^value_40\s=\s4000$/ })).toHaveCount(1)
  await expect(original.locator('.view-line').filter({ hasText: /4000/ })).toHaveCount(0)
  const [left, right] = [await original.boundingBox(), await modified.boundingBox()]
  expect(left.x + left.width).toBeLessThanOrEqual(right.x + 1)

  await page.getByTestId('file-diff-inline').click()
  await expect(view).toHaveAttribute('data-layout', 'inline')
  await expect(page).toHaveURL(/[?&]layout=inline(&|#|$)/)
  // Inline, both sides share the modified editor's width: the deleted HEAD
  // line is drawn into it as a view zone.
  await expect(modified.locator('.view-zones')).toContainText(/value_40\s=\s40(?!0)/)
  await page.getByTestId('file-diff-side').click()
  await expect(view).toHaveAttribute('data-layout', 'side')

  // Closing the diff returns to the source view.
  await page.getByTestId('file-action-diff').click()
  await expect(page.getByTestId('file-code-view')).toBeVisible()
  await expect(page).not.toHaveURL(/view=diff/)

  for (const path of ['clean.py', 'new.py']) {
    await page.goto(fileUrl(path))
    await expect(page.getByTestId('file-actions')).toBeVisible()
    await expect(page.getByTestId('file-action-diff')).toHaveCount(0)
  }
  // A view=diff URL on a clean file falls back to the source view.
  await page.goto(fileUrl('clean.py', '&view=diff'))
  await expect(page.getByTestId('file-code-view')).toBeVisible()
  await expect(page.getByTestId('file-diff-view')).toHaveCount(0)
})

test('test_worktree_head_switch_and_copy_actions', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.goto(fileUrl('main.py'))
  await expect(page.getByTestId('file-code-view')).toHaveAttribute('data-ready', 'true', { timeout: 20000 })
  await expect(page.getByTestId('file-rev-worktree')).toHaveAttribute('aria-pressed', 'true')

  await page.getByTestId('file-rev-head').click()
  await expect(page).toHaveURL(/[?&]rev=HEAD(&|#|$)/)
  const code = page.getByTestId('file-code-view')
  await expect(code).toHaveAttribute('data-ready', 'true', { timeout: 20000 })
  await expect(page.getByTestId('file-rev-head')).toHaveAttribute('aria-pressed', 'true')
  await page.goto(fileUrl('main.py', '&rev=HEAD', '#L40'))
  await expect(code.locator('.view-line').filter({ hasText: /^value_40\s=\s40$/ })).toHaveCount(1)
  await page.getByTestId('file-rev-worktree').click()
  await expect(page).not.toHaveURL(/rev=HEAD/)
  await expect(code).toHaveAttribute('data-ready', 'true', { timeout: 20000 })

  await page.getByTestId('file-action-copy-path').click()
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe('main.py')
  await page.getByTestId('file-action-copy-content').click()
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(
    lines({ 40: 'value_40 = 4000', 41: 'value_41 = 4100' }))

  const download = page.waitForEvent('download')
  await page.getByTestId('file-action-download').click()
  expect((await download).suggestedFilename()).toBe('main.py')

  // An untracked file has no HEAD to switch to.
  await page.goto(fileUrl('new.py'))
  await expect(page.getByTestId('file-actions')).toBeVisible()
  await expect(page.getByTestId('file-rev-head')).toHaveCount(0)
})

test('test_current_on_disk_reopens_a_span_source_from_disk', async ({ page }) => {
  await page.goto(fileUrl('main.py'))
  const abs = await page.getByTestId('file-viewer-name').getAttribute('title')
  const traceId = `f133-s5-${randomUUID()}`
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  const seeded = await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` },
    data: [{
      trace_id: traceId, span_id: 'read-1', parent_id: null, name: 'tool.Read',
      start_time: '2026-10-05T10:00:00',
      attributes: { file_path: abs, content: 'value_10 = 10\nvalue_11 = 11\n', start_line: 10, is_test: true },
    }],
  })
  expect(seeded.ok(), await seeded.text()).toBeTruthy()

  await page.goto(`/files?session=${traceId}&span=read-1`)
  const code = page.getByTestId('file-code-view')
  await expect(code).toHaveAttribute('data-ready', 'true', { timeout: 20000 })
  await expect(code.locator('.view-line')).toHaveCount(3)
  // A span is not a git path: no revision switch, no diff.
  await expect(page.getByTestId('file-rev-head')).toHaveCount(0)
  await page.getByTestId('file-action-on-disk').click()

  await expect(page).toHaveURL((url) => url.searchParams.get('session') === traceId
    && url.searchParams.get('path') === abs && !url.searchParams.has('span'))
  await expect(code).toHaveAttribute('data-ready', 'true', { timeout: 20000 })
  await expect(code.locator('.view-line').filter({ hasText: /^value_1\s=\s1$/ })).toHaveCount(1)
  await expect(page.getByTestId('file-action-on-disk')).toHaveCount(0)
})

test('test_open_in_vs_code_shows_only_on_a_loopback_host', async ({ page }) => {
  await page.goto(fileUrl('main.py', '', '#L42'))
  const abs = await page.getByTestId('file-viewer-name').getAttribute('title')
  expect(abs).toMatch(/\/main\.py$/)
  await expect(page.getByTestId('file-action-vscode')).toHaveAttribute('href', `vscode://file${abs}:42`)

  await page.goto(`http://127.0.0.1:${VITE_PORT}${fileUrl('notes.md')}`)
  await expect(page.getByTestId('file-action-vscode')).toHaveAttribute('href', /^vscode:\/\/file\/.+\/notes\.md:1$/)

  // A name that is not loopback, served by the same dev server: the browser
  // never resolves it, every request is answered from 127.0.0.1.
  const remote = `http://regin-remote.test:${VITE_PORT}`
  await page.route(`${remote}/**`, async (route) => {
    const url = route.request().url().replace(remote, `http://127.0.0.1:${VITE_PORT}`)
    await route.fulfill({ response: await route.fetch({ url }) })
  })
  await page.goto(`${remote}${fileUrl('notes.md')}`)
  await expect(page.getByTestId('file-viewer-name')).toHaveText('notes.md')
  await expect(page.getByTestId('file-action-copy-path')).toBeVisible()
  expect(await page.evaluate(() => window.location.hostname)).toBe('regin-remote.test')
  await expect(page.getByTestId('file-action-vscode')).toHaveCount(0)
  // The dev client keeps requesting after the last assertion.
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

test('test_files_page_state_is_fully_addressed_by_its_url', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.goto(fileUrl('main.py', '&rev=HEAD', '#L38'))
  await expect(page.getByTestId('file-code-view')).toHaveAttribute('data-ready', 'true', { timeout: 20000 })
  await page.getByTestId('file-action-diff').click()
  await expect(page).toHaveURL(/#L38$/)
  const view = await diffReady(page)
  await page.getByTestId('file-diff-inline').click()

  // Copy link names the line the user selected, and becomes the URL's anchor.
  const line40 = view.locator('.editor.modified .view-line').filter({ hasText: /^value_40\s=\s4000$/ })
  await line40.click()
  await page.getByTestId('file-action-copy-link').click()
  await expect(page).toHaveURL(/#L40$/)
  const link = await page.evaluate(() => navigator.clipboard.readText())
  expect(link).toBe(page.url())
  expect(new URL(link).searchParams.get('view')).toBe('diff')
  expect(new URL(link).searchParams.get('layout')).toBe('inline')
  expect(new URL(link).searchParams.get('rev')).toBe('HEAD')

  const fresh = await context.newPage()
  await fresh.goto(link)
  const again = await diffReady(fresh)
  await expect(fresh.getByTestId('file-viewer-name')).toHaveText('main.py')
  await expect(fresh.getByTestId('file-rev-head')).toHaveAttribute('aria-pressed', 'true')
  await expect(again).toHaveAttribute('data-layout', 'inline')
  await expect(again.locator('.editor.modified .fv-line-highlight')).toHaveCount(1)
  await expect(again.locator('.editor.modified .view-line').filter({ hasText: /^value_40\s=\s4000$/ }))
    .toBeInViewport()

  // The view mode of a rendered kind survives a reload the same way.
  await fresh.goto(fileUrl('notes.md'))
  await fresh.getByTestId('file-view-source').click()
  await expect(fresh).toHaveURL(/[?&]view=source(&|#|$)/)
  await fresh.reload()
  await expect(fresh.getByTestId('file-code-view')).toHaveAttribute('data-ready', 'true', { timeout: 20000 })
  await expect(fresh.getByTestId('file-view-source')).toHaveAttribute('aria-pressed', 'true')
})
