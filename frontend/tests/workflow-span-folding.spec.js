/**
 * Workflow span folding (collapse-by-default + drill-in):
 *
 *  - Run view: a captured workflow run with many agents folds every agent's
 *    subtree by default — the agent header rows stay, their turns/tools are
 *    hidden until the chevron expands one. "expand all" opens them at once.
 *  - Parent session: the launching session's `tool.Workflow` row renders a
 *    rich collapsed summary (agent count + status) sourced from the enriched
 *    /workflow-runs endpoint, without containing any of the run's detail spans.
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
                  tool_calls: tools, agent_type: 'workflow-subagent', is_test: true },
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
// run. Holds NO workflow detail spans — the summary is computed server-side.
function parentSpans(runId) {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`
  return {
    traceId,
    spans: [
      { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
        start_time: T, attributes: { text: 'kick off a review workflow', is_test: true } },
      { trace_id: traceId, span_id: `wf-${traceId.slice(0, 8)}`, parent_id: prompt,
        name: 'tool.Workflow', start_time: T,
        attributes: { workflow_name: 'review-diff', workflow_run_id: runId, is_test: true } },
    ],
  }
}

test('run view folds agent subtrees by default and drills in on expand', async ({ page }) => {
  const { runId, spans } = runSpans()
  expect((await page.request.post('/api/session-spans', { data: spans })).ok()).toBeTruthy()

  await page.addInitScript(() => {
    localStorage.setItem('regin_session_view_mode', 'conversation')
  })
  await page.goto(`/trace/sessions/${runId}`)

  // Count a turn marker only inside the spine's in-agent rows (the
  // `border-pink-300` rail) — that's exactly what folding controls. The
  // right-hand detail sidebar also echoes a selected span's text, which is
  // unrelated to the fold, so a bare getByText would double-count it.
  const inAgentTurn = (text) =>
    page.locator('.border-pink-300', { hasText: text })

  // The agent headers render (projection ran).
  await expect(page.getByText('find:bugs').first()).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('find:perf').first()).toBeVisible()

  // expand-all forces the subtrees to load + render, so a later count(0)
  // means "folded away", not "not yet fetched" (avoids the deep-load race).
  await page.getByRole('button', { name: 'expand all' }).click()
  await expect(inAgentTurn('BUGS_AGENT_THINKING_MARKER')).toHaveCount(1)
  await expect(inAgentTurn('PERF_AGENT_THINKING_MARKER')).toHaveCount(1)

  // collapse-all hides every agent's turns — the headers stay.
  await page.getByRole('button', { name: 'collapse all' }).click()
  await expect(page.getByText('find:bugs').first()).toBeVisible()
  await expect(inAgentTurn('BUGS_AGENT_THINKING_MARKER')).toHaveCount(0)
  await expect(inAgentTurn('PERF_AGENT_THINKING_MARKER')).toHaveCount(0)

  // Drill into just the first agent → its turn returns; the other stays folded.
  await page.locator('[title="Expand agent"]').first().click()
  await expect(inAgentTurn('BUGS_AGENT_THINKING_MARKER')).toHaveCount(1)
  await expect(inAgentTurn('PERF_AGENT_THINKING_MARKER')).toHaveCount(0)

  await page.screenshot({ path: '/tmp/wf-fold-run.png', fullPage: false })
})

// A regular interactive session (no workflow.phase, no run_id) that fanned out
// `tool.Agent` subagents whose internal turns WERE captured — the same fold
// must apply here, not just to dynamic-workflow runs.
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

test('regular session folds its tool.Agent subagents too', async ({ page }) => {
  const { traceId, spans } = plainSessionWithSubagents()
  expect((await page.request.post('/api/session-spans', { data: spans })).ok()).toBeTruthy()

  await page.addInitScript(() => {
    localStorage.setItem('regin_session_view_mode', 'conversation')
  })
  await page.goto(`/trace/sessions/${traceId}`)

  const inAgentTurn = (text) => page.locator('.border-pink-300', { hasText: text })

  // Agent headers render, but their captured turns are folded by default —
  // this session is NOT a workflow (no phases / run_id), proving the fold is
  // no longer workflow-gated.
  await expect(page.getByText('explore:auth').first()).toBeVisible({ timeout: 10_000 })
  await expect(inAgentTurn('AUTH_SUBAGENT_WORK')).toHaveCount(0)
  await expect(inAgentTurn('DB_SUBAGENT_WORK')).toHaveCount(0)

  // Drill into the first agent → its turn appears; the other stays folded.
  await page.locator('[title="Expand agent"]').first().click()
  await expect(inAgentTurn('AUTH_SUBAGENT_WORK')).toHaveCount(1)
  await expect(inAgentTurn('DB_SUBAGENT_WORK')).toHaveCount(0)
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
  await expect(page.getByText('⚙ Workflow').first()).toBeVisible({ timeout: 10_000 })
  await expect(page.getByText('2 agents').first()).toBeVisible()
  await expect(page.getByRole('link', { name: 'view run →' }).first()).toBeVisible()

  await page.screenshot({ path: '/tmp/wf-fold-parent.png', fullPage: false })
})
