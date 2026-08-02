/**
 * Approve or deny a parked tool call from `/live` (Agent SDK tier).
 *
 * The tmux tier can only drive an on-screen widget with keystrokes; a session
 * regin LAUNCHED is different — `can_use_tool` parks the call and a typed
 * allow/deny resolves it. The `/live` QA sheet therefore shows a decision card
 * for a PENDING `permission.request` whose attributes carry the SDK-only
 * `kind` (`plan` | `tool`), and only then: a hook-observed session's permission
 * prompt is waiting on someone else's terminal and stays read-only.
 *
 * These tests pin the BROWSER-side contract only — which card renders, what
 * stages vs. what sends, and the exact POST body reaching
 * `/api/sessions/<id>/bridge-decide`. The route itself is stubbed
 * (tests/agent_sdk/test_decision_routing.py owns the server side).
 *
 * Viewport: 375x667, the design doc's iPhone SE baseline, matching
 * live-card.spec.js (the repo's `mobile` project only matches responsive.spec).
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'
import { settle } from './helpers/overflow.js'

test.use({ viewport: { width: 375, height: 667 } })

async function post(page, spans) {
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
}

function quietLastSeen() {
  return new Date(Date.now() - 30_000).toISOString()
}

// A regin-owned session has no pane: reachable with `bridge_pane: null` is
// exactly what `_bridge_reachability` returns for one.
async function sdkOwnedMap(page, traceId, { reachable = true, phase } = {}) {
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const resp = await route.fetch()
    const json = await resp.json()
    await route.fulfill({
      response: resp,
      json: {
        ...json,
        bridge_reachable: reachable,
        bridge_pane: null,
        last_seen: quietLastSeen(),
        ...(phase
          ? { phase, agent_phase: { main: phase },
              phase_config: { working_window_sec: 12, idle_settle_sec: 6,
                inactive_threshold_sec: 600 } }
          : {}),
      },
    })
  })
}

async function stubDecide(page, traceId, result = { delivered: true, detail: 'decision delivered' }) {
  const posts = []
  await page.route(`**/api/sessions/${traceId}/bridge-decide`, async (route) => {
    posts.push(route.request().postDataJSON())
    await route.fulfill({ json: { id: 1, ...result } })
  })
  return posts
}

// Seed one PENDING permission.request. `attrs` decides which tier it looks
// like: `kind` present ⇒ regin-owned (decidable), absent ⇒ hook-observed.
async function seedPending(page, attrs, { reachable = true, phase } = {}) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  const spanId = `permreq-tu-${sfx}`
  const toolUseId = `tu-${sfx}`
  await post(page, [
    { trace_id: traceId, span_id: `prompt-${sfx}`, parent_id: null, name: 'prompt',
      start_time: now, attributes: { text: 'approve-from-phone fixture', is_test: true } },
    { trace_id: traceId, span_id: spanId, parent_id: null, name: 'permission.request',
      start_time: now, status_code: 'PENDING',
      attributes: { tool_use_id: toolUseId, live: true, is_test: true, ...attrs } },
  ])
  await sdkOwnedMap(page, traceId, { reachable, phase })
  return { traceId, spanId, toolUseId }
}

async function openSheet(page, traceId, spanId) {
  await page.goto(`/live/${traceId}`)
  await settle(page)
  const row = page.locator(`[data-testid="live-row"][data-span-id="${spanId}"]`)
  await expect(row).toBeVisible({ timeout: 10_000 })
  await row.click()
  const sheet = page.locator('[data-testid="live-sheet"]')
  await expect(sheet).toBeVisible()
  return sheet
}

const TOOL_ATTRS = {
  kind: 'tool',
  tool_name: 'Bash',
  command_preview: 'rm -rf build',
  requested_permission: 'Run shell command: rm -rf build',
}

const PLAN_ATTRS = {
  kind: 'plan',
  tool_name: 'ExitPlanMode',
  requested_permission: 'Approve the plan and start building',
  plan: '## Steps\n\n1. PLAN_BODY_MARKER — rewrite the router\n2. ship it',
}

test.describe('Decide a parked tool call from the /live sheet', () => {
  test('a gated tool call shows what it wants, then Allow stages before it sends', async ({ page }) => {
    const { traceId, spanId, toolUseId } = await seedPending(page, TOOL_ATTRS)
    const posts = await stubDecide(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    // The card names the command, not just the tool.
    await expect(sheet).toContainText('rm -rf build')
    const decision = sheet.locator('[data-testid="live-qa-decision"]')
    await expect(decision).toBeVisible()

    // Tap Allow → staged, nothing sent (a mis-tap must not run the command).
    await sheet.locator('[data-testid="live-qa-allow"]').click()
    await expect(sheet.locator('[data-testid="live-qa-decide-confirm"]')).toBeVisible()
    await page.waitForTimeout(200)
    expect(posts.length).toBe(0)

    await sheet.locator('[data-testid="live-qa-decide-send"]').click()
    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0].behavior).toBe('allow')
    expect(posts[0].reason).toBeUndefined()
    // Names the call it decides: a gated session can be parked on several at
    // once, and the card must resolve the one the operator was looking at.
    expect(posts[0].tool_use_id).toBe(toolUseId)
    await expect(sheet).toBeHidden({ timeout: 5_000 })
  })

  test('Deny carries the typed reason to the agent', async ({ page }) => {
    const { traceId, spanId, toolUseId } = await seedPending(page, TOOL_ATTRS)
    const posts = await stubDecide(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    await sheet.locator('[data-testid="live-qa-deny"]').click()
    await sheet.locator('[data-testid="live-qa-decide-reason"]').fill('use the staging bucket')
    await sheet.locator('[data-testid="live-qa-decide-send"]').click()

    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0]).toEqual({
      behavior: 'deny', reason: 'use the staging bucket', tool_use_id: toolUseId })
  })

  test('Cancel discards the staged decision and sends nothing', async ({ page }) => {
    const { traceId, spanId } = await seedPending(page, TOOL_ATTRS)
    const posts = await stubDecide(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    await sheet.locator('[data-testid="live-qa-deny"]').click()
    await expect(sheet.locator('[data-testid="live-qa-decide-confirm"]')).toBeVisible()
    await sheet.locator('[data-testid="live-qa-decide-cancel"]').click()
    await expect(sheet.locator('[data-testid="live-qa-decide-confirm"]')).toBeHidden()
    await page.waitForTimeout(200)
    expect(posts.length).toBe(0)
    await expect(sheet).toBeVisible()
  })

  test('a failed delivery keeps the sheet open and shows the refusal', async ({ page }) => {
    const { traceId, spanId } = await seedPending(page, TOOL_ATTRS)
    await stubDecide(page, traceId,
      { delivered: false, detail: 'agent session is no longer running' })

    const sheet = await openSheet(page, traceId, spanId)
    await sheet.locator('[data-testid="live-qa-allow"]').click()
    await sheet.locator('[data-testid="live-qa-decide-send"]').click()

    await expect(sheet).toContainText('agent session is no longer running')
    await expect(sheet).toBeVisible()
  })

  test('a plan renders its body and approves', async ({ page }) => {
    const { traceId, spanId } = await seedPending(page, PLAN_ATTRS)
    const posts = await stubDecide(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    await expect(sheet.locator('[data-testid="live-qa-plan"]')).toContainText('PLAN_BODY_MARKER')
    await expect(sheet.locator('[data-testid="live-qa-allow"]')).toContainText('Approve plan')

    await sheet.locator('[data-testid="live-qa-allow"]').click()
    await sheet.locator('[data-testid="live-qa-decide-send"]').click()
    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0].behavior).toBe('allow')
  })

  test('a long plan does not make the 375px sheet scroll sideways', async ({ page }) => {
    const long = Array.from({ length: 40 },
      (_, i) => `- step ${i} /a/very/long/unbroken/path/that/would/overflow/${i}`).join('\n')
    const { traceId, spanId } = await seedPending(page, { ...PLAN_ATTRS, plan: long })

    const sheet = await openSheet(page, traceId, spanId)
    const overflow = await sheet.evaluate(el => el.scrollWidth - el.clientWidth)
    expect(overflow, 'the sheet overflows horizontally on a long plan').toBeLessThanOrEqual(1)
  })

  test('a hook-observed permission request stays read-only (no kind)', async ({ page }) => {
    const { traceId, spanId } = await seedPending(page, {
      tool_name: 'Bash',
      command_preview: 'npm publish',
      requested_permission: 'Run shell command: npm publish',
    })
    const posts = await stubDecide(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    await expect(sheet).toContainText('npm publish')
    await expect(sheet.locator('[data-testid="live-qa-decision"]')).toHaveCount(0)
    await expect(sheet).toContainText('Waiting for your decision')
    expect(posts.length).toBe(0)
  })

  test('an unreachable session cannot be decided', async ({ page }) => {
    const { traceId, spanId } = await seedPending(page, TOOL_ATTRS, { reachable: false })

    const sheet = await openSheet(page, traceId, spanId)
    await expect(sheet.locator('[data-testid="live-qa-decision"]')).toHaveCount(0)
  })

  test('the NOW zone offers "decide" for an owned session and only "details" otherwise', async ({ page }) => {
    const owned = await seedPending(page, TOOL_ATTRS, { phase: 'waiting-permission' })
    await page.goto(`/live/${owned.traceId}`)
    await settle(page)
    const decide = page.locator('[data-testid="live-now-decide"]')
    await expect(decide).toBeVisible({ timeout: 10_000 })
    await expect(decide).toContainText('decide')
    // It opens the same decision card the tail row does.
    await decide.click()
    await expect(page.locator('[data-testid="live-qa-decision"]')).toBeVisible()

    const observed = await seedPending(page, {
      tool_name: 'Bash', requested_permission: 'Run shell command: npm publish',
    }, { phase: 'waiting-permission' })
    await page.goto(`/live/${observed.traceId}`)
    await settle(page)
    const details = page.locator('[data-testid="live-now-decide"]')
    await expect(details).toBeVisible({ timeout: 10_000 })
    await expect(details).toContainText('details')
  })

  test('deciding logs no console errors', async ({ page }) => {
    const errors = []
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
    page.on('pageerror', (e) => errors.push(String(e)))

    const { traceId, spanId } = await seedPending(page, PLAN_ATTRS)
    await stubDecide(page, traceId)
    const sheet = await openSheet(page, traceId, spanId)
    await sheet.locator('[data-testid="live-qa-deny"]').click()
    await sheet.locator('[data-testid="live-qa-decide-reason"]').fill('not yet')
    await sheet.locator('[data-testid="live-qa-decide-send"]').click()
    await expect(sheet).toBeHidden({ timeout: 5_000 })

    expect(errors, `console errors: ${errors.join(' | ')}`).toEqual([])
  })
})

// An SDK-owned session parks EVERY gated call through one `permission.request`
// — including `AskUserQuestion`, which parks as `kind: 'question'` — and the
// serve-time merge then retires the `tool.AskUserQuestion` placeholder as that
// row's duplicate. So the parked ask reaches the card ONLY as a permission
// span, and a surface keyed on the span NAME showed it the allow/deny card:
// "Waiting for your decision", three options sitting unreachable in
// `attributes.questions`, and no way to answer from the phone at all.
const QUESTION_ATTRS = {
  kind: 'question',
  tool_name: 'AskUserQuestion',
  requested_permission: 'Use tool: AskUserQuestion',
  questions: [{
    question: 'What should happen to the commit?',
    header: 'Commit',
    multiSelect: false,
    options: [
      { label: 'Keep it', description: 'Leave it on the branch' },
      { label: 'Uncommit', description: 'Soft reset, keep the edits' },
    ],
  }],
}

async function stubAnswer(page, traceId, result = { delivered: true, detail: 'answer delivered' }) {
  const posts = []
  await page.route(`**/api/sessions/${traceId}/bridge-answer`, async (route) => {
    posts.push(route.request().postDataJSON())
    await route.fulfill({ json: { id: 1, ...result } })
  })
  return posts
}

test.describe('A parked question is answerable, not a decision', () => {
  test('the sheet offers the options and sends the pick to bridge-answer', async ({ page }) => {
    const { traceId, spanId } = await seedPending(page, QUESTION_ATTRS)
    const answers = await stubAnswer(page, traceId)
    const decisions = await stubDecide(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    // Allow/deny means nothing to a question — the backend refuses a decision
    // aimed at a question park, so offering one would be a dead button.
    await expect(sheet.locator('[data-testid="live-qa-decision"]')).toHaveCount(0)
    await expect(sheet).not.toContainText('Waiting for your decision')

    const picks = sheet.locator('[data-testid="live-qa-pick"]')
    await expect(picks).toHaveCount(2)
    await expect(picks.first()).toContainText('Keep it')

    await picks.first().click()
    await sheet.locator('[data-testid="live-qa-confirm-send"]').click()

    await expect.poll(() => answers.length).toBe(1)
    expect(answers[0].option_index).toBe(0)
    expect(answers[0].label).toBe('Keep it')
    expect(decisions.length).toBe(0)
  })

  test('the tail row reads as the question, not as a permission', async ({ page }) => {
    const { traceId, spanId } = await seedPending(page, QUESTION_ATTRS)
    await page.goto(`/live/${traceId}`)
    await settle(page)
    const row = page.locator(`[data-testid="live-row"][data-span-id="${spanId}"]`)
    await expect(row).toBeVisible({ timeout: 10_000 })
    await expect(row).toContainText('Ask user')
    await expect(row).toContainText('What should happen to the commit?')
  })

  test('the NOW zone opens the options even though the phase says waiting-permission', async ({ page }) => {
    // The server reports a parked ask as `waiting-permission` — it IS parked in
    // can_use_tool — so a zone that mapped that phase to the permission card
    // alone left the one span the session is blocked on unreachable.
    const { traceId } = await seedPending(page, QUESTION_ATTRS,
      { phase: 'waiting-permission' })
    await page.goto(`/live/${traceId}`)
    await settle(page)
    const options = page.locator('[data-testid="live-now-options"]')
    await expect(options).toBeVisible({ timeout: 10_000 })
    await options.click()
    await expect(page.locator('[data-testid="live-qa-answer"]')).toBeVisible()
  })
})
