/**
 * SessionTraceView (conversation mode): the ≥xl agent COMPANION PANE — the
 * master–detail counterpart of the <xl takeover (trace-agent-scope.spec.js).
 *
 * Covers the redesign's acceptance items:
 *  1. ≥xl: entering a scope opens a right pane BESIDE the still-visible main
 *     feed (both markers visible); the originating card shows a scoped outline.
 *  3. `?agent=` deep link opens the split at ≥xl and the takeover at <xl;
 *     unknown id → not-found in the pane; ✕ / Esc clear the param.
 *  4. Divergence in the pane: span_count 0 → terminal empty (no spinner);
 *     span_count > 0 with the subtree fetch in flight → spinner, no false empty.
 *  6. Clicking a roster row / another card swaps pane content in place (param
 *     updates); a nested "Agent view →" retargets the same pane.
 *  7. ≥xl the Agents button opens pane roster mode (no popover); <xl keeps it.
 *  8. Detail-rail interplay: ≥2xl feed + pane + rail coexist; at xl opening the
 *     rail collapses the pane (rail wins, restorable).
 *
 * Fixtures mirror trace-agent-scope.spec.js (synthetic `is_test: true` sessions
 * posted via /api/session-spans, subagent internals parented under the
 * `subagent.start` marker so the pane projects the loaded span TREE).
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

const XL = { width: 1440, height: 900 }      // ≥xl, <2xl — the split floor.
const XXL = { width: 1600, height: 900 }     // ≥2xl — three columns fit.
const NARROW = { width: 1024, height: 900 }  // <xl — takeover.

const MAIN_CMD = 'echo MAIN_ONLY_CMD_MARKER'
const INT_FILE = 'src/AGENT_INTERNAL_MARKER.js'
const INT_FILE_B = 'src/AGENT_B_INTERNAL_MARKER.js'
const REAL_TASK_PROMPT = 'REAL_TASK_PROMPT_MARKER — verify the acceptance items'

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

// One prompt turn: main-only Bash + one running subagent whose internals nest
// under its start marker. Options: a real captured task prompt, an empty
// finished agent (terminal-empty state), a second agent (switch test), filler.
async function seedScoped(page, {
  realPrompt = false, emptyAgent = false, secondAgent = false, filler = 0,
  desc = 'Map the breakpoints',
} = {}) {
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
        description: desc, tool_use_id: `tu-${sfx}`, agent_id: agId, is_test: true } },
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
  const agIdB = `ag-run-b-${sfx}`
  if (secondAgent) {
    const startB = `substart-b-${sfx}`
    spans.push({ trace_id: traceId, span_id: `agent-b-${sfx}`, parent_id: `prompt-${sfx}`, name: 'tool.Agent',
      start_time: now, attributes: { subagent_type: 'checker',
        description: 'Recheck the split floor', tool_use_id: `tub-${sfx}`, agent_id: agIdB, is_test: true } })
    spans.push({ trace_id: traceId, span_id: startB, parent_id: `prompt-${sfx}`, name: 'subagent.start',
      start_time: now, attributes: { agent_type: 'checker', agent_id: agIdB, is_test: true } })
    spans.push({ trace_id: traceId, span_id: `int-read-b-${sfx}`, parent_id: startB, name: 'tool.Read',
      start_time: later, attributes: { file_path: INT_FILE_B, agent_id: agIdB, is_test: true } })
  }
  for (let i = 0; i < filler; i++) {
    spans.push({ trace_id: traceId, span_id: `fill-${sfx}-${i}`, parent_id: `prompt-${sfx}`,
      name: 'tool.Read', start_time: now,
      attributes: { file_path: `src/fill${i}.js`, is_test: true } })
  }
  await post(page, spans)
  return { traceId, sfx, agId, agIdB }
}

const pane = (page) => page.locator('[data-testid="trace-agent-pane"]')
const scopeBar = (page) => page.locator('[data-testid="trace-scope-bar"]')
const agentsBtn = (page) => page.locator('[data-testid="trace-agents-btn"]')
const mainMarker = (page) => page.getByText('MAIN_ONLY_CMD_MARKER').first()

async function gotoTrace(page, traceId, query = '') {
  await page.goto(`/trace/sessions/${traceId}${query}`)
  await expect(page.getByText('scoping fixture prompt').first()).toBeVisible({ timeout: 10_000 })
}

// ---- acceptance 1: split beside the feed --------------------------------------

test.describe('Companion pane split (≥xl)', () => {
  test.use({ viewport: XL })

  test('deep link opens the pane BESIDE the still-visible main feed; card shows scoped outline', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    await gotoTrace(page, traceId, `?agent=${agId}`)

    // Pane open, and — unlike the takeover — the main feed stays put.
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await expect(mainMarker(page)).toBeVisible()
    // Scoped subtree renders inside the pane.
    await expect(page.getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible({ timeout: 10_000 })
    // No thin takeover bar at ≥xl.
    await expect(scopeBar(page)).toHaveCount(0)
    // Originating card in the main feed carries the active-scope outline.
    await expect(page.locator('[data-testid="trace-subagent-scoped"]').first())
      .toBeVisible({ timeout: 10_000 })
  })

  test('a captured prompt-sa span leads the pane feed', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page, { realPrompt: true })
    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await expect(pane(page).getByText('REAL_TASK_PROMPT_MARKER').first())
      .toBeVisible({ timeout: 10_000 })
  })

  test('the main-feed "Agent view →" opens the pane, not a takeover', async ({ page }) => {
    const { traceId } = await seedScoped(page)
    await gotoTrace(page, traceId)
    await expect(mainMarker(page)).toBeVisible({ timeout: 10_000 })

    await page.locator('[data-testid="trace-agent-view"]').first().click()
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await expect(mainMarker(page)).toBeVisible()          // feed not swapped
    await expect(scopeBar(page)).toHaveCount(0)
    expect(page.url()).toContain('agent=')

    // ✕ closes the pane and clears the param.
    await page.locator('[data-testid="trace-pane-exit"]').click()
    await expect(pane(page)).toHaveCount(0)
    expect(page.url()).not.toContain('agent=')
  })

  test('Esc clears the scope and closes the pane', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await page.keyboard.press('Escape')
    await expect(pane(page)).toHaveCount(0)
    expect(page.url()).not.toContain('agent=')
  })

  test('main-feed subagent card keeps sane geometry under the pane squeeze (no per-char wrap)', async ({ page }) => {
    // A long launch description is what triggered the regression: with
    // break-all the squeezed label wrapped one character per line (~253×694px
    // card at this exact viewport). It must truncate to a single row instead.
    const longDesc = 'verify the split-pane redesign end-to-end across breakpoints, '
      + 'rails, rosters and takeover fallbacks with a deliberately long launch description'
    const { traceId, agId } = await seedScoped(page, { desc: longDesc })
    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })

    const card = page.locator('[data-testid="trace-subagent-scoped"]').first()
    await expect(card).toBeVisible({ timeout: 10_000 })
    const bb = await card.boundingBox()
    expect(bb.width, 'card must keep a usable width beside the open pane')
      .toBeGreaterThan(300)
    expect(bb.height, 'label must truncate to one row, not wrap per character')
      .toBeLessThan(60)
  })
})

// ---- acceptance 7: roster mode fills the pane ---------------------------------

test.describe('Pane roster mode (≥xl)', () => {
  test.use({ viewport: XL })

  test('Agents button opens the pane roster (no popover); picking a row scopes in place', async ({ page }) => {
    const { traceId } = await seedScoped(page, { secondAgent: true })
    await gotoTrace(page, traceId)
    await expect(mainMarker(page)).toBeVisible({ timeout: 10_000 })

    await agentsBtn(page).click()
    // Pane roster, NOT the 320px popover.
    await expect(page.locator('[data-testid="trace-agent-pane-roster"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="trace-agents-popover"]')).toHaveCount(0)
    await expect(pane(page)).toContainText('explorer')
    await expect(pane(page)).toContainText('checker')

    // Picking a row swaps the pane to that agent's scope in place (param
    // updates). Target the explorer row inside the pane by its description.
    await pane(page).getByText('Map the breakpoints').first().click()
    await expect(page.locator('[data-testid="trace-agent-pane-roster"]')).toHaveCount(0)
    await expect(pane(page).getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible({ timeout: 10_000 })
    expect(page.url()).toContain('agent=')
  })

  test('switching to another agent updates the pane content in place', async ({ page }) => {
    const { traceId, agId, agIdB } = await seedScoped(page, { secondAgent: true })
    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible({ timeout: 10_000 })

    // Re-open the roster while scoped and pick the other agent — target the
    // roster row INSIDE the pane (the main feed also shows a card for it, which
    // only selects, not scopes).
    await agentsBtn(page).click()
    await expect(page.locator('[data-testid="trace-agent-pane-roster"]')).toBeVisible({ timeout: 10_000 })
    await pane(page).getByText('Recheck the split floor').first().click()

    await expect(pane(page).getByText('AGENT_B_INTERNAL_MARKER').first())
      .toBeVisible({ timeout: 10_000 })
    expect(page.url()).toContain(`agent=${agIdB}`)
  })
})

// ---- acceptance 3/4: not-found + divergence in the pane -----------------------

test.describe('Pane divergence states (≥xl)', () => {
  test.use({ viewport: XL })

  test('unknown ?agent= → not-found in the pane; main feed intact; ✕ clears; no console error', async ({ page }) => {
    const { traceId } = await seedScoped(page)
    const consoleErrors = []
    const pageErrors = []
    page.on('console', (msg) => { if (msg.type() === 'error') consoleErrors.push(msg.text()) })
    page.on('pageerror', (err) => pageErrors.push(String(err)))

    await gotoTrace(page, traceId, '?agent=ag-nope')
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="trace-pane-notfound"]')).toBeVisible({ timeout: 10_000 })
    await expect(mainMarker(page)).toBeVisible()

    await page.locator('[data-testid="trace-pane-exit"]').click()
    await expect(pane(page)).toHaveCount(0)

    expect(pageErrors, `page errors: ${pageErrors.join(' | ')}`).toEqual([])
    expect(consoleErrors, `console errors: ${consoleErrors.join(' | ')}`).toEqual([])
  })

  test('span_count 0 → terminal "no spans captured" in the pane, not a spinner', async ({ page }) => {
    const { traceId, sfx } = await seedScoped(page, { emptyAgent: true })
    await gotoTrace(page, traceId, `?agent=ag-empty-${sfx}`)
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="trace-scope-empty"]'))
      .toContainText('no spans captured for this agent', { timeout: 10_000 })
    await expect(page.locator('[data-testid="trace-scope-loading"]')).toHaveCount(0)
  })

  test('span_count > 0 with the subtree fetch in flight → spinner, never a false empty', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    let release
    const gate = new Promise((resolve) => { release = resolve })
    await page.route(`**/api/sessions/${traceId}/spans/**/children*`, async (route) => {
      await gate
      await route.continue()
    })

    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="trace-scope-loading"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="trace-scope-empty"]')).toHaveCount(0)

    release()
    await expect(page.getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[data-testid="trace-scope-loading"]')).toHaveCount(0)
  })
})

// ---- acceptance 3: deep link is a takeover below xl ---------------------------

test.describe('Deep link falls back to takeover (<xl)', () => {
  test.use({ viewport: NARROW })

  test('the same ?agent= link opens the takeover bar, not the pane', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(scopeBar(page)).toBeVisible({ timeout: 10_000 })
    await expect(pane(page)).toHaveCount(0)
    // Feed swapped: main-only rows gone under the takeover.
    await expect(page.getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible({ timeout: 10_000 })
    await expect(page.getByText('MAIN_ONLY_CMD_MARKER')).toHaveCount(0)
  })
})

// ---- split ⇄ full toggle (user chooses only-subagent presentation) ------------

test.describe('Split ⇄ full toggle (≥xl)', () => {
  test.use({ viewport: XL })

  test('expanding shows only the subagent trace; collapsing restores the split', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await expect(mainMarker(page)).toBeVisible()

    // Expand → full-width takeover: only the subagent trace, no main feed, no pane.
    await page.locator('[data-testid="trace-pane-expand"]').click()
    await expect(scopeBar(page)).toBeVisible({ timeout: 10_000 })
    await expect(pane(page)).toHaveCount(0)
    await expect(page.getByText('MAIN_ONLY_CMD_MARKER')).toHaveCount(0)
    await expect(page.getByText('AGENT_INTERNAL_MARKER').first()).toBeVisible()

    // Collapse → back to the split, main feed restored.
    await page.locator('[data-testid="trace-scope-collapse"]').click()
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await expect(mainMarker(page)).toBeVisible()
  })

  test('the full-vs-split choice persists across scope re-entry (localStorage)', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await page.locator('[data-testid="trace-pane-expand"]').click()
    await expect(scopeBar(page)).toBeVisible({ timeout: 10_000 })

    // Reload the same deep link — the persisted 'full' choice is honored, so
    // the scope re-opens as the takeover, not the split.
    await page.reload()
    await expect(page.getByText('scoping fixture prompt').first()).toBeVisible({ timeout: 10_000 })
    await expect(scopeBar(page)).toBeVisible({ timeout: 10_000 })
    await expect(pane(page)).toHaveCount(0)
  })

  test('in full mode the Agents button keeps the popover picker — no silent flip back to split', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await page.locator('[data-testid="trace-pane-expand"]').click()
    await expect(scopeBar(page)).toBeVisible({ timeout: 10_000 })

    await agentsBtn(page).click()
    await expect(page.locator('[data-testid="trace-agents-popover"]')).toBeVisible()
    await expect(page.locator('[data-testid="trace-agent-pane-roster"]')).toHaveCount(0)
    // The takeover presentation is untouched by opening the picker.
    await expect(scopeBar(page)).toBeVisible()
    await expect(pane(page)).toHaveCount(0)
  })
})

// ---- acceptance 8: detail-rail interplay --------------------------------------

async function openDetailRailFromPane(page) {
  // Select a span inside the pane, then open the opt-in detail rail.
  await pane(page).getByText('AGENT_INTERNAL_MARKER').first().click()
  const showBtn = page.locator('[aria-label="Show span details"]')
  await expect(showBtn).toBeVisible({ timeout: 10_000 })
  await showBtn.click()
  await expect(page.locator('[aria-label="Hide span details"]')).toBeVisible({ timeout: 10_000 })
}

test.describe('Detail-rail interplay at xl (rail wins, restorable)', () => {
  test.use({ viewport: XL })

  test('opening the rail collapses the pane; closing the rail restores it', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })

    await openDetailRailFromPane(page)
    // Right slot is shared at xl — the rail wins, the pane yields.
    await expect(pane(page)).toHaveCount(0)

    await page.locator('[aria-label="Hide span details"]').click()
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
  })

  test('with the rail open, the Agents button closes the rail and shows the roster pane', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    await openDetailRailFromPane(page)
    await expect(pane(page)).toHaveCount(0)

    // Explicit roster intent outranks the rail: no silent no-op — the rail
    // closes and the roster pane opens immediately.
    await agentsBtn(page).click()
    await expect(page.locator('[data-testid="trace-agent-pane-roster"]')).toBeVisible({ timeout: 10_000 })
    await expect(page.locator('[aria-label="Hide span details"]')).toHaveCount(0)
  })
})

test.describe('Detail-rail interplay at 2xl (three columns coexist)', () => {
  test.use({ viewport: XXL })

  test('feed + pane + detail rail are all visible', async ({ page }) => {
    const { traceId, agId } = await seedScoped(page)
    await gotoTrace(page, traceId, `?agent=${agId}`)
    await expect(pane(page)).toBeVisible({ timeout: 10_000 })
    // Feed is visible alongside the pane before any rail interaction.
    await expect(mainMarker(page)).toBeVisible()

    await openDetailRailFromPane(page)
    // All three columns stay mounted at ≥2xl — the pane does NOT yield.
    await expect(pane(page)).toBeVisible()
    await expect(page.locator('[aria-label="Hide span details"]')).toBeVisible()
  })
})

// ---- roster popover viewport containment (390px) -------------------------------

test.describe('Roster popover at 390px', () => {
  test.use({ viewport: { width: 390, height: 844 } })

  test('the popover lies fully within the viewport (no off-screen clipping)', async ({ page }) => {
    // Pre-fix: `absolute right-0 w-80` put the menu at x≈-142, clipping 142px
    // of every row on narrow phones.
    const { traceId } = await seedScoped(page)
    await gotoTrace(page, traceId)
    await expect(agentsBtn(page)).toBeVisible({ timeout: 10_000 })

    await agentsBtn(page).click()
    const pop = page.locator('[data-testid="trace-agents-popover"]')
    await expect(pop).toBeVisible()
    const bb = await pop.boundingBox()
    expect(bb.x, 'popover left edge must be on-screen').toBeGreaterThanOrEqual(0)
    expect(bb.x + bb.width, 'popover right edge must be on-screen')
      .toBeLessThanOrEqual(390 + 1)
    expect(bb.width, 'rows must keep a usable width').toBeGreaterThan(250)
  })

  test('the popover closes when the page scrolls under it', async ({ page }) => {
    // The menu is fixed-position, measured only at open — a scroll would leave
    // it visually detached from the button, so it dismisses instead.
    const { traceId } = await seedScoped(page, { filler: 30 })
    await gotoTrace(page, traceId)
    await expect(agentsBtn(page)).toBeVisible({ timeout: 10_000 })

    await agentsBtn(page).click()
    const pop = page.locator('[data-testid="trace-agents-popover"]')
    await expect(pop).toBeVisible()

    await page.locator('.content-scroll').evaluate((el) => { el.scrollTop = 200 })
    await expect(pop).toBeHidden()
  })
})
