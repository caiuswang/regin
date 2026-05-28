/**
 * Regression coverage for the rule.check row in the conversation view.
 *
 * Two invariants we keep tripping over and that are invisible to type
 * checks / unit tests:
 *
 *   1. Engine·language chip — the row must surface what kind of rules
 *      ran (`grit·vue`), so a user scanning the conversation can tell
 *      at a glance which checker fired.
 *
 *   2. Selection cleanliness — dragging across the row must produce a
 *      single-line string with real spaces, matching what's in the DOM.
 *      Old bug: flex children render as block-level for selection, so
 *      each span got a newline in `getSelection().toString()` and
 *      pasting into Cmd+F never matched anything in the page.
 *
 * Data is injected via the public `/api/session-spans` ingest endpoint
 * with `is_test=true`, so the synthetic trace is auto-hidden from the
 * sessions list and the test runs on any clean DB.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

const PROMPT_ATTRS = { text: 'rule-row regression fixture' }
const RULE_ATTRS = {
  is_test: true,
  file_path: '/tmp/regin-test/Example.vue',
  relative_path: 'Example.vue',
  status: 'ok',
  applicable_rules: [
    {
      id: 'no-direct-style',
      severity: 'warn',
      summary: 'avoid inline style attributes',
      guide: 'vue-style',
      match_count: 0,
      violated: false,
    },
  ],
  engine_tags: [{ engine: 'grit', language: 'vue' }],
  applicable_rule_count: 1,
  violating_rule_count: 0,
  total_rules: 1,
}

async function ingestFixture(page) {
  const traceId = randomUUID()
  const promptId = `prompt-${traceId.slice(0, 8)}`
  const ruleId = `rule-${traceId.slice(0, 8)}`
  const t0 = '2026-05-17T08:00:00.000000'
  const t1 = '2026-05-17T08:00:01.000000'

  const res = await page.request.post('/api/session-spans', {
    data: [
      {
        trace_id: traceId,
        span_id: promptId,
        parent_id: null,
        name: 'prompt',
        start_time: t0,
        attributes: { ...PROMPT_ATTRS, is_test: true },
      },
      {
        trace_id: traceId,
        span_id: ruleId,
        parent_id: promptId,
        name: 'rule.check',
        start_time: t1,
        attributes: RULE_ATTRS,
      },
    ],
  })
  expect(res.ok()).toBeTruthy()
  return { traceId, promptId, ruleId }
}

test('rule.check row renders engine·language chip', async ({ page }) => {
  const { traceId } = await ingestFixture(page)
  await page.goto(`/trace/sessions/${traceId}`)

  await expect(page.locator('text=grit·vue').first())
    .toBeVisible({ timeout: 10_000 })
})

test('rule.check row selection is single-line and matches DOM text', async ({ page }) => {
  const { traceId } = await ingestFixture(page)
  await page.goto(`/trace/sessions/${traceId}`)

  const chip = page.locator('text=grit·vue').first()
  await expect(chip).toBeVisible({ timeout: 10_000 })

  const row = chip.locator('xpath=ancestor::div[1]')
  await row.scrollIntoViewIfNeeded()
  const box = await row.boundingBox()
  expect(box).toBeTruthy()

  // Drag-select left-to-right across the row.
  await page.mouse.move(box.x + 2, box.y + box.height / 2)
  await page.mouse.down()
  await page.mouse.move(box.x + box.width - 2, box.y + box.height / 2, { steps: 30 })
  await page.mouse.up()

  const selected = await page.evaluate(() => window.getSelection().toString())
  // (1) No newlines — would break a Cmd+F paste.
  expect(selected.includes('\n')).toBe(false)
  // (2) Selected substring must appear verbatim in the page text,
  //     otherwise pasting into the find bar would return zero results.
  const found = await page.evaluate((needle) => {
    return document.body.innerText.includes(needle.trim())
  }, selected)
  expect(found).toBe(true)
})
