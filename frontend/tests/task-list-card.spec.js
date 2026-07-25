/**
 * The conversation spine's TASK LIST card.
 *
 * `session.task_list.events` is an event log, not a list of snapshots — the
 * card replays it client-side so each task-write span shows the list AS OF
 * that span. The regression this guards is a card that renders the FINAL
 * state everywhere: it looks right on the last card and lies on every earlier
 * one, which no screenshot review catches.
 *
 * Also pinned here: task spans whose attributes carry no `task_id` never reach
 * the event log, so they must keep their generic inline row; and the three
 * statuses must be tellable apart WITHOUT colour (glyph / strike / label).
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

// Classify every task row by what a colour-blind reader can see: a check glyph
// (svg) inside the status dot, a struck-through subject, or an "in progress"
// label. Colour is deliberately not consulted.
function readCards() {
  return [...document.querySelectorAll('[data-testid="task-list-card"]')].map(card => {
    const spanId = card.closest('[data-span-id]')?.getAttribute('data-span-id') || ''
    const rows = [...card.querySelectorAll('[data-testid="task-list-row"]')].map(row => {
      const subject = row.querySelector('span:nth-child(2)')
      return {
        text: (subject?.textContent || '').trim(),
        struck: getComputedStyle(subject).textDecorationLine.includes('line-through'),
        hasCheckGlyph: !!row.querySelector('svg'),
        hasProgressLabel: /in progress/i.test(row.textContent || ''),
      }
    })
    return {
      spanId,
      counts: (card.querySelector('[data-testid="task-list-counts"]')?.textContent || '').trim(),
      rows,
    }
  })
}

test('task-list card snapshots each write span without future state', async ({ page }) => {
  const traceId = randomUUID()
  const sfx = traceId.slice(0, 8)
  const prompt = `p-${sfx}`
  const at = i => `2026-05-08T11:00:${String(i).padStart(2, '0')}`
  const id = n => `${n}-${sfx}`
  const taskSpan = (n, i, name, attrs) => ({
    trace_id: traceId, span_id: id(n), parent_id: prompt, name,
    start_time: at(i), attributes: { tool_name: name.slice(5), is_test: true, ...attrs },
  })

  const spans = [
    { trace_id: traceId, span_id: prompt, parent_id: null, name: 'prompt',
      start_time: at(0), attributes: { text: 'task list card demo', is_test: true } },
    taskSpan('c1', 1, 'tool.TaskCreate', { task_id: '1', subject: 'Wire the card' }),
    taskSpan('c2', 2, 'tool.TaskCreate', { task_id: '2', subject: 'Write the spec' }),
    taskSpan('c3', 3, 'tool.TaskCreate', { task_id: '3', subject: 'Measure contrast' }),
    taskSpan('u1', 4, 'tool.TaskUpdate', { task_id: '1', status: 'in_progress' }),
    taskSpan('u2', 5, 'tool.TaskUpdate', { task_id: '1', status: 'completed' }),
    taskSpan('u3', 6, 'tool.TaskUpdate', { task_id: '2', status: 'in_progress' }),
    // No task_id → the server emits no event for it → must stay an inline row.
    taskSpan('n1', 7, 'tool.TaskCreate', {}),
  ]

  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  expect(token).toBeTruthy()
  expect((await page.request.post('/api/session-spans', {
    headers: { Authorization: `Bearer ${token}` }, data: spans,
  })).ok()).toBeTruthy()

  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.event-spine')).toBeVisible({ timeout: 10_000 })
  await expect(page.locator('[data-testid="task-list-card"]').first()).toBeVisible()

  const cards = await page.evaluate(readCards)
  const byId = Object.fromEntries(cards.map(c => [c.spanId, c]))

  // One card per task-write span that produced an event; the task_id-less
  // TaskCreate is not one of them and keeps its inline row.
  expect(cards.map(c => c.spanId)).toEqual(['c1', 'c2', 'c3', 'u1', 'u2', 'u3'].map(id))
  await expect(page.locator(`[data-span-id="${id('n1')}"] [data-testid="task-list-card"]`))
    .toHaveCount(0)
  await expect(page.locator(`[data-span-id="${id('n1')}"]`)).toContainText('TaskCreate')

  // ── No future state ──────────────────────────────────────────
  // The first card knows about exactly one task, and nothing is done yet.
  expect(byId[id('c1')].rows.map(r => r.text)).toEqual(['Wire the card'])
  expect(byId[id('c1')].counts).toBe('1 open')
  expect(byId[id('c1')].rows.some(r => r.struck || r.hasCheckGlyph)).toBe(false)

  // At `u1` task 1 is only in progress — its later `completed` must not leak
  // back, and task 2's later `in_progress` must not either.
  const u1 = byId[id('u1')]
  expect(u1.rows.map(r => r.text)).toEqual(['Wire the card', 'Write the spec', 'Measure contrast'])
  expect(u1.rows.some(r => r.struck || r.hasCheckGlyph), 'future "completed" leaked into an earlier card').toBe(false)
  expect(u1.rows.map(r => r.hasProgressLabel)).toEqual([true, false, false])
  expect(u1.counts).toBe('1 active · 2 open')

  // ── Three statuses, distinguishable without colour ───────────
  const u3 = byId[id('u3')]
  expect(u3.rows.map(r => r.text)).toEqual(['Wire the card', 'Write the spec', 'Measure contrast'])
  const [done, active, open] = u3.rows
  expect({ check: done.hasCheckGlyph, struck: done.struck, label: done.hasProgressLabel })
    .toEqual({ check: true, struck: true, label: false })
  expect({ check: active.hasCheckGlyph, struck: active.struck, label: active.hasProgressLabel })
    .toEqual({ check: false, struck: false, label: true })
  expect({ check: open.hasCheckGlyph, struck: open.struck, label: open.hasProgressLabel })
    .toEqual({ check: false, struck: false, label: false })

  // ── Summary counts agree with the rows on every card ─────────
  for (const card of cards) {
    const tally = { done: 0, active: 0, open: 0 }
    for (const r of card.rows) {
      if (r.hasCheckGlyph && r.struck) tally.done++
      else if (r.hasProgressLabel) tally.active++
      else tally.open++
    }
    const expected = [
      tally.done ? `${tally.done} done` : '',
      tally.active ? `${tally.active} active` : '',
      tally.open ? `${tally.open} open` : '',
    ].filter(Boolean).join(' · ')
    expect(card.counts, `counts disagree with the rows on ${card.spanId}`).toBe(expected)
  }
})
