/**
 * Two things the session trace view never said out loud.
 *
 * 1. WHICH PROJECT. The header carried a trace id, a model and a clock but
 *    never the working directory, so two checkouts of one repo — or a session
 *    from an entirely different project — read identically. A terminal keeps
 *    that on screen at all times; so does the header now, leading the meta row.
 *
 * 2. WHAT IS STILL RUNNING. A span the agent had not finished rendered exactly
 *    like one that had. The marker is asserted in BOTH desktop projections
 *    (conversation spine and timeline), because a reader who switches view
 *    mode must not lose the one row that is still moving — the four-projection
 *    failure this repo has already paid for twice.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'
import { selectViewMode } from './helpers/viewMode.js'

const CWD = '/Users/tester/checkouts/demo-project'

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

/** A live session in `CWD` with one still-running tool under its prompt. */
async function seed(page, { pending = true } = {}) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  const spans = [
    { trace_id: traceId, span_id: `start-${sfx}`, parent_id: null, name: 'session.start',
      start_time: now, attributes: { cwd: CWD, is_test: true } },
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: now, attributes: { text: 'running-marker fixture', is_test: true } },
  ]
  if (pending) {
    spans.push({
      trace_id: traceId, span_id: `pending-tu-${sfx}`, parent_id: `prompt-${sfx}`,
      name: 'tool.Bash', start_time: now, status_code: 'PENDING',
      attributes: { command_preview: 'npx vite build', tool_use_id: `tu-${sfx}`, is_test: true },
    })
  } else {
    spans.push({
      trace_id: traceId, span_id: `done-${sfx}`, parent_id: `prompt-${sfx}`,
      name: 'tool.Bash', start_time: now, duration_ms: 120,
      attributes: { command_preview: 'npx vite build', is_test: true },
    })
  }
  await page.goto('/trace/sessions')
  await post(page, spans)
  return traceId
}

test('the header names the project the session ran in', async ({ page }) => {
  const traceId = await seed(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const chip = page.locator('[data-testid="trace-header-project"]')
  await expect(chip).toBeVisible({ timeout: 15_000 })
  // The directory you are "in" is the emphasized part; the full path is what
  // tells two checkouts apart, so it must survive somewhere reachable.
  await expect(chip).toContainText('demo-project')
  await expect(chip).toHaveAttribute('title', new RegExp(CWD.replace(/\//g, '\\/')))
})

test('the collapsed compact header keeps the project name', async ({ page }) => {
  const traceId = await seed(page)
  await page.goto(`/trace/sessions/${traceId}`)
  await expect(page.locator('[data-testid="trace-header-project"]')).toBeVisible({ timeout: 15_000 })

  // Collapsed is the normal reading state at >=lg, so the project has to
  // survive the fold — a fact that only exists while the header is expanded
  // is a fact the reader mostly does not have.
  await page.getByTestId('header-details-toggle').click()
  await expect(page.locator('[data-testid="trace-header-project-compact"]'))
    .toContainText('demo-project')
})

test('a running span is marked as running in the conversation feed', async ({ page }) => {
  const traceId = await seed(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const row = page.locator('.event-spine-row[data-run-state="running"]')
  await expect(row).toHaveCount(1, { timeout: 15_000 })
  // The in-flight card IS the marker in this view — it carries its own pill,
  // so the row-level `span-running-tag` would be a second badge saying the
  // same word. The tag still owns every OTHER projection (timeline, and any
  // collapsed-ancestor rollup), which the next test pins.
  await expect(row.getByTestId('running-span-card')).toContainText('running')
  await expect(row.locator('[data-testid="span-running-tag"]')).toHaveCount(0)
})

test('the timeline view marks the same running span', async ({ page }) => {
  const traceId = await seed(page)
  await page.goto(`/trace/sessions/${traceId}`)
  await expect(page.locator('.event-spine-row[data-run-state="running"]'))
    .toHaveCount(1, { timeout: 15_000 })

  await selectViewMode(page, 'Timeline')
  const tag = page.locator('[data-testid="spine-row"] [data-testid="span-running-tag"]')

  // Collapsed: the running span is a CHILD of the prompt, so the only thing
  // the reader can see is the parent — which must say the work is under it
  // rather than swallow it silently.
  await expect(tag.first()).toBeVisible({ timeout: 10_000 })
  await expect(tag.first()).toHaveAttribute('data-run-folded', 'true')
  await expect(tag.first()).toContainText('running inside')

  // Expanded: the marker moves onto the row that actually owns it.
  await page.locator('[data-testid="spine-toggle"]').first().click()
  const own = page.locator('[data-testid="span-running-tag"]:not([data-run-folded])')
  await expect(own).toHaveCount(1, { timeout: 10_000 })
  await expect(own).toContainText('running')
})

test('a parked permission reads as waiting on you, not as running', async ({ page }) => {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  await page.goto('/trace/sessions')
  await post(page, [
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: now, attributes: { text: 'parked-permission fixture', is_test: true } },
    { trace_id: traceId, span_id: `permreq-${sfx}`, parent_id: `prompt-${sfx}`,
      name: 'permission.request', start_time: now, status_code: 'PENDING',
      attributes: { tool_name: 'Bash', kind: 'question',
                    questions: [{ question: 'Run the migration?' }], is_test: true } },
  ])
  await page.goto(`/trace/sessions/${traceId}`)

  // The one state allowed to demand action must not be filed under the state
  // that says the agent is fine on its own — that is the whole point of the
  // two-state split, and it is the half no other case covers.
  const row = page.locator('.event-spine-row[data-run-state="waiting"]')
  await expect(row).toHaveCount(1, { timeout: 15_000 })
  await expect(row.locator('[data-testid="span-running-tag"]')).toContainText('waiting on you')
  await expect(page.locator('.event-spine-row[data-run-state="running"]')).toHaveCount(0)
})

test('a session with nothing in flight shows no running marker', async ({ page }) => {
  const traceId = await seed(page, { pending: false })
  await page.goto(`/trace/sessions/${traceId}`)

  // Anchor on the feed having rendered before asserting an absence, or this
  // passes against a blank pane.
  await expect(page.locator('.event-spine-row').first()).toBeVisible({ timeout: 15_000 })
  await expect(page.locator('[data-testid="span-running-tag"]')).toHaveCount(0)
})
