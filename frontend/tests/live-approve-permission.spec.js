/**
 * Approve, deny, or pick an option for a parked/pending permission request
 * from `/live` — both tiers.
 *
 * A regin-owned session (`sdkOwned`) is decided over its typed channel:
 * `can_use_tool` parks the call, and a typed allow/deny resolves it — gated
 * on the SDK-only `kind` attribute (`plan` | `tool`) AND `meta.sdk_owned`.
 *
 * A tmux-observed session has no typed channel — deciding means driving
 * whatever widget is on screen with keystrokes — but is still decidable BY
 * POSITION: from the request's own hook-captured `options` (structured —
 * Bash, Edit, and friends carry real suggestions), or, for a request with
 * none (`ExitPlanMode` today — Claude Code's hook never sends
 * `permission_suggestions` for plan approval), a fresh live-parsed read of
 * the real pane text via `GET bridge-menu` (see `lib/agent_bridge
 * /menu_parse.py`). Only an unreachable session, or one whose live read
 * can't be trusted, stays read-only.
 *
 * These tests pin the BROWSER-side contract only — which card renders, what
 * stages vs. what sends, and the exact POST body reaching
 * `/api/sessions/<id>/bridge-decide` / GET to `bridge-menu`. The routes
 * themselves are stubbed (tests/agent_sdk/test_decision_routing.py and
 * tests/web/test_bridge_decide_tmux.py own the server side).
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

// `sdkOwned: true` mirrors what `_bridge_reachability` reports for a session
// regin launched itself (no pane, `bridge_pane: null`); `false` (the
// default) is a hook-observed tmux session, which DOES have a pane.
async function stubMap(page, traceId,
                       { reachable = true, phase, sdkOwned = false } = {}) {
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const resp = await route.fetch()
    const json = await resp.json()
    await route.fulfill({
      response: resp,
      json: {
        ...json,
        bridge_reachable: reachable,
        bridge_pane: sdkOwned ? null : '%7',
        sdk_owned: sdkOwned,
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

async function stubMenu(page, traceId, result) {
  const gets = []
  await page.route(`**/api/sessions/${traceId}/bridge-menu`, async (route) => {
    gets.push(true)
    await route.fulfill({ json: result })
  })
  return gets
}

// Seed one PENDING permission.request. `attrs` shapes which tier it can be
// decided over: `kind` (+ `sdkOwned: true`) ⇒ regin-owned; real `options` (+
// `sdkOwned: false`, the default) ⇒ tmux-observed with structured data;
// neither ⇒ tmux-observed falling back to a live pane read.
async function seedPending(page, attrs,
                           { reachable = true, phase, sdkOwned = false } = {}) {
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
  await stubMap(page, traceId, { reachable, phase, sdkOwned })
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
    const { traceId, spanId, toolUseId } = await seedPending(page, TOOL_ATTRS, { sdkOwned: true })
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
    const { traceId, spanId, toolUseId } = await seedPending(page, TOOL_ATTRS, { sdkOwned: true })
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
    const { traceId, spanId } = await seedPending(page, TOOL_ATTRS, { sdkOwned: true })
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
    const { traceId, spanId } = await seedPending(page, TOOL_ATTRS, { sdkOwned: true })
    await stubDecide(page, traceId,
      { delivered: false, detail: 'agent session is no longer running' })

    const sheet = await openSheet(page, traceId, spanId)
    await sheet.locator('[data-testid="live-qa-allow"]').click()
    await sheet.locator('[data-testid="live-qa-decide-send"]').click()

    await expect(sheet).toContainText('agent session is no longer running')
    await expect(sheet).toBeVisible()
  })

  test('a plan renders its body and approves', async ({ page }) => {
    const { traceId, spanId } = await seedPending(page, PLAN_ATTRS, { sdkOwned: true })
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

  test('a hook-observed request with real suggestion options offers picks', async ({ page }) => {
    // Bash/Edit-type requests carry real hook-captured `options` — no live
    // pane read needed, the structured path drives the decide UI directly.
    const { traceId, spanId } = await seedPending(page, {
      tool_name: 'Bash',
      command_preview: 'npm publish',
      requested_permission: 'Run shell command: npm publish',
      options: [
        { id: 'allow_session_1', label: 'Yes' },
        { id: 'allow_localSettings_1', label: "Yes, and don't ask again" },
        { id: 'deny', label: 'No' },
      ],
    })
    const menuGets = await stubMenu(page, traceId, { parsed: false, detail: 'unused' })
    const posts = await stubDecide(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    await expect(sheet).toContainText('npm publish')
    await expect(sheet.locator('[data-testid="live-qa-decision"]')).toBeVisible()
    const picks = sheet.locator('[data-testid="live-qa-decide-pick"]')
    await expect(picks).toHaveCount(3)
    await expect(picks.nth(1)).toContainText("Yes, and don't ask again")
    expect(menuGets.length).toBe(0)  // structured data present → never fetches the live menu

    await picks.nth(1).click()
    await sheet.locator('[data-testid="live-qa-decide-send"]').click()
    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0]).toEqual({
      option_index: 1, label: "Yes, and don't ask again"})
    await expect(sheet).toBeHidden({ timeout: 5_000 })
  })

  test('a hook-observed request with no structured options reads the live pane', async ({ page }) => {
    // ExitPlanMode today: the hook payload carries no `permission_suggestions`
    // at all, so the sheet fetches GET bridge-menu for the real, on-screen
    // options — the exact 4-way menu captured from a live v2.1.221 pane.
    const { traceId, spanId } = await seedPending(page, {
      tool_name: 'ExitPlanMode',
      requested_permission: 'Approve the plan and start building',
      plan: 'PLAN_BODY_MARKER',
      option_count: 1, default_option_id: 'deny',
    })
    // A short artificial delay makes the transient loading state observable
    // — otherwise the mocked GET resolves before the first assertion runs.
    await page.route(`**/api/sessions/${traceId}/bridge-menu`, async (route) => {
      await new Promise(r => setTimeout(r, 150))
      await route.fulfill({ json: {
        parsed: true, cursor_index: 0, detail: 'ok',
        options: [
          'Yes, auto-accept edits', 'Yes, manually approve edits',
          'No, refine with Ultraplan on Claude Code on the web',
          'Tell Claude what to change',
        ],
      } })
    })
    const posts = await stubDecide(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    await expect(sheet.locator('[data-testid="live-qa-decide-loading"]')).toBeVisible()
    const picks = sheet.locator('[data-testid="live-qa-decide-pick"]')
    await expect(picks).toHaveCount(4)
    await expect(picks.first()).toContainText('Yes, auto-accept edits')

    await picks.first().click()
    await sheet.locator('[data-testid="live-qa-decide-send"]').click()
    await expect.poll(() => posts.length).toBe(1)
    expect(posts[0]).toEqual(
      { option_index: 0, label: 'Yes, auto-accept edits', live: true })
  })

  test('an unparseable live menu refuses to guess and says so', async ({ page }) => {
    const { traceId, spanId } = await seedPending(page, {
      tool_name: 'ExitPlanMode',
      requested_permission: 'Approve the plan and start building',
      option_count: 1, default_option_id: 'deny',
    })
    await stubMenu(page, traceId,
      { parsed: false, detail: 'could not reliably read a menu on screen' })
    const posts = await stubDecide(page, traceId)

    const sheet = await openSheet(page, traceId, spanId)
    const unavailable = sheet.locator('[data-testid="live-qa-decide-unavailable"]')
    await expect(unavailable).toBeVisible()
    await expect(unavailable).toContainText("Can't be decided from here")
    await expect(unavailable).toContainText('resolve it in the terminal')
    await expect(sheet.locator('[data-testid="live-qa-decide-pick"]')).toHaveCount(0)
    expect(posts.length).toBe(0)
  })

  test('an unreachable session cannot be decided, owned or observed', async ({ page }) => {
    const owned = await seedPending(page, TOOL_ATTRS,
      { reachable: false, sdkOwned: true })
    const sheetOwned = await openSheet(page, owned.traceId, owned.spanId)
    await expect(sheetOwned.locator('[data-testid="live-qa-decision"]')).toHaveCount(0)

    const observed = await seedPending(page,
      { tool_name: 'Bash', requested_permission: 'Run shell command: npm publish' },
      { reachable: false })
    const sheetObserved = await openSheet(page, observed.traceId, observed.spanId)
    await expect(sheetObserved.locator('[data-testid="live-qa-decision"]')).toHaveCount(0)
  })

  test('the NOW zone offers "decide" for both tiers when reachable, "details" only when not', async ({ page }) => {
    const owned = await seedPending(page, TOOL_ATTRS,
      { phase: 'waiting-permission', sdkOwned: true })
    await page.goto(`/live/${owned.traceId}`)
    await settle(page)
    const decideOwned = page.locator('[data-testid="live-now-decide"]')
    await expect(decideOwned).toBeVisible({ timeout: 10_000 })
    await expect(decideOwned).toContainText('decide')
    // It opens the same decision card the tail row does.
    await decideOwned.click()
    await expect(page.locator('[data-testid="live-qa-decision"]')).toBeVisible()

    const observed = await seedPending(page, {
      tool_name: 'Bash', requested_permission: 'Run shell command: npm publish',
    }, { phase: 'waiting-permission' })
    await page.goto(`/live/${observed.traceId}`)
    await settle(page)
    const decideObserved = page.locator('[data-testid="live-now-decide"]')
    await expect(decideObserved).toBeVisible({ timeout: 10_000 })
    await expect(decideObserved).toContainText('decide')

    const unreachable = await seedPending(page, {
      tool_name: 'Bash', requested_permission: 'Run shell command: npm publish',
    }, { phase: 'waiting-permission', reachable: false })
    await page.goto(`/live/${unreachable.traceId}`)
    await settle(page)
    const details = page.locator('[data-testid="live-now-decide"]')
    await expect(details).toBeVisible({ timeout: 10_000 })
    await expect(details).toContainText('details')
  })

  test('deciding logs no console errors', async ({ page }) => {
    const errors = []
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()) })
    page.on('pageerror', (e) => errors.push(String(e)))

    const { traceId, spanId } = await seedPending(page, PLAN_ATTRS, { sdkOwned: true })
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
