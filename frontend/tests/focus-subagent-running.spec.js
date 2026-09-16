/**
 * A running subagent in the main feed, under Focus.
 *
 * The parent's only handle on a delegated run is the subagent card: its
 * `tool.Agent` placeholder is folded into that card, and the marker span is
 * never PENDING, so nothing on the row itself says the agent is alive. The
 * roster does. These pin that the card reads as running off the roster, that
 * Focus keeps the card on the spine with the strip folded, and that a row the
 * server tagged with the agent's id never renders in the parent.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

async function seed(page, { stopped, answered = false }) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const agentId = `a${p8}deadbeef`
  const prompt = `p-${p8}`
  // Recent, not fixed: the roster stales an unstopped agent on age alone, so
  // a run seeded months in the past would read as stale, never running.
  const base = Date.now() - 60_000
  const at = (i) => {
    const d = new Date(base + i * 1000)
    const p = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
      + `T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  }
  const T = (span_id, name, start_time, attributes, extra = {}) => ({
    trace_id: traceId, span_id, parent_id: prompt, name, start_time,
    attributes: { ...attributes, is_test: true }, ...extra,
  })
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'delegate the build', is_test: true } },
    T(`s1-${p8}`, 'tool.Edit', at(1), { tool_name: 'Edit', file_path: '/repo/a.py', diff: '+ x' }, { duration_ms: 200 }),
    // The launch: PENDING while the agent runs, and folded into the card.
    T(`pending-toolu_${p8}`, 'tool.Agent', at(2), {
      tool_name: 'Agent', tool_use_id: `toolu_${p8}`, subagent_type: 'goal-builder',
      description: 'Build the thing', prompt: 'Build it end to end',
    }, stopped ? { status_code: 'OK', duration_ms: 9000 } : { status_code: 'PENDING' }),
    T(`start-${p8}`, 'subagent.start', at(3), {
      agent_type: 'goal-builder', agent_id: agentId, launch_tool_use_id: `toolu_${p8}`,
    }),
    // The agent's own work: served by its trace, never by the parent's feed.
    T(`ab-${p8}`, 'tool.Bash', at(4), {
      agent_id: agentId, tool_name: 'Bash', command: 'pytest -q', command_preview: 'pytest -q',
    }, stopped ? { duration_ms: 4000 } : { status_code: 'PENDING' }),
  ]
  // The agent's answer as it was written — the stop marker reports a
  // whitespace-flattened 200-char cut of this, which is markdown SOURCE.
  if (answered) {
    spans.push(T(`ar-${p8}`, 'assistant_response', at(8), {
      agent_id: agentId, text: '## Verdict\n\n- **PASS** the flake is fixed',
    }))
  }
  if (stopped) {
    spans.push(T(`stop-${p8}`, 'subagent.stop', at(9), {
      agent_type: 'goal-builder', agent_id: agentId,
      result_preview: answered
        ? '## Verdict - **PASS** the flake is fixed'
        : 'Fixed the flake in trace_merge and re-ran the suite',
    }))
  }
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return { traceId, agentId, startId: `start-${p8}`, stopId: `stop-${p8}`, bashId: `ab-${p8}` }
}

test('a running subagent reads as running on its card, and Focus keeps the card', async ({ page }) => {
  test.setTimeout(60_000)
  const { startId, bashId } = await seed(page, { stopped: false })
  const card = page.locator(`.event-spine-row[data-span-id="${startId}"]`)

  await expect(card).toHaveAttribute('data-run-state', 'running')
  await expect(card.getByTestId('span-running-tag')).toHaveText(/running/)
  await expect(card.getByTestId('subagent-progress')).toContainText(/running/)
  await expect(card.getByTestId('subagent-progress')).toContainText('pytest -q')
  await expect(card.getByTestId('trace-agent-view')).toBeVisible()
  // The agent's Bash is its own trace's; the parent never renders it.
  await expect(page.locator(`.event-spine-row[data-span-id="${bashId}"]`)).toHaveCount(0)

  await page.getByTestId('latest-toggle').click()
  // Focus folds the edit behind the strip, but the agent card stays out with
  // its state and its Agent view button — with the strip CLOSED.
  await expect(page.getByTestId('fold-strip')).toHaveCount(1)
  await expect(card).toBeVisible()
  await expect(card).toHaveAttribute('data-run-state', 'running')
  await expect(card.getByTestId('trace-agent-view')).toBeVisible()
})

test('a finished subagent carries no run state and no progress line', async ({ page }) => {
  test.setTimeout(60_000)
  const { startId } = await seed(page, { stopped: true })
  const card = page.locator(`.event-spine-row[data-span-id="${startId}"]`)
  await expect(card).toBeVisible()
  await expect(card).not.toHaveAttribute('data-run-state', /.+/)
  await expect(card.getByTestId('span-running-tag')).toHaveCount(0)
  await expect(card.getByTestId('subagent-progress')).toHaveCount(0)
  await expect(page.locator('[data-testid="span-running-tag"]')).toHaveCount(0)
})

test('a finished subagent shows the answer it reported on its own card', async ({ page }) => {
  test.setTimeout(60_000)
  const { startId } = await seed(page, { stopped: true })
  // Scoped to the card: the same result text can reach the page through the
  // companion pane too, and an unscoped match would go green while the card
  // itself is bare.
  const card = page.locator(`.event-spine-row[data-span-id="${startId}"]`)
  await expect(card.getByTestId('agent-result'))
    .toContainText('Fixed the flake in trace_merge')
})

test("a finished subagent's answer renders as markdown, not as source", async ({ page }) => {
  test.setTimeout(60_000)
  const { startId } = await seed(page, { stopped: true, answered: true })
  const result = page.locator(`.event-spine-row[data-span-id="${startId}"]`)
    .getByTestId('agent-result')
  await expect(result.locator('h2')).toHaveText('Verdict')
  await expect(result.locator('li strong')).toHaveText('PASS')
  await expect(result).not.toContainText('## Verdict')
})

test("the reader opens a finished subagent on its report, not on its attributes", async ({ page }) => {
  test.setTimeout(60_000)
  const { startId } = await seed(page, { stopped: true, answered: true })
  // Double-click the row is one of the three routes into the reader; the card
  // itself carries no ⤢ of its own.
  await page.locator(`.event-spine-row[data-span-id="${startId}"]`)
    .dblclick({ position: { x: 200, y: 8 } })
  const reader = page.locator('[role="dialog"]')
  await expect(reader).toContainText('Agent result')
  await expect(reader.locator('.pattern-content h2')).toHaveText('Verdict')
  // The body key leaves the attributes table — the answer was a raw key/value
  // row there, which is what "shows it in the presentation card" hit.
  await expect(reader).not.toContainText('agent_result_preview')
})


test("the stop that reported the answer does not print it a second time", async ({ page }) => {
  test.setTimeout(60_000)
  const { startId, stopId } = await seed(page, { stopped: true, answered: true })
  await expect(page.locator(`.event-spine-row[data-span-id="${startId}"]`)
    .getByTestId('agent-result')).toContainText('PASS')
  // The "Subagent done" row carried the same answer as flattened source,
  // directly under the card that renders it.
  await expect(page.locator(`.event-spine-row[data-span-id="${stopId}"]`)).toHaveCount(0)
  await expect(page.locator('.event-spine').getByText('Subagent done')).toHaveCount(0)
})
