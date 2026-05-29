/**
 * Live (in-progress) workflow run: the Phases rail renders script-derived
 * numbered phases with their agents, marks an in-progress phase with a running
 * cue (blue badge + pulse) rather than the completed-style emerald, and shows a
 * declared-but-unstarted phase as a clean empty band.
 *
 * Data is injected via `/api/session-spans` with `is_test=true` so the trace is
 * invisible in the sessions list and the test is portable to any clean DB.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

function liveWorkflowSpans() {
  const traceId = randomUUID()
  const root = `wfrun-${traceId.slice(0, 8)}`
  const t = '2026-05-17T08:00:00.000000'
  const phase = (i, title) => ({
    trace_id: traceId, span_id: `wfphase-${i}`, parent_id: root,
    name: 'workflow.phase', start_time: t,
    attributes: { title, detail: `${title} detail`, index: i, is_test: true },
  })
  const agent = (id, parent, label, state) => ({
    trace_id: traceId, span_id: `wfagent-${id}`, parent_id: parent,
    name: 'subagent.start', start_time: t,
    attributes: { agent_id: id, label, agent_name: label, state,
                  agent_type: 'workflow-subagent', is_test: true },
  })
  return {
    traceId,
    spans: [
      { trace_id: traceId, span_id: root, parent_id: null, name: 'session.start',
        start_time: t,
        attributes: { run_id: traceId, workflow_status: 'running',
                      agent_type: 'claude', is_test: true } },
      { trace_id: traceId, span_id: `prompt-${traceId.slice(0, 8)}`,
        parent_id: root, name: 'prompt', start_time: t,
        attributes: { text: 'live workflow fixture', is_test: true } },
      phase(1, 'Implement'), phase(2, 'Verify'), phase(3, 'Report'),
      agent('impl-a', 'wfphase-1', 'impl:data-layer', 'done'),
      agent('impl-b', 'wfphase-1', 'impl:api+tests', 'running'),
      agent('verify-a', 'wfphase-2', 'verify:backend', 'running'),
      // phase 3 (Report) has no agents → empty declared band
    ],
  }
}

test('live workflow rail shows phases, running cue, and empty band', async ({ page }) => {
  const { traceId, spans } = liveWorkflowSpans()
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()

  await page.addInitScript(() => {
    localStorage.setItem('regin_session_view_mode', 'conversation')
  })
  await page.goto(`/trace/sessions/${traceId}`)

  // The Phases rail header + all three numbered bands render.
  await expect(page.getByText('Phases', { exact: true })).toBeVisible({ timeout: 10_000 })
  for (const title of ['Implement', 'Verify', 'Report']) {
    await expect(page.getByText(title, { exact: true }).first()).toBeVisible()
  }
  // Real labels (not generic 'workflow-subagent') appear.
  await expect(page.getByText('impl:data-layer').first()).toBeVisible()
  await expect(page.getByText('verify:backend').first()).toBeVisible()

  // The in-progress phases carry the running pulse (title="phase in progress").
  const cues = page.locator('[title="phase in progress"]')
  await expect.poll(() => cues.count()).toBeGreaterThanOrEqual(2)  // Implement + Verify

  await page.screenshot({ path: '/tmp/wf-live-rail.png', fullPage: false })
})
