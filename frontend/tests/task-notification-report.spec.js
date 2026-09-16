/**
 * The background-task card's payload coverage.
 *
 * Two thirds of `task.notification` spans carry the finished agent's whole
 * report plus its token / tool-call / duration counters, and the card used to
 * render none of it — one "Agent X finished" line over a 5.6 KB verdict. These
 * pin the vitals row, the report disclosure, and the fact that a notification
 * with no report at all still renders exactly as before.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const REPORT = `## Verdict per acceptance item\n\n${'**1. PASS** — the fold holds.\n'.repeat(30)}\nSHIP.`

async function seed(page, notificationAttrs) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: '2026-05-08T12:00:00',
      attributes: { text: 'run the verifier', is_test: true } },
    { trace_id: traceId, span_id: `n-${p8}`, parent_id: prompt,
      name: 'task.notification', start_time: '2026-05-08T12:00:01',
      duration_ms: 30, attributes: { ...notificationAttrs, is_test: true } },
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}`)
  return { spanId: `n-${p8}`,
    card: page.locator('.bg-amber-50').filter({ hasText: 'Background task' }).first() }
}

test('the card shows the agent vitals and opens the report', async ({ page }) => {
  const errors = []
  page.on('console', m => { if (m.type() === 'error') errors.push(m.text()) })

  const { card, spanId } = await seed(page, {
    task_id: 'a3c88e215a255291e',
    status: 'completed',
    summary: 'Agent "Adversarially verify follow-collapse" finished',
    agent_type: 'claude',
    result: REPORT,
    subagent_tokens: 42228,
    tool_uses: 14,
    duration_ms: 200548,
    text: '<task-notification>\n<task-id>a3c88e215a255291e</task-id>\n</task-notification>',
  })

  await expect(card).toContainText('claude')
  await expect(card).toContainText('3m20s')
  await expect(card).toContainText('42.2k tok')
  await expect(card).toContainText('14 tool calls')

  // Collapsed: a preview, not the whole 900-char report.
  await expect(card).toContainText('## Verdict per acceptance item')
  await expect(card).not.toContainText('SHIP.')

  await card.getByText(/Show report · \d+ chars/).click()
  await expect(card).toContainText('SHIP.')
  await card.getByText('Collapse report').click()
  await expect(card).not.toContainText('SHIP.')

  // The presentation reader is the surface that showed only the summary: its
  // body table mapped this span kind to `summary` alone.
  const row = page.locator(`[data-span-id="${spanId}"]`)
  await row.hover()
  await row.locator('.spine-present').click()
  const readerEl = page.getByTestId('span-present-reader')
  await expect(readerEl).toBeVisible()
  // `.present-col` is the prose column, NOT the attributes aside beside it —
  // asserting on `.present-body` passes on the raw attribute value alone and
  // proves nothing about the body table.
  const prose = readerEl.locator('.present-col')
  await expect(prose).toContainText('Verdict per acceptance item')
  await expect(prose).toContainText('SHIP.')
  await expect(readerEl).toContainText('Agent report')

  expect(errors.filter(e => !/favicon/.test(e))).toHaveLength(0)
})

test('a notification with no report renders the summary alone', async ({ page }) => {
  const { card } = await seed(page, {
    task_id: 'belbeprx7',
    status: 'killed',
    summary: 'Background command "Start worktree vite" was stopped',
    text: '<task-notification>\n<task-id>belbeprx7</task-id>\n</task-notification>',
  })

  await expect(card).toContainText('Background command "Start worktree vite" was stopped')
  await expect(card).not.toContainText('Show report')
  // A killed run is not a success — the chip used to paint everything but
  // `failed` green.
  await expect(card.locator('.text-red-700')).toContainText('killed')
})
