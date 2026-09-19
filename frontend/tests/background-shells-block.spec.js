/**
 * Running background shells are listed at the FOOT of the conversation.
 *
 * A shell outlives the turn that launched it, so by the time it matters its
 * card has scrolled out of reach — and several can run at once, which is why
 * this is a collapsible list rather than a status row. The roster is
 * whole-session (server-owned), not derived from the loaded page.
 */
import { randomUUID } from 'node:crypto'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tmpdir } from 'node:os'
import { test, expect } from './auth-fixture.js'

async function seed(page, spansFor) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: '2026-05-09T09:00:00',
      attributes: { text: 'shells', is_test: true } },
    ...spansFor(traceId, p8, prompt),
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return traceId
}

const shellSpan = (traceId, prompt, id, taskId, desc) => ({
  trace_id: traceId, span_id: id, parent_id: prompt, name: 'tool.Bash',
  start_time: '2026-05-09T09:00:01', end_time: '2026-05-09T09:00:01',
  status_code: 'OK', duration_ms: 3,
  attributes: { tool_name: 'Bash', command_preview: `cmd ${taskId}`,
    command_description: desc, run_in_background: true,
    background_task_id: taskId, is_test: true },
})

const block = page => page.locator('[data-background-shells]')

test('a running shell is listed at the foot of the feed', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, (t, p8, prompt) => [shellSpan(t, prompt, `b1-${p8}`, 'shellA', 'Start the dev server')])

  await expect(block(page)).toBeVisible()
  await expect(block(page).locator('[data-background-shells-count]')).toHaveText('1 running')
  await expect(block(page).locator('[data-background-shell-row]')).toHaveCount(1)
  await expect(block(page)).toContainText('Start the dev server')
})

test('several running shells are listed together', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, (t, p8, prompt) => [
    shellSpan(t, prompt, `b1-${p8}`, 'shellA', 'Dev server'),
    shellSpan(t, prompt, `b2-${p8}`, 'shellB', 'Watch tests'),
    shellSpan(t, prompt, `b3-${p8}`, 'shellC', 'Tail the log'),
  ])

  await expect(block(page).locator('[data-background-shells-count]')).toHaveText('3 running')
  await expect(block(page).locator('[data-background-shell-row]')).toHaveCount(3)
})

test('a finished shell drops out of the block', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, (t, p8, prompt) => [
    shellSpan(t, prompt, `b1-${p8}`, 'shellA', 'Dev server'),
    shellSpan(t, prompt, `b2-${p8}`, 'shellB', 'Watch tests'),
    { trace_id: t, span_id: `task-${p8}`, parent_id: prompt,
      name: 'task.notification', start_time: '2026-05-09T09:00:31',
      attributes: { text: 'x', task_id: 'shellB', status: 'completed',
        summary: 'done', is_test: true } },
  ])

  await expect(block(page).locator('[data-background-shells-count]')).toHaveText('1 running')
  await expect(block(page)).toContainText('Dev server')
  await expect(block(page)).not.toContainText('Watch tests')
})

test('no running shells means no block at all', async ({ page }) => {
  test.setTimeout(60_000)
  // A plain foreground Bash call: the turn has a spine to render, and nothing
  // about it is a background shell.
  await seed(page, (t, p8, prompt) => [{
    trace_id: t, span_id: `b1-${p8}`, parent_id: prompt, name: 'tool.Bash',
    start_time: '2026-05-09T09:00:01', end_time: '2026-05-09T09:00:01',
    status_code: 'OK', duration_ms: 40,
    attributes: { tool_name: 'Bash', command_preview: 'ls -la',
      stdout: 'a.py', is_test: true },
  }])

  await expect(page.locator('.event-spine')).toBeVisible()
  await expect(block(page)).toHaveCount(0)
})

test('expanding a shell streams its output file', async ({ page }) => {
  test.setTimeout(60_000)
  // The running shell's own path is stated nowhere a hook can see; a finished
  // sibling's notification is what locates the directory they share.
  const dir = mkdtempSync(join(tmpdir(), 'regin-shell-'))
  writeFileSync(join(dir, 'sibling.output'), 'old\n')
  writeFileSync(join(dir, 'shellA.output'), 'compiling module 1\ncompiling module 2\n')

  await seed(page, (t, p8, prompt) => [
    shellSpan(t, prompt, `b1-${p8}`, 'shellA', 'Dev server'),
    { trace_id: t, span_id: `task-sib-${p8}`, parent_id: prompt,
      name: 'task.notification', start_time: '2026-05-09T09:00:05',
      attributes: { text: 'x', task_id: 'sibling', status: 'completed',
        output_file: join(dir, 'sibling.output'), is_test: true } },
  ])

  await block(page).locator('[data-background-shell-row]').first().click()
  await expect(block(page).locator('[data-background-shell-output]'))
    .toContainText('compiling module 2', { timeout: 15_000 })

  // It re-reads: output written after the panel opened shows up on its own.
  writeFileSync(join(dir, 'shellA.output'),
    'compiling module 1\ncompiling module 2\ncompiling module 3\n')
  await expect(block(page).locator('[data-background-shell-output]'))
    .toContainText('compiling module 3', { timeout: 20_000 })
})

test('a shell with no locatable output says so', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, (t, p8, prompt) => [shellSpan(t, prompt, `b1-${p8}`, 'shellA', 'Dev server')])

  await block(page).locator('[data-background-shell-row]').first().click()
  await expect(block(page).locator('[data-background-shell-output-missing]'))
    .toHaveText('no readable output yet', { timeout: 15_000 })
})

test('an ended session shows no running shells', async ({ page }) => {
  test.setTimeout(60_000)
  // A shell is a child of the process that launched it. Once the session is
  // over the shell is too — whether teardown killed it or it was orphaned —
  // so a launch nothing ever reported on must not keep ticking upward.
  const traceId = await seed(page, (t, p8, prompt) => [
    shellSpan(t, prompt, `b1-${p8}`, 'shellA', 'Dev server'),
  ])
  await expect(block(page).locator('[data-background-shells-count]'))
    .toHaveText('1 running')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post(`/api/sessions/${traceId}/close`, {
    headers: { Authorization: `Bearer ${token}` }, data: {},
  })).ok()).toBeTruthy()

  await page.reload()
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  await expect(block(page)).toHaveCount(0)
})

test('the block follows the roster on the live poll, with no reload', async ({ page }) => {
  test.setTimeout(90_000)
  // The roster is server-owned and whole-session, so it changes without any
  // span the client is watching changing — a shell settles when a notification
  // lands, and the block must stop claiming it is running.
  const traceId = await seed(page, (t, p8, prompt) => [
    shellSpan(t, prompt, `b1-${p8}`, 'shellA', 'Dev server'),
  ])
  await expect(block(page).locator('[data-background-shells-count]'))
    .toHaveText('1 running')

  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` },
    data: [{
      trace_id: traceId, span_id: `task-late-${traceId.slice(0, 8)}`,
      parent_id: `p-${traceId.slice(0, 8)}`, name: 'task.notification',
      start_time: '2026-05-09T09:00:31',
      attributes: { text: 'x', task_id: 'shellA', status: 'stopped',
        summary: 'killed', is_test: true },
    }],
  })).ok()).toBeTruthy()

  await expect(block(page)).toHaveCount(0, { timeout: 30_000 })
})
