/**
 * Presentation mode's Reply affordance: the same bridge follow-up the
 * conversation feed offers, addressed from the span you are reading.
 *
 * The invariants worth pinning are the ones that only exist because the
 * composer now lives INSIDE a modal reader that already owns the keyboard:
 * that `c` and the span-walk keys go inert while the caret is in the field,
 * that Escape retires the composer before the reader, and that the draft
 * survives both a dismiss and a span step (the composer must never unmount).
 * Reachability is patched onto the map response — a live bridge pane is not
 * something an E2E run can stand up.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`

// The map route is patched per test and the view polls it; a handler still
// fetching when the page closes reports as a test failure otherwise.
test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

async function seed(page, { bridged = true } = {}) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const prompt = `p-${p8}`
  const spans = [
    {
      trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'Add token-bucket rate limiting.', is_test: true },
    },
    {
      trace_id: traceId, span_id: `a-${p8}`, parent_id: prompt, name: 'assistant_response',
      start_time: at(1), duration_ms: 2200,
      attributes: { text: 'Here is where the limiter should hook in.', is_test: true },
    },
  ]

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()

  if (bridged) {
    await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
      const res = await route.fetch()
      const body = await res.json()
      body.ended_at = null
      body.bridge_reachable = true
      body.model = 'sonnet-4.6'
      body.bridge_pane = '%3'
      body.context_pct = 41.4
      await route.fulfill({ response: res, json: body })
    })
  }

  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine:not(.event-spine-row)')).toBeVisible({ timeout: 20_000 })
  return traceId
}

const reader = (page) => page.getByTestId('span-present-reader')
const replyBtn = (page) => page.getByTestId('present-reply')
const composer = (page) => reader(page).getByTestId('followup-composer')
const textarea = (page) => reader(page).getByTestId('followup-textarea')
const rowFor = (page, text) => page.locator('.event-spine-row').filter({ hasText: text }).first()

// The prompt has no spine row of its own (it heads the turn), so reach it the
// way the reader itself does: open on the response and step back one.
async function openReader(page) {
  await rowFor(page, 'Assistant').locator('.present-open').click()
  await expect(reader(page)).toBeVisible()
  await page.keyboard.press('ArrowUp')
  await expect(reader(page)).toContainText('Add token-bucket rate limiting.')
}

test('no Reply on a session regin cannot steer', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page, { bridged: false })
  await openReader(page)

  await expect(replyBtn(page)).toHaveCount(0)
  await expect(reader(page).locator('.present-legend')).not.toContainText('reply')
  // The key is dead too, not merely unlabelled.
  await page.keyboard.press('c')
  await expect(composer(page)).toHaveCount(0)
})

test('Reply docks the composer, addressed from this span', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)
  await openReader(page)

  await expect(reader(page).locator('.present-legend')).toContainText('C reply')
  // Collapsed it contributes no panel — the header button is the only way in,
  // so the feed's resting bubble must not render here.
  await expect(reader(page).getByTestId('followup-pill')).toHaveCount(0)

  await replyBtn(page).click()
  await expect(textarea(page)).toBeFocused()
  await expect(composer(page)).toContainText('Continue session')
  await expect(composer(page)).toContainText('about prompt')
  await expect(textarea(page)).toHaveAttribute('placeholder', /Ask about this span/)
  await expect(replyBtn(page)).toHaveAttribute('aria-pressed', 'true')

  // The composer is a band, not an overlay: it must not cover the document.
  const body = await reader(page).locator('.present-body').boundingBox()
  const panel = await composer(page).boundingBox()
  expect(panel.y).toBeGreaterThanOrEqual(body.y + body.height - 1)
})

test('`c` toggles Reply, and is inert while typing in it', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)
  await openReader(page)

  await page.keyboard.press('c')
  await expect(textarea(page)).toBeFocused()

  // A literal `c` is text, not a shortcut — and neither are the walk keys.
  await textarea(page).pressSequentially('cjk check')
  await expect(textarea(page)).toHaveValue('cjk check')
  await expect(composer(page).locator('.followup-panel')).toBeVisible()
  await expect(reader(page)).toContainText('Add token-bucket rate limiting.')
})

test('Escape retires the composer before the reader', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)
  await openReader(page)

  await replyBtn(page).click()
  await expect(textarea(page)).toBeFocused()

  await page.keyboard.press('Escape')
  await expect(composer(page).locator('.followup-panel')).toHaveCount(0)
  await expect(reader(page), 'one Escape must not take both').toBeVisible()

  await page.keyboard.press('Escape')
  await expect(reader(page)).toHaveCount(0)
})

test('the draft survives a dismiss and a span step', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)
  await openReader(page)

  await replyBtn(page).click()
  await textarea(page).fill('keep me')

  // Dismiss + reopen: the composer must not unmount behind the toggle.
  await replyBtn(page).click()
  await expect(composer(page).locator('.followup-panel')).toHaveCount(0)
  await replyBtn(page).click()
  await expect(textarea(page)).toHaveValue('keep me')

  // Walking to the next span re-renders the document, not the composer.
  await reader(page).click({ position: { x: 10, y: 10 } })
  await page.keyboard.press('ArrowDown')
  await expect(reader(page)).toContainText('Here is where the limiter should hook in.')
  await expect(textarea(page)).toHaveValue('keep me')
})

// The autocomplete list is Teleported to <body>, i.e. outside the dialog: from
// Reka's point of view accepting a row is an interaction outside the modal, and
// Escape reaches the reader after the menu has already consumed it.
test.describe('the composer autocomplete, inside a modal reader', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/sessions/*/bridge-commands', route => route.fulfill({
      json: { commands: [{ name: 'compact', description: 'compact the context window' }] },
    }))
  })

  test('accepting a row neither dismisses the reader nor loses the draft', async ({ page }) => {
    test.setTimeout(90_000)
    await seed(page)
    await openReader(page)
    await replyBtn(page).click()

    await textarea(page).pressSequentially('/comp')
    const menu = page.getByTestId('live-command-menu')
    await expect(menu).toBeVisible()

    await menu.getByTestId('live-command-item').first().click()
    await expect(reader(page), 'the row click must not read as a click outside').toBeVisible()
    await expect(textarea(page)).toHaveValue('/compact ')
  })

  test('Escape dismisses the list without also retiring the composer', async ({ page }) => {
    test.setTimeout(90_000)
    await seed(page)
    await openReader(page)
    await replyBtn(page).click()

    await textarea(page).pressSequentially('/comp')
    await expect(page.getByTestId('live-command-menu')).toBeVisible()

    await page.keyboard.press('Escape')
    await expect(page.getByTestId('live-command-menu')).toHaveCount(0)
    await expect(textarea(page), 'one Escape must not spend twice').toHaveValue('/comp')
    await expect(reader(page)).toBeVisible()
  })
})

