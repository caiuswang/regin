/**
 * Regression: deep-link jump to an orphan rule.check whose chronologically
 * nearest prompt is a RETIRED `promptlive-` placeholder.
 *
 * The store is append-only, so a live `promptlive-<hash>` prompt placeholder
 * coexists with the real `prompt-<uuid>` anchor that later supersedes it. The
 * serve-time merge drops the placeholder and grafts NULL-parent orphans
 * (rule.check) under the surviving anchor. The `/spans/<id>/ancestors`
 * endpoint that the `?span=` deep-link uses to find the subtree root must
 * mirror THAT merged shape — if it resolves the chain off raw rows it lands on
 * the retired placeholder, a root the frontend never renders, so the subtree
 * load misses and the jump silently fails.
 *
 * This seeds exactly that arrangement (placeholder + promoted anchor + orphan
 * rule.check) and asserts the deep-link selects the rule.check span. It fails
 * against the pre-fix raw-row ancestors walk and passes once ancestors walks
 * the merged span set.
 *
 * Tests bootstrap their own data per the portable-E2E pattern — no hardcoded
 * trace IDs, no shared DB fixture.
 */
import { test, expect } from './auth-fixture.js'
import { randomUUID, createHash } from 'node:crypto'

const API_BASE = 'http://localhost:8321'

// Mirror lib/trace/pending_spans.prompt_placeholder_id: sha1 of
// `<session>\x00<text-prefix>`, first 13 hex chars, `promptlive-` prefixed.
function placeholderId(sessionId, text) {
  const key = `${sessionId || ''}\x00${(text || '').trim().slice(0, 512)}`
  const digest = createHash('sha1').update(key, 'utf8').digest('hex').slice(0, 13)
  return `promptlive-${digest}`
}

async function seedFixture(page) {
  const traceId = randomUUID()
  const promptText = `placeholder deep-link fixture ${traceId.slice(0, 8)}`
  const anchorId = `prompt-${traceId.slice(0, 8)}`
  const placeholder = placeholderId(traceId, promptText)
  const checkId = `chk-${traceId.slice(0, 8)}`
  const ruleId = `e2e_ph_rule_${traceId.slice(0, 8)}`

  const now = new Date()
  const iso = (ms) => new Date(now.getTime() - ms).toISOString().slice(0, 19).replace('T', ' ')
  // Anchor first, then the placeholder a touch LATER (so a raw chronological
  // walk would pick the placeholder as the nearest prompt), then the orphan.
  const tAnchor = iso(3600_000)
  const tPlaceholder = iso(3590_000)
  const tCheck = iso(3500_000)

  const spanRes = await page.request.post(`${API_BASE}/api/session-spans`, {
    data: [
      // Real promoted anchor — carries the prompt text so the merge correlates
      // and drops the placeholder by text hash.
      {
        trace_id: traceId, span_id: anchorId, parent_id: null,
        name: 'prompt', start_time: tAnchor,
        attributes: { text: promptText, is_test: true },
      },
      // Live placeholder for the same prompt — PENDING, retired by the merge.
      {
        trace_id: traceId, span_id: placeholder, parent_id: null,
        name: 'prompt', start_time: tPlaceholder, status_code: 'PENDING',
        attributes: { text: promptText, is_test: true },
      },
      // Orphan rule.check (parent_id null) — the merge grafts it under the
      // surviving anchor; the deep-link must resolve its root to that anchor.
      {
        trace_id: traceId, span_id: checkId, parent_id: null,
        name: 'rule.check', start_time: tCheck,
        attributes: {
          is_test: true,
          file_path: '/tmp/regin-e2e/Placeholder.java',
          relative_path: 'Placeholder.java',
          status: 'violation',
          applicable_rules: [
            { id: ruleId, severity: 'warn', summary: 'fixture',
              guide: null, match_count: 2, violated: true },
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

  return { traceId, anchorId, placeholder, checkId, ruleId }
}

test('deep-link selects an orphan rule.check past a retired placeholder', async ({ page }) => {
  const { traceId, checkId } = await seedFixture(page)

  await page.goto(`/trace/sessions/${traceId}?span=${checkId}&view=conversation`)
  await page.waitForURL(`**/trace/sessions/${traceId}*`)

  // Selection actually applied: the orphan rule.check is grafted under the
  // surviving anchor, materialised by the deep-link subtree fetch, expanded,
  // and highlighted. The `rule` label + `bg-blue-50` background are jointly
  // load-bearing — both miss if the ancestors root is the retired placeholder.
  const selectedCheckRow = page.locator('div.bg-blue-50:has-text("rule")').first()
  await expect(selectedCheckRow).toBeVisible({ timeout: 10000 })
  await expect(page.locator('body')).toContainText('Placeholder.java', { timeout: 10000 })
})
