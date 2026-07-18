/**
 * Regression: the shared ui/Select trigger must not collapse when its value
 * changes. Reka's <SelectValue> mirrors the selected item from its internal
 * collection and goes empty for one frame during the open→select→close
 * transition; with a width:auto trigger that collapsed the pill to just the
 * chevron and reflowed the sibling facet row — the desktop "filter blink".
 * Select.vue now renders the label from modelValue+options, so the trigger
 * text never empties. This drives the sessions facet pills and asserts the
 * changed pill never renders an empty value frame.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

function row(id) {
  return {
    trace_id: id, title: `W_${id.slice(0, 8)}`, status: 'ended', ended_reason: null, is_test: 0,
    started_at: '2026-07-10T11:00:00.000000', last_seen: '2026-07-10T12:00:00.000000',
    span_count: 3, file_edits: 1, tool_calls: 2, skill_reads: 0, rule_checks: 0, plans: 0, prompts: 1,
    agent_type: 'claude', agent_kind: 'claude', origin: 'session', category: 'user',
    is_workflow: false, is_run: false, model: null, cwd: null, repos: [], primary_repo: null,
    is_multi_repo: false, context_pct: null, context_pct_all: null, active_work_ms: null,
    active_pct: null, idle_ms: null, tags: [{ slug: 'user', source: 'auto', builtin: true }],
  }
}
const env = (rows) => ({
  items: rows, sessions: rows, pagination: { next_cursor: null, size: 50, has_next: false },
  tag_counts: { user: rows.length }, builtin_tags: [{ slug: 'user', label: 'User' }],
  server_now: '2026-07-10T13:00:00.000000', server_now_utc: '2026-07-10T09:00:00.000Z',
})

test('measure facet pill widths when a Select is clicked (desktop)', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 })
  await page.route('**/api/sessions?*', (r) => r.fulfill({ json: env([row(randomUUID())]) }))
  await page.goto('/trace/sessions')
  await expect(page.locator('form.session-filters')).toBeVisible()
  await page.waitForTimeout(300)

  // Sample each facet pill's width every frame for ~600ms while we click one.
  await page.evaluate(() => {
    window.__w = []
    let run = true
    window.__stop = () => { run = false }
    const tick = () => {
      const pills = [...document.querySelectorAll('.facet-pill')]
      window.__w.push(pills.map((p) => {
        const label = p.querySelector('.facet-pill__label')?.textContent?.trim() || '?'
        const trig = p.querySelector('.ds-select-trigger')
        const val = p.querySelector('.ds-select-value')?.textContent?.trim() || ''
        return `${label}:${Math.round(p.getBoundingClientRect().width)}/${trig ? Math.round(trig.getBoundingClientRect().width) : -1}[${val}]`
      }).join(' | '))
      if (run) requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  })

  // Change a DIFFERENT filter (Range) → triggers a reload that recomputes
  // tagOptions, which may re-render the Tag Select and collapse its trigger.
  await page.getByLabel('Filter by last activity time range').click()
  await page.getByRole('option', { name: 'All time' }).click()
  await page.waitForTimeout(700)

  const frames = await page.evaluate(() => { window.__stop(); return window.__w })
  // The changed pill (Range) must never render an empty value — an empty
  // frame is the collapse that reflows the row (the "blink").
  const rangeEmpty = frames.some((f) => /Range:\d+\/\d+\[\]/.test(f))
  const out = []
  for (const f of frames) if (out.at(-1) !== f) out.push(f)
  console.log('WIDTH FRAMES (label:pillW/trigW[value]):')
  out.forEach((f, i) => console.log(`  [${i}] ${f}`))
  expect(rangeEmpty, 'Range pill value went empty for a frame (trigger collapsed → row reflow)').toBe(false)
})
