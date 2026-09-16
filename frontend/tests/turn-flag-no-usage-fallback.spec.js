/**
 * The Activity strip must never invent turn flags out of `turn_usage` rows.
 *
 * A `turn_usage` row is one API response, not one user prompt, so a tool-heavy
 * session carries far more of them than it had turns. The strip used to fall
 * back to them whenever no `prompt` span was loaded — which is not only the
 * "session genuinely has no prompts" case but also every case where the tree
 * failed to load. One real session (6 prompts, 212 usage rows) painted 36
 * flags numbered up to 212, each captioned "user prompt · start of turn N".
 *
 * So this seeds the ORIGINATING state — usage rows present, prompt spans
 * absent — and asserts the strip stays empty rather than filling the vacuum.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

test('no prompt spans + many turn_usage rows renders zero turn flags', async ({ page }) => {
  const traceId = randomUUID()
  const short = traceId.slice(0, 8)

  // Root spans that are NOT prompts — a session whose loaded tree holds no
  // prompt anchor, exactly as a blank/failed tree load looks to the strip.
  const spans = [0, 1, 2].map(i => ({
    trace_id: traceId, span_id: `b${i}-${short}`, parent_id: null,
    name: 'tool.Bash', start_time: `2026-05-07T10:0${i}:00`,
    end_time: `2026-05-07T10:0${i}:30`,
    attributes: { command: `echo ${i}`, is_test: true },
  }))

  // Twelve billing rows spread over the same window: the fabrication fuel.
  const usage = Array.from({ length: 12 }, (_, i) => ({
    trace_id: traceId,
    turn_uuid: `u${i}-${short}`,
    turn_index: i,
    timestamp: `2026-05-07T10:00:${String(i * 5).padStart(2, '0')}`,
    model: 'claude-opus-5',
    input_tokens: 10, output_tokens: 20,
  }))

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  const auth = { Authorization: `Bearer ${token}` }

  expect((await page.request.post('/api/session-spans', { headers: auth, data: spans })).ok())
    .toBeTruthy()
  expect((await page.request.post('/api/turn-usage', { headers: auth, data: usage })).ok())
    .toBeTruthy()

  await page.goto(`/trace/sessions/${traceId}`)
  // The strip renders with the tree, so wait on a bar rather than on a flag —
  // waiting on the thing we expect to be absent proves nothing about timing.
  await expect(page.getByTestId('overview-strip-bar').first()).toBeVisible({ timeout: 10_000 })

  await expect(page.getByTestId('turn-flag')).toHaveCount(0)

  // Same fabricated source fed the vitals "Turns" cell; an em-dash is correct.
  const turnsCell = await page.evaluate(() => {
    const strip = document.querySelector('[data-testid="trace-vitals-strip"]')
    const cell = [...(strip?.children || [])]
      .find(c => c.querySelector('dt')?.textContent.trim() === 'Turns')
    return cell?.querySelector('dd')?.textContent.trim() ?? null
  })
  expect(turnsCell, 'vitals "Turns" counted turn_usage rows').toBe('—')
})
