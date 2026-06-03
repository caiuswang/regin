/**
 * "Tokens by tool" drill-down: expand a tool to see which specific files /
 * commands cost the most tokens.
 *
 * The serve-time rollup (`queries.py fetch_tool_token_rollup` +
 * `_tool_target_breakdown`) hangs a per-tool target list off each rollup row,
 * built from the tool span's `input_tokens` + its `file_path` (Read/Edit) or
 * `tool_input.command` (Bash). `ToolTokenRollup.vue` renders each tool as an
 * expandable row revealing those targets ranked by tokens.
 *
 * Portable: seeds its own tool spans (is_test=true) via /api/session-spans,
 * then fills input_tokens through the real tool-attribution path (keyed by
 * tool_use_id, exactly like production). No hardcoded local trace IDs.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

test('tokens-by-tool drill-down ranks a tool\'s files by token cost', async ({ page }) => {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`
  const tu1 = `tuA-${traceId.slice(0, 12)}`
  const tu2 = `tuB-${traceId.slice(0, 12)}`
  const rd1 = `rd1-${traceId.slice(0, 8)}`  // the pricier read (5000) — the jump target
  const rd2 = `rd2-${traceId.slice(0, 8)}`

  // Two reads of the SAME file (so the drill-down aggregates to "2×"), plus a
  // prompt so the session renders.
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: '2026-05-04T10:00:00', attributes: { text: 'drilldown demo', is_test: true } },
    { trace_id: traceId, span_id: rd1, parent_id: prompt, name: 'tool.Read',
      start_time: '2026-05-04T10:00:01',
      attributes: { tool_name: 'Read', file_path: '/repo/huge_module.py', tool_use_id: tu1, is_test: true } },
    { trace_id: traceId, span_id: rd2, parent_id: prompt, name: 'tool.Read',
      start_time: '2026-05-04T10:00:02',
      attributes: { tool_name: 'Read', file_path: '/repo/huge_module.py', tool_use_id: tu2, is_test: true } },
  ]

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  const headers = { Authorization: `Bearer ${token}` }

  expect((await page.request.post('/api/session-spans', { headers, data: spans })).ok()).toBeTruthy()
  // input_tokens lands only via the production attribution path (keyed by tool_use_id).
  const attrib = await page.request.post('/api/turn-usage/tool-attribution', {
    headers,
    data: { trace_id: traceId, turn_uuid: null, tool_calls: [
      { tool_use_id: tu1, name: 'Read', input_tokens: 5000, output_tokens: 0 },
      { tool_use_id: tu2, name: 'Read', input_tokens: 3000, output_tokens: 0 },
    ] },
  })
  expect(attrib.ok()).toBeTruthy()

  await page.evaluate(() => localStorage.setItem('regin_session_view_mode', 'conversation'))
  await page.goto(`/trace/sessions/${traceId}`)

  // Expand the rollup, then the Read tool row, then assert the file drill-down.
  await page.getByRole('button', { name: /Tokens by tool/ }).click()
  await page.getByRole('button', { name: /Read/ }).first().click()

  // huge_module.py read 2× for 5000+3000 = 8000 — the drill-down target is a
  // button labelled with the file (first match: the rollup sits above the
  // conversation, so .first() is the drill-down row, not a span card).
  const target = page.getByRole('button', { name: /huge_module\.py/ }).first()
  await expect(target).toBeVisible({ timeout: 10_000 })

  // Jump: clicking the target selects its most expensive call (rd1: 5000 >
  // rd2: 3000); the span-details panel then shows that span's id.
  await target.click()
  await expect(page.getByText(rd1)).toBeVisible({ timeout: 10_000 })

  await page.screenshot({ path: '/tmp/regin-verify/tool-rollup-drilldown.png', fullPage: true })
})
