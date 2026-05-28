/**
 * End-to-end coverage for the "mark as noise" workflow on /trace/triggers.
 *
 * Seeds three fired events for one synthetic rule_id, opens its drawer,
 * marks one event as noise, and verifies:
 *   - the drawer event row striples out (`rule-drawer__event--suppressed`)
 *   - the rule card's fires/checks drop from 3/3 to 2/2
 *   - the card shows the "1 suppressed" hint
 *
 * Then un-marks and asserts restoration. Tests bootstrap their own data
 * via /api/rule-triggers so they're portable across DBs.
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

// Mint a fresh admin token via the Python helper so the spec works
// even when the auth-fixture's hardcoded password is out of sync with
// the local users table. Token expiry is 7 days — well within a CI run.
function mintAdminToken() {
  const out = execFileSync(
    PYTHON,
    ['-c', 'from lib.auth import create_token; print(create_token(1, "e2e-admin", "admin"))'],
    { cwd: REPO_ROOT, encoding: 'utf8' },
  )
  return out.trim()
}

const test = base.extend({
  page: async ({ page }, use) => {
    const token = mintAdminToken()
    const user = { id: 1, username: 'e2e-admin', role: 'admin' }
    await page.addInitScript(({ token, user }) => {
      localStorage.setItem('regin_auth_token', token)
      localStorage.setItem('regin_auth_user', JSON.stringify(user))
    }, { token, user })
    await use(page)
  },
})

async function seedThreeEvents(page) {
  // Distinct rule_id per run so concurrent suites don't collide.
  const ruleId = `e2e_suppress_${randomUUID().slice(0, 8)}`
  const now = new Date()
  // Three fired events, each backed by a real rule.check span so the
  // drawer's "Recent matched events" deep-link carries a valid span_id.
  // Skipping the span here was creating orphan rule_trigger rows that
  // surfaced at the top of /trace/triggers (sort by rate desc, 100%
  // trigger rate) with non-clickable session links.
  for (let i = 0; i < 3; i++) {
    const sessionId = `e2e-suppress-${randomUUID().slice(0, 8)}`
    const t = new Date(now.getTime() - (i + 1) * 60_000)
      .toISOString().slice(0, 19).replace('T', ' ')
    const promptId = `pmt-${randomUUID().slice(0, 8)}`
    const checkId = `chk-${randomUUID().slice(0, 8)}`
    const filePath = `/tmp/regin-e2e/F${i}.java`

    // Seed parent prompt + rule.check span so the trigger row has a
    // navigable target in the session-trace view.
    const spanRes = await page.request.post(`${API_BASE}/api/session-spans`, {
      data: [
        {
          trace_id: sessionId, span_id: promptId, parent_id: null,
          name: 'prompt', start_time: t,
          attributes: { text: 'suppress-spec fixture', is_test: true },
        },
        {
          trace_id: sessionId, span_id: checkId, parent_id: promptId,
          name: 'rule.check', start_time: t,
          attributes: {
            is_test: true, file_path: filePath, relative_path: `F${i}.java`,
            status: 'violation',
            applicable_rules: [
              { id: ruleId, severity: 'warn', summary: 'suppress fixture',
                guide: null, match_count: 1, violated: true },
            ],
          },
        },
      ],
    })
    expect(spanRes.ok()).toBeTruthy()

    const res = await page.request.post(`${API_BASE}/api/rule-triggers`, {
      data: {
        rule_id: ruleId, file_path: filePath,
        match_count: 1, severity: 'warn',
        session_id: sessionId, span_id: checkId,
        source: 'e2e-suppression',
      },
    })
    expect(res.ok()).toBeTruthy()
  }
  return ruleId
}

test('mark-as-noise excludes an event from rule metrics, un-mark restores', async ({ page }) => {
  const ruleId = await seedThreeEvents(page)

  await page.goto(`/trace/triggers?range=7d&search=${encodeURIComponent(ruleId)}`)
  const card = page.locator('article.rule-card').first()
  await expect(card).toBeVisible({ timeout: 10000 })

  // Baseline: 3 fires / 3 checks, no "suppressed" hint yet.
  await expect(card.locator('.rule-card__counts'))
    .toContainText('3 fires / 3 checks')
  await expect(card.locator('.rule-card__suppressed-hint')).toHaveCount(0)

  // Expand and click the first 🔇 button → inline reason input appears.
  await card.locator('button.rule-card__header').click()
  const eventTable = card.locator('.rule-drawer__table').last()
  await expect(eventTable).toBeVisible({ timeout: 10000 })
  const firstSuppressBtn = eventTable.locator('button[title="Mark as noise"]').first()
  await firstSuppressBtn.click()
  // Fill the reason input and press Enter to commit.
  const reasonInput = eventTable.locator('input[placeholder*="why"]').first()
  await reasonInput.fill('e2e false-positive')
  await reasonInput.press('Enter')

  // After the round-trip:
  // - the card's metrics drop (suppressed events leave fires AND checks)
  // - the "1 suppressed" hint appears
  // - the drawer event row stripes out
  await expect(card.locator('.rule-card__counts'))
    .toContainText('2 fires / 2 checks', { timeout: 10000 })
  await expect(card.locator('.rule-card__suppressed-hint'))
    .toContainText('1 suppressed')
  await expect(
    eventTable.locator('tr.rule-drawer__event--suppressed')
  ).toHaveCount(1)

  // Un-mark restores everything.
  await eventTable.locator('button[title="Un-mark as noise"]').first().click()
  await expect(card.locator('.rule-card__counts'))
    .toContainText('3 fires / 3 checks', { timeout: 10000 })
  await expect(card.locator('.rule-card__suppressed-hint')).toHaveCount(0)
  await expect(
    eventTable.locator('tr.rule-drawer__event--suppressed')
  ).toHaveCount(0)
})
