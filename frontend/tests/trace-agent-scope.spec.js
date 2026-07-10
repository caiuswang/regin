/**
 * SessionTraceView (conversation mode): per-sub-agent scoped view — the
 * desktop sibling of the /live card's agent scoping.
 *
 * Covers the feature's acceptance items 1–6:
 *  1. agent_roster merges on every poll; Agents button + running badge;
 *     button absent for 0-agent sessions.
 *  2. picking an agent scopes the feed to its subtree, led by its task
 *     prompt (real `prompt-sa-` span when captured; synthesized fallback).
 *  3. scope bar shows type/description/status; ✕ exits and restores the
 *     main-feed scroll position.
 *  4. `?agent=<id>` deep link opens scoped; unknown id → not-found state,
 *     no crash / console error.
 *  5. span_count 0 → "no spans captured for this agent"; span_count > 0
 *     with the subtree fetch in flight → spinner, never a false empty.
 *  6. subagent cards in the main feed carry "Agent view →".
 *
 * Fixtures are synthetic `is_test: true` sessions posted via
 * `/api/session-spans` (same convention as trace-live-prompt-handoff.spec.js
 * / live-card.spec.js), with the subagent's internal spans PARENTED under
 * its `subagent.start` marker — the desktop scope projects the loaded span
 * TREE (flattenDescendants of the roster's start_span_id), unlike the /live
 * card's flat attribute partition.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

// This suite exercises the <xl full-feed TAKEOVER form of agent scope (feed
// swapped in place + TraceScopeBar). At ≥xl the scope renders as the companion
// pane instead (covered by trace-agent-pane.spec.js), so pin a viewport below
// the xl (1280px) split floor to keep testing the takeover path.
test.use({ viewport: { width: 1152, height: 900 } })

const MAIN_CMD = 'echo MAIN_ONLY_CMD_MARKER'
const INT_FILE = 'src/AGENT_INTERNAL_MARKER.js'
const REAL_TASK_PROMPT = 'REAL_TASK_PROMPT_MARKER — verify the acceptance items'

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

// One prompt turn with main-only activity + one running subagent whose
// internal spans nest under its start marker. Options add a real captured
// task prompt (`prompt-sa-<agent_id>`) and a finished agent with zero
// internal spans (the empty-scope terminal state).
async function seedScoped(page, { realPrompt = false, emptyAgent = false, filler = 0 } = {}) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  const later = new Date(Date.now() + 2000).toISOString()
  const agId = `ag-run-${sfx}`
  const startId = `substart-${sfx}`
  const spans = [
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: now, attributes: { text: 'scoping fixture prompt', is_test: true } },
    { trace_id: traceId, span_id: `mainbash-${sfx}`, parent_id: `prompt-${sfx}`, name: 'tool.Bash',
      start_time: now, attributes: { command: MAIN_CMD, command_preview: MAIN_CMD, is_test: true } },
    { trace_id: traceId, span_id: `agent-${sfx}`, parent_id: `prompt-${sfx}`, name: 'tool.Agent',
      start_time: now, attributes: { subagent_type: 'explorer',
        description: 'Map the breakpoints', tool_use_id: `tu-${sfx}`, agent_id: agId, is_test: true } },
    { trace_id: traceId, span_id: startId, parent_id: `prompt-${sfx}`, name: 'subagent.start',
      start_time: now, attributes: { agent_type: 'explorer', agent_id: agId, is_test: true } },
    { trace_id: traceId, span_id: `int-read-${sfx}`, parent_id: startId, name: 'tool.Read',
      start_time: later, attributes: { file_path: INT_FILE, agent_id: agId, is_test: true } },
  ]
  if (realPrompt) {
    spans.push({ trace_id: traceId, span_id: `prompt-sa-${agId}`, parent_id: startId,
      name: 'prompt', start_time: now,
      attributes: { text: REAL_TASK_PROMPT, agent_id: agId, is_test: true } })
  }
  if (emptyAgent) {
    spans.push({ trace_id: traceId, span_id: `substart2-${sfx}`, parent_id: `prompt-${sfx}`,
      name: 'subagent.start', start_time: now,
      attributes: { agent_type: 'ghost', agent_id: `ag-empty-${sfx}`, is_test: true } })
    spans.push({ trace_id: traceId, span_id: `substop2-${sfx}`, parent_id: `prompt-${sfx}`,
      name: 'subagent.stop', start_time: now,
      attributes: { agent_type: 'ghost', agent_id: `ag-empty-${sfx}`,
        result_preview: 'nothing captured', is_test: true } })
  }
  for (let i = 0; i < filler; i++) {
    spans.push({ trace_id: traceId, span_id: `fill-${sfx}-${i}`, parent_id: `prompt-${sfx}`,
      name: 'tool.Read', start_time: now,
      attributes: { file_path: `src/fill${i}.js`, is_test: true } })
  }
  await post(page, spans)
  return { traceId, sfx, agId, startId }
}

const scopeBar = (page) => page.locator('[data-testid="trace-scope-bar"]')
const agentsBtn = (page) => page.locator('[data-testid="trace-agents-btn"]')
const userBadges = (page) => page.getByText('USER', { exact: true })
const scroller = (page) => page.locator('.content-scroll')

async function gotoTrace(page, traceId, query = '') {
  await page.goto(`/trace/sessions/${traceId}${query}`)
  await expect(page.getByText('scoping fixture prompt').first()).toBeVisible({ timeout: 10_000 })
}

async function enterScopeViaPopover(page) {
  await agentsBtn(page).click()
  await expect(page.locator('[data-testid="trace-agents-popover"]')).toBeVisible()
  await page.locator('[data-testid="trace-agents-item"]').first().click()
  await expect(scopeBar(page)).toBeVisible({ timeout: 5_000 })
}

// ---- acceptance 1: roster button / badge / poll merge -----------------------

test.describe('Agents roster button (acceptance 1)', () => {
  test('running agent → button + violet badge; roster refreshes on the live poll', async ({ page }) => {
    const { traceId, sfx } = await seedScoped(page)
    await gotoTrace(page, traceId)

    await expect(agentsBtn(page)).toBeVisible({ timeout: 10_000 })
    const badge = page.locator('[data-testid="trace-agents-badge"]')
    await expect(badge).toHaveText('1')

    await agentsBtn(page).click()
    const popover = page.locator('[data-testid="trace-agents-popover"]')
    await expect(popover).toBeVisible()
    await expect(popover).toContainText('explorer')
    await expect(popover).toContainText('Map the breakpoints')
    // Esc closes.
    await page.keyboard.press('Escape')
    await expect(popover).toBeHidden()

    // agent_roster must MERGE on every poll, not freeze at page-load: a
    // second agent posted after load surfaces in the badge within ~2 polls.
    await post(page, [
      { trace_id: traceId, span_id: `substart-late-${sfx}`, parent_id: null,
        name: 'subagent.start', start_time: new Date().toISOString(),
        attributes: { agent_type: 'checker', agent_id: `ag-late-${sfx}`, is_test: true } },
    ])
    await expect(badge).toHaveText('2', { timeout: 15_000 })
  })

  test('no agents launched → no Agents button', async ({ page }) => {
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: new Date().toISOString(),
        attributes: { text: 'scoping fixture prompt', is_test: true } },
    ])
    await gotoTrace(page, traceId)
    await expect(agentsBtn(page)).toHaveCount(0)
  })
})

// ---- acceptance 2: scoped feed + task prompt --------------------------------

test.describe('Scoped feed (acceptance 2)', () => {
  test('picking an agent shows only its subtree, led by a synthesized task prompt', async ({ page }) => {
    const { traceId } = await seedScoped(page)
    await gotoTrace(page, traceId)
    await expect(page.getByText('MAIN_ONLY_CMD_MARKER').first()).toBeVisible({ timeout: 10_000 })

    await enterScopeViaPopover(page)

    // Only the agent's subtree renders; main-only rows are gone.
    await expect(page.getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('MAIN_ONLY_CMD_MARKER')).toHaveCount(0)

    // Exactly one auto-expanded group, led by the task prompt. No captured
    // prompt-sa span here, so the synthesized fallback (roster description).
    await expect(userBadges(page)).toHaveCount(1)
    await expect(page.getByText('Map the breakpoints').first()).toBeVisible()
  })

  test('a captured prompt-sa span leads the scoped feed instead of the fallback', async ({ page }) => {
    const { traceId } = await seedScoped(page, { realPrompt: true })
    await gotoTrace(page, traceId)

    await enterScopeViaPopover(page)
    await expect(userBadges(page)).toHaveCount(1)
    await expect(page.getByText('REAL_TASK_PROMPT_MARKER').first()).toBeVisible({ timeout: 10_000 })
  })
})

// ---- acceptance 3: scope bar + exit restores scroll --------------------------

test.describe('Scope bar (acceptance 3)', () => {
  test('bar shows type/description/status/span count; ✕ exits and restores scroll', async ({ page }) => {
    const { traceId } = await seedScoped(page, { filler: 40 })
    await gotoTrace(page, traceId)
    await expect(page.getByText('MAIN_ONLY_CMD_MARKER').first()).toBeVisible({ timeout: 10_000 })

    // Land the main feed at a non-zero scroll position first.
    await scroller(page).evaluate((el) => { el.scrollTop = Math.floor(el.scrollHeight / 3) })
    await page.waitForTimeout(150)
    const before = await scroller(page).evaluate((el) => el.scrollTop)
    expect(before).toBeGreaterThan(0)

    await enterScopeViaPopover(page)
    const bar = scopeBar(page)
    await expect(bar).toContainText('explorer')
    await expect(bar).toContainText('Map the breakpoints')
    // Server-status phrasing via agentStatusLabel + the roster span count.
    await expect(bar.locator('[data-testid="trace-scope-status"]')).toContainText('running')
    await expect(bar).toContainText(/\d+ spans?/)

    await page.locator('[data-testid="trace-scope-exit"]').click()
    await expect(bar).toHaveCount(0)
    await expect(page.getByText('MAIN_ONLY_CMD_MARKER').first()).toBeVisible()
    await page.waitForTimeout(300)
    const after = await scroller(page).evaluate((el) => el.scrollTop)
    expect(Math.abs(after - before), 'exiting scope must restore the main-feed scroll position')
      .toBeLessThanOrEqual(2)
  })
})

// ---- acceptance 4: ?agent= deep link -----------------------------------------

test.describe('?agent= deep link (acceptance 4)', () => {
  test('opens scoped directly (direct start_span_id fetch)', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    await gotoTrace(page, traceId, `?agent=${agId}`)

    await expect(scopeBar(page)).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('MAIN_ONLY_CMD_MARKER')).toHaveCount(0)
  })

  test('unknown id → not-found state with ✕, no crash, no console error', async ({ page }) => {
    const { traceId } = await seedScoped(page)
    const consoleErrors = []
    const pageErrors = []
    page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()) })
    page.on('pageerror', (err) => pageErrors.push(String(err)))

    await gotoTrace(page, traceId, '?agent=ag-does-not-exist')

    await expect(page.locator('[data-testid="trace-scope-notfound"]'))
      .toContainText('agent not found', { timeout: 10_000 })
    // Main feed stays rendered underneath — no crash.
    await expect(page.getByText('MAIN_ONLY_CMD_MARKER').first()).toBeVisible()

    // ✕ clears the bad param and returns to the plain main view.
    await page.locator('[data-testid="trace-scope-exit"]').click()
    await expect(scopeBar(page)).toHaveCount(0)

    expect(pageErrors, `page errors: ${pageErrors.join(' | ')}`).toEqual([])
    expect(consoleErrors, `console errors: ${consoleErrors.join(' | ')}`).toEqual([])
  })
})

// ---- acceptance 5: count-vs-loaded divergence --------------------------------

test.describe('Empty vs loading scope (acceptance 5)', () => {
  test('span_count 0 → terminal "no spans captured", not a spinner', async ({ page }) => {
    const { traceId, sfx } = await seedScoped(page, { emptyAgent: true })
    await gotoTrace(page, traceId, `?agent=ag-empty-${sfx}`)

    await expect(scopeBar(page)).toBeVisible({ timeout: 10_000 })
    const empty = page.locator('[data-testid="trace-scope-empty"]')
    await expect(empty).toContainText('no spans captured for this agent', { timeout: 10_000 })
    await expect(page.locator('[data-testid="trace-scope-loading"]')).toHaveCount(0)
  })

  test('a RUNNING agent with span_count 0 never shows the terminal empty (count lags ingest)', async ({ page }) => {
    // A start marker with no stop and no internal spans: the server roster
    // reports it running with span_count 0. That 0 is a lagging snapshot for
    // a live agent — the scope must show a waiting/loading state, never the
    // terminal "no spans captured" verdict.
    const traceId = randomUUID()
    const sfx = traceId.slice(0, 8)
    const now = new Date().toISOString()
    const agId = `ag-fresh-${sfx}`
    await post(page, [
      { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
        start_time: now, attributes: { text: 'scoping fixture prompt', is_test: true } },
      { trace_id: traceId, span_id: `substart-${sfx}`, parent_id: `prompt-${sfx}`,
        name: 'subagent.start', start_time: now,
        attributes: { agent_type: 'fresh', agent_id: agId, is_test: true } },
    ])
    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(scopeBar(page)).toBeVisible({ timeout: 10_000 })
    await expect(scopeBar(page).locator('[data-testid="trace-scope-status"]'))
      .toContainText(/running|waiting|stale/)
    await expect(page.locator('[data-testid="trace-scope-empty"]')).toHaveCount(0)
  })

  test('span_count > 0 with the subtree fetch in flight → spinner, never a false empty', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)

    // Hold EVERY children fetch (the scoped deep fetch AND the prompt
    // auto-expand fetch that would populate the same subtree) so the
    // loading state is actually observable, then release.
    let release
    const gate = new Promise((resolve) => { release = resolve })
    await page.route(`**/api/sessions/${traceId}/spans/**/children*`, async (route) => {
      await gate
      await route.continue()
    })

    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(scopeBar(page)).toBeVisible({ timeout: 10_000 })

    // While the fetch is held: spinner, and NEVER the terminal empty text.
    await expect(page.locator('[data-testid="trace-scope-loading"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="trace-scope-empty"]')).toHaveCount(0)

    release()
    await expect(page.getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="trace-scope-loading"]')).toHaveCount(0)
  })
})

// ---- verify-round regressions ------------------------------------------------

// Orphan roster entry: agent_id-tagged spans survive but the subagent.start
// marker was lost, so the server returns span_count > 0 with a null
// start_span_id — there is no anchor to tree-walk. The scope must fall back
// to the /live attribute partition (popover path: spans already loaded;
// deep-link path: full-map orphan load) rather than waiting on a subtree
// fetch that can never be issued.
const ORPHAN_FILE = 'src/ORPHAN_INTERNAL_MARKER.js'
async function seedOrphan(page) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  const agId = `ag-orphan-${sfx}`
  await post(page, [
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: now, attributes: { text: 'scoping fixture prompt', is_test: true } },
    { trace_id: traceId, span_id: `mainbash-${sfx}`, parent_id: `prompt-${sfx}`, name: 'tool.Bash',
      start_time: now, attributes: { command: MAIN_CMD, command_preview: MAIN_CMD, is_test: true } },
    { trace_id: traceId, span_id: `orph-read-${sfx}`, parent_id: `prompt-${sfx}`, name: 'tool.Read',
      start_time: now, attributes: { file_path: ORPHAN_FILE, agent_id: agId, is_test: true } },
  ])
  return { traceId, sfx, agId }
}

test.describe('Orphan agents and synthetic prompts (verify-round regressions)', () => {
  test('orphan agent (span_count > 0, no start marker) scopes via the attribute fallback — no dead spinner', async ({ page }) => {
    const { traceId } = await seedOrphan(page)
    await gotoTrace(page, traceId)
    await expect(page.getByText('MAIN_ONLY_CMD_MARKER').first()).toBeVisible({ timeout: 10_000 })

    await enterScopeViaPopover(page)
    await expect(page.getByText('ORPHAN_INTERNAL_MARKER').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('MAIN_ONLY_CMD_MARKER')).toHaveCount(0)
    await expect(page.locator('[data-testid="trace-scope-loading"]')).toHaveCount(0)
  })

  test('orphan agent deep link loads the full map and renders — no dead spinner', async ({ page }) => {
    const { traceId, agId } = await seedOrphan(page)
    const pageErrors = []
    page.on('pageerror', (err) => pageErrors.push(String(err)))

    await page.goto(`/trace/sessions/${traceId}?agent=${agId}`)
    await expect(scopeBar(page)).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('ORPHAN_INTERNAL_MARKER').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="trace-scope-loading"]')).toHaveCount(0)
    expect(pageErrors, `page errors: ${pageErrors.join(' | ')}`).toEqual([])
  })

  test('clicking the synthesized task-prompt card never fetches its content (no 404, no console error)', async ({ page }) => {
    const { traceId } = await seedScoped(page)
    const consoleErrors = []
    const synthRequests = []
    page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()) })
    page.on('request', (req) => {
      if (req.url().includes('prompt-sa-synth')) synthRequests.push(req.url())
    })

    await gotoTrace(page, traceId)
    await enterScopeViaPopover(page)

    // The synthesized USER card (fallback text = roster description).
    const promptCard = page.getByText('Map the breakpoints').first()
    await expect(promptCard).toBeVisible({ timeout: 10_000 })
    await promptCard.click()
    await page.waitForTimeout(500)

    expect(synthRequests, `synthetic-id API requests: ${synthRequests.join(' | ')}`).toEqual([])
    expect(consoleErrors, `console errors: ${consoleErrors.join(' | ')}`).toEqual([])
  })
})

// ---- acceptance 6: "Agent view →" on the subagent card ------------------------

test.describe('Agent view affordance (acceptance 6)', () => {
  test('the main-feed subagent card enters the same scope', async ({ page }) => {
    const { traceId } = await seedScoped(page)
    await gotoTrace(page, traceId)

    // The latest prompt auto-expands, so the subagent.start card is in the
    // feed with its scope affordance.
    const viewBtn = page.locator('[data-testid="trace-agent-view"]').first()
    await expect(viewBtn).toBeVisible({ timeout: 10_000 })
    await viewBtn.click()

    await expect(scopeBar(page)).toBeVisible({ timeout: 5_000 })
    await expect(scopeBar(page)).toContainText('explorer')
    await expect(page.getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('MAIN_ONLY_CMD_MARKER')).toHaveCount(0)
    // The URL carries the scope for shareability.
    expect(page.url()).toContain('agent=')
  })
})
