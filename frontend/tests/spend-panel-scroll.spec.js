/**
 * The "Overview · token spend" disclosure must let you reach its last row.
 *
 * Regression guard for a flex-shrink trap: the panel is a `max-h` scroll
 * container whose children are flex items, so with the default
 * `flex-shrink: 1` the two sections SHRANK to the cap instead of overflowing
 * it. Their own `overflow-hidden` (there for the rounded corners) then clipped
 * the squeezed-out rows, and because nothing overflowed the scroller,
 * `scrollHeight === clientHeight` — a scroll container with nothing to scroll.
 * Measured on the bug: 4 of 13 tool rows and 2 of 5 bill rows reachable.
 *
 * So the load-bearing assertion is NOT "does it overflow" — it is "is the last
 * row reachable", which is the thing the user could not do.
 *
 * Portable: seeds its own tool spans (is_test=true), enough of them to exceed
 * the panel's cap on any viewport.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

// Enough distinct tools that the leaderboard alone outgrows the 42vh cap.
const TOOLS = [
  'Read', 'Edit', 'Write', 'Bash', 'Grep', 'Glob', 'Task', 'WebFetch',
  'NotebookEdit', 'TodoWrite', 'WebSearch', 'MultiEdit', 'Agent', 'LSP',
]

async function seedSession(page) {
  const traceId = randomUUID()
  const prompt = `p-${traceId.slice(0, 8)}`
  const spans = [{
    trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
    start_time: '2026-05-04T10:00:00',
    attributes: { text: 'spend panel scroll demo', is_test: true },
  }]
  const toolCalls = []
  TOOLS.forEach((name, i) => {
    const tu = `tu${i}-${traceId.slice(0, 12)}`
    spans.push({
      trace_id: traceId, span_id: `sp${i}-${traceId.slice(0, 8)}`, parent_id: prompt,
      name: `tool.${name}`,
      start_time: `2026-05-04T10:00:${String(i + 1).padStart(2, '0')}`,
      attributes: { tool_name: name, file_path: `/repo/file_${i}.py`, tool_use_id: tu, is_test: true },
    })
    // Descending so the ranks are stable and every row carries a cost.
    toolCalls.push({ tool_use_id: tu, name, input_tokens: 9000 - i * 300, output_tokens: 400 })
  })

  // A priced turn is what makes the "Full session bill" section render at all —
  // without it the panel is leaderboard-only and the last-row check below would
  // be measuring half the panel.
  const turns = [{
    trace_id: traceId, turn_uuid: `t1-${traceId.slice(0, 8)}`, timestamp: '2026-05-04T10:00:30',
    model: 'claude-sonnet-4-5', input_tokens: 4200, output_tokens: 8800,
    cache_read_tokens: 620000, cache_creation_tokens: 48000, context_used_tokens: 42000,
  }]

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  const headers = { Authorization: `Bearer ${token}` }
  expect((await page.request.post('/api/session-spans', { headers, data: spans })).ok()).toBeTruthy()
  expect((await page.request.post('/api/turn-usage', { headers, data: turns })).ok()).toBeTruthy()
  const attrib = await page.request.post('/api/turn-usage/tool-attribution', {
    headers,
    data: { trace_id: traceId, turn_uuid: `t1-${traceId.slice(0, 8)}`, tool_calls: toolCalls },
  })
  expect(attrib.ok()).toBeTruthy()
  return traceId
}

test('spend panel: the last row is reachable by scrolling', async ({ page }) => {
  const traceId = await seedSession(page)

  await page.evaluate(() => localStorage.setItem('regin_session_view_mode', 'conversation'))
  await page.goto(`/trace/sessions/${traceId}`)

  const toggle = page.getByTestId('trace-spend-toggle')
  await toggle.click()

  const panel = page.getByTestId('trace-spend-scroll')
  await expect(panel).toBeVisible({ timeout: 10_000 })

  // Every seeded tool made it into the leaderboard — otherwise the reach
  // assertion below could pass on a panel that simply dropped its content.
  const rowCount = await panel.getByTestId('spend-tool-row').count()
  expect(rowCount).toBe(TOOLS.length)

  // Wait for the bill section before measuring anything: it mounts only once
  // /tool-rollup resolves, and measuring a half-built panel makes both
  // assertions below race the fetch.
  const total = panel.getByTestId('spend-bill-total')
  await expect(total).toBeAttached({ timeout: 10_000 })

  // The content genuinely exceeds the cap, so there is something to scroll.
  // RED on the bug: the sections shrank, leaving scrollHeight === clientHeight.
  const scrollable = await panel.evaluate(el => el.scrollHeight - el.clientHeight)
  expect(scrollable, 'spend panel has no scrollable overflow — its children shrank instead').toBeGreaterThan(50)

  // ...and scrolling actually lands the final row inside the visible box.
  const reached = await panel.evaluate(el => {
    el.scrollTop = el.scrollHeight
    const row = el.querySelector('[data-testid="spend-bill-total"]')
    if (!row) return null
    const p = el.getBoundingClientRect()
    const r = row.getBoundingClientRect()
    return { fits: r.bottom <= p.bottom + 1 && r.top >= p.top - 1, rowBottom: Math.round(r.bottom), panelBottom: Math.round(p.bottom) }
  })
  expect(reached, 'no Total spend row found in the panel').not.toBeNull()
  expect(
    reached.fits,
    `Total spend row still out of reach after scrolling: row bottom ${reached?.rowBottom} vs panel bottom ${reached?.panelBottom}`,
  ).toBe(true)
})

test('spend panel: the leaderboard fits a 390px phone', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const traceId = await seedSession(page)

  await page.evaluate(() => localStorage.setItem('regin_session_view_mode', 'conversation'))
  await page.goto(`/trace/sessions/${traceId}`)
  await page.getByTestId('trace-spend-toggle').click()

  const panel = page.getByTestId('trace-spend-scroll')
  await expect(panel).toBeVisible({ timeout: 10_000 })

  // Measuring overflow alone is NOT enough here: the name column is a `1fr`
  // track, so when the fixed columns claim more than the phone has, the track
  // collapses to 0px instead of overflowing and every overflow check stays
  // green on a visibly broken row. Assert the name column keeps real width.
  const MIN_NAME_W = 90
  const geometry = await panel.evaluate(el => {
    const rows = [...el.querySelectorAll('[data-testid="spend-tool-row"]')]
    const section = el.querySelector('section')
    return {
      names: rows.map(r => Math.round(r.children[1].getBoundingClientRect().width)),
      spill: section ? section.scrollWidth - section.clientWidth : 0,
    }
  })
  const starved = geometry.names.filter(w => w < MIN_NAME_W)
  expect(starved, `tool-name column collapsed below ${MIN_NAME_W}px: ${JSON.stringify(geometry.names)}`).toEqual([])
  expect(geometry.spill, 'leaderboard section spills horizontally on a phone').toBeLessThanOrEqual(1)

  // A grid cell defaults to min-width:auto, so a big token figure does not
  // shrink its column — it draws straight over the Calls column beside it.
  // Neither an overflow check nor a min-width check sees that, so compare the
  // painted text boxes directly.
  const collisions = await panel.evaluate(() => {
    const out = []
    for (const row of document.querySelectorAll('[data-testid="spend-tool-row"]')) {
      const cells = [...row.children].map(c => c.getBoundingClientRect())
      for (let i = 1; i < cells.length; i++) {
        if (cells[i].left < cells[i - 1].right - 0.5) {
          out.push(`row cell ${i - 1}→${i}: ${Math.round(cells[i - 1].right)} > ${Math.round(cells[i].left)}`)
        }
      }
    }
    return out
  })
  expect(collisions, `spend columns overlap on a phone: ${collisions.join('; ')}`).toEqual([])

  // Not overlapping is not the same as readable: a `truncate` cell clips
  // silently, and a clipped tabular number is a WRONG number — `116×` shown as
  // `11…`. Assert the numeric columns are wide enough for their own content.
  const clipped = await panel.evaluate(() => {
    const out = []
    for (const row of document.querySelectorAll('[data-testid="spend-tool-row"]')) {
      // Skip the tool-name cell (index 1): ellipsising a long tool name is the
      // intended behaviour there.
      for (const i of [2, 3, 4]) {
        const cell = row.children[i]
        if (!cell) continue
        for (const el of [cell, ...cell.querySelectorAll('*')]) {
          if (el.scrollWidth > el.clientWidth + 1 && el.textContent.trim()) {
            out.push(`${el.textContent.trim()} (${el.clientWidth}px box, ${el.scrollWidth}px content)`)
          }
        }
      }
    }
    return [...new Set(out)]
  })
  expect(clipped, `numeric spend values clipped mid-number: ${clipped.join('; ')}`).toEqual([])
})
