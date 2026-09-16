/**
 * SessionTraceView: the agent pane must not LOSE spans it is already showing.
 *
 * The pane's rows come from the agent's own `sa-<agent_id>` trace
 * (useAgentTrace), re-read on the parent's live poll. That re-read is keyed on
 * roster facts — status, span count, last seen — every one of which freezes the
 * moment the agent finishes. So a refresh that fails, or that loses one of its
 * per-root deep reads, used to be terminal in two ways at once: it replaced the
 * rendered rows (with nothing, or with structure-only copies), and it stamped
 * itself as the loaded answer, so no later poll owed a retry. A reader who had
 * the agent's summary on screen watched it disappear for the rest of the tab's
 * life.
 *
 * Every assertion is scoped to the pane: the agent's summary also rides the
 * main feed's subagent card, so an unscoped `getByText` passes while the pane
 * beside it is empty.
 *
 * Fixtures follow trace-agent-pane.spec.js: synthetic `is_test: true` sessions
 * posted via /api/session-spans, the agent's internals tagged with its
 * `agent_id` so the child trace serves them.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

test.use({ viewport: { width: 1440, height: 900 } })

const INT_FILE = 'src/AGENT_INTERNAL_MARKER.js'
const SUMMARY = 'AGENT_SUMMARY_MARKER — what the agent concluded'
const LATE_FILE = 'src/AGENT_LATE_MARKER'

const pane = (page) => page.locator('[data-testid="trace-agent-pane"]')

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

// One prompt turn with a FINISHED agent: a read, then its closing summary.
async function seedFinishedAgent(page) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  const later = new Date(Date.now() + 2000).toISOString()
  const agId = `ag-run-${sfx}`
  const startId = `substart-${sfx}`
  await post(page, [
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: now, attributes: { text: 'refresh fixture prompt', is_test: true } },
    { trace_id: traceId, span_id: `agent-${sfx}`, parent_id: `prompt-${sfx}`, name: 'tool.Agent',
      start_time: now, attributes: { subagent_type: 'explorer', description: 'Summarize the thing',
        tool_use_id: `tu-${sfx}`, agent_id: agId, is_test: true } },
    { trace_id: traceId, span_id: startId, parent_id: `prompt-${sfx}`, name: 'subagent.start',
      start_time: now, attributes: { agent_type: 'explorer', agent_id: agId, is_test: true } },
    { trace_id: traceId, span_id: `int-read-${sfx}`, parent_id: startId, name: 'tool.Read',
      start_time: later, attributes: { file_path: INT_FILE, agent_id: agId, is_test: true } },
    { trace_id: traceId, span_id: `resp-sa-${sfx}`, parent_id: startId, name: 'assistant_response',
      start_time: later, attributes: { text: SUMMARY, agent_id: agId, is_test: true } },
    { trace_id: traceId, span_id: `substop-${sfx}`, parent_id: `prompt-${sfx}`, name: 'subagent.stop',
      start_time: later, attributes: { agent_type: 'explorer', agent_id: agId,
        result_preview: SUMMARY, is_test: true } },
  ])
  return { traceId, sfx, agId }
}

// A new row for the agent: moves the parent tail AND the agent's roster facts,
// which together are what make the pane ask for its trace again.
async function postLateSpan(page, { traceId, sfx, agId }, n) {
  await post(page, [{
    trace_id: traceId, span_id: `int-late-${sfx}-${n}`, parent_id: `substart-${sfx}`,
    name: 'tool.Read', start_time: new Date().toISOString(),
    attributes: { file_path: `${LATE_FILE}-${n}.js`, agent_id: agId, is_test: true },
  }])
}

async function openScope(page, traceId, agId) {
  await page.goto(`/trace/sessions/${traceId}?agent=${agId}`)
  await expect(pane(page)).toBeVisible({ timeout: 10_000 })
  await expect(pane(page).getByText('AGENT_SUMMARY_MARKER').first()).toBeVisible({ timeout: 10_000 })
}

test('a failed re-read keeps the rows on screen, and the next poll retries', async ({ page }) => {
  const fixture = await seedFinishedAgent(page)
  await openScope(page, fixture.traceId, fixture.agId)

  let fail = true
  await page.route('**/api/sessions/sa-*/**', async (route) => {
    if (fail) return route.abort('connectionfailed')
    return route.continue()
  })

  await postLateSpan(page, fixture, 1)
  // Two poll periods: long enough for the refresh to fire and fail.
  await page.waitForTimeout(9000)
  await expect(pane(page).getByText('AGENT_SUMMARY_MARKER').first()).toBeVisible()
  await expect(page.locator('[data-testid="trace-scope-empty"]')).toHaveCount(0)

  // The failure must not have counted as "loaded": once the server answers
  // again, an ordinary tick picks the missing row up with no reload.
  // A second, distinct row is what moves the tail again — the re-post of one
  // the store already holds changes nothing for the poll to notice.
  fail = false
  await postLateSpan(page, fixture, 2)
  await expect(pane(page).getByText('AGENT_LATE_MARKER').first()).toBeVisible({ timeout: 25_000 })
})

test('a partial re-read does not strip the bodies already rendered', async ({ page }) => {
  const fixture = await seedFinishedAgent(page)
  await openScope(page, fixture.traceId, fixture.agId)
  await expect(pane(page).getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible({ timeout: 10_000 })

  // Only the deep read fails: the map still answers, so the refresh returns a
  // full row list whose bodies are stripped to the map's keep-list.
  let fail = true
  await page.route('**/api/sessions/sa-*/spans/**/children*', async (route) => {
    if (fail) return route.abort('connectionfailed')
    return route.continue()
  })

  await postLateSpan(page, fixture, 1)
  await page.waitForTimeout(9000)
  await expect(pane(page).getByText('AGENT_SUMMARY_MARKER').first()).toBeVisible()
  await expect(pane(page).getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible()

  fail = false
  await postLateSpan(page, fixture, 2)
  await expect(pane(page).getByText('AGENT_LATE_MARKER').first()).toBeVisible({ timeout: 25_000 })
})

test('a 0-span answer does not count as the truth about a run that has rows', async ({ page }) => {
  const fixture = await seedFinishedAgent(page)
  await openScope(page, fixture.traceId, fixture.agId)

  // The shape an unresolved `sa-<id>` answers with: 200, no rows. The store is
  // append-only, so this can only be a race — never "the run has nothing".
  let starve = true
  await page.route('**/api/sessions/sa-*/map*', async (route) => {
    if (starve) return route.fulfill({ status: 200, contentType: 'application/json',
      body: JSON.stringify({ spans: [] }) })
    return route.continue()
  })

  await postLateSpan(page, fixture, 1)
  await page.waitForTimeout(9000)
  await expect(pane(page).getByText('AGENT_SUMMARY_MARKER').first()).toBeVisible()

  starve = false
  await postLateSpan(page, fixture, 2)
  await expect(pane(page).getByText('AGENT_LATE_MARKER').first()).toBeVisible({ timeout: 25_000 })
})
