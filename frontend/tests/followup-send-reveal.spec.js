/**
 * Sending a follow-up clears the deck for the turn it produces.
 *
 * A send is the one moment the reader has said what they want to look at next,
 * so it is allowed to do what ordinary live-tailing must never do: close turns
 * they opened by hand. The three halves of that are pinned here — the composer
 * gets out of the way, the other turns collapse, and the new turn's fold strip
 * opens — plus the two carve-outs that keep it from being destructive: a failed
 * send changes nothing, and no send at all leaves hand-opened turns alone.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const at = i => `2026-05-08T12:00:${String(i).padStart(2, '0')}`

test.afterEach(async ({ page }) => {
  await page.unrouteAll({ behavior: 'ignoreErrors' })
})

// Only a real turn body carries this; SpineLiveNode shares the class but adds
// `event-spine-row`, and matching both is a strict-mode violation.
const spines = (page) => page.locator('.event-spine:not(.event-spine-row)')
const showDetail = (page) => page.locator('button', { hasText: 'show detail' })
const panel = (page) => page.locator('.followup-panel')
const pill = (page) => page.getByTestId('followup-pill')

function turnSpans(traceId, tag, i, { thinking = false } = {}) {
  const prompt = `p-${tag}`
  const out = [
    {
      trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(i), attributes: { text: `turn ${tag} prompt`, is_test: true },
    },
    {
      trace_id: traceId, span_id: `a-${tag}`, parent_id: prompt, name: 'assistant_response',
      start_time: at(i + 1), duration_ms: 1200,
      attributes: { text: `turn ${tag} answer`, is_test: true },
    },
  ]
  if (thinking) {
    out.push({
      trace_id: traceId, span_id: `t-${tag}`, parent_id: prompt, name: 'assistant.thinking',
      start_time: at(i + 2),
      attributes: { text: 'weighing the options', is_test: true },
    })
  }
  return out
}

async function post(page, spans) {
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
}

// A live, bridgeable session is what mounts the composer at all; neither is
// something an E2E run can stand up, so both are patched onto the map poll.
async function seed(page, { focus = false } = {}) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)

  await page.goto('/trace/sessions')
  if (focus) await page.evaluate(() => localStorage.setItem('regin.focus', '1'))
  else await page.evaluate(() => localStorage.removeItem('regin.focus'))
  await post(page, [...turnSpans(traceId, `one${p8}`, 0), ...turnSpans(traceId, `two${p8}`, 10)])

  await page.route(`**/api/sessions/${traceId}/map*`, async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    body.ended_at = null
    body.bridge_reachable = true
    body.model = 'sonnet-4.6'
    await route.fulfill({ response: res, json: body })
  })

  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  // The newest turn auto-expands; the older one rests behind its chips.
  await expect(spines(page)).toHaveCount(1)
  return { traceId, p8 }
}

async function openBoth(page) {
  await showDetail(page).first().click()
  await expect(spines(page)).toHaveCount(2)
}

async function sendFollowUp(page, traceId, { delivered = true } = {}) {
  await page.route(`**/api/sessions/${traceId}/bridge-send`, route => route.fulfill({
    json: delivered
      ? { delivered: true, detail: 'delivered to %3' }
      : { delivered: false, detail: 'no pane registered' },
  }))
  await pill(page).click()
  await page.getByTestId('followup-textarea').fill('now do the other half')
  await page.getByTestId('followup-send').click()
}

test('a delivered send retires the composer and clears the deck for the new turn', async ({ page }) => {
  test.setTimeout(90_000)
  const { traceId, p8 } = await seed(page)
  await openBoth(page)

  await sendFollowUp(page, traceId)
  await expect(page.getByTestId('followup-status')).toContainText('delivered to %3')

  // The panel is transcript the reader can no longer see; it goes once the
  // acknowledgement has had its moment.
  await expect(panel(page)).toHaveCount(0, { timeout: 10_000 })
  await expect(pill(page)).toBeVisible()

  // Both turns are still open — nothing collapses on the send itself, only on
  // the turn it produces.
  await expect(spines(page)).toHaveCount(2)

  await post(page, turnSpans(traceId, `thr${p8}`, 20))
  // Exactly one spine: the arriving turn. The two the reader had open folded.
  await expect(spines(page)).toHaveCount(1, { timeout: 20_000 })
  await expect(spines(page)).toContainText('turn thr' + p8 + ' answer')
})

test('the new turn arrives with its fold strip already open', async ({ page }) => {
  test.setTimeout(90_000)
  const { traceId, p8 } = await seed(page, { focus: true })

  await sendFollowUp(page, traceId)
  await expect(page.getByTestId('followup-status')).toContainText('delivered to %3')

  await post(page, turnSpans(traceId, `thr${p8}`, 20, { thinking: true }))
  const strip = page.getByTestId('fold-strip')
  await expect(strip).toHaveCount(1, { timeout: 20_000 })
  await expect(strip).toHaveAttribute('aria-expanded', 'true')
  await expect(strip).toContainText('hide')
})

test('a failed send keeps the composer and the draft, and clears no deck', async ({ page }) => {
  test.setTimeout(90_000)
  const { traceId, p8 } = await seed(page)
  await openBoth(page)

  await sendFollowUp(page, traceId, { delivered: false })
  await expect(page.getByTestId('followup-status')).toContainText('no pane registered')

  // The panel must stay up with the text in it — it is the only copy.
  await expect(panel(page)).toBeVisible()
  await expect(page.getByTestId('followup-textarea')).toHaveValue('now do the other half')

  // The turn that lands next is governed by the ordinary auto-expand rule —
  // the previously AUTO-opened turn folds, the hand-opened one does not — so a
  // failed send is indistinguishable from no send at all.
  await post(page, turnSpans(traceId, `thr${p8}`, 20))
  await expect(spines(page)).toHaveCount(2, { timeout: 20_000 })
  await expect(panel(page)).toBeVisible()
})

test('without a send, a hand-opened turn survives the next one arriving', async ({ page }) => {
  test.setTimeout(90_000)
  const { traceId, p8 } = await seed(page)
  await openBoth(page)

  await post(page, turnSpans(traceId, `thr${p8}`, 20))
  // The newest turn opens and the previously AUTO-opened one folds, as ever —
  // but the turn the reader opened by hand is theirs to close.
  await expect(spines(page)).toHaveCount(2, { timeout: 20_000 })
})

test('a pinned turn is exempt from the clear-deck', async ({ page }) => {
  test.setTimeout(90_000)
  const { traceId, p8 } = await seed(page)
  await openBoth(page)

  // The pin holds a turn's position across updates, so its rows have to stay
  // mounted — the send is not allowed to fold it out from under the anchor.
  await page.locator('button[title="Pin to view (hold position across updates)"]').first().click()

  await sendFollowUp(page, traceId)
  await expect(page.getByTestId('followup-status')).toContainText('delivered to %3')

  await post(page, turnSpans(traceId, `thr${p8}`, 20))
  // The pinned turn plus the arriving one; only the unpinned middle turn went.
  await expect(spines(page)).toHaveCount(2, { timeout: 20_000 })
  await expect(spines(page).first()).toContainText('turn one' + p8 + ' answer')
})

// A send is a send, wherever it was typed. The span reader's Reply is the same
// bridge send addressed from the span you are reading, so the feed underneath
// has to clear its deck for the turn it produces — otherwise the reveal was a
// property of one mount of the composer rather than of sending.
test('a send from the span reader clears the feed\'s deck too', async ({ page }) => {
  test.setTimeout(90_000)
  const { traceId, p8 } = await seed(page)
  await openBoth(page)

  await page.route(`**/api/sessions/${traceId}/bridge-send`, route => route.fulfill({
    json: { delivered: true, detail: 'delivered to %3' },
  }))

  await page.locator('.event-spine-row').filter({ hasText: 'Assistant' }).first()
    .locator('.present-open').click()
  const reader = page.getByTestId('span-present-reader')
  await expect(reader).toBeVisible()
  await page.getByTestId('present-reply').click()
  await reader.getByTestId('followup-textarea').fill('now do the other half')
  await reader.getByTestId('followup-send').click()
  await expect(page.getByTestId('followup-status')).toContainText('delivered to %3')

  // The docked panel retires itself on the confirm window, exactly as the
  // feed's does; only then is Escape the reader's to spend.
  await expect(reader.locator('.followup-panel')).toHaveCount(0, { timeout: 10_000 })
  await page.keyboard.press('Escape')
  await expect(reader).toHaveCount(0)

  await expect(spines(page)).toHaveCount(2)
  await post(page, turnSpans(traceId, `thr${p8}`, 20))
  await expect(spines(page)).toHaveCount(1, { timeout: 20_000 })
  await expect(spines(page)).toContainText('turn thr' + p8 + ' answer')
})
