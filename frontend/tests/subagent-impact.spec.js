/**
 * Subagent main-session impact chip.
 *
 * A subagent's result text is injected back into the PARENT context as the
 * `input_tokens` of the parent's `tool.Agent` launch span. The serve-time
 * derivation (`queries.py _attach_subagent_impact`) stamps that count onto the
 * matching `subagent.start` as `main_session_impact_tokens`, and the
 * conversation header renders it as a `+N → main` chip — but only for an
 * UNAMBIGUOUS 1:1 turn (one launch, one subagent) whose launch carried result
 * tokens.
 *
 * Portable: bootstraps its own synthetic spans via `/api/session-spans`
 * (is_test=true), then fills the launch's `input_tokens` through the real
 * tool-attribution path (keyed by tool_use_id, exactly like production). No
 * hardcoded local trace IDs.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const T = '2026-05-01T10:00:00'

// One prompt → one tool.Agent launch (carrying a tool_use_id so the
// attribution UPDATE can find it) → one subagent.start in the same turn.
function oneToOneSession() {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`
  const toolUseId = `toolu_${traceId.slice(0, 12)}`
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: T, attributes: { text: 'delegate to one subagent', is_test: true } },
    { trace_id: traceId, span_id: `ag-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'tool.Agent', start_time: '2026-05-01T10:00:05',
      attributes: { tool_name: 'Agent', tool_use_id: toolUseId, is_test: true } },
    { trace_id: traceId, span_id: `sa-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'subagent.start', start_time: '2026-05-01T10:00:02',
      attributes: { agent_id: `a-${traceId.slice(0, 8)}`, agent_type: 'Explore', is_test: true } },
  ]
  return { traceId, toolUseId, spans }
}

test('subagent header shows main-session impact for a 1:1 turn', async ({ page }) => {
  const { traceId, toolUseId, spans } = oneToOneSession()
  expect((await page.request.post('/api/session-spans', { data: spans })).ok()).toBeTruthy()

  // Land on an app page first so the fixture's addInitScript token is readable
  // from localStorage (about:blank denies localStorage access).
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()

  // Fill the launch's input_tokens via the production attribution path
  // (the column the chip reads is only populated this way, never by span ingest).
  const attrib = await page.request.post('/api/turn-usage/tool-attribution', {
    headers: { Authorization: `Bearer ${token}` },
    data: { trace_id: traceId, turn_uuid: null,
            tool_calls: [{ tool_use_id: toolUseId, name: 'Agent',
                           input_tokens: 4321, output_tokens: 600 }] },
  })
  expect(attrib.ok()).toBeTruthy()

  await page.evaluate(() => {
    localStorage.setItem('regin_session_view_mode', 'conversation')
  })
  await page.goto(`/trace/sessions/${traceId}`)

  // The chip reads "+<formatted> → main"; 4321 formats to "4.3k".
  const chip = page.getByText(/\+[\d.]+k? → main/)
  await expect(chip.first()).toBeVisible({ timeout: 10_000 })
})
