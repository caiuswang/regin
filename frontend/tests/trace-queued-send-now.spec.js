/**
 * "Send now" on the trace page's queued list (`TraceQueuedPrompts.vue`).
 *
 * The control aborts the running turn through the session's regin-bridge mod
 * so Claude Code runs its own queue next. It exists only where a mod listener
 * does — `live-state.mod_listener` — because the tmux fallback (Escape) pulls
 * the queue back into the composer instead of running it.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`

async function post(page, spans) {
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
}

async function seed(page, { modListener }) {
  await page.goto('/trace/sessions')
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  await post(page, [
    { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: `SEND_NOW_FIXTURE_${sfx}`, is_test: true } },
    { trace_id: traceId, span_id: `a-${sfx}`, parent_id: `p-${sfx}`,
      name: 'assistant_response', start_time: at(1), duration_ms: 900,
      attributes: { text: 'working on it', is_test: true } },
  ])
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const res = await route.fetch()
    let body = {}
    try { body = await res.json() } catch { body = {} }
    await route.fulfill({ json: {
      ...body, ended_at: null,
      queued_prompts: [{ content: 'run me next' }, { content: 'and me' }],
    } })
  })
  const probes = []
  await page.route(`**/api/sessions/${traceId}/live-state`, (route) => {
    probes.push(route.request().method())
    return route.fulfill({
      json: { tier: 'tmux', live: true, starting: false, undetermined: false,
              detail: '', mod_listener: modListener },
    })
  })
  const sent = []
  await page.route(`**/api/sessions/${traceId}/bridge-send-now`, (route) => {
    sent.push(route.request().method())
    return route.fulfill({ json: { delivered: true, detail: 'abort sent via mod listener',
                                   channel: 'mod' } })
  })
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  return { sent, probes }
}

test('queued prompts offer send now only with a mod listener', async ({ page }) => {
  const { sent } = await seed(page, { modListener: true })
  await expect(page.getByTestId('trace-queued-item')).toHaveCount(2)
  const button = page.getByTestId('trace-queued-send-now')
  await expect(button).toBeVisible()

  await button.click()
  await expect.poll(() => sent).toEqual(['POST'])
  await expect(page.getByTestId('trace-queued-notice')).toHaveCount(0)
})

test('no send now without a mod listener', async ({ page }) => {
  const { sent, probes } = await seed(page, { modListener: false })
  await expect(page.getByTestId('trace-queued-item')).toHaveCount(2)
  // The page did ask, so the button's absence is the answer, not a race.
  await expect.poll(() => probes.length).toBeGreaterThan(0)
  await expect(page.getByTestId('trace-queued-send-now')).toHaveCount(0)
  expect(sent).toEqual([])
})
