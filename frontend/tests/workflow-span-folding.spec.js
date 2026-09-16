/**
 * Workflow spans in the conversation spine:
 *
 *  - Run view: a captured workflow run reads straight through — every agent's
 *    turns render inline under its header, closed by the agent's result card.
 *    There is no per-agent fold: a subagent's work is either in this trace (a
 *    workflow run's is) or served by `sa-<agent_id>`, so a chevron would only
 *    ever hide rows the reader came for.
 *  - Parent session: the launching session's `tool.Workflow` row renders a
 *    rich collapsed summary (agent count + status) sourced from the enriched
 *    /workflow-runs endpoint, without containing any of the run's detail spans.
 *    THAT fold stays — it hides a whole other trace's markers, not work.
 *
 * Both traces are injected via `/api/session-spans` with `is_test=true`, so
 * they stay out of the sessions list and the test is portable to any clean DB.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

const T = '2026-05-18T09:00:00.000000'

// A completed run: 1 phase, 2 agents, each with a couple of turn/tool spans
// (the "wall" that folding hides). run_id == trace_id, matching ingest.
function runSpans() {
  const runId = randomUUID()
  const root = `wfrun-${runId.slice(0, 8)}`
  const agent = (id, label, tokens, tools) => ({
    trace_id: runId, span_id: `wfagent-${id}`, parent_id: 'wfphase-1',
    name: 'subagent.start', start_time: T,
    attributes: { agent_id: id, label, state: 'done', tokens,
                  tool_calls: tools, agent_type: 'workflow-subagent',
                  // A workflow agent carries its result on the marker — the
                  // spine projects it as a trailing `workflow.agent_result`.
                  result_preview: `${id.toUpperCase()}_AGENT_RESULT_MARKER`,
                  is_test: true },
  })
  const turn = (id, parent, text) => ({
    trace_id: runId, span_id: id, parent_id: parent,
    name: 'assistant_response', start_time: T,
    attributes: { text, is_test: true },
  })
  return {
    runId,
    spans: [
      { trace_id: runId, span_id: root, parent_id: null, name: 'session.start',
        start_time: T,
        attributes: { run_id: runId, workflow_status: 'completed',
                      workflow_name: 'review-diff', agent_type: 'claude', is_test: true } },
      { trace_id: runId, span_id: `prompt-${runId.slice(0, 8)}`, parent_id: root,
        name: 'prompt', start_time: T,
        attributes: { text: 'review the diff', is_test: true } },
      { trace_id: runId, span_id: 'wfphase-1', parent_id: root, name: 'workflow.phase',
        start_time: T, attributes: { title: 'Find', detail: 'find bugs', index: 1, is_test: true } },
      agent('bugs', 'find:bugs', 12000, 3),
      turn('wfturn-bugs-1', 'wfagent-bugs', 'BUGS_AGENT_THINKING_MARKER'),
      { trace_id: runId, span_id: 'wftool-bugs-1', parent_id: 'wfagent-bugs',
        name: 'tool.Read', start_time: T,
        attributes: { tool_name: 'Read', file_path: 'lib/orm/engine.py', is_test: true } },
      agent('perf', 'find:perf', 9000, 2),
      turn('wfturn-perf-1', 'wfagent-perf', 'PERF_AGENT_THINKING_MARKER'),
    ],
  }
}

// The launching session: a prompt with a `tool.Workflow` call pointing at the
// run, plus the workflow runtime's `subagent.start` markers — Claude Code fires
// SubagentStart into the launching session for every workflow agent, and ingest
// re-parents those markers under the tool.Workflow span. The conversation view
// folds them behind the workflow card (the run's full detail lives in its own
// wf_ session). The per-tool/per-turn activity is intentionally absent: the
// trace hook gate drops it for agent_type='workflow-subagent'.
function parentSpans(runId) {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`
  const wfSpan = `wf-${traceId.slice(0, 8)}`
  const marker = (id) => ({
    trace_id: traceId, span_id: `sa-${id}`, parent_id: wfSpan,
    name: 'subagent.start', start_time: T,
    attributes: { agent_id: id, agent_type: 'workflow-subagent', is_test: true },
  })
  return {
    traceId,
    spans: [
      { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
        start_time: T, attributes: { text: 'kick off a review workflow', is_test: true } },
      { trace_id: traceId, span_id: wfSpan, parent_id: prompt,
        name: 'tool.Workflow', start_time: T,
        attributes: { workflow_name: 'review-diff', workflow_run_id: runId, is_test: true } },
      marker('mk-a'),
      marker('mk-b'),
    ],
  }
}

test('run view reads through: every agent\'s turns inline, closed by its result card', async ({ page }) => {
  const { runId, spans } = runSpans()
  expect((await page.request.post('/api/session-spans', { data: spans })).ok()).toBeTruthy()

  await page.addInitScript(() => {
    localStorage.setItem('regin_session_view_mode', 'conversation')
  })
  await page.goto(`/trace/sessions/${runId}`)

  // Count a turn marker only inside the spine's in-agent rows (the nested
  // rail, keyed by testid not colour): the right-hand detail sidebar also
  // echoes a selected span's text, and a bare getByText would double-count it.
  const inAgentTurn = (text) =>
    page.getByTestId('in-agent-row').filter({ hasText: text })

  // The agent headers render (projection ran)…
  await expect(page.getByText('find:bugs').first()).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('find:perf').first()).toBeVisible()

  // …and so do their turns, with nothing to expand first.
  await expect(inAgentTurn('BUGS_AGENT_THINKING_MARKER')).toHaveCount(1)
  await expect(inAgentTurn('PERF_AGENT_THINKING_MARKER')).toHaveCount(1)

  // The synthetic `workflow.agent_result` row closes each agent — it is only
  // reachable because nothing collapses an agent's run any more.
  await expect(page.getByText('BUGS_AGENT_RESULT_MARKER').first())
    .toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('PERF_AGENT_RESULT_MARKER').first()).toBeVisible()

  // No per-agent fold survives: no chevron, no expand/collapse all.
  await expect(page.locator('[title="Expand agent"], [title="Collapse agent"]')).toHaveCount(0)
  await expect(page.getByRole('button', { name: /expand all|collapse all/ })).toHaveCount(0)

  await page.screenshot({ path: '/tmp/wf-fold-run.png', fullPage: false })
})

// A regular interactive session (no workflow.phase, no run_id) that fanned out
// `tool.Agent` subagents whose internal turns WERE captured into this trace
// (rows predating the agent_id tagging that now routes them to `sa-…`).
function plainSessionWithSubagents() {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`
  const agent = (id, label) => [
    { trace_id: traceId, span_id: `sa-${id}`, parent_id: prompt, name: 'subagent.start',
      start_time: T,
      attributes: { agent_id: id, agent_type: 'Explore', label, is_test: true } },
    { trace_id: traceId, span_id: `sa-${id}-t`, parent_id: `sa-${id}`, name: 'assistant_response',
      start_time: T, attributes: { text: `${label.toUpperCase()}_SUBAGENT_WORK`, is_test: true } },
  ]
  return {
    traceId,
    spans: [
      { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt', start_time: T,
        attributes: { text: 'explore the codebase in parallel', is_test: true } },
      ...agent('one', 'explore:auth'),
      ...agent('two', 'explore:db'),
    ],
  }
}

test('regular session shows a subagent\'s in-trace turns inline, with no fold', async ({ page }) => {
  const { traceId, spans } = plainSessionWithSubagents()
  expect((await page.request.post('/api/session-spans', { data: spans })).ok()).toBeTruthy()

  await page.addInitScript(() => {
    localStorage.setItem('regin_session_view_mode', 'conversation')
  })
  await page.goto(`/trace/sessions/${traceId}`)

  const inAgentTurn = (text) => page.getByTestId('in-agent-row').filter({ hasText: text })

  // Headers AND turns, for both agents at once — this session is NOT a
  // workflow (no phases / run_id), so it proves the read-through is general.
  await expect(page.getByText('explore:auth').first()).toBeVisible({ timeout: 10_000 })
  await expect(inAgentTurn('AUTH_SUBAGENT_WORK')).toHaveCount(1)
  await expect(inAgentTurn('DB_SUBAGENT_WORK')).toHaveCount(1)

  // Nothing to expand — and the rows keep their in-agent rail either way.
  await expect(page.locator('[title="Expand agent"], [title="Collapse agent"]')).toHaveCount(0)
})

test('parent session shows a rich collapsed workflow summary node', async ({ page }) => {
  const { runId, spans } = runSpans()
  expect((await page.request.post('/api/session-spans', { data: spans })).ok()).toBeTruthy()
  const { traceId, spans: pSpans } = parentSpans(runId)
  expect((await page.request.post('/api/session-spans', { data: pSpans })).ok()).toBeTruthy()

  await page.addInitScript(() => {
    localStorage.setItem('regin_session_view_mode', 'conversation')
  })
  await page.goto(`/trace/sessions/${traceId}`)

  // The inline tool.Workflow row carries the run name, the computed agent
  // count from /workflow-runs, and a link to drill into the run.
  await expect(page.getByTestId('workflow-launch-row').first()).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('2 agents').first()).toBeVisible()
  await expect(page.getByRole('link', { name: 'view run →' }).first()).toBeVisible()

  await page.screenshot({ path: '/tmp/wf-fold-parent.png', fullPage: false })
})

test('parent session folds workflow subagent markers behind the card', async ({ page }) => {
  const { runId, spans } = runSpans()
  expect((await page.request.post('/api/session-spans', { data: spans })).ok()).toBeTruthy()
  const { traceId, spans: pSpans } = parentSpans(runId)
  expect((await page.request.post('/api/session-spans', { data: pSpans })).ok()).toBeTruthy()

  await page.addInitScript(() => {
    localStorage.setItem('regin_session_view_mode', 'conversation')
  })
  await page.goto(`/trace/sessions/${traceId}`)

  await expect(page.getByTestId('workflow-launch-row').first()).toBeVisible({ timeout: 10_000 })
  // The two re-parented markers are folded away by default (not in the spine).
  await expect(page.getByText(/workflow-subagent/)).toHaveCount(0)
  // The fold chevron reports the marker count and reveals them on click.
  const chevron = page.getByTitle(/Expand 2 agent markers/)
  await expect(chevron).toBeVisible()
  await chevron.click()
  await expect(page.getByText(/workflow-subagent/)).toHaveCount(2)
})
