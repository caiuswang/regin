/**
 * SessionTraceView (timeline mode): clicking a non-leaf prompt row in the
 * TreeTable triggers the lazy `/spans/<id>/children` fetch and grows the
 * visible tree.
 *
 * Data is injected per run via `/api/session-spans` with `is_test=true`
 * so the trace is invisible in the sessions list and the test is portable
 * to any clean DB.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

async function ingestPromptWithChild(page) {
  const traceId = randomUUID()
  const promptId = `prompt-${traceId.slice(0, 8)}`
  const toolId = `tool-${traceId.slice(0, 8)}`
  const t0 = '2026-05-17T08:00:00.000000'
  const t1 = '2026-05-17T08:00:01.000000'

  const res = await page.request.post('/api/session-spans', {
    data: [
      {
        trace_id: traceId,
        span_id: promptId,
        parent_id: null,
        name: 'prompt',
        start_time: t0,
        attributes: { text: 'lazy-children fixture', is_test: true },
      },
      {
        trace_id: traceId,
        span_id: toolId,
        parent_id: promptId,
        name: 'tool.Bash',
        start_time: t1,
        attributes: { command_preview: 'echo hi', is_test: true },
      },
    ],
  })
  expect(res.ok()).toBeTruthy()
  return { traceId, promptId, toolId }
}

test('clicking a non-leaf prompt loads and expands children', async ({ page }) => {
  const { traceId, promptId } = await ingestPromptWithChild(page)

  await page.addInitScript(() => {
    localStorage.setItem('regin_session_view_mode', 'timeline')
  })

  // Watch for the on-demand children fetch.
  let childrenApiHit = false
  page.on('response', (resp) => {
    if (
      resp.request().method() === 'GET' &&
      resp.url().includes(`/api/sessions/${traceId}/spans/${promptId}/children`)
    ) {
      childrenApiHit = true
    }
  })

  await page.goto(`/trace/sessions/${traceId}`)
  const rows = page.locator('.p-treetable-tbody > tr')
  await expect(rows.first()).toBeVisible({ timeout: 10_000 })
  const rootCountBefore = await rows.count()

  // Synthetic fixture has exactly one prompt at the root. The row has a
  // custom expander button (title="Expand", `▸`) — that's what triggers
  // the on-demand `/children` fetch in `ensureNodeChildrenLoaded`,
  // not a plain row-click (selecting the row alone doesn't expand it).
  const promptRow = page.locator('.p-treetable-tbody > tr:has-text("prompt")').first()
  const expandBtn = promptRow.locator('button[title="Expand"]').first()
  await expandBtn.click()

  await expect.poll(() => childrenApiHit, { timeout: 10_000 }).toBeTruthy()
  await expect.poll(
    async () => page.locator('.p-treetable-tbody > tr').count(),
    { timeout: 10_000 },
  ).toBeGreaterThan(rootCountBefore)
})
