/**
 * Every subagent card carries its OWN brief — including agents an agent
 * launched.
 *
 * A nested agent's `tool.Agent` is the launching agent's delegated work, so
 * the parent trace withholds it; only the top-level agent's launch survives in
 * the feed. With the brief read solely off that span, the first card showed a
 * description and a task prompt and every card after it showed neither.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

async function seed(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const parentAgent = `a${p8}aaaaaaaa`
  const nestedAgent = `a${p8}bbbbbbbb`
  const at = (i) => new Date(Date.now() - 60_000 + i * 1000).toISOString().slice(0, 19)
  const spans = [
    { trace_id: traceId, span_id: `p-${p8}`, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'go', is_test: true } },
    { trace_id: traceId, span_id: `launch-a-${p8}`, parent_id: `p-${p8}`, name: 'tool.Agent',
      start_time: at(1), status_code: 'OK',
      attributes: { tool_name: 'Agent', tool_use_id: `toolu_a_${p8}`,
        subagent_type: 'general-purpose', description: 'Survey the docs',
        prompt: 'Survey every doc under docs/ and report back', is_test: true } },
    { trace_id: traceId, span_id: `start-a-${p8}`, parent_id: `p-${p8}`, name: 'subagent.start',
      start_time: at(2),
      attributes: { agent_type: 'general-purpose', agent_id: parentAgent,
        launch_tool_use_id: `toolu_a_${p8}`, is_test: true } },
    // Emitted BY the first agent: withheld from this trace's feed.
    { trace_id: traceId, span_id: `launch-b-${p8}`, parent_id: `start-a-${p8}`, name: 'tool.Agent',
      start_time: at(3), status_code: 'OK',
      attributes: { tool_name: 'Agent', tool_use_id: `toolu_b_${p8}`,
        agent_id: parentAgent, subagent_type: 'general-purpose',
        description: 'Check the changelog',
        prompt: 'Read CHANGELOG.md and list every entry since v2', is_test: true } },
    { trace_id: traceId, span_id: `start-b-${p8}`, parent_id: `start-a-${p8}`, name: 'subagent.start',
      start_time: at(4),
      attributes: { agent_type: 'general-purpose', agent_id: nestedAgent,
        parent_agent_id: parentAgent, launch_tool_use_id: `toolu_b_${p8}`, is_test: true } },
    { trace_id: traceId, span_id: `stop-b-${p8}`, parent_id: `start-b-${p8}`, name: 'subagent.stop',
      start_time: at(5),
      attributes: { agent_id: nestedAgent, result_preview: 'nested done', is_test: true } },
    { trace_id: traceId, span_id: `stop-a-${p8}`, parent_id: `start-a-${p8}`, name: 'subagent.stop',
      start_time: at(6),
      attributes: { agent_id: parentAgent, result_preview: 'done', is_test: true } },
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return { first: `start-a-${p8}`, nested: `start-b-${p8}` }
}

test('a nested agent card shows its own brief, not an empty one', async ({ page }) => {
  test.setTimeout(60_000)
  const { first, nested } = await seed(page)

  const firstCard = page.locator(`.event-spine-row[data-span-id="${first}"]`)
  await expect(firstCard).toContainText('Survey the docs')
  await expect(firstCard).toContainText('Survey every doc under docs/')

  const nestedCard = page.locator(`.event-spine-row[data-span-id="${nested}"]`)
  await expect(nestedCard).toContainText('Check the changelog')
  await expect(nestedCard).toContainText('Read CHANGELOG.md and list every entry')
  // Each card keeps the brief of ITS OWN launch.
  await expect(nestedCard).not.toContainText('Survey every doc under docs/')
})
