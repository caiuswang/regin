/**
 * Suppress a rule from inside the session-trace view.
 *
 * The user discovers a false positive while reading a session
 * conversation, clicks the rule.check row, and 🔇s the offending rule
 * from the side panel — without leaving the trace. This spec wires the
 * full flow:
 *   ingest spans + a backing rule_trigger row → open the session →
 *   click the rule.check row → click the 🔇 button → assert the row
 *   strikes out and the backing row's suppressed flag flips.
 */
import { test as base, expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const API_BASE = 'http://localhost:8321'

// Resolve the repo root from this spec's location so the Python helper
// runs on any machine/CI checkout, not just the author's.
const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..')
const PYTHON = path.join(REPO_ROOT, '.venv', 'bin', 'python')

function mintAdminToken() {
  return execFileSync(
    PYTHON,
    ['-c', 'from lib.auth import create_token; print(create_token(1, "convview-admin", "admin"))'],
    { cwd: REPO_ROOT, encoding: 'utf8' },
  ).trim()
}

const test = base.extend({
  page: async ({ page }, use) => {
    const token = mintAdminToken()
    const user = { id: 1, username: 'convview-admin', role: 'admin' }
    await page.addInitScript(({ token, user }) => {
      localStorage.setItem('regin_auth_token', token)
      localStorage.setItem('regin_auth_user', JSON.stringify(user))
    }, { token, user })
    await use(page)
  },
})

test('suppress a rule from the session conversation view', async ({ page }) => {
  const traceId = randomUUID()
  const promptId = `prompt-${traceId.slice(0, 8)}`
  const checkId = `chk-${traceId.slice(0, 8)}`
  const ruleId = `e2e_conv_${traceId.slice(0, 8)}`

  const now = new Date()
  const t0 = new Date(now.getTime() - 3600_000).toISOString().slice(0, 19).replace('T', ' ')
  const t1 = new Date(now.getTime() - 3500_000).toISOString().slice(0, 19).replace('T', ' ')

  // Span: prompt + rule.check.
  const spanRes = await page.request.post(`${API_BASE}/api/session-spans`, {
    data: [
      {
        trace_id: traceId, span_id: promptId, parent_id: null,
        name: 'prompt', start_time: t0,
        attributes: { text: 'convview suppress fixture', is_test: true },
      },
      {
        trace_id: traceId, span_id: checkId, parent_id: promptId,
        name: 'rule.check', start_time: t1,
        attributes: {
          is_test: true,
          file_path: '/tmp/regin-conv/Example.java',
          relative_path: 'Example.java',
          status: 'violation',
          applicable_rules: [
            { id: ruleId, severity: 'warn', summary: 'convview fixture',
              guide: null, match_count: 1, violated: true },
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

  // Rule trigger row tied to the span (mirrors PR-2 hook ingest).
  const trigRes = await page.request.post(`${API_BASE}/api/rule-triggers`, {
    data: {
      rule_id: ruleId, file_path: '/tmp/regin-conv/Example.java',
      match_count: 1, severity: 'warn',
      session_id: traceId, span_id: checkId,
      source: 'e2e-convview',
    },
  })
  expect(trigRes.ok()).toBeTruthy()

  await page.goto(`/trace/sessions/${traceId}`)
  // The rule.check span isn't in the shallow span load, so the
  // deep-link `?span=` can't pre-select it. Click the inline
  // rule.check row to bring up its applicable_rules in the side panel.
  const ruleCheckRow = page.getByText('grit·java').first()
  await expect(ruleCheckRow).toBeVisible({ timeout: 10000 })
  await ruleCheckRow.click()

  // The applicable_rules list renders inside the now-visible side panel.
  const ruleRow = page.locator('li', { hasText: ruleId })
  await expect(ruleRow).toBeVisible({ timeout: 10000 })

  const suppressBtn = ruleRow.locator('button[title="Mark as noise"]')
  await expect(suppressBtn).toBeVisible({ timeout: 10000 })
  await suppressBtn.click()
  // Fill the reason input + Enter to commit.
  const reasonInput = ruleRow.locator('input[placeholder*="why"]')
  await expect(reasonInput).toBeVisible()
  await reasonInput.fill('conv-view e2e fixture')
  await reasonInput.press('Enter')

  // After the round-trip: the row's text strikes through and the
  // un-mark button replaces the suppress button.
  await expect(ruleRow.locator('.line-through')).toBeVisible({ timeout: 10000 })
  await expect(
    ruleRow.locator('button[title="Un-mark as noise"]')
  ).toBeVisible()

  // Confirm via the API that the underlying trigger row flipped.
  const check = await page.request.get(
    `${API_BASE}/api/triggers/by-span/${checkId}`,
  )
  const body = await check.json()
  expect(body.triggers[0].suppressed).toBe(true)
})
