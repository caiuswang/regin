/**
 * End-to-end deep-link from rule-triggers drawer to session trace span.
 *
 * Seeds a synthetic prompt + rule.check span via /api/session-spans
 * (with is_test:true so the session stays out of the default sessions
 * view), plus a matching rule_trigger row tied to that rule.check
 * span. Then asserts that clicking the event row in the drawer routes
 * to /trace/sessions/<sid>?span=<spid>, and the session-trace view
 * loads with that span recognised in the URL.
 *
 * Tests bootstrap their own data per the portable-E2E pattern — no
 * hardcoded trace IDs, no shared DB fixture.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID } from 'node:crypto'

const API_BASE = 'http://localhost:8321'

async function seedFixture(page) {
  // Use timestamps that fall comfortably inside the default 7d range
  // (now - 1h gives us headroom for clock skew between runner and box).
  const traceId = randomUUID()
  const promptId = `prompt-${traceId.slice(0, 8)}`
  const checkId = `chk-${traceId.slice(0, 8)}`
  const ruleId = `e2e_deeplink_rule_${traceId.slice(0, 8)}`

  const now = new Date()
  const t0 = new Date(now.getTime() - 3600_000).toISOString().slice(0, 19).replace('T', ' ')
  const t1 = new Date(now.getTime() - 3500_000).toISOString().slice(0, 19).replace('T', ' ')

  // Span: prompt + rule.check (parent = prompt).
  const spanRes = await page.request.post(`${API_BASE}/api/session-spans`, {
    data: [
      {
        trace_id: traceId, span_id: promptId, parent_id: null,
        name: 'prompt', start_time: t0,
        attributes: { text: 'deep-link fixture prompt', is_test: true },
      },
      {
        trace_id: traceId, span_id: checkId, parent_id: promptId,
        name: 'rule.check', start_time: t1,
        attributes: {
          is_test: true,
          file_path: '/tmp/regin-e2e/Example.java',
          relative_path: 'Example.java',
          status: 'violation',
          applicable_rules: [
            { id: ruleId, severity: 'warn', summary: 'fixture',
              guide: null, match_count: 3, violated: true },
          ],
          engine_tags: [{ engine: 'grit', language: 'java' }],
          applicable_rule_count: 1,
          violating_rule_count: 1,
          total_rules: 1,
        },
      },
    ],
  })
  expect(spanRes.ok()).toBeTruthy()

  // Rule trigger row carrying the span_id (post-PR-2 ingest behavior).
  const trigRes = await page.request.post(`${API_BASE}/api/rule-triggers`, {
    data: {
      rule_id: ruleId, file_path: '/tmp/regin-e2e/Example.java',
      match_count: 3, severity: 'warn',
      session_id: traceId, span_id: checkId,
      source: 'e2e-test',
    },
  })
  expect(trigRes.ok()).toBeTruthy()

  return { traceId, promptId, checkId, ruleId }
}

test('drawer event row deep-links to span in session trace', async ({ page }) => {
  const { traceId, checkId, ruleId } = await seedFixture(page)

  await page.goto(`/trace/triggers?range=7d&search=${encodeURIComponent(ruleId)}`)
  // The card list is filtered by our unique rule_id so there's exactly one.
  const card = page.locator('article.rule-card').first()
  await expect(card).toBeVisible({ timeout: 10000 })
  await card.locator('button.rule-card__header').click()

  // Drawer mounts and fetches detail; the recent-events table appears.
  const eventRow = card.locator('.rule-drawer__table').last().locator('tbody tr').first()
  await expect(eventRow).toBeVisible({ timeout: 10000 })

  // The event row's session-link carries ?span=, ?t=, and the
  // view=conversation override so the session view always opens the
  // conversation tab (where surrounding prompts/responses live)
  // regardless of the user's localStorage default.
  const sessionLink = eventRow.locator('a').first()
  const href = await sessionLink.getAttribute('href')
  expect(href).toContain(`/trace/sessions/${traceId}`)
  expect(href).toContain(`span=${checkId}`)
  expect(href).toContain('view=conversation')

  // Pin localStorage to a non-conversation view to prove ?view= wins
  // over the saved preference. Honored on initial mount only — we
  // don't write back to localStorage so the user's default survives.
  await page.evaluate(() => localStorage.setItem('regin_session_view_mode', 'timeline'))

  await sessionLink.click()
  await page.waitForURL(`**/trace/sessions/${traceId}*`)
  expect(page.url()).toContain(`span=${checkId}`)
  expect(page.url()).toContain('view=conversation')

  // Conversation tab is visible and active. Other tabs (Timeline,
  // Terminal) exist as siblings so we use an active-state assertion
  // rather than just `toContainText('Conversation')`.
  const convoTab = page.locator('button', { hasText: 'Conversation' }).first()
  await expect(convoTab).toBeVisible({ timeout: 10000 })

  // Selection actually applied — rule.check spans nest under their
  // owning prompt and aren't in the initial shallow paginated load, so
  // the deep-link path falls back to the full-map fetch before
  // resolving. The rule.check row only renders inside the conversation
  // view when (a) the parent prompt is expanded and (b) the span is in
  // allSpans, so its "rule" label + `bg-blue-50` selection background
  // are jointly load-bearing.
  const selectedCheckRow = page.locator(
    'div.bg-blue-50:has-text("rule")'
  ).first()
  await expect(selectedCheckRow).toBeVisible({ timeout: 10000 })
  // The detail panel reflects the rule.check span's file_path
  // attribute because selectedSpan binds the aside content.
  await expect(page.locator('body')).toContainText('Example.java', { timeout: 10000 })
})
