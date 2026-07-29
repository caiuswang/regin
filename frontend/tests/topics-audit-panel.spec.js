import { test, expect } from './auth-fixture.js'

// The audit tab groups issues by code. A single boundary regression can emit
// dozens of rows under one code (regin itself once had 82), so each group opens
// truncated at 10 with a toggle — the panel must stay readable at that scale
// and must still show every row on demand.

// The two codes the backend reports as informational: refs absent from this
// checkout that the panel can neither fix nor prove dead. audit_graph still
// emits them at severity=error for the authoring gate.
const INFORMATIONAL_CODES = ['graph.ref_on_other_branch', 'graph.ref_unverifiable']

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
        informational_codes: INFORMATIONAL_CODES,
        error_count: 0,
        warning_count: list.length,
        info_count: 0,
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

// An anchor whose file lives on an unmerged branch is not rot: offering the
// bulk strip for it would delete curation nothing in the UI can restore, and
// telling the user to "resolve it manually" sends them to do exactly that.
test('a branch-owned ref offers no fix and is not labelled manual', async ({ page }) => {
  const branchOwned = [{
    severity: 'error',
    code: 'graph.ref_on_other_branch',
    message: 'topic live-session-mobile-card ref does not exist in this working tree '
      + '(it is present on an unmerged branch, or tracked at HEAD but not checked out): '
      + 'frontend/src/components/live/LiveQaDecision.vue',
    topic_ids: ['live-session-mobile-card'],
    paths: ['frontend/src/components/live/LiveQaDecision.vue'],
  }]
  await page.route('**/api/repos/*/topics/audit', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        issues: branchOwned,
        by_code: { 'graph.ref_on_other_branch': branchOwned },
        auto_fixable_codes: ['graph.dead_ref', 'graph.orphan_edge_target'],
        informational_codes: INFORMATIONAL_CODES,
        error_count: 0,
        warning_count: 0,
        info_count: 1,
      }),
    }))
  await page.goto('/repos/regin/topics?tab=audit')

  const group = page.getByTestId('audit-group')
  await expect(group).toContainText('graph.ref_on_other_branch')
  await expect(group).toContainText('not checked out')
  // The hint has to survive saying why there is nothing to fix: "clears when
  // it merges" was false for a ref deleted after its branch merged (CAI-37),
  // and for one HEAD tracks but the checkout hides there is no merge coming.
  await expect(group.locator('[title]').first())
    .toHaveAttribute('title', /git still has it \(an unmerged branch, or HEAD\)/)
  await expect(group).not.toContainText('manual')
  await expect(group.locator('input[type=checkbox]')).toBeDisabled()

  // Not red: the group's own tag says there is nothing to fix, so styling it as
  // an error made a permanent unfixable error group (CAI-35).
  await expect(group).toHaveClass(/bg-slate-50/)
  await expect(group).not.toHaveClass(/bg-red-50/)
  const panel = page.getByTestId('audit-panel')
  await expect(panel).toContainText('0 errors, 0 warnings, 1 informational')
  await expect(panel).not.toContainText('Graph is clean')
})

// The backend can only say "no branch carries this" when git answered. When it
// could not — no repo, unborn HEAD, a ref git refuses as a pathspec — the ref
// is equally unfixable here, but the branch-owned wording would explain the
// absent fix button with a branch that may not exist.
test('a ref the branch check could not verify says so', async ({ page }) => {
  const unverifiable = [{
    severity: 'error',
    code: 'graph.ref_unverifiable',
    message: 'topic live-session-mobile-card ref does not exist in this checkout '
      + 'and could not be verified against branch tips: '
      + 'frontend/src/components/live/LiveQaDecision.vue',
    topic_ids: ['live-session-mobile-card'],
    paths: ['frontend/src/components/live/LiveQaDecision.vue'],
  }]
  await page.route('**/api/repos/*/topics/audit', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        issues: unverifiable,
        by_code: { 'graph.ref_unverifiable': unverifiable },
        auto_fixable_codes: ['graph.dead_ref', 'graph.orphan_edge_target'],
        informational_codes: INFORMATIONAL_CODES,
        error_count: 0,
        warning_count: 0,
        info_count: 1,
      }),
    }))
  await page.goto('/repos/regin/topics?tab=audit')

  const group = page.getByTestId('audit-group')
  await expect(group).toContainText('graph.ref_unverifiable')
  await expect(group).toContainText('unverified')
  await expect(group).not.toContainText('manual')
  await expect(group).not.toContainText('not checked out')
  await expect(group.locator('input[type=checkbox]')).toBeDisabled()
  await expect(group).not.toHaveClass(/bg-red-50/)
})

// The informational bucket must not swallow real rot: a dead ref is still a red
// error and still carries the enabled auto-fix checkbox.
test('a genuinely dead ref stays a red, fixable error', async ({ page }) => {
  const dead = [{
    severity: 'error',
    code: 'graph.dead_ref',
    message: 'topic a ref does not exist in this checkout: lib/gone.py',
    topic_ids: ['a'],
    paths: ['lib/gone.py'],
  }]
  await page.route('**/api/repos/*/topics/audit', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        issues: dead,
        by_code: { 'graph.dead_ref': dead },
        auto_fixable_codes: ['graph.dead_ref', 'graph.orphan_edge_target'],
        informational_codes: INFORMATIONAL_CODES,
        error_count: 1,
        warning_count: 0,
        info_count: 0,
      }),
    }))
  await page.goto('/repos/regin/topics?tab=audit')

  const group = page.getByTestId('audit-group')
  await expect(group).toHaveClass(/bg-red-50/)
  await expect(group.locator('input[type=checkbox]')).toBeEnabled()
  const panel = page.getByTestId('audit-panel')
  await expect(panel).toContainText('1 error, 0 warnings')
  await expect(panel).not.toContainText('informational')
})

// The backend owns the informational set, so it can name a code this panel has
// no copy for. Falling through to "manual" would send the reader off to fix
// something the same panel is reporting as not-a-defect.
test('an informational code the panel has no copy for is not labelled manual', async ({ page }) => {
  const rows = [{
    severity: 'error',
    code: 'graph.some_future_code',
    message: 'something the panel has never heard of',
    topic_ids: ['a'],
    paths: ['lib/x.py'],
  }]
  await page.route('**/api/repos/*/topics/audit', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        ok: true,
        issues: rows,
        by_code: { 'graph.some_future_code': rows },
        auto_fixable_codes: ['graph.dead_ref', 'graph.orphan_edge_target'],
        informational_codes: [...INFORMATIONAL_CODES, 'graph.some_future_code'],
        error_count: 0,
        warning_count: 0,
        info_count: 1,
      }),
    }))
  await page.goto('/repos/regin/topics?tab=audit')

  const group = page.getByTestId('audit-group')
  await expect(group).toContainText('informational')
  await expect(group).not.toContainText('manual')
  await expect(group).not.toHaveClass(/bg-red-50/)
})
