/**
 * The turn header's "N events" must equal the rows the spine actually renders.
 *
 * ConversationSpanCard is a v-if chain with no catch-all, so span families it
 * has no branch for (`turn`, `hook.*`, `cwd.*`, `session.title`, …) render
 * nothing. A header count computed from the raw descendant list therefore
 * advertised events the reader could not find — one real turn claimed 259 over
 * 61 rendered rows.
 *
 * `dispatch.js` is the single predicate both sides go through; this spec is
 * what keeps them honest, so a branch added to one and not the other fails
 * here instead of drifting quietly.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

test('turn header count equals the rendered spine rows', async ({ page }) => {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`
  const at = i => `2026-05-08T10:00:${String(i).padStart(2, '0')}`

  // Three spans that render, four that the dispatcher has no branch for.
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'event count demo', is_test: true } },
    { trace_id: traceId, span_id: `r1-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'tool.Read', start_time: at(1),
      attributes: { tool_name: 'Read', file_path: '/repo/a.py', is_test: true } },
    { trace_id: traceId, span_id: `r2-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'tool.Grep', start_time: at(2),
      attributes: { tool_name: 'Grep', pattern: 'needle', is_test: true } },
    { trace_id: traceId, span_id: `r3-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'assistant.thinking', start_time: at(3), attributes: { is_test: true } },
    // Undispatched: must count for nothing and render nothing.
    { trace_id: traceId, span_id: `u1-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'turn', start_time: at(4), attributes: { is_test: true } },
    { trace_id: traceId, span_id: `u2-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'cwd.changed', start_time: at(5), attributes: { cwd: '/repo', is_test: true } },
    { trace_id: traceId, span_id: `u3-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'hook.stop_summary', start_time: at(6), attributes: { is_test: true } },
    { trace_id: traceId, span_id: `u4-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: 'session.title', start_time: at(7), attributes: { title: 'x', is_test: true } },
  ]

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()

  await page.goto(`/trace/sessions/${traceId}?view=conversation`)

  const spine = page.locator('.event-spine')
  await expect(spine).toBeVisible({ timeout: 10_000 })

  const measured = await page.evaluate(() => {
    // The label shares its span with the `· ▾` disclosure glyph, so match
    // inside the string rather than requiring the whole node to be the count.
    const label = [...document.querySelectorAll('span')]
      .map(el => el.textContent.trim())
      .find(t => /^\d+ events? · [▴▾]$/.test(t))
    const rows = [...document.querySelectorAll('.event-spine-row')]
      .filter(el => getComputedStyle(el).display !== 'none')
    return { claimed: label ? parseInt(label, 10) : null, rendered: rows.length }
  })

  expect(measured.claimed, 'no "N events" label found on the turn header').not.toBeNull()
  expect(
    measured.claimed,
    `header claims ${measured.claimed} events but the spine rendered ${measured.rendered} rows`,
  ).toBe(measured.rendered)
  // ...and it is the dispatched subset, not the raw descendant list (7).
  expect(measured.rendered).toBe(3)
})

/**
 * Focus mode splits the same count in two — `N shown · M folded` — and the two
 * numbers must still add up to what the spine renders. `tierOf` lives beside
 * `rendersInConversation` in dispatch.js precisely so this can't drift: shown
 * counts the rows on screen, folded counts the ones behind the strip, and the
 * strip itself is not an event.
 */
test('focus mode: shown + folded equals the dispatched rows', async ({ page }) => {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`
  const at = i => `2026-05-08T11:00:${String(i).padStart(2, '0')}`
  const sid = n => `${n}-${traceId.slice(0, 8)}`

  // 2 spine rows (Tier 1 edit + Tier 2 read) and 3 that fold. Only spans the
  // card chain HAS a branch for can be folded — one with no branch (cwd.changed,
  // session.title, …) never reaches the feed in either mode, which is the
  // invariant the test above pins.
  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'focus mode demo', is_test: true } },
    { trace_id: traceId, span_id: sid('t1'), parent_id: prompt,
      name: 'assistant.thinking', start_time: at(1),
      attributes: { thinking_text: 'weighing the fold', is_test: true } },
    { trace_id: traceId, span_id: sid('k1'), parent_id: prompt, name: 'tool.Read',
      start_time: at(2),
      attributes: { tool_name: 'Read', file_path: '/repo/dispatch.js', is_test: true } },
    { trace_id: traceId, span_id: sid('c1'), parent_id: prompt, name: 'rule.check',
      start_time: at(3), attributes: { rule_id: 'no-orphan-branch', is_test: true } },
    { trace_id: traceId, span_id: sid('c2'), parent_id: prompt, name: 'harness.recap',
      start_time: at(4), attributes: { content: 're-injected task list', is_test: true } },
    { trace_id: traceId, span_id: sid('e1'), parent_id: prompt, name: 'tool.Edit',
      start_time: at(5),
      attributes: { tool_name: 'Edit', file_path: '/repo/a.py', diff: '+ x\n- y', is_test: true } },
  ]

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()

  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 10_000 })

  // Focus off: all five dispatched spans render, no strip.
  await expect(page.locator('.event-spine-row')).toHaveCount(5)
  await expect(page.getByTestId('fold-strip')).toHaveCount(0)

  await page.getByTestId('focus-toggle').click()

  // On: the three Tier 3 spans collapse behind one strip; the strip is an
  // anchor, not an event, so it does not inflate the shown count.
  const strip = page.getByTestId('fold-strip')
  await expect(strip).toBeVisible()
  await expect(strip).toContainText('background events')
  await expect(strip).toContainText('thinking')
  // The volume is carried by the proportion bar and the category names. No
  // numeral anywhere describes how many spans are behind the strip.
  await expect(strip).not.toContainText(/\d/)
  await expect(page.getByTestId('turn-set-aside')).toHaveText('set aside')

  const rows = await page.evaluate(() => [...document.querySelectorAll('.event-spine-row')]
    .filter(el => !el.querySelector('[data-testid="fold-strip"]')).length)
  // Only the two non-tier-3 spans are on the spine while the strip is closed;
  // the strip is an anchor, not an event, so it does not inflate that.
  expect(rows).toBe(2)

  // Expanding restores the folded spans in place — same order, dimmer.
  await strip.click()
  await expect(strip).toContainText('shown in place')
  await expect(page.locator('.event-spine-row[data-tier="3"]')).toHaveCount(3)
})
