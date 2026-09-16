/**
 * Focus mode's second card: the turn's newest WORK.
 *
 * Focus's one card answers "what did this turn produce", and it cannot also
 * answer "what is the agent on right now". On a turn that replied and then
 * went back to the shell those are different spans — the reply outranks a
 * `tool.Bash` on every rung of the headline ladder, so the run the reader came
 * to watch sat behind the strip with nothing on the spine naming it.
 *
 * The bar: at most TWO spine cards per turn — the headline and the turn's
 * newest work — collapsing to one whenever they are the same span. The second
 * card is newest *work*, not the positional last span: tier-3 noise is the
 * last row of roughly a quarter of real turns, and a `harness.recap` as a
 * turn's second card is exactly the noise the mode exists to remove.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

async function seed(page) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const base = Date.now() - 120_000
  const at = (i) => {
    const d = new Date(base + i * 1000)
    const p = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
      + `T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  }
  const spans = []
  let i = 0
  const prompt = (n) => {
    const id = `p${n}-${p8}`
    spans.push({ trace_id: traceId, span_id: id, parent_id: null, name: 'prompt',
      start_time: at(i++), attributes: { text: `MARKER prompt ${n}`, is_test: true } })
    return id
  }
  const add = (parent, name, attributes) => {
    spans.push({ trace_id: traceId, span_id: `s${i}-${p8}`, parent_id: parent, name,
      start_time: at(i), duration_ms: 100, attributes: { ...attributes, is_test: true } })
    i++
  }

  // Turn A — reply, then back to the shell. Two cards.
  const a = prompt(1)
  add(a, 'tool.Edit', { tool_name: 'Edit', file_path: '/repo/a.py', diff: '+ x\n- y' })
  add(a, 'assistant_response', { text: 'MARKER reply A' })
  add(a, 'tool.Bash', { tool_name: 'Bash', command: 'MARKER-LATEST-CMD --run',
    command_preview: 'MARKER-LATEST-CMD --run', stdout: 'ok' })

  // Turn B — the reply IS the newest work. One card.
  const b = prompt(2)
  add(b, 'tool.Bash', { tool_name: 'Bash', command: 'MARKER-EARLIER-CMD --run',
    command_preview: 'MARKER-EARLIER-CMD --run', stdout: 'ok' })
  add(b, 'assistant_response', { text: 'MARKER reply B' })

  // Turn C — the reply is the newest work, but a tier-3 recap came after it.
  // One card: "newest work" is not "newest span".
  const c = prompt(3)
  add(c, 'tool.Edit', { tool_name: 'Edit', file_path: '/repo/c.py', diff: '+ p\n- q' })
  add(c, 'assistant_response', { text: 'MARKER reply C' })
  add(c, 'harness.recap', { text: 'MARKER-RECAP-NOISE' })

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine').first()).toBeVisible({ timeout: 20_000 })
  return { traceId, a, b, c }
}

const turnBlock = (page, promptId) => page.locator(`div:has(> [data-span-id="${promptId}"])`).first()
const tiersOf = (block) => block.evaluate((el) => [...el.querySelectorAll('.event-spine-row')]
  .map((r) => r.getAttribute('data-tier') ?? 'strip'))

test('Focus shows the newest work beside the headline, at most two cards', async ({ page }) => {
  test.setTimeout(60_000)
  const { a, b, c } = await seed(page)
  await page.getByTestId('latest-toggle').click()

  // Turn A: the reply headlines at full prominence and the shell run that came
  // after it rides beside it as a one-liner. The Edit is the only span the
  // strip holds.
  const blockA = turnBlock(page, a)
  expect(await tiersOf(blockA)).toEqual(['strip', '1', '2'])
  await expect(blockA.locator('.event-spine-row[data-tier="1"]')).toContainText('MARKER reply A')
  await expect(blockA.locator('.event-spine-row[data-tier="2"]')).toContainText('MARKER-LATEST-CMD')
  await expect(blockA.getByTestId('turn-set-aside')).toHaveText('set aside')
  await expect(page.getByText('/repo/a.py')).toHaveCount(0)

  // Turn B: headline and newest work are the SAME span, so the turn stays at
  // one card — the second is never a duplicate row.
  const blockB = turnBlock(page, b)
  expect(await tiersOf(blockB)).toEqual(['strip', '1'])
  await expect(blockB.locator('.event-spine-row[data-tier="1"]')).toContainText('MARKER reply B')
  await expect(page.getByText('MARKER-EARLIER-CMD')).toHaveCount(0)

  // Turn C: a tier-3 recap is the turn's last SPAN but not its last WORK, so
  // it stays behind the strip and the turn keeps one card.
  const blockC = turnBlock(page, c)
  expect(await tiersOf(blockC)).toEqual(['strip', '1'])
  await expect(blockC.locator('.event-spine-row[data-tier="1"]')).toContainText('MARKER reply C')
  await expect(page.getByText('MARKER-RECAP-NOISE')).toHaveCount(0)

  // Nothing is filtered away: every strip still gives its whole turn back.
  await blockA.getByTestId('fold-strip').click()
  await expect(blockA.getByTestId('turn-set-aside')).toHaveText('all spans shown')
  // The strip anchors at the first set-aside span, so the Edit comes back in
  // its own place — right after it — as a card, not a dimmed one-liner.
  expect(await tiersOf(blockA)).toEqual(['strip', '1', '1', '2'])
  await expect(blockA.getByText('a.py')).toBeVisible()
  await blockC.getByTestId('fold-strip').click()
  await expect(page.getByText('MARKER-RECAP-NOISE')).toBeVisible()
})
