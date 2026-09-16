/**
 * Detail modes — Full / Condensed / Focus, the carried span, and the in-flight
 * span.
 *
 * Focus's tier folding is pinned next door in focus-mode.spec.js. What this
 * file covers is everything the three-mode split added: the control itself,
 * Focus's keep-set spine, the span each prompt carries from the turn above,
 * and the card a span renders as while it is still running.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

// Two turns, so the carried span has somewhere to come from. The second turn
// deliberately ENDS on a thinking span while its real work is an Edit earlier
// in the turn: that is the shape the positional "newest span" rule got wrong,
// and over 254 turns of the live store it got it wrong in 47 of them
// (`harness.recap` ×18, `assistant.thinking` ×14, `harness.local_command` ×13,
// `rule.check` ×2). Turn 1 has no tier-1 span at all, which pins the other
// half: a turn whose keep-set is empty must still render exactly one card.
async function seedTwoTurns(page, { running = false, runningEarly = false } = {}) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  // Naive-local and RECENT: serve-time merge demotes a PENDING span to
  // "⏹ interrupted" once its session has been idle past the stale window, so a
  // fixture dated in the past can never render as running.
  const base = Date.now() - 9000
  const at = (i) => {
    const d = new Date(base + i * 1000)
    const p = n => String(n).padStart(2, '0')
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
  const add = (parent, name, attributes, extra = {}) => {
    spans.push({ trace_id: traceId, span_id: `s${i}-${p8}`, parent_id: parent, name,
      start_time: at(i), duration_ms: 100, ...extra,
      attributes: { ...attributes, is_test: true } })
    i++
    return `s${i - 1}-${p8}`
  }

  const t1 = prompt(1)
  add(t1, 'assistant.thinking', { thinking_text: 'MARKER t1 thinking' })
  add(t1, 'tool.Read', { tool_name: 'Read', file_path: '/repo/one.js' })
  // The tail of turn 1 — this is the span turn 2 must carry.
  const t1Tail = add(t1, 'tool.Bash', {
    tool_name: 'Bash', command: 'MARKER-CARRIED-CMD --run',
    command_preview: 'MARKER-CARRIED-CMD --run', stdout: 'MARKER carried stdout',
    tokens: 4242,
  })

  const t2 = prompt(2)
  const runningBash = () => add(t2, 'tool.Bash', {
    tool_name: 'Bash', command: 'MARKER-RUNNING-CMD --watch',
    command_preview: 'MARKER-RUNNING-CMD --watch',
  }, { status_code: 'PENDING', duration_ms: null })
  // `runningEarly` puts the still-open run BEFORE the turn's edit, which is
  // what a parallel tool call looks like once the fast one lands: the edit then
  // wins Focus's headline and the run is the row Focus must keep anyway.
  if (runningEarly) runningBash()
  add(t2, 'tool.Edit', { tool_name: 'Edit', file_path: '/repo/two.py', diff: '+ a\n- b' })
  add(t2, 'rule.check', { rule_id: 'r', file_path: '/repo/two.py' })
  if (running) {
    runningBash()
  } else if (!runningEarly) {
    add(t2, 'assistant.thinking', { thinking_text: 'MARKER t2 tail thinking' })
  }

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine').first()).toBeVisible({ timeout: 20_000 })
  return { traceId, t1, t2, t1Tail }
}

const turnBlock = (page, promptId) => page.locator(`div:has(> [data-span-id="${promptId}"])`).first()

test('the Detail control is three modes, Conversation only, drawn not lettered', async ({ page }) => {
  test.setTimeout(60_000)
  const { traceId } = await seedTwoTurns(page)

  const control = page.getByTestId('detail-control')
  await expect(control).toBeVisible()
  await expect(control.locator('button')).toHaveCount(3)

  // Every item is an SVG stroke, never a font glyph: ⊟ / ↧ / ◐ render as tofu
  // on Windows and in PDF export.
  await expect(control.locator('svg')).toHaveCount(3)

  // Exactly one item is active, and it is the only one carrying a surface.
  await expect(control.locator('[aria-pressed="true"]')).toHaveCount(1)
  await expect(page.getByTestId('detail-full')).toHaveClass(/(^| )bg-surface( |$)/)
  await expect(page.getByTestId('focus-toggle')).not.toHaveClass(/(^| )bg-surface( |$)/)

  for (const view of ['timeline', 'terminal', 'messages']) {
    await page.goto(`/trace/sessions/${traceId}?view=${view}`)
    await expect(page.getByTestId('detail-control')).toHaveCount(0)
  }
})

test('Focus shows one card per turn: its newest USEFUL span', async ({ page }) => {
  test.setTimeout(60_000)
  const { t1, t2 } = await seedTwoTurns(page)
  await page.getByTestId('latest-toggle').click()

  // Exactly one card per turn — never two, never none.
  await expect(page.locator('.event-spine-row[data-tier="1"]')).toHaveCount(2)
  await expect(page.getByTestId('fold-strip')).toHaveCount(2)

  // Turn 2 ENDS on a thinking span but its work is the Edit. "Newest" alone
  // picked the thinking; "newest useful" picks the Edit.
  const t2Card = turnBlock(page, t2).locator('.event-spine-row[data-tier="1"]')
  await expect(t2Card).toContainText('two.py')
  await expect(t2Card).not.toContainText('MARKER t2 tail thinking')

  // Turn 1 has no outcome span and nothing that failed, so rung 1 is empty and
  // the ladder falls to the turn's own work — its last shell run. It still
  // renders exactly one card, not a bare strip that reads as an empty turn.
  await expect(turnBlock(page, t1).locator('.event-spine-row[data-tier="1"]'))
    .toContainText('MARKER-CARRIED-CMD')

  // No numeral describes the spans it set aside, in the strip or the header.
  await expect(page.getByTestId('fold-strip').first()).not.toContainText(/\d/)
  await expect(page.getByTestId('turn-set-aside').first()).toHaveText('set aside')

  // Focus opens every turn — but as a DEFAULT now, not a lock, so each one
  // keeps a working collapse trigger.
  await expect(page.locator('.event-spine')).toHaveCount(2)
  await expect(page.getByText('hide detail ▴')).toHaveCount(2)
})

// The strip is not a dump for background noise: Focus sets aside real work —
// edits, shell runs, whole subagent runs. "background events" would be
// describing the wrong session.
test('a Focus strip says it holds the rest of the turn, not background noise', async ({ page }) => {
  test.setTimeout(60_000)
  const { t2 } = await seedTwoTurns(page)

  await page.getByTestId('focus-toggle').click()
  await expect(turnBlock(page, t2).getByTestId('fold-strip'))
    .toContainText('background events')

  await page.getByTestId('latest-toggle').click()
  await expect(turnBlock(page, t2).getByTestId('fold-strip'))
    .toContainText('the rest of this turn')
})

test('a Focus strip puts set-aside spans back in their own places, one turn at a time', async ({ page }) => {
  test.setTimeout(60_000)
  const { t1, t2 } = await seedTwoTurns(page)
  await page.getByTestId('latest-toggle').click()

  const t2Block = turnBlock(page, t2)
  await t2Block.getByTestId('fold-strip').click()

  // Revealed spans come back as dimmed one-liners AT THEIR OWN POSITIONS: the
  // Edit stays first because that is where it happened, and the strip sits
  // where the first set-aside span was — after it, not above the whole turn.
  const tiers = await t2Block.evaluate(el => [...el.querySelectorAll('.event-spine-row')]
    .map(r => r.getAttribute('data-tier') ?? 'strip'))
  expect(tiers).toEqual(['1', 'strip', '3', '3'])
  await expect(t2Block.getByTestId('turn-set-aside')).toHaveText('all spans shown')

  // Opening one turn's strip leaves the other alone.
  const t1Tiers = await turnBlock(page, t1)
    .evaluate(el => [...el.querySelectorAll('.event-spine-row')]
      .map(r => r.getAttribute('data-tier') ?? 'strip'))
  expect(t1Tiers).toEqual(['strip', '1'])

  // Full → back to Focus returns to the same opened strip: switching mode must
  // not clear what the reader opened.
  await page.getByTestId('detail-full').click()
  await page.getByTestId('latest-toggle').click()
  await expect(t2Block.getByTestId('turn-set-aside')).toHaveText('all spans shown')
})

// Selecting a span reveals whatever hides it — but a span Focus KEPT is
// hidden by nothing, so clicking it must reveal nothing. It used to open that
// turn's strip unconditionally, so a plain click on a card the mode chose to
// show spilled every set-aside span back onto the spine.
test('opening a span Focus kept does not spill the turn back out', async ({ page }) => {
  test.setTimeout(60_000)
  const { t1, t2 } = await seedTwoTurns(page)
  await page.getByTestId('latest-toggle').click()

  const t2Block = turnBlock(page, t2)
  const kept = t2Block.locator('.event-spine-row[data-tier="1"]')
  const tiersOf = (block) => block.evaluate(el => [...el.querySelectorAll('.event-spine-row')]
    .map(r => r.getAttribute('data-tier') ?? 'strip'))

  await kept.click()
  expect(await tiersOf(t2Block), 'a click sprang the strip open').toEqual(['1', 'strip'])

  // Same selection, reached through the presentation reader: it opens BEHIND
  // the reader, so closing the reader used to leave the whole turn spilled.
  await kept.hover()
  await kept.locator('.present-open').click()
  await expect(page.getByTestId('span-present-reader')).toBeVisible()
  expect(await tiersOf(t2Block), 'the reader sprang the strip open behind itself')
    .toEqual(['1', 'strip'])
  await page.keyboard.press('Escape')
  await expect(page.getByTestId('span-present-reader')).toHaveCount(0)
  expect(await tiersOf(t2Block), 'the strip stayed open after the reader closed')
    .toEqual(['1', 'strip'])

  await expect(t2Block.getByTestId('turn-set-aside')).toHaveText('set aside')
  // And the neighbouring turn is untouched.
  await expect(turnBlock(page, t1).getByTestId('turn-set-aside')).toHaveText('set aside')
})

test('a folded turn keeps its own useful tail, once, without its bill', async ({ page }) => {
  test.setTimeout(60_000)
  const { t1, t2, t1Tail } = await seedTwoTurns(page)

  // Turn 1 is closed on arrival, so it shows its own tail under its chips.
  // Turn 2 is open, so its tail is on its spine and it shows none.
  const carried = page.getByTestId('carried-span')
  await expect(carried).toHaveCount(1)
  await expect(turnBlock(page, t1).getByTestId('carried-span')).toHaveCount(1)
  await expect(turnBlock(page, t2).getByTestId('carried-span')).toHaveCount(0)

  // Rendered, not summarised — the shell card, with its output.
  await expect(carried).toContainText('MARKER-CARRIED-CMD')

  // Spend belongs to the turn that incurred it, and position already says
  // where the span came from.
  await expect(carried).not.toContainText('tok')
  await expect(carried).not.toContainText(/from turn/i)

  // A reference, not a copy. It resolves to the ORIGINAL span id, and that id
  // appears exactly once in the document — duplicating the span into this
  // turn's own event list is what would make counts, exports and keyboard
  // walks see it twice.
  await expect(page.locator(`[data-span-id="${t1Tail}"]`)).toHaveCount(1)
  await expect(carried.locator(`[data-span-id="${t1Tail}"]`)).toHaveCount(1)

  // A 2px SOLID stem bridges the gap to the chips above. Solid vs dashed is
  // load-bearing here: solid is a span of THIS turn, and now it is one —
  // dashed used to mean the card had crossed a turn boundary.
  const stem = await carried.locator('.carried-stem').evaluate((el) => {
    const cs = getComputedStyle(el)
    return { style: cs.borderLeftStyle, width: cs.borderLeftWidth, height: cs.height }
  })
  expect(stem.style).toBe('solid')
  expect(stem.width).toBe('2px')
  expect(parseFloat(stem.height)).toBeGreaterThan(0)

  // …and lands on the SAME vertical line as the card's inner spine, or it
  // reads as two unrelated rules rather than one carried tail.
  const lines = await carried.evaluate((el) => {
    const s = el.querySelector('.carried-stem').getBoundingClientRect().left
    const inner = el.querySelector('.carried-body').firstElementChild.getBoundingClientRect().left
    return { stem: s, inner }
  })
  expect(Math.abs(lines.stem - lines.inner)).toBeLessThanOrEqual(1)

  // Clicking selects that original span: `P` reads the SELECTION, so the
  // reader opening on the carried command proves where the click landed.
  await carried.getByText('MARKER-CARRIED-CMD').click()
  await page.keyboard.press('p')
  const reader = page.getByTestId('span-present-reader')
  await expect(reader).toBeVisible()
  await expect(reader).toContainText('MARKER carried stdout')
  await page.keyboard.press('Escape')

  // Selecting it opens its own turn — and an open turn's spine already holds
  // that span, so the folded copy goes away. Two copies of one span on screen
  // is the bug this rule prevents.
  await expect(turnBlock(page, t1).locator('.event-spine')).toBeVisible()
  await expect(page.getByTestId('carried-span')).toHaveCount(0)

  // Focus expands every turn, so nothing is folded and nothing carries.
  await page.getByTestId('latest-toggle').click()
  await expect(page.getByTestId('carried-span')).toHaveCount(0)
})

// Folding a turn must not cost the reader what it produced — including the
// LAST turn, and including while it is still being written.
//
// This is the case that broke twice. The tail used to be placed RELATIVE to
// other turns (above the next prompt, with a special case for the last), and on
// a live session `merge_spans` keeps a PENDING `promptlive-` placeholder beside
// the turn in flight — so the trailing group is the placeholder, not the turn,
// and the tail was handed to a group that renders no card. It looked right on
// every settled fixture, because a newer prompt makes the placeholder stale and
// merge drops it. The placeholder below is what reproduces it.
//
// The fix is to key the tail on the turn that PRODUCED it, so no successor is
// consulted at all. A folded turn with a successor is covered next door, in
// 'a folded turn keeps its own useful tail'; this one pins the LAST turn.
//
// The span kept is also the newest USEFUL one, never the `harness.recap` that
// happened to come last — the same bug Focus's headline had.
test('folding the last turn keeps its newest useful span, never its recap', async ({ page }) => {
  test.setTimeout(90_000)
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const base = Date.now() - 300_000
  const at = (i) => {
    const d = new Date(base + i * 1000)
    const p = n => String(n).padStart(2, '0')
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
      start_time: at(i), duration_ms: 80, attributes: { ...attributes, is_test: true } })
    i++
  }
  const t1 = prompt(1)
  add(t1, 'assistant_response', { text: 'MARKER FIRST REPLY' })
  const t2 = prompt(2)
  add(t2, 'tool.Edit', { tool_name: 'Edit', file_path: '/repo/a.js', diff: '+x' })
  add(t2, 'assistant_response', { text: 'MARKER LAST REPLY' })
  // The last row of the last turn, and exactly what must NOT be carried.
  add(t2, 'harness.recap', { text: 'MARKER TRAILING RECAP' })

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()

  for (const mode of ['detail-full', 'focus-toggle', 'latest-toggle']) {
    await page.goto(`/trace/sessions/${traceId}?view=conversation`)
    await expect(page.locator('.event-spine').first()).toBeVisible({ timeout: 20_000 })
    await page.getByTestId(mode).click()

    // Turn 1 is collapsed on arrival, so turn 2 already carries ITS tail above
    // its own prompt. The trailing carry is a different row and is matched by
    // content, not by count.
    const trailing = turnBlock(page, t2).getByTestId('carried-span')
      .filter({ hasText: 'MARKER LAST REPLY' })

    // Expanded: the reply is on the spine, so nothing trails.
    await expect(page.getByText('MARKER LAST REPLY')).toBeVisible()
    await expect(trailing).toHaveCount(0)

    await turnBlock(page, t2).getByTestId('spine-fold-toggle').first().click()
    await expect(turnBlock(page, t2).locator('.event-spine')).toHaveCount(0)

    // Folded, and the outcome survives — under its OWN chips, so having no
    // next prompt to hang it above cannot matter.
    await expect(trailing).toHaveCount(1)

    // …and it is the reply, never the recap that happened to come last.
    await expect(page.getByTestId('carried-span')
      .filter({ hasText: 'MARKER TRAILING RECAP' })).toHaveCount(0)
  }
})

test('a running span renders as its own card in all three modes', async ({ page }) => {
  test.setTimeout(60_000)
  const { t2 } = await seedTwoTurns(page, { running: true })

  for (const mode of ['detail-full', 'focus-toggle', 'latest-toggle']) {
    await page.getByTestId(mode).click()
    const card = page.getByTestId('running-span-card')
    await expect(card).toHaveCount(1)
    await expect(card).toContainText('running')
    await expect(card).toContainText('MARKER-RUNNING-CMD')
    // Elapsed, not duration: duration is only written when the span closes.
    await expect(card.getByTestId('running-elapsed')).toBeVisible()
    // No output panel — an empty one reads as "finished with no output".
    await expect(card.locator('.code-surface')).toHaveCount(0)
    await expect(card).not.toContainText('stdout')
    await expect(card).not.toContainText('tok')
  }

  // A live turn arrives open, but every affordance that folds a finished turn
  // folds this one too — the running state picks the DEFAULT, it does not take
  // the choice away.
  await page.getByTestId('detail-full').click()
  await expect(turnBlock(page, t2).locator('.event-spine')).toBeVisible()
  await expect(turnBlock(page, t2).getByText('hide detail ▴')).toBeVisible()
  await turnBlock(page, t2).locator(`[data-span-id="${t2}"]`).click()
  await expect(turnBlock(page, t2).locator('.event-spine')).toHaveCount(0)
})

// The one-card rule yields to work still in flight. `tierOf` promotes a
// running span to Tier 1 in every mode; Focus's keep-set reads the headline id
// rather than the tier, so the promotion used to do nothing here and the only
// moving row on the page sat behind a strip that advertises its mix, not its
// liveness.
test('Focus keeps a running span on the spine when it is not the headline', async ({ page }) => {
  test.setTimeout(60_000)
  const { t2 } = await seedTwoTurns(page, { runningEarly: true })
  await page.getByTestId('latest-toggle').click()
  const block = turnBlock(page, t2)

  // Two Tier 1 rows and only two: the Edit landed after the run started so it
  // wins the headline, and the run is kept beside it rather than instead of it.
  const tier1 = block.locator('.event-spine-row[data-tier="1"]')
  await expect(tier1).toHaveCount(2)
  await expect(tier1.filter({ hasText: 'two.py' })).toHaveCount(1)
  // …and the run is on the spine anyway, without opening anything.
  await expect(block.getByTestId('fold-strip')).toHaveAttribute('aria-expanded', 'false')
  const card = block.getByTestId('running-span-card')
  await expect(card).toHaveCount(1)
  await expect(card).toContainText('MARKER-RUNNING-CMD')
  await expect(card).toContainText('running')
  // The remainder still folds — keeping the run is not "open the whole turn".
  await expect(block.getByTestId('fold-strip')).toHaveCount(1)
})

test('with motion still, the pulse stops but the running copy stays', async ({ page }) => {
  test.setTimeout(60_000)
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await seedTwoTurns(page, { running: true })

  const card = page.getByTestId('running-span-card')
  await expect(card).toContainText('running')
  const animation = await card.locator('.span-running-pip')
    .evaluate(el => getComputedStyle(el).animationName)
  expect(animation).toBe('none')
})

// N single-shape turns, each ending on an Edit whose path is its own marker.
// Deliberately more than RELOAD_DEEP_REFRESH_TAIL, so the early turns arrive
// with no children and only Focus's own hydration can fill them in.
async function seedFlatTurns(page, turns) {
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const base = Date.now() - 60_000
  const at = (i) => {
    const d = new Date(base + i * 1000)
    const p = n => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
      + `T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  }
  const spans = []
  let i = 0
  for (let n = 1; n <= turns; n++) {
    const pid = `p${n}-${p8}`
    spans.push({ trace_id: traceId, span_id: pid, parent_id: null, name: 'prompt',
      start_time: at(i++), attributes: { text: `MARKER prompt ${n}`, is_test: true } })
    spans.push({ trace_id: traceId, span_id: `a${n}-${p8}`, parent_id: pid, name: 'tool.Read',
      start_time: at(i++), duration_ms: 100,
      attributes: { tool_name: 'Read', file_path: `/repo/f${n}.js`, is_test: true } })
    spans.push({ trace_id: traceId, span_id: `b${n}-${p8}`, parent_id: pid, name: 'tool.Edit',
      start_time: at(i++), duration_ms: 100,
      attributes: { tool_name: 'Edit', file_path: `/repo/MARKER-TAIL-${n}.py`, diff: '+ a', is_test: true } })
  }

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine').first()).toBeVisible({ timeout: 20_000 })
  return traceId
}

test('Focus hydrates the turns it opens, past the deep-refresh tail', async ({ page }) => {
  test.setTimeout(90_000)
  // Only the last few roots come back with their subtree on a reload
  // (RELOAD_DEEP_REFRESH_TAIL), so turn 1 of a six-turn session arrives with
  // no children at all. Focus force-opens every turn WITHOUT going through
  // `togglePromptExpanded`, so unless it asks for those subtrees itself the
  // early turns render no spine — and the newest span it promises "body
  // intact" is not on the page at all.
  const TURNS = 6
  await seedFlatTurns(page, TURNS)

  await page.getByTestId('latest-toggle').click()

  // Every turn shows its own tail, including the ones the reload never
  // deep-loaded.
  await expect(page.locator('.event-spine-row[data-tier="1"]'))
    .toHaveCount(TURNS, { timeout: 20_000 })
  for (let n = 1; n <= TURNS; n++) {
    await expect(page.locator('.event-spine').first().page()
      .getByText(`MARKER-TAIL-${n}.py`).first()).toBeVisible()
  }
})

test('Focus asks for every turn it opened in ONE request', async ({ page }) => {
  test.setTimeout(90_000)
  // Focus force-opens the whole page window at once. Asked one root at a time
  // that is a deep fetch per turn — 50 on a full page, each re-running the
  // whole-session merge server side — so the entry must go out on the bulk
  // `/subtrees?ids=` endpoint instead.
  const TURNS = 6
  const perRoot = []
  const bulk = []
  page.on('request', (r) => {
    const url = r.url()
    if (/\/children\?deep=1/.test(url)) perRoot.push(url)
    if (/\/subtrees\?/.test(url)) bulk.push(url)
  })

  const traceId = await seedFlatTurns(page, TURNS)
  perRoot.length = 0
  bulk.length = 0

  await page.getByTestId('latest-toggle').click()
  await expect(page.locator('.event-spine-row[data-tier="1"]'))
    .toHaveCount(TURNS, { timeout: 20_000 })

  // Turn 1 is outside RELOAD_DEEP_REFRESH_TAIL, so the reconcile's own
  // /subtrees refresh never asks for it: a request carrying it can only be the
  // Focus entry. Exactly one of them, and no per-root fetch alongside.
  const oldest = `p1-${traceId.slice(0, 8)}`
  const carrying = bulk.filter(u =>
    (new URL(u).searchParams.get('ids') || '').split(',').includes(oldest))
  expect(carrying).toHaveLength(1)
  // How many of the tail the reconcile had already deep-loaded varies, so the
  // count is not fixed — that it is more than one is the whole claim.
  expect(new URL(carrying[0]).searchParams.get('ids').split(',').length)
    .toBeGreaterThan(1)
  expect(perRoot).toHaveLength(0)
})

test('a turn folds from the gutter at any depth in an opened run', async ({ page }) => {
  test.setTimeout(90_000)
  // 40 set-aside spans is taller than any viewport, so the strip that opened
  // them is off screen by the time the reader has read them — the gutter rides
  // the rail and is the only control still in reach.
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const base = Date.now() - 120_000
  const at = (i) => {
    const d = new Date(base + i * 1000)
    const p = n => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
      + `T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  }
  const pid = `p1-${p8}`
  const spans = [{ trace_id: traceId, span_id: pid, parent_id: null, name: 'prompt',
    start_time: at(0), attributes: { text: 'MARKER long turn', is_test: true } }]
  for (let n = 0; n < 40; n++) {
    spans.push({ trace_id: traceId, span_id: `s${n}-${p8}`, parent_id: pid, name: 'tool.Read',
      start_time: at(n + 1), duration_ms: 50,
      attributes: {
        tool_name: 'Read',
        // Long enough that a row which sized itself to its content would push
        // the feed wider than its column.
        file_path: `/repo/a-very-deeply-nested-directory-name/and-another-one/file-${n}-with-a-long-tail.js`,
        is_test: true,
      } })
  }
  spans.push({ trace_id: traceId, span_id: `tail-${p8}`, parent_id: pid, name: 'assistant_response',
    start_time: at(41), duration_ms: 50, attributes: { text: 'MARKER the tail reply', is_test: true } })

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine').first()).toBeVisible({ timeout: 20_000 })
  await page.getByTestId('latest-toggle').click()

  // Closed, the gutter folds the turn — the strip's own ▸ is the reveal.
  await expect(page.locator('[data-testid="spine-fold-toggle"][data-action="turn"]'))
    .toHaveCount(1)
  await page.getByTestId('fold-strip').click()
  // Opening a FOCUS strip hands the turn back at its natural tiers, so 40
  // `tool.Read` rows come back as tier-2 rows, not a wall of dimmed tier-3.
  await expect(page.locator('.event-spine-row[data-tier="2"]')).toHaveCount(40)

  const collapse = page.getByTestId('spine-fold-toggle')
  await expect(collapse).toHaveCount(1)

  // Revealed rows stay inside the feed and ellipsize — a wrapper box around
  // them would size itself to the nowrap subject lines and overflow.
  const widths = await page.evaluate(() => {
    const feed = document.querySelector('.conversation-feed')
    const box = feed.getBoundingClientRect()
    const rows = [...document.querySelectorAll('.event-spine-row[data-tier="2"]')]
    return {
      overflowing: rows.filter(r => r.getBoundingClientRect().right > box.right + 1).length,
      scrolls: feed.scrollWidth > feed.clientWidth + 1,
    }
  })
  expect(widths.overflowing, 'revealed rows spilled past the feed').toBe(0)
  expect(widths.scrolls, 'the feed grew a horizontal scrollbar').toBe(false)

  // It rides the rail: still on screen, and clear of every span card, from
  // deep inside the run.
  await page.locator('.event-spine-row[data-tier="2"]').nth(35).scrollIntoViewIfNeeded()
  const headVisible = await page.getByTestId('fold-strip').evaluate((el) => {
    const r = el.getBoundingClientRect()
    return r.bottom > 0 && r.top < window.innerHeight
  })
  expect(headVisible, 'the opening strip was still on screen — fixture too short').toBe(false)
  await expect(collapse).toBeInViewport()

  const geometry = await collapse.evaluate((el) => {
    const b = el.getBoundingClientRect()
    const spine = el.closest('.event-spine').getBoundingClientRect()
    const card = document.querySelector('.event-spine-row[data-tier="2"]').getBoundingClientRect()
    return {
      centre: b.left + b.width / 2, rail: spine.left + 1,
      cardLeft: card.left, right: b.right, size: b.width,
    }
  })
  // Centred on the turn's spine line, in the gutter, left of every card.
  expect(Math.abs(geometry.centre - geometry.rail)).toBeLessThanOrEqual(1.5)
  expect(geometry.right).toBeLessThanOrEqual(geometry.cardLeft)
  // Big enough to hit, and it WINS that gutter: the per-row pin shares this
  // column and carries `z-10`, so a smaller, lower button lost the click to
  // whichever pin the approach had just revealed.
  expect(geometry.size).toBeGreaterThanOrEqual(24)
  const onTop = await collapse.evaluate((el) => {
    const b = el.getBoundingClientRect()
    const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2)
    return hit === el || el.contains(hit)
  })
  expect(onTop, 'something in the gutter paints over the collapse button').toBe(true)

  // One click folds the whole turn — the escape from deep inside a revealed
  // run — and puts the reader back on the prompt card that now stands for it.
  await collapse.click()
  await expect(page.locator('.event-spine-row[data-tier="2"]')).toHaveCount(0)
  await expect(page.locator(`[data-span-id="${pid}"]`)).toBeInViewport()
  // The control stays, now offering the turn back.
  await expect(page.getByTestId('spine-fold-toggle'))
    .toHaveAttribute('data-action', 'expand')
})

// The gutter used to answer three different questions depending on the mode
// ('turn' / 'strip' / 'reveal'), because a Focus turn had no collapsed state
// for a turn-level control to read. Now that Focus is a default rather than a
// lock, every turn folds, so the gutter has exactly one job in every mode and
// the strip keeps its own ▸/▾ disclosure to itself.
test('the gutter folds the turn in every mode, and belongs to one turn', async ({ page }) => {
  test.setTimeout(90_000)
  const { t1, t2 } = await seedTwoTurns(page)

  for (const mode of ['detail-full', 'focus-toggle', 'latest-toggle']) {
    await page.getByTestId(mode).click()
    await expect(page.locator('[data-testid="spine-fold-toggle"][data-action="strip"]'))
      .toHaveCount(0)
    await expect(page.locator('[data-testid="spine-fold-toggle"][data-action="reveal"]'))
      .toHaveCount(0)
  }

  // Focus opens both turns, so both carry the FOLD half.
  const folds = page.locator('[data-testid="spine-fold-toggle"][data-action="turn"]')
  await expect(folds).toHaveCount(2)

  // Two stacked chevrons pointing up — not a caret on a rule, and not a
  // download arrow, which promises an export. And it rides rather than sits:
  // its wrapper is sticky, so the run has no closing strip at its bottom.
  const btn = turnBlock(page, t2).getByTestId('spine-fold-toggle')
  expect(await btn.locator('svg path').evaluateAll(ps => ps.map(p => p.getAttribute('d'))))
    .toEqual(['m17 11-5-5-5 5', 'm17 18-5-5-5 5'])
  expect(await btn.evaluate(el => getComputedStyle(el.parentElement).position)).toBe('sticky')
  await expect(page.getByTestId('fold-strip-tail')).toHaveCount(0)

  // An OPEN strip must not change what the gutter means. It briefly did —
  // the control silently became "close the strip", so clicking it left the
  // turn open and read as a dead button.
  await turnBlock(page, t2).getByTestId('fold-strip').click()
  await expect(turnBlock(page, t2).getByTestId('spine-fold-toggle'))
    .toHaveAttribute('data-action', 'turn')
  await turnBlock(page, t2).getByTestId('spine-fold-toggle').click()
  await expect(turnBlock(page, t2).locator('.event-spine')).toHaveCount(0)
  await turnBlock(page, t2).getByTestId('spine-fold-toggle').click()

  // Folding turn 2 leaves turn 1 exactly as it was.
  await btn.click()
  await expect(turnBlock(page, t2).locator('.event-spine')).toHaveCount(0)
  await expect(turnBlock(page, t2).getByTestId('spine-fold-toggle'))
    .toHaveAttribute('data-action', 'expand')
  await expect(turnBlock(page, t1).locator('.event-spine')).toBeVisible()
  await expect(turnBlock(page, t1).getByTestId('spine-fold-toggle'))
    .toHaveAttribute('data-action', 'turn')
})

test('every expanded turn folds from the gutter, in all three modes', async ({ page }) => {
  test.setTimeout(90_000)
  const { t1, t2 } = await seedTwoTurns(page)

  const folds = page.locator('[data-testid="spine-fold-toggle"][data-action="turn"]')
  for (const mode of ['detail-full', 'focus-toggle']) {
    await page.getByTestId(mode).click()
    // Only the newest turn auto-expands, so it is the one with a spine — and
    // the only one carrying the FOLD half. Turn 1 carries the expand half.
    // (Focus is covered next door: it opens BOTH turns by default.)
    await expect(folds).toHaveCount(1)
    const t2Block = turnBlock(page, t2)
    await expect(t2Block.getByTestId('spine-fold-toggle'))
      .toHaveAttribute('title', 'Fold this turn back to its chips')

    // Open turn 1 as well: both expanded turns get one.
    await turnBlock(page, t1).locator(`[data-span-id="${t1}"]`).click()
    await expect(folds).toHaveCount(2)

    // It folds the turn to its chips — the spine goes, the chip preview
    // arrives, and the neighbouring turn is untouched.
    await turnBlock(page, t1).getByTestId('spine-fold-toggle').click()
    await expect(turnBlock(page, t1).locator('.event-spine')).toHaveCount(0)
    await expect(turnBlock(page, t1).getByText('show detail ▾')).toBeVisible()
    await expect(t2Block.locator('.event-spine')).toBeVisible()
  }
})

// A running span used to blank the gutter in every mode: `live` was read as a
// prohibition rather than a starting position, so the fold had nothing to do
// and hid itself for as long as anything was still running — which is exactly
// when the feed is longest.
test('a live turn keeps its gutter control, in all three modes', async ({ page }) => {
  test.setTimeout(90_000)
  const { t2 } = await seedTwoTurns(page, { running: true })

  for (const [mode, action] of [['detail-full', 'turn'], ['focus-toggle', 'turn'],
    ['latest-toggle', 'turn']]) {
    await page.getByTestId(mode).click()
    await expect(turnBlock(page, t2).locator('.event-spine')).toBeVisible()
    await expect(turnBlock(page, t2).getByTestId('spine-fold-toggle'))
      .toHaveAttribute('data-action', action)
  }
})

test('a live turn opens by default but the reader can still fold it away', async ({ page }) => {
  test.setTimeout(60_000)
  const { t2 } = await seedTwoTurns(page, { running: true })
  const block = () => turnBlock(page, t2)

  // Nobody clicked anything: the running turn is open on arrival.
  await expect(block().locator('.event-spine')).toBeVisible()

  await block().getByTestId('spine-fold-toggle').click()
  await expect(block().locator('.event-spine')).toHaveCount(0)
  await expect(block().getByTestId('spine-fold-toggle'))
    .toHaveAttribute('data-action', 'expand')

  // The default must not win it back on the next live poll — an explicit
  // collapse is a state, not a momentary suppression. Waits on the poll
  // itself, not on a clock.
  await page.waitForResponse((r) => /\/map\b/.test(r.url()), { timeout: 30_000 })
  await expect(block().locator('.event-spine')).toHaveCount(0)

  await block().getByTestId('spine-fold-toggle').click()
  await expect(block().locator('.event-spine')).toBeVisible()
  await expect(block().getByTestId('spine-fold-toggle'))
    .toHaveAttribute('data-action', 'turn')
})

test('the gutter answers both halves: the same control folds and reopens a turn', async ({ page }) => {
  test.setTimeout(60_000)
  const { t1, t2 } = await seedTwoTurns(page)

  // Turn 1 arrives collapsed, so it carries the expand half — a single chevron
  // pointing down, in the same gutter column as the fold half.
  const t1Block = turnBlock(page, t1)
  const t1Toggle = t1Block.getByTestId('spine-fold-toggle')
  await expect(t1Toggle).toHaveAttribute('data-action', 'expand')
  await expect(t1Toggle).toHaveAttribute('title', "Show this turn's spans")
  expect(await t1Toggle.locator('svg path').evaluateAll(ps => ps.map(p => p.getAttribute('d'))))
    .toEqual(['m6 9 6 6 6-6'])

  // Both halves sit on one vertical line — the turn's rail — whichever state
  // the turn is in.
  const columnOf = (loc) => loc.evaluate((el) => {
    const b = el.getBoundingClientRect()
    const feed = document.querySelector('.conversation-feed').getBoundingClientRect()
    return b.left + b.width / 2 - feed.left
  })
  const expandColumn = await columnOf(t1Toggle)
  const foldColumn = await columnOf(turnBlock(page, t2).getByTestId('spine-fold-toggle'))
  expect(Math.abs(expandColumn - foldColumn)).toBeLessThanOrEqual(1)

  // It opens the turn, and turns into the fold half in place.
  await t1Toggle.click()
  await expect(t1Block.locator('.event-spine')).toBeVisible()
  await expect(t1Block.getByTestId('spine-fold-toggle')).toHaveAttribute('data-action', 'turn')

  // …and folds it back.
  await t1Block.getByTestId('spine-fold-toggle').click()
  await expect(t1Block.locator('.event-spine')).toHaveCount(0)
  await expect(t1Block.getByTestId('spine-fold-toggle')).toHaveAttribute('data-action', 'expand')
})

// Focus opens every turn, but as a DEFAULT: a default with no closed state is
// a toggle that silently does nothing, and Focus used to be exactly that.
test('a Focus turn can be folded away and stays folded', async ({ page }) => {
  test.setTimeout(60_000)
  const { t2 } = await seedTwoTurns(page)
  await page.getByTestId('latest-toggle').click()

  const t2Block = turnBlock(page, t2)
  const toggle = t2Block.getByTestId('spine-fold-toggle')
  await expect(toggle).toHaveAttribute('data-action', 'turn')
  await expect(toggle).toHaveAttribute('title', 'Fold this turn back to its chips')

  await toggle.click()
  await expect(t2Block.locator('.event-spine')).toHaveCount(0)
  await expect(t2Block.getByText('show detail ▾')).toBeVisible()

  // Leaving Focus and coming back must not resurrect it: the collapse is a
  // state the reader chose, not a momentary suppression of the default.
  await page.getByTestId('detail-full').click()
  await page.getByTestId('latest-toggle').click()
  await expect(t2Block.locator('.event-spine')).toHaveCount(0)

  await t2Block.getByTestId('spine-fold-toggle').click()
  await expect(t2Block.locator('.event-spine')).toBeVisible()
})

// A collapsed turn's chips are the entire account of what the turn did — it
// has no strip to reveal the rest — so they name its WORK in every mode, and
// leave out only the tier-3 noise that would vanish on expand. Focus narrowing
// them to its own one-card keep-set is what made a turn that ran three test
// suites read as `Edit`, with no way to learn a shell had run at all.
test('a collapsed turn\'s chips name its work in Focus too, minus the tier-3 noise', async ({ page }) => {
  test.setTimeout(60_000)
  const { t1, t2 } = await seedTwoTurns(page)
  await page.getByTestId('latest-toggle').click()

  const t2Block = turnBlock(page, t2)
  await t2Block.getByTestId('spine-fold-toggle').click()
  await expect(t2Block.getByText('show detail ▾')).toBeVisible()

  const chips = t2Block.getByTestId('turn-tool-chips')
  await expect(chips).toContainText('Edit')
  // Tier 3 still stays out: it is the noise the mode exists to remove, and the
  // open turn folds it.
  await expect(chips).not.toContainText('rule')

  // Turn 1's only work is a Read and a shell run — neither survives Focus's
  // keep-set, so its collapsed chips used to be empty of everything but the
  // `show detail` trigger.
  const t1Block = turnBlock(page, t1)
  await t1Block.getByTestId('spine-fold-toggle').click()
  const t1Chips = t1Block.getByTestId('turn-tool-chips')
  await expect(t1Chips).toContainText('Shell')
  await expect(t1Chips).toContainText('Read')
})

// "set aside" / "all spans shown" describe a STRIP, and a collapsed turn has
// none — the label sat on a folded turn claiming every span was on screen
// while the turn showed four chips, and its button revealed nothing.
test('a collapsed turn\'s header counts its events instead of describing a strip', async ({ page }) => {
  test.setTimeout(60_000)
  const { t2 } = await seedTwoTurns(page)
  await page.getByTestId('latest-toggle').click()
  const t2Block = turnBlock(page, t2)

  // Open, the strip is real and the label describes it.
  await expect(t2Block.getByTestId('turn-set-aside')).toHaveText('set aside')
  await t2Block.getByTestId('turn-set-aside').click()
  await expect(t2Block.getByTestId('turn-set-aside')).toHaveText('all spans shown')

  // Folded — with the strip open, which is the state that produced the lie.
  await t2Block.getByTestId('spine-fold-toggle').click()
  await expect(t2Block.getByText('show detail ▾')).toBeVisible()
  await expect(t2Block.getByTestId('turn-set-aside')).toHaveCount(0)
})

// A subagent is ONE unit in Focus. Expanding it flattens its whole run onto
// the parent turn's spine, where its shell calls are indistinguishable from
// work the parent did — 1194 such rows across 254 turns of the live store.
// Focus folds the entire run behind the strip even though the reader expanded
// the agent, and even the child that FAILED: the turn gets one card, and a
// child's failure is not the turn's newest useful span when the agent went on
// to answer. The strip's mix bar still names the `failures` bucket, and one
// click brings the whole run back.
test('Focus keeps the subagent card and puts its run behind the strip, failures included', async ({ page }) => {
  test.setTimeout(60_000)
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const base = Date.now() - 120_000
  const at = (i) => {
    const d = new Date(base + i * 1000)
    const p = n => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
      + `T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  }
  const pid = `p1-${p8}`
  const aid = `ag-${p8}`
  const spans = [
    { trace_id: traceId, span_id: pid, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'MARKER agent turn', is_test: true } },
    { trace_id: traceId, span_id: aid, parent_id: pid, name: 'subagent.start',
      start_time: at(1), duration_ms: 100,
      attributes: { agent_type: 'Explore', description: 'MARKER the agent', is_test: true } },
  ]
  for (let n = 0; n < 5; n++) {
    spans.push({ trace_id: traceId, span_id: `c${n}-${p8}`, parent_id: aid, name: 'tool.Bash',
      start_time: at(2 + n), duration_ms: 50,
      attributes: { tool_name: 'Bash', command: `MARKER-CHILD-CMD-${n}`,
        command_preview: `MARKER-CHILD-CMD-${n}`, stdout: 'ok', is_test: true } })
  }
  spans.push({ trace_id: traceId, span_id: `cf-${p8}`, parent_id: aid, name: 'tool.Bash',
    start_time: at(9), duration_ms: 50,
    attributes: { tool_name: 'Bash', command: 'MARKER-CHILD-BOOM',
      command_preview: 'MARKER-CHILD-BOOM', stderr: '3 errors', is_test: true } })
  spans.push({ trace_id: traceId, span_id: `tail-${p8}`, parent_id: pid, name: 'assistant_response',
    start_time: at(10), duration_ms: 50,
    attributes: { text: 'MARKER the reply', is_test: true } })

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine').first()).toBeVisible({ timeout: 20_000 })

  // Full: the agent's steps sit on the parent spine under its card.
  await expect(page.getByText('MARKER-CHILD-CMD-3')).toBeVisible()

  // Focus puts the run away — steps and the failing step — and leaves the
  // turn's useful card, the reply, PLUS the agent's card: it is the parent's
  // whole account of the agent (goal, result, "Agent view →"), so it is never
  // set aside, strip open or closed.
  await page.getByTestId('latest-toggle').click()
  await expect(page.getByText('MARKER the reply')).toBeVisible()
  await expect(page.locator('.event-spine-row[data-tier="1"]')).toHaveCount(2)
  await expect(page.getByText('MARKER-CHILD-CMD-3')).toHaveCount(0)
  await expect(page.getByText('MARKER-CHILD-BOOM')).toHaveCount(0)
  await expect(page.locator(`[data-span-id="${aid}"]`)).toBeVisible()

  // Nothing is filtered: the strip names what it holds and gives it all back.
  const strip = page.getByTestId('fold-strip').first()
  await expect(strip).toContainText('failures')
  await strip.click()
  await expect(page.getByText('MARKER-CHILD-CMD-3')).toBeVisible()
  await expect(page.getByText('MARKER-CHILD-BOOM')).toBeVisible()
  await expect(page.locator(`[data-span-id="${aid}"]`)).toBeVisible()
})

// Opening a Focus strip used to dim EVERY row it gave back to a tier-3
// one-liner. A `subagent.start` rendered that way is a CondensedRow — which
// loses the card's controls, the "Agent view →" among them.
test('a revealed Focus turn comes back at its natural tiers, the agent card intact', async ({ page }) => {
  test.setTimeout(60_000)
  const traceId = randomUUID()
  const p8 = traceId.slice(0, 8)
  const base = Date.now() - 120_000
  const at = (i) => {
    const d = new Date(base + i * 1000)
    const p = n => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
      + `T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
  }
  const pid = `p1-${p8}`
  const aid = `ag-${p8}`
  const spans = [
    { trace_id: traceId, span_id: pid, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'MARKER turn', is_test: true } },
    { trace_id: traceId, span_id: `ed-${p8}`, parent_id: pid, name: 'tool.Edit',
      start_time: at(1), duration_ms: 50,
      attributes: { tool_name: 'Edit', file_path: '/repo/a.js', diff: '+x', is_test: true } },
    { trace_id: traceId, span_id: aid, parent_id: pid, name: 'subagent.start',
      start_time: at(2), duration_ms: 900,
      attributes: { agent_type: 'Explore', description: 'MARKER agent', is_test: true } },
    { trace_id: traceId, span_id: `c1-${p8}`, parent_id: aid, name: 'tool.Read',
      start_time: at(3), duration_ms: 50,
      attributes: { tool_name: 'Read', file_path: '/repo/MARKER-CHILD.js', is_test: true } },
    { trace_id: traceId, span_id: `rp-${p8}`, parent_id: pid, name: 'assistant_response',
      start_time: at(4), duration_ms: 50, attributes: { text: 'MARKER reply', is_test: true } },
  ]
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()
  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine').first()).toBeVisible({ timeout: 20_000 })
  await page.getByTestId('latest-toggle').click()
  const agent = page.locator(`[data-span-id="${aid}"]`)
  // Strip closed: the agent's card is on the spine at its own tier; its
  // step and the Edit are behind the strip.
  await expect(agent).toHaveAttribute('data-tier', '1')
  await expect(page.getByText('MARKER-CHILD.js')).toHaveCount(0)
  await expect(page.locator(`[data-span-id="ed-${p8}"]`)).toHaveCount(0)

  await page.getByTestId('fold-strip').click()
  // Revealed: the Edit is a card again and the agent's step comes back under
  // it — not a dimmed tier-3 one-liner. Only genuine noise stays at tier 3.
  await expect(agent).toHaveAttribute('data-tier', '1')
  await expect(page.locator(`[data-span-id="ed-${p8}"]`)).toHaveAttribute('data-tier', '1')
  await expect(page.getByText('MARKER-CHILD.js')).toBeVisible()
})
