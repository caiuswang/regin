/**
 * Rendering coverage for the Option-B RuleCard on /trace/triggers.
 *
 * Stubs the /api/triggers/rules endpoint with three fixtures
 * (noisy / active / dead) so the test asserts the visual encoding of
 * each status independently of whatever rule_triggers exist in the
 * live DB. Network interception keeps the test hermetic — no ingest,
 * no cleanup, runs on any clean dev server.
 */
import { test, expect } from './auth-fixture.js'

const FIXTURE = {
  kpis: { configured: 3, active: 1, noisy: 1, dead: 1 },
  thresholds: {
    noisy_min_rate_pct: 30, noisy_min_fires: 5,
    dead_min_checks: 3, default_range: '7d',
  },
  range: '7d',
  rules: [
    {
      rule_id: 'fixture-noisy-rule',
      severity: 'warn', source: 'grit',
      fires: 28, checks: 67, trigger_rate_pct: 42,
      last_seen: '2026-05-22 15:32:11',
      status: 'noisy',
      spark: [3, 8, 14, 22, 12, 4, 2],
      guide_preview: 'Do not generate getters/setters on entities; use the builder pattern',
      top_files: [
        { name: 'Order.java', n: 12 },
        { name: 'Payment.java', n: 8 },
      ],
      experiment_id: null,
    },
    {
      rule_id: 'fixture-active-rule',
      severity: 'error', source: 'grit',
      fires: 3, checks: 38, trigger_rate_pct: 8,
      last_seen: '2026-05-22 13:00:00',
      status: 'active',
      spark: [0, 1, 0, 1, 0, 0, 1],
      guide_preview: 'Controllers must call service layer',
      top_files: [{ name: 'OrderController.java', n: 2 }],
      experiment_id: null,
    },
    {
      rule_id: 'fixture-dead-rule',
      severity: 'info', source: 'grit',
      fires: 0, checks: 22, trigger_rate_pct: 0,
      last_seen: null,
      status: 'dead',
      spark: [0, 0, 0, 0, 0, 0, 0],
      guide_preview: 'ServiceImpl classes must be stateless',
      top_files: [],
      experiment_id: null,
    },
  ],
}

async function stubRulesEndpoint(page) {
  await page.route('**/api/triggers/rules*', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(FIXTURE),
    })
  })
}

test('renders KPI strip from API', async ({ page }) => {
  await stubRulesEndpoint(page)
  await page.goto('/trace/triggers')

  // KPI tiles surface the four health buckets at a glance.
  const strip = page.locator('.kpi-strip')
  await expect(strip).toContainText('3')           // configured
  await expect(strip).toContainText('rules configured')
  await expect(strip).toContainText('active in window')
  await expect(strip).toContainText('noisy')
  await expect(strip).toContainText('dead')
})

test('noisy rule shows yellow status badge', async ({ page }) => {
  await stubRulesEndpoint(page)
  await page.goto('/trace/triggers')

  const noisyCard = page.locator('article.rule-card', { hasText: 'fixture-noisy-rule' })
  await expect(noisyCard).toHaveClass(/rule-card--noisy/)
  // The noisy status badge uses the badge-yellow color class.
  await expect(noisyCard.locator('.badge-yellow', { hasText: 'noisy' })).toBeVisible()
  // Trigger rate is the primary metric.
  await expect(noisyCard.locator('.rule-card__rate')).toHaveText('42%')
  // Guide preview is shown.
  await expect(noisyCard).toContainText('Do not generate getters/setters')
  // Top files render with basenames.
  await expect(noisyCard).toContainText('Order.java')
  await expect(noisyCard).toContainText('Payment.java')
})

test('active rule shows green status badge, no accent stripe', async ({ page }) => {
  await stubRulesEndpoint(page)
  await page.goto('/trace/triggers')

  const activeCard = page.locator('article.rule-card', { hasText: 'fixture-active-rule' })
  await expect(activeCard).not.toHaveClass(/rule-card--noisy/)
  await expect(activeCard).not.toHaveClass(/rule-card--dead/)
  await expect(activeCard.locator('.badge-green', { hasText: 'active' })).toBeVisible()
})

test('dead rule dims card and shows rule-editor CTA', async ({ page }) => {
  await stubRulesEndpoint(page)
  await page.goto('/trace/triggers')

  const deadCard = page.locator('article.rule-card', { hasText: 'fixture-dead-rule' })
  await expect(deadCard).toHaveClass(/rule-card--dead/)
  await expect(deadCard.locator('.badge-gray', { hasText: 'dead' })).toBeVisible()
  // Dead CTA explains the count + links to the rule editor.
  const cta = deadCard.locator('.rule-card__dead-cta')
  await expect(cta).toContainText('never fired across 22 checks')
  const link = cta.locator('a')
  await expect(link).toHaveText('open rule editor →')
  await expect(link).toHaveAttribute('href', '/rules/fixture-dead-rule')
})

test('clicking a card header toggles the expanded drawer', async ({ page }) => {
  await stubRulesEndpoint(page)
  // Drawer detail endpoint also needs a stub so the test doesn't hit a 404.
  await page.route('**/api/triggers/rules/fixture-noisy-rule*', async (route) => {
    await route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify({
        rule_id: 'fixture-noisy-rule', severity: 'warn', source: 'grit',
        guide: 'FULL GUIDE TEXT FOR THE DRAWER',
        files: [], sessions: [], events: [],
        range: '7d',
      }),
    })
  })
  await page.goto('/trace/triggers')

  const noisyCard = page.locator('article.rule-card', { hasText: 'fixture-noisy-rule' })
  await expect(noisyCard).not.toHaveClass(/rule-card--open/)
  await noisyCard.locator('button.rule-card__header').click()
  await expect(noisyCard).toHaveClass(/rule-card--open/)
  // Drawer renders its guide block.
  await expect(noisyCard.locator('.rule-drawer__guide'))
    .toHaveText('FULL GUIDE TEXT FOR THE DRAWER')
})
