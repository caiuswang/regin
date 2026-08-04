/**
 * SessionTraceView reading affordances, four behaviours that only show up on a
 * long, live-polling trace:
 *
 *  1. the Agents roster button survives the header fold — the running-count
 *     badge is live session status, so it must stay legible exactly while the
 *     reader is deep in a transcript;
 *  2. a poll that appends spans must NOT move the scroller. Sticking to the
 *     newest activity is the Follow-latest pill's job alone;
 *  3. pinning a span must not unfold anything. `agentAncestorId()` is
 *     inclusive, so pinning a subagent row used to resolve to that very span
 *     and toggle its whole subtree open;
 *  4. a back-to-top control returns the page scroller to the header from
 *     anywhere in the trace.
 *
 * Scrolls are driven with real wheel input: the header state machine ignores
 * layout-driven scrolls, so a plain scrollTop write would no-op (see
 * session-header-collapse.spec.js).
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

const SUB_MARKER = 'AGENT_INTERNAL_MARKER'

async function post(page, spans) {
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
}

// A feed tall enough to scroll well past the back-to-top threshold, plus one
// subagent whose internal spans nest under its start marker (so a collapsed
// agent has something to reveal).
async function seedTallSession(page) {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const agId = `ag-${sfx}`
  const startId = `substart-${sfx}`
  const filler = 'Lorem ipsum dolor sit amet, consectetur adipiscing elit. '.repeat(30)
  const spans = []
  // 20 roots stays under the /map page size (50) so "load older" never fires
  // at the top and yanks the scroll mid-assertion.
  for (let i = 0; i < 20; i++) {
    const pid = `p-${sfx}-${i}`
    spans.push({ trace_id: traceId, span_id: pid, parent_id: null, name: 'prompt',
      start_time: `2026-05-09T10:${String(i).padStart(2, '0')}:00`,
      attributes: { text: `prompt number ${i} — padding the feed so the page scrolls`, is_test: true } })
    spans.push({ trace_id: traceId, span_id: `a-${sfx}-${i}`, parent_id: pid, name: 'assistant_response',
      start_time: `2026-05-09T10:${String(i).padStart(2, '0')}:05`,
      attributes: { text: filler, is_test: true } })
  }
  // The subagent hangs off the LAST prompt, which is the one auto-expanded.
  const lastPrompt = `p-${sfx}-19`
  spans.push({ trace_id: traceId, span_id: `agent-${sfx}`, parent_id: lastPrompt, name: 'tool.Agent',
    start_time: '2026-05-09T10:19:06',
    attributes: { subagent_type: 'explorer', description: 'Map the breakpoints', agent_id: agId, is_test: true } })
  spans.push({ trace_id: traceId, span_id: startId, parent_id: lastPrompt, name: 'subagent.start',
    start_time: '2026-05-09T10:19:07',
    attributes: { agent_type: 'explorer', agent_id: agId, is_test: true } })
  for (let i = 0; i < 3; i++) {
    spans.push({ trace_id: traceId, span_id: `int-${sfx}-${i}`, parent_id: startId, name: 'tool.Read',
      start_time: '2026-05-09T10:19:08',
      attributes: { file_path: `src/${SUB_MARKER}${i}.js`, agent_id: agId, is_test: true } })
  }
  await page.goto('/trace/sessions')
  await post(page, spans)
  return { traceId, sfx, startId }
}

// The right gutter is .content-scroll's own padding strip, so no span card can
// swallow the wheel before it reaches the page scroller.
async function wheel(page, deltaY, times = 1) {
  const vp = page.viewportSize()
  await page.mouse.move(vp.width - 20, Math.round(vp.height / 2))
  for (let i = 0; i < times; i++) {
    await page.mouse.wheel(0, deltaY)
    await page.waitForTimeout(40)
  }
}

const scrollTop = (page) =>
  page.evaluate(() => Math.round(document.querySelector('.content-scroll').scrollTop))

test.describe('SessionTraceView reading affordances', () => {
  test.use({ viewport: { width: 1440, height: 900 } })

  test('the Agents button and a back-to-top control stay reachable deep in a long trace', async ({ page }) => {
    const { traceId } = await seedTallSession(page)
    await page.goto(`/trace/sessions/${traceId}`)

    const agents = page.getByTestId('trace-agents-btn')
    const backTop = page.getByTestId('trace-back-to-top')
    const toggle = page.getByTestId('header-details-toggle')
    await expect(agents).toBeVisible({ timeout: 10_000 })
    // No assertion on back-to-top yet: the view lands the reader on the newest
    // turn, so a long trace legitimately opens already scrolled deep. Whether
    // the control hides at the top is asserted after the click below.

    await wheel(page, 400, 25)
    await page.waitForTimeout(600)
    expect(await scrollTop(page)).toBeGreaterThan(800)

    // The header has folded to its compact row...
    await expect(toggle).toContainText('Details')
    // ...and the roster button rode the fold instead of being hidden by it.
    await expect(agents).toBeVisible()
    expect(await page.evaluate(() => {
      const h = document.querySelector('[data-testid="trace-sticky-header"]')
      const b = document.querySelector('[data-testid="trace-agents-btn"]')
      return !!(h && b && h.contains(b))
    })).toBe(true)

    // Back-to-top returns the reader to the header in one click.
    await expect(backTop).toBeVisible()
    await backTop.click()
    await expect.poll(() => scrollTop(page), { timeout: 5_000 }).toBe(0)
    await expect(backTop).toBeHidden()
    await expect(toggle).toContainText('Hide details')
  })

  test('pinning a subagent row does not unfold its subtree', async ({ page }) => {
    const { traceId, startId } = await seedTallSession(page)
    await page.goto(`/trace/sessions/${traceId}`)

    const agentRow = page.locator(`[data-span-id="${startId}"]`)
    await expect(agentRow).toBeVisible({ timeout: 10_000 })
    const inAgent = page.getByTestId('in-agent-row')
    await expect(inAgent).toHaveCount(0)

    await agentRow.locator('.spine-pin').click()

    // The pin took effect (amber ring on the row)...
    await expect(agentRow).toHaveClass(/ring-amber-400/)
    // ...without dragging the agent's three internal spans into the spine.
    await expect(inAgent).toHaveCount(0)
    await expect(page.getByText(`${SUB_MARKER}0`)).toHaveCount(0)
  })

  test('a poll that appends spans leaves the scroll position alone', async ({ page }) => {
    const { traceId, sfx } = await seedTallSession(page)
    await page.goto(`/trace/sessions/${traceId}`)
    await expect(page.getByText('prompt number 0').first()).toBeVisible({ timeout: 10_000 })
    await page.waitForTimeout(1_500)

    // Select a row inside the auto-expanded last turn — i.e. at the bottom of
    // the feed — then read back up. The selection is now far below the
    // viewport, which is the state a poll used to break.
    await page.locator(`[data-span-id="a-${sfx}-19"]`).last().click()
    await page.waitForTimeout(800)
    await wheel(page, -600, 40)
    await page.waitForTimeout(1_000)
    const before = await scrollTop(page)
    expect(await page.evaluate(() => {
      const sel = document.querySelector('.event-selected')
      return sel ? sel.getBoundingClientRect().top > window.innerHeight : null
    })).toBe(true)

    // A live poll lands a new span.
    await post(page, [{
      trace_id: traceId, span_id: `late-${sfx}`, parent_id: `p-${sfx}-19`, name: 'tool.Read',
      start_time: '2026-05-09T10:19:30',
      attributes: { file_path: 'src/LATE_ARRIVAL.js', is_test: true },
    }])
    // The trace polls every ~4s; give it two ticks to land and settle.
    await page.waitForTimeout(9_000)

    // The feed may grow, but the reader's position must not be dragged back
    // down to the selection. Tolerance covers layout settling only.
    expect(Math.abs(await scrollTop(page) - before)).toBeLessThan(20)
  })
})
