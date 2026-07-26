import { test, expect } from './auth-fixture.js'

// The audit tab groups issues by code. A single boundary regression can emit
// dozens of rows under one code (regin itself once had 82), so each group opens
// truncated at 10 with a toggle — the panel must stay readable at that scale
// and must still show every row on demand.

function issues(n) {
  return Array.from({ length: n }, (_, i) => ({
    severity: 'warning',
    code: 'graph.shared_primary_ref',
    message: `file lib/x/${i}.py is a primary ref of 2 topics (a, b)`,
    topic_ids: ['a', 'b'],
    paths: [`lib/x/${i}.py`],
  }))
}

async function mockAudit(page, list) {
  await page.route('**/api/repos/*/topics/audit', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        issues: list,
        by_code: list.length ? { 'graph.shared_primary_ref': list } : {},
        auto_fixable_codes: ['graph.dead_ref', 'graph.orphan_edge_target'],
        error_count: 0,
        warning_count: list.length,
      }),
    }))
}

test('long issue groups open truncated and expand on demand', async ({ page }) => {
  await mockAudit(page, issues(25))
  await page.goto('/repos/regin/topics?tab=audit')

  const group = page.getByTestId('audit-group')
  await expect(group).toBeVisible()
  await expect(group.locator('li')).toHaveCount(10)

  const toggle = page.getByTestId('audit-toggle-graph.shared_primary_ref')
  await expect(toggle).toHaveText(/Show all 25/)
  await toggle.click()
  await expect(group.locator('li')).toHaveCount(25)
  await expect(toggle).toHaveText(/Show fewer/)
})

test('a clean graph reports no issues', async ({ page }) => {
  await mockAudit(page, [])
  await page.goto('/repos/regin/topics?tab=audit')

  await expect(page.getByTestId('audit-panel')).toContainText('No issues found')
  await expect(page.getByTestId('audit-group')).toHaveCount(0)
})
