/**
 * An agent's launch prompt is INPUT text, not model-authored markdown.
 *
 * Rendered through the markdown pipeline, a brief full of indented lists,
 * paths and `**`/`_` inside snippets lost its indentation and had its marks
 * eaten. Both surfaces that show a brief — the feed's task-prompt card and the
 * presentation reader — must show it literally.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

// Longer than AGENT_PROMPT_PREVIEW_CHARS (140) so the card offers its
// "Show full prompt" expansion — the state that used to render markdown.
const PROMPT = [
  'Fix the **bold** marker handling in the reader and in the feed card.',
  '',
  'Steps:',
  '  1. read lib/a_*.py and lib/b_*.py, then the two render paths behind them',
  '  2. keep the indentation exactly as written, it is load-bearing here',
].join('\n')

async function seed(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const agentId = `a${p8}feedface`
  const at = (i) => new Date(Date.now() - 60_000 + i * 1000).toISOString().slice(0, 19)
  const spans = [
    { trace_id: traceId, span_id: `p-${p8}`, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'go', is_test: true } },
    { trace_id: traceId, span_id: `toolu-${p8}`, parent_id: `p-${p8}`, name: 'tool.Agent',
      start_time: at(1), status_code: 'OK', duration_ms: 900,
      attributes: { tool_name: 'Agent', tool_use_id: `toolu_${p8}`, subagent_type: 'claude',
        description: 'Fix markers', prompt: PROMPT, is_test: true } },
    { trace_id: traceId, span_id: `start-${p8}`, parent_id: `p-${p8}`, name: 'subagent.start',
      start_time: at(2),
      attributes: { agent_type: 'claude', agent_id: agentId,
        launch_tool_use_id: `toolu_${p8}`, is_test: true } },
    { trace_id: traceId, span_id: `stop-${p8}`, parent_id: `p-${p8}`, name: 'subagent.stop',
      start_time: at(3), attributes: { agent_id: agentId, result_preview: 'done', is_test: true } },
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return { startId: `start-${p8}` }
}

test('the feed renders an agent brief verbatim, marks and indentation intact', async ({ page }) => {
  test.setTimeout(60_000)
  const { startId } = await seed(page)
  const card = page.locator(`.event-spine-row[data-span-id="${startId}"]`)

  await card.getByRole('button', { name: /Show full prompt/ }).click()
  await expect(card).toContainText('**bold**')
  await expect(card).toContainText('  1. read lib/a_*.py')
  await expect(card.locator('strong')).toHaveCount(0)
})

test('the reader shows an agent brief in a verbatim well, not as markdown', async ({ page }) => {
  test.setTimeout(60_000)
  const { startId } = await seed(page)
  const card = page.locator(`.event-spine-row[data-span-id="${startId}"]`)

  await card.locator('[data-testid="trace-agent-view"], div').first().click()
  await page.keyboard.press('p')
  const reader = page.locator('.present-verbatim')
  await expect(reader.first()).toContainText('**bold**')
  await expect(page.locator('.present-prose strong')).toHaveCount(0)
})
