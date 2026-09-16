/**
 * A RESUMED subagent in the main feed.
 *
 * A `SendMessage` continuation emits a second `subagent.start` under the SAME
 * agent id, so one agent owns a card per run — while the roster holds one
 * entry keyed on that id, the whole-agent rollup lands on the first marker
 * alone, and the single `tool.Agent` launch is claimed by the first card. The
 * result before `agent_segment_*` was two cards that looked like one span
 * rendered twice: both claiming to run, with the same status and elapsed.
 *
 * These pin that the runs are told apart and that only the newest one is live.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

async function seed(page, { pendingLaunch = false, finished = false, usage = false, turns = false } = {}) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const agentId = `a${p8}deadbeef`
  const prompt = `p-${p8}`
  // Recent, not fixed: the roster stales an unstopped agent on age alone.
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
  const agent = (extra) => ({ agent_type: 'goal-verifier', agent_id: agentId, ...extra })
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'verify the build', is_test: true } },
    T(`toolu-${p8}`, 'tool.Agent', at(1), {
      tool_name: 'Agent', tool_use_id: `toolu_${p8}`, subagent_type: 'goal-verifier',
      description: 'Adversarially verify', prompt: 'Assume it is broken',
    }, pendingLaunch ? { status_code: 'PENDING' } : { status_code: 'OK', duration_ms: 9000 }),
    // Run 1: two spans, then a stop.
    T(`start1-${p8}`, 'subagent.start', at(2),
      agent({ launch_tool_use_id: `toolu_${p8}` })),
    // What each run was ASKED: one agent-scoped prompt per transcript turn.
    ...(turns ? [T(`prompt-sa-${agentId}`, 'prompt', at(2), {
      agent_id: agentId, text: 'Assume it is broken and prove it',
    })] : []),
    T(`a1-${p8}`, 'tool.Bash', at(3), {
      agent_id: agentId, tool_name: 'Bash', command_preview: 'pytest -q',
    }, { end_time: at(5), duration_ms: 2000 }),
    T(`a2-${p8}`, 'tool.Read', at(4), {
      agent_id: agentId, tool_name: 'Read', file_path: '/repo/a.py',
    }, { end_time: at(6), duration_ms: 500 }),
    // The ingest promotes `attributes.output_tokens` to its column only on an
    // assistant row, and `_billed_total` sums the agent's spans when its stop
    // marker carries no transcript total.
    ...(usage ? [T(`a2t-${p8}`, 'assistant_response', at(5), {
      agent_id: agentId, text: 'checking', output_tokens: 1200,
    }, { end_time: at(5) })] : []),
    T(`stop1-${p8}`, 'subagent.stop', at(6), agent({ result_preview: 'found three' })),
    // The resume, and run 2 — still going.
    T(`send-${p8}`, 'tool.SendMessage', at(8), {
      tool_name: 'SendMessage', tool_use_id: `toolu_send_${p8}`,
    }, { duration_ms: 100 }),
    T(`start2-${p8}`, 'subagent.start', at(9), agent({})),
    ...(turns ? [T(`prompt-sa-${agentId}-u2`, 'prompt', at(9), {
      agent_id: agentId, text: 'The coordinator sent a message: re-verify',
    })] : []),
    T(`a3-${p8}`, 'tool.Bash', at(10), {
      agent_id: agentId, tool_name: 'Bash', command_preview: 'radon cc',
    }, { end_time: at(11), duration_ms: 1000 }),
    // The call in flight — excluded from the counts, and what keeps the
    // roster calling this agent alive.
    T(`a4-${p8}`, 'tool.Grep', at(12), {
      agent_id: agentId, tool_name: 'Grep',
    }, finished ? { end_time: at(13), duration_ms: 900 } : { status_code: 'PENDING' }),
  ]
  // `finished` closes run 2 as well: the summary chips are the only source
  // left once the progress line is gone, so that is where they render.
  if (finished) {
    spans.push(T(`stop2-${p8}`, 'subagent.stop', at(14),
      agent({ result_preview: 're-verified' })))
  }
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 20_000 })
  return { first: `start1-${p8}`, second: `start2-${p8}` }
}

test('the two runs of a resumed agent are told apart', async ({ page }) => {
  test.setTimeout(60_000)
  const { first, second } = await seed(page)
  const card = id => page.locator(`.event-spine-row[data-span-id="${id}"]`)

  await expect(card(first).getByTestId('subagent-segment')).toHaveText('run 1/2')
  await expect(card(second).getByTestId('subagent-segment')).toHaveText('run 2/2')
})

test('only the newest run of a resumed agent reads as running', async ({ page }) => {
  test.setTimeout(60_000)
  const { first, second } = await seed(page)
  const card = id => page.locator(`.event-spine-row[data-span-id="${id}"]`)

  await expect(card(second)).toHaveAttribute('data-run-state', 'running')
  await expect(card(first)).not.toHaveAttribute('data-run-state', 'running')
  // The live progress line reads the one roster entry — before the split,
  // both cards drew it with identical text.
  await expect(card(first).getByTestId('subagent-progress')).toHaveCount(0)
  await expect(card(second).getByTestId('subagent-progress')).toHaveCount(1)
})

test('every card of a resumed agent reports its OWN run', async ({ page }) => {
  test.setTimeout(60_000)
  const { first, second } = await seed(page, { finished: true })
  const card = id => page.locator(`.event-spine-row[data-span-id="${id}"]`)

  // 2 + 2 spans across the two runs. The first card carries the whole-agent
  // rollup in its attributes, but must not render it as this run's work.
  await expect(card(first)).toContainText('2 spans')
  await expect(card(first)).not.toContainText('4 spans')
  await expect(card(second)).toContainText('2 spans')
  await expect(card(second)).toContainText('2 tools')
  // The single launch is claimed by run 1, so only it carries the prompt.
  await expect(card(first)).toContainText('task prompt')
  await expect(card(second)).not.toContainText('task prompt')
})

test('agent-wide usage is carried by one card and named for its scope', async ({ page }) => {
  test.setTimeout(60_000)
  // Tokens and spend have no per-run split — the stop marker totals the whole
  // transcript — so they ride the first card with an explicit qualifier
  // instead of reading as that run's bill.
  const { first, second } = await seed(page, { finished: true, usage: true })
  const card = id => page.locator(`.event-spine-row[data-span-id="${id}"]`)

  await expect(card(first).getByTestId('subagent-metric-scope')).toHaveText('all runs')
  await expect(card(second).getByTestId('subagent-metric-tokens')).toHaveCount(0)
  await expect(card(second).getByTestId('subagent-metric-scope')).toHaveCount(0)
})

test('an earlier run stays finished even while its launch is still open', async ({ page }) => {
  test.setTimeout(60_000)
  // A foreground `tool.Agent` stays PENDING for the whole run, and it is
  // folded into the FIRST card — so the run-state fallback would put the
  // agent's liveness back on a segment that already stopped.
  const { first, second } = await seed(page, { pendingLaunch: true })
  const card = id => page.locator(`.event-spine-row[data-span-id="${id}"]`)

  await expect(card(second)).toHaveAttribute('data-run-state', 'running')
  await expect(card(first)).not.toHaveAttribute('data-run-state', 'running')
})

test('a live card shows no figure the progress line already owns', async ({ page }) => {
  test.setTimeout(60_000)
  // `agent_duration_ms` spans the work already RECORDED, so mid-run it lags
  // the clock: the chip read 49s beside a live line reading 1m03s.
  const { first, second } = await seed(page)
  const card = id => page.locator(`.event-spine-row[data-span-id="${id}"]`)

  await expect(card(second).getByTestId('subagent-progress')).toContainText('running')
  await expect(card(second).getByTestId('subagent-metric-duration')).toHaveCount(0)
  // Same for the count: the chip is window-scoped, the live line is
  // session-wide, and mid-run the two are different numbers for one thing.
  await expect(card(second).getByTestId('subagent-metric-spans')).toHaveCount(0)
  // The finished run reports both — nothing is left to contradict them.
  await expect(card(first).getByTestId('subagent-metric-duration')).toHaveCount(1)
  await expect(card(first).getByTestId('subagent-metric-spans')).toHaveCount(1)
})

test('each run shows the turn it was asked and the answer it gave', async ({ page }) => {
  test.setTimeout(60_000)
  // The single `tool.Agent` launch describes the FIRST run only, and the
  // result has always lived on the stop marker the card never read.
  const { first, second } = await seed(page, { finished: true, turns: true })
  const card = id => page.locator(`.event-spine-row[data-span-id="${id}"]`)

  await expect(card(first)).toContainText('Assume it is broken and prove it')
  await expect(card(first)).toContainText('found three')
  await expect(card(second)).toContainText('task prompt')
  await expect(card(second)).toContainText('The coordinator sent a message: re-verify')
  await expect(card(second)).toContainText('re-verified')
  // Each run's own answer, not the other's.
  await expect(card(second)).not.toContainText('found three')
})
