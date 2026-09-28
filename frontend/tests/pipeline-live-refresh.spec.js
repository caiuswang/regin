/**
 * R7 — the Pipeline stays fresh from `pipeline` frames, never from a poll.
 *
 * The writes go through the real CLI in a separate process, because that is
 * the path that has to cross into the server: a CLI write lands in its own
 * interpreter and reaches the open page only over the loopback notify.
 */
import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './auth-fixture.js'

// Serial: a frame is broadcast to every open page, so a sibling's write would
// wake this file's idle page during the no-request window.
test.describe.configure({ mode: 'serial' })

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')

function cli(...args) {
  return execFileSync(PY, ['cli/regin.py', ...args], { cwd: ROOT, encoding: 'utf8' })
}

function addTask(title) {
  const out = cli('task', 'add', title, '--json')
  return JSON.parse(out).id
}

test('test_pipeline_views_refresh_on_event', async ({ page }) => {
  const title = `live refresh ${Date.now()}`
  const id = addTask(title)

  await page.goto(`/tasks/${id}`)
  await expect(page.getByText(title).first()).toBeVisible()

  const detailReads = []
  page.on('request', (r) => {
    if (new URL(r.url()).pathname === `/api/tasks/${id}`) detailReads.push(r.url())
  })

  cli('task', 'delete', String(id), '--yes')
  await expect(page.getByText(`No task`)).toBeVisible()
  expect(detailReads.length).toBeGreaterThan(0)
})

test('a burst of events during a read costs exactly one more read', async ({ page }) => {
  await page.goto('/overview')
  const reads = await page.evaluate(async () => {
    const { createLiveReader } = await import('/src/composables/usePipelineEvents.js')
    let n = 0
    let release
    const run = createLiveReader(() => {
      n += 1
      return new Promise((r) => { release = r })
    })
    run()
    run(); run(); run()
    release()
    await new Promise((r) => queueMicrotask(r))
    await new Promise((r) => queueMicrotask(r))
    const during = n
    release()
    await new Promise((r) => setTimeout(r, 0))
    run()
    return { during, total: n }
  })
  expect(reads.during).toBe(2)
  // Settled, so the next event reads again rather than being swallowed.
  expect(reads.total).toBe(3)
})

test('overview re-reads on a CLI write and issues nothing while idle', async ({ page }) => {
  await page.goto('/overview')
  await expect(page.getByTestId('pipeline-count-tasks')).toBeVisible()

  const overviewReads = []
  page.on('request', (r) => {
    if (new URL(r.url()).pathname === '/api/pipeline/overview') overviewReads.push(r.url())
  })
  // The one fixed wait in this file, and it is the assertion itself: a poll
  // would show up as a request inside the window.
  await page.waitForTimeout(3000)
  expect(overviewReads).toEqual([])

  const read = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/pipeline/overview')
  const counts = page.waitForRequest((r) => new URL(r.url()).pathname === '/api/pipeline/counts')
  const id = addTask(`overview live ${Date.now()}`)
  await Promise.all([read, counts])
  cli('task', 'delete', String(id), '--yes')
})
