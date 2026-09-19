/**
 * A backgrounded shell must be legible in the conversation.
 *
 * `run_in_background` hands control back in milliseconds, so the Bash span
 * closes as an instant success with no stdout, and the completion lands much
 * later as a separate `task.notification` row. Before this, the short-command
 * case failed the card chain's attribute guard and degraded to a bare inline
 * row — the `background` badge could not render at all — and nothing said
 * whether the shell was still running or how it ended.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const OUT = '/tmp/tasks/bo58vwcaw.output'

async function seed(page, { notification = null } = {}) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: '2026-05-09T09:00:00',
      attributes: { text: 'background shell', is_test: true } },
    // Exactly what the hook stores: preview only, no stdout, 3 ms.
    { trace_id: traceId, span_id: `b1-${p8}`, parent_id: prompt, name: 'tool.Bash',
      start_time: '2026-05-09T09:00:01', duration_ms: 3,
      attributes: { tool_name: 'Bash', command_preview: 'npm run dev',
        command_description: 'Start the dev server', run_in_background: true,
        background_task_id: 'bo58vwcaw', is_test: true } },
  ]
  if (notification) {
    spans.push({
      trace_id: traceId, span_id: `task-bo58vwcaw-${p8}`, parent_id: prompt,
      name: 'task.notification', start_time: '2026-05-09T09:00:31',
      attributes: { text: '<task-notification/>', task_id: 'bo58vwcaw',
        tool_use_id: 'toolu_bg1', output_file: OUT, is_test: true,
        ...notification },
    })
  }
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return traceId
}

const shellRow = page => page.locator('.event-spine-row').filter({ hasText: 'npm run dev' })

test('a shell with no completion reads as a running background shell', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page)

  const row = shellRow(page)
  await expect(row).toContainText('Shell')
  await expect(row.locator('[data-bash-background]')).toHaveText('background · running')
  // It is a card with a body, not the bodyless inline row.
  await expect(row).toContainText('▸')

  await row.getByText('npm run dev').first().click()
  await expect(row.locator('[data-bash-background-detail]'))
    .toContainText('no completion reported yet')
})

test('the completion is joined onto the launching call', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, {
    notification: {
      status: 'completed',
      summary: 'Background command "Start the dev server" completed (exit code 0)',
    },
  })

  const row = shellRow(page)
  await expect(row.locator('[data-bash-background]')).toHaveText('background · completed')
  // 30 s of shell, beside the 3 ms the tool call itself cost.
  await expect(row.locator('[data-bash-background-elapsed]')).toHaveText('30s')

  await row.getByText('npm run dev').first().click()
  const detail = row.locator('[data-bash-background-detail]')
  await expect(detail).toContainText('exit code 0')
  await expect(detail).toContainText(OUT)
})

test('a failed shell is badged as failed', async ({ page }) => {
  test.setTimeout(60_000)
  await seed(page, { notification: { status: 'failed', summary: 'exit code 1' } })

  await expect(shellRow(page).locator('[data-bash-background]'))
    .toHaveText('background · failed')
})
