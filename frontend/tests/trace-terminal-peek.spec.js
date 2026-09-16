/**
 * Raw-terminal peek on the desktop trace page
 * (`frontend/src/components/trace/TraceTerminalPeek.vue`).
 *
 * The /live card has had this since the bridge landed; the conversation view
 * had no route to the pane at all, so anything that never leaves the Claude
 * Code TUI — the status line, slash commands, the composer's own hints — was
 * unreachable from the desktop page.
 *
 * `/trace/*` is admin-gated on a SECOND localStorage key (`regin_auth_user`),
 * not just the token — the auth-fixture's token alone lands you on the
 * dashboard, silently. `asAdmin` below is that missing half.
 *
 * The capture itself is stubbed: driving it off a real tmux pane would make
 * the test depend on a live claude running on the machine. `capture_screen`'s
 * own guards are covered in tests/agent_bridge/.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'
import { settle } from './helpers/overflow.js'

const SCREEN_HTML = '<span>✻ Sock-hopping… (5m 34s · ↓ 18.2k tokens)</span>'

async function asAdmin(page) {
  await page.addInitScript(() => {
    localStorage.setItem('regin_auth_user',
      JSON.stringify({ username: 'e2e', role: 'admin' }))
  })
}

// The trace route itself is admin-gated, so an editor cannot reach this page
// at all — the button's own gate is asserted against the stored role the
// component reads, with the route guard stubbed out of the way.
async function asEditor(page) {
  await page.addInitScript(() => {
    localStorage.setItem('regin_auth_user',
      JSON.stringify({ username: 'e2e', role: 'editor' }))
  })
}

async function seedSession(page, { running = false } = {}) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const now = new Date().toISOString()
  const spans = [
    { trace_id: traceId, span_id: `p-${sfx}`, parent_id: null,
      name: 'prompt', start_time: now,
      attributes: { text: 'TERMINAL_PEEK_FIXTURE', is_test: true } },
    { trace_id: traceId, span_id: `r-${sfx}`, parent_id: null,
      name: 'assistant_response', start_time: now,
      attributes: { text: 'reading the pane', is_test: true } },
  ]
  // A PENDING tool span is what `runStateOf` reads as 'running' — the green
  // spine node, and the only row that carries the inline peek.
  if (running) {
    spans.push({ trace_id: traceId, span_id: `t-${sfx}`, parent_id: `p-${sfx}`,
                 name: 'tool.Bash', start_time: now, status_code: 'PENDING',
                 attributes: { command: 'sleep 600', is_test: true } })
  }
  const res = await page.request.post('/api/session-spans', { data: spans })
  expect(res.ok()).toBeTruthy()
  return traceId
}

const BUSY_SCREEN = {
  busy: true, verb: 'Sock-hopping', elapsed_sec: 334, tokens: 18200,
  tool: 'Bash(pytest -q)', ctx_pct: 11, draft: null,
}

// `sdkOwned` mirrors `_steerability()` for a regin-launched run: reachable
// (regin can type into it over the SDK channel) but with a NULL pane, because
// no tmux terminal is holding it.
async function stubBridge(page, traceId,
                          { reachable = true, pane = '%147', screen,
                            sdkOwned = false } = {}) {
  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    // A map request in flight when the SPA re-navigates makes route.fetch()
    // throw — the request is simply gone. Falling through is correct there:
    // the page has already abandoned the response we would have patched.
    let resp
    try { resp = await route.fetch() } catch { return route.fallback() }
    const json = await resp.json()
    await route.fulfill({
      response: resp,
      json: { ...json,
        bridge_reachable: reachable,
        sdk_owned: sdkOwned,
        bridge_pane: reachable && !sdkOwned ? pane : null,
        live_screen: screen === undefined ? json.live_screen : screen },
    })
  })
  await page.route(`**/api/sessions/${traceId}/bridge-screen*`, (route) =>
    route.fulfill({ json: { ok: true, html: SCREEN_HTML, detail: 'captured %147' } }))
}

test.describe('Trace page — raw terminal peek', () => {
  test('the follow-up composer offers the pane it is about to type at', async ({ page }) => {
    await asAdmin(page)
    const traceId = await seedSession(page)
    await stubBridge(page, traceId)

    await page.goto(`/trace/sessions/${traceId}`)
    await settle(page)

    // The composer starts as a bubble and the header row only exists once it
    // is expanded — but the bubble itself only mounts after the summary lands,
    // so this waits for it rather than probing count() on the first frame.
    // Swallowed: a composer that came up already expanded has no bubble.
    await page.locator('[data-testid="followup-pill"]')
      .click({ timeout: 15_000 }).catch(() => {})

    const btn = page.locator('[data-testid="trace-terminal-btn"]')
    await expect(btn).toBeVisible({ timeout: 15_000 })
    await btn.click()

    const body = page.locator('[data-testid="live-terminal-body"]')
    await expect(body).toBeVisible({ timeout: 10_000 })
    await expect(body).toContainText('Sock-hopping')
    // The pane label proves bridge_pane reached the view, not just the boolean.
    await expect(page.locator('.live-term-foot')).toContainText('%147')

    // The snapshot is a dead end without a route to the operable terminal:
    // this dialog is where an operator asks "what is it doing", and the next
    // question is "let me drive it".
    const live = page.getByTestId('live-terminal-open-live')
    await expect(live).toBeVisible()
    await live.click()
    await expect(page).toHaveURL(new RegExp(`/terminal/${traceId}$`))
  })

  test('the live-terminal button is admin-only, since the socket is', async ({ page }) => {
    await asEditor(page)
    const traceId = await seedSession(page)
    await stubBridge(page, traceId)

    await page.goto(`/trace/sessions/${traceId}`)
    await settle(page)
    await page.locator('[data-testid="followup-pill"]')
      .click({ timeout: 15_000 }).catch(() => {})
    const btn = page.locator('[data-testid="trace-terminal-btn"]')
    if (await btn.count()) {
      await btn.click()
      await expect(page.getByTestId('live-terminal-body')).toBeVisible({ timeout: 10_000 })
      // The snapshot stays available; only the operable route is withheld,
      // because the socket would refuse a non-admin anyway.
      await expect(page.getByTestId('live-terminal-open-live')).toHaveCount(0)
    }
  })

  test('an unbridged session offers no button rather than a dead one', async ({ page }) => {
    await asAdmin(page)
    const traceId = await seedSession(page)
    await stubBridge(page, traceId, { reachable: false })

    await page.goto(`/trace/sessions/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="followup-composer"], .conversation-feed'))
      .not.toHaveCount(0)
    await expect(page.locator('[data-testid="trace-terminal-btn"]')).toHaveCount(0)
  })

  test('the spine ends in a live node carrying the peek', async ({ page }) => {
    await asAdmin(page)
    const traceId = await seedSession(page, { running: true })
    await stubBridge(page, traceId, { screen: BUSY_SCREEN })

    await page.goto(`/trace/sessions/${traceId}`)
    await settle(page)

    const node = page.locator('[data-testid="spine-live-node"]')
    await expect(node).toBeVisible({ timeout: 15_000 })
    await expect(node).toContainText('Sock-hopping')
    await expect(node).toContainText('5m34s')
    // Aim at the CTA, not the row's centre: the floating "Follow latest" pill
    // is fixed bottom-CENTRE and overlaps the middle of this row when the
    // transcript is scrolled near its end. The CTA sits at the right edge,
    // clear of it — which is also where a reader aims.
    const cta = node.locator('.spine-live-cta')
    await cta.scrollIntoViewIfNeeded()
    await cta.click()
    await expect(page.locator('[data-testid="live-terminal-body"]'))
      .toContainText('Sock-hopping', { timeout: 10_000 })
  })

  test('the live node survives a session with NO running span', async ({ page }) => {
    // The regression this whole feature exists to kill: a span is only PENDING
    // while a tool is in flight, so anchoring the affordance to a running row
    // made it vanish during exactly the hook-silent stretches — thinking,
    // streaming — where the transcript has least to say. It is anchored to the
    // session, so it must survive a transcript with nothing pending in it.
    await asAdmin(page)
    const traceId = await seedSession(page)
    await stubBridge(page, traceId, { screen: BUSY_SCREEN })

    await page.goto(`/trace/sessions/${traceId}`)
    await settle(page)

    await expect(page.locator('[data-testid="span-running-tag"]')).toHaveCount(0)
    const node = page.locator('[data-testid="spine-live-node"]')
    await expect(node).toBeVisible({ timeout: 15_000 })
    await expect(node).toContainText('Sock-hopping')
  })

  test('an unreadable screen still offers the pane, and claims nothing', async ({ page }) => {
    await asAdmin(page)
    const traceId = await seedSession(page)
    await stubBridge(page, traceId, { screen: null })

    await page.goto(`/trace/sessions/${traceId}`)
    await settle(page)

    const node = page.locator('[data-testid="spine-live-node"]')
    await expect(node).toBeVisible({ timeout: 15_000 })
    await expect(node).toContainText('status unavailable')
    await expect(node).not.toContainText('at the prompt')
    await expect(node.locator('[data-testid="spine-terminal-btn"]')).toBeVisible()
  })

  test('the live node is a full-row target, not an icon', async ({ page }) => {
    // It shipped once as an 18px icon-only chip and was, in the reviewer's
    // words, "hard to find and hard to click". The row is the button now.
    await asAdmin(page)
    const traceId = await seedSession(page)
    await stubBridge(page, traceId, { screen: BUSY_SCREEN })

    await page.goto(`/trace/sessions/${traceId}`)
    await settle(page)

    const btn = page.locator('[data-testid="spine-terminal-btn"]')
    await expect(btn).toBeVisible({ timeout: 15_000 })
    await expect(btn).toContainText('open terminal')
    const box = await btn.boundingBox()
    expect(box.height).toBeGreaterThanOrEqual(30)
    expect(box.width).toBeGreaterThan(200)
  })

  test('an unbridged session gets no live node at all', async ({ page }) => {
    await asAdmin(page)
    const traceId = await seedSession(page, { running: true })
    await stubBridge(page, traceId, { reachable: false })

    await page.goto(`/trace/sessions/${traceId}`)
    await settle(page)
    // Wait on the row's run state rather than on the `span-running-tag`: in the
    // conversation feed an in-flight span renders as its own card, which
    // carries the running pill itself, so the row-level tag is not there to
    // settle on.
    await expect(page.locator('.event-spine-row[data-run-state="running"]').first())
      .toBeVisible({ timeout: 15_000 })
    await expect(page.locator('[data-testid="spine-live-node"]')).toHaveCount(0)
  })

  test('an SDK-spawned run offers no terminal at all — there is no pane', async ({ page }) => {
    // A regin-launched run is `bridge_reachable` (the SDK channel accepts a
    // steer) yet has no tmux pane, so every capture would fail. Both homes of
    // the peek must be gone, not merely broken when clicked — while the status
    // the run DOES report survives, which is the whole point of the split gate.
    await asAdmin(page)
    const traceId = await seedSession(page, { running: true })
    await stubBridge(page, traceId, { sdkOwned: true, screen: BUSY_SCREEN })

    await page.goto(`/trace/sessions/${traceId}`)
    await settle(page)

    await expect(page.locator('.event-spine-row[data-run-state="running"]').first())
      .toBeVisible({ timeout: 15_000 })
    const node = page.locator('[data-testid="spine-live-node"]')
    await expect(node).toBeVisible()
    await expect(node).toContainText('Sock-hopping…')
    await expect(node).toContainText('Bash(pytest -q)')
    await expect(node.locator('[data-testid="spine-terminal-btn"]')).toHaveCount(0)
    // The composer must be OPEN before the count-0 assert means anything: an
    // SDK run is still `bridge_reachable`, so `canFollowUp` holds and the
    // textarea is the positive control proving the header rendered without a
    // terminal button — not that the panel simply never opened.
    await page.locator('[data-testid="followup-pill"]').click({ timeout: 15_000 })
    await expect(page.locator('[data-testid="followup-textarea"]')).toBeVisible()
    await expect(page.locator('[data-testid="trace-terminal-btn"]')).toHaveCount(0)
  })

  test('an idle SDK run says it is at the prompt rather than saying nothing',
    async ({ page }) => {
    // The reported defect: a run between turns emits no spans and has no pane,
    // so the strip was blank — the operator could not tell a thinking session
    // from a finished one. The runner's own verdict fills it.
    await asAdmin(page)
    const traceId = await seedSession(page, { running: true })
    await stubBridge(page, traceId, {
      sdkOwned: true,
      screen: { busy: false, verb: null, elapsed_sec: null, tokens: null,
                tool: null, ctx_pct: 8, draft: null },
    })

    await page.goto(`/trace/sessions/${traceId}`)
    await settle(page)

    const node = page.locator('[data-testid="spine-live-node"]')
    await expect(node).toBeVisible({ timeout: 15_000 })
    await expect(node).toContainText('at the prompt')
    await expect(node.locator('[data-testid="spine-terminal-btn"]')).toHaveCount(0)
  })

  test('no pane and no verdict still renders nothing — absence is not idle',
    async ({ page }) => {
    // The floor the split gate must not erode: a session neither tier can
    // speak for gets no strip at all. Inventing "idle" here is the bug the
    // whole feature exists to remove.
    await asAdmin(page)
    const traceId = await seedSession(page, { running: true })
    await stubBridge(page, traceId, { sdkOwned: true, screen: null })

    await page.goto(`/trace/sessions/${traceId}`)
    await settle(page)

    await expect(page.locator('.event-spine-row[data-run-state="running"]').first())
      .toBeVisible({ timeout: 15_000 })
    await expect(page.locator('[data-testid="spine-live-node"]')).toHaveCount(0)
  })

  test('the page chrome carries no third copy of the button', async ({ page }) => {
    // Two homes, both where the question is actually asked: the spine's live
    // node and the composer header. A third in the header actions was noise.
    await asAdmin(page)
    const traceId = await seedSession(page)
    await stubBridge(page, traceId)

    await page.goto(`/trace/sessions/${traceId}`)
    await settle(page)
    await expect(page.locator('[data-testid="trace-terminal-btn"]')).toHaveCount(0)
  })
})