test('Send delivers over the bridge and clears', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = await seed(page)
  let sent = null
  await page.route(`**/api/sessions/${traceId}/bridge-send`, async (route) => {
    sent = route.request().postDataJSON()
    await route.fulfill({ json: { delivered: true, detail: 'delivered to %3' } })
  })

  await openReader(page)
  await replyBtn(page).click()
  await textarea(page).fill('why here and not the handler?')
  await page.getByTestId('followup-send').click()

  await expect(page.getByTestId('followup-status')).toContainText('delivered to %3')
  await expect(textarea(page)).toHaveValue('')
  expect(sent).toEqual({ text: 'why here and not the handler?' })
})

// The docked composer and the feed's are ONE component; the only reason they
// ever showed different chrome is that the host hand-picked which parts of the
// session's follow-up context to forward. Pinned here as a parity assertion:
// whatever the feed's copy is given, this one is given too.
test('the docked composer carries the same session context the feed does', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)

  // The feed's own composer first — the reference for what "the same" means.
  await page.getByTestId('followup-pill').click()
  const feedPanel = page.locator('[data-testid="trace-conversation-feed"] .followup-panel')
  await expect(feedPanel).toContainText('sonnet-4.6')
  await expect(feedPanel).toContainText('ctx 41% used')
  await expect(feedPanel.getByTestId('trace-terminal-btn')).toBeVisible()
  // The parity under test is between the two mounts, not both open at once —
  // dismiss the feed's before opening the reader's.
  await feedPanel.getByRole('button', { name: 'Dismiss composer' }).click()
  await expect(feedPanel).toHaveCount(0)

  await openReader(page)
  await replyBtn(page).click()
  await expect(composer(page)).toContainText('sonnet-4.6')
  await expect(composer(page)).toContainText('ctx 41% used')
  await expect(composer(page).getByTestId('trace-terminal-btn')).toBeVisible()
})

// Defect #212: at 1280x720 (Playwright's default viewport) the expanded
// header + stats + activity strip leave the bounded feed column ~180px, and
// the feed's own open composer used to claim every pixel of it — the
// transcript scroll region collapsed to 0px tall (invisible, unscrollable,
// and unrecoverable: the usual escape is scrolling to collapse the header,
// which a 0-height scroller can't do), while the composer sat over whatever
// would have rendered beneath it.
test('an open feed composer at 1280x720 never zeroes out the transcript', async ({ page }) => {
  test.setTimeout(90_000)
  await seed(page)
  await page.getByTestId('followup-pill').click()

  const scroller = page.locator('[data-testid="trace-conversation-feed"] [data-trace-scroller]')
  await expect(scroller).toBeVisible()
  const height = await scroller.evaluate((el) => el.clientHeight)
  expect(height).toBeGreaterThan(0)

  // Genuinely usable, not a 1px sliver: the seeded prompt row must be both
  // visible and the thing that actually receives the click (not the dock
  // sitting over it).
  const promptRow = page.locator('[data-testid="trace-conversation-feed"] .conversation-scroll')
    .getByText('Add token-bucket rate limiting.')
  await expect(promptRow).toBeVisible()
  await promptRow.click({ timeout: 5000 })

  // The composer itself must stay reachable — scrollable to Send, not clipped
  // off under the fixed-viewport shell.
  const dock = page.locator('[data-testid="trace-conversation-feed"] .followup-dock')
  await dock.evaluate((el) => { el.scrollTop = el.scrollHeight })
  await expect(page.getByTestId('followup-send')).toBeVisible()
})

// The send lifecycle is the composer's, not the host's: a delivered send
// retires the panel after its confirm window in BOTH mounts, and the reader's
// Reply button — which drives the same `open` model — flips back with it.
test('a delivered send retires the docked panel, as it does in the feed', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = await seed(page)
  await page.route(`**/api/sessions/${traceId}/bridge-send`, route =>
    route.fulfill({ json: { delivered: true, detail: 'delivered to %3' } }))

  await openReader(page)
  await replyBtn(page).click()
  await textarea(page).fill('take the other branch')
  await page.getByTestId('followup-send').click()

  await expect(page.getByTestId('followup-status')).toContainText('delivered to %3')
  await expect(composer(page).locator('.followup-panel')).toHaveCount(0, { timeout: 10_000 })
  await expect(replyBtn(page)).toHaveAttribute('aria-pressed', 'false')
  await expect(reader(page), 'retiring the composer must not retire the reader').toBeVisible()
})
