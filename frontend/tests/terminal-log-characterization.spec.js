/**
 * Characterization of SessionTerminalLog's flat-log span label/detail output.
 *
 * The Terminal view renders each span as a row: a "Span" label (2nd cell) and
 * a "Detail" string (3rd cell), produced by the SFC's terminalSpanLabel /
 * terminalSpanDetail formatters. No existing green spec renders this panel, so
 * this spec pins the EXACT label+detail text for a spread of span families —
 * especially the tool.* branches (command_preview / file+lines / Task tools /
 * ToolSearch loaded_tools / pattern) that the formatter decomposition splits
 * into helpers.
 *
 * Portable: bootstraps its own synthetic session (is_test=true) via
 * `/api/session-spans`, then drives the real Terminal view-mode tab.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

test('Terminal view renders the expected span label + detail per span family', async ({ page }) => {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)

  const spans = [
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: '2026-05-03T10:00:00', duration_ms: 0,
      attributes: { text: 'characterize the terminal log', is_test: true } },
    { trace_id: traceId, span_id: `bash-${sfx}`, parent_id: null, name: 'tool.Bash',
      start_time: '2026-05-03T10:00:01', duration_ms: 12,
      attributes: { command_preview: 'ls -la /tmp', is_test: true } },
    { trace_id: traceId, span_id: `read-${sfx}`, parent_id: null, name: 'tool.Read',
      start_time: '2026-05-03T10:00:02', duration_ms: 5,
      attributes: { file_path: '/repo/src/a.js', lines: 42, is_test: true } },
    { trace_id: traceId, span_id: `task-${sfx}`, parent_id: null, name: 'tool.TaskUpdate',
      start_time: '2026-05-03T10:00:03', duration_ms: 3,
      attributes: { task_id: '7', status: 'completed', is_test: true } },
    { trace_id: traceId, span_id: `search-${sfx}`, parent_id: null, name: 'tool.ToolSearch',
      start_time: '2026-05-03T10:00:04', duration_ms: 2,
      attributes: { loaded_tools: ['mcp__gitnexus__query', 'mcp__gitnexus__impact'], is_test: true } },
    { trace_id: traceId, span_id: `grep-${sfx}`, parent_id: null, name: 'tool.Grep',
      start_time: '2026-05-03T10:00:05', duration_ms: 4,
      attributes: { pattern: 'TODO', file_path: '/repo/src/b.js', is_test: true } },
    { trace_id: traceId, span_id: `subs-${sfx}`, parent_id: null, name: 'subagent.start',
      start_time: '2026-05-03T10:00:06', duration_ms: 0,
      attributes: { agent_type: 'code-reviewer', is_test: true } },
    { trace_id: traceId, span_id: `rule-${sfx}`, parent_id: null, name: 'rule.check',
      start_time: '2026-05-03T10:00:07', duration_ms: 1,
      attributes: { rule_id: 'no-bare-except', findings: 0, is_test: true } },
    { trace_id: traceId, span_id: `cpost-${sfx}`, parent_id: null, name: 'compact.post',
      start_time: '2026-05-03T10:00:08', duration_ms: 0,
      attributes: { trigger: 'auto', summary: 'rolled up the context', is_test: true } },
  ]

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  const headers = { Authorization: `Bearer ${token}` }

  expect((await page.request.post('/api/session-spans', { headers, data: spans })).ok()).toBeTruthy()

  // Land directly on the Terminal tab so the flat log mounts.
  await page.evaluate(() => localStorage.setItem('regin_session_view_mode', 'terminal'))
  await page.goto(`/trace/sessions/${traceId}`)
  await page.getByRole('button', { name: 'Terminal', exact: true }).click()

  // Each row is a tr[data-span-id]; the Span label is the medium-weight span
  // in the 2nd cell, the Detail string(s) in the 3rd cell.
  const rowLabel = (spanId) =>
    page.locator(`tr[data-span-id="${spanId}"] td:nth-child(2) .font-medium`)
  const rowDetail = (spanId) =>
    page.locator(`tr[data-span-id="${spanId}"] td:nth-child(3)`)

  // Wait for the synthetic session to render.
  await expect(rowLabel(`prompt-${sfx}`)).toBeVisible({ timeout: 10_000 })

  // prompt → label "prompt", detail = the (whitespace-collapsed) text.
  await expect(rowLabel(`prompt-${sfx}`)).toHaveText('prompt')
  await expect(rowDetail(`prompt-${sfx}`)).toContainText('characterize the terminal log')

  // tool.Bash → label "tool.Bash", detail = command_preview verbatim.
  await expect(rowLabel(`bash-${sfx}`)).toHaveText('tool.Bash')
  await expect(rowDetail(`bash-${sfx}`)).toContainText('ls -la /tmp')

  // tool.Read → file tail + line count.
  await expect(rowLabel(`read-${sfx}`)).toHaveText('tool.Read')
  await expect(rowDetail(`read-${sfx}`)).toContainText('a.js (42 lines)')

  // tool.TaskUpdate → "#<id> → <status>".
  await expect(rowLabel(`task-${sfx}`)).toHaveText('tool.TaskUpdate')
  await expect(rowDetail(`task-${sfx}`)).toContainText('#7 → completed')

  // tool.ToolSearch → loaded_tools, last `__` segment, comma-joined.
  await expect(rowLabel(`search-${sfx}`)).toHaveText('tool.ToolSearch')
  await expect(rowDetail(`search-${sfx}`)).toContainText('query, impact')

  // tool.Grep → "<pattern> in <file tail>".
  await expect(rowLabel(`grep-${sfx}`)).toHaveText('tool.Grep')
  await expect(rowDetail(`grep-${sfx}`)).toContainText('TODO in b.js')

  // subagent.start → agent_type.
  await expect(rowLabel(`subs-${sfx}`)).toHaveText('subagent.start')
  await expect(rowDetail(`subs-${sfx}`)).toContainText('code-reviewer')

  // rule.check → "<rule_id> (no findings)" when findings === 0.
  await expect(rowLabel(`rule-${sfx}`)).toHaveText('rule.check')
  await expect(rowDetail(`rule-${sfx}`)).toContainText('no-bare-except (no findings)')

  // compact.post → "[trigger] summary: <text>".
  await expect(rowLabel(`cpost-${sfx}`)).toHaveText('compact.post')
  await expect(rowDetail(`cpost-${sfx}`)).toContainText('[auto] summary: rolled up the context')
})
