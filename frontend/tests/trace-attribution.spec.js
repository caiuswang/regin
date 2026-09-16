/**
 * What a session says it built, in the trace header (CAI-256).
 *
 * The attribution read is stubbed; the trace itself is whatever the fixture
 * database holds. The assertions are about the line's claims — which the
 * merged reader supplies and the declarations-only one would get wrong on 22
 * of 23 real traces.
 */
import { test, expect } from './auth-fixture.js'
import { BASELINE_TRACE } from './helpers/fixtures.js'

async function stubAttribution(page, features) {
  await page.route(/\/api\/sessions\/[^/]+\/features$/, (route) =>
    route.fulfill({ json: { trace_id: 'x', features } }))
}

async function openTrace(page) {
  await page.goto(`/trace/sessions/${BASELINE_TRACE}`)
  await expect(page.getByTestId('session-attribution')).toBeVisible()
}

test('an unattributed session says none declared', async ({ page }) => {
  await stubAttribution(page, [])
  await openTrace(page)

  const line = page.getByTestId('session-attribution')
  await expect(line).toBeVisible()
  await expect(page.getByTestId('attr-none')).toHaveText('none declared')
})

test('a task-lane attribution never renders the word task bare',
  async ({ page }) => {
    await stubAttribution(page, [{
      feature_id: 242, title: 'Feature ledger', provenance: 'run',
      is_primary: true, round: null, task_id: 44,
    }])
    await openTrace(page)

    const line = page.getByTestId('session-attribution')
    // The label disambiguates rather than the tag being renamed: a tag whose
    // wording forks by surface is worse than a local collision with the
    // TodoWrite badge two lines up.
    await expect(line).toContainText('feature')
    await expect(line.locator('.attr-via-link')).toContainText('via task #44')
    await expect(line.locator('.attr-via-link'))
      .toHaveAttribute('href', '/tasks/44')
  })

test('a task-lane row offers no un-declare and no disabled control',
  async ({ page }) => {
    await stubAttribution(page, [{
      feature_id: 242, title: 'Feature ledger', provenance: 'run',
      is_primary: true, round: null, task_id: 44,
    }])
    await openTrace(page)

    await expect(page.locator('.attr-undo')).toHaveCount(0)
  })

test('a declared row offers un-declare inline', async ({ page }) => {
  await stubAttribution(page, [{
    feature_id: 242, title: 'Feature ledger', provenance: 'declared',
    is_primary: true, round: 1, task_id: null,
  }])
  await openTrace(page)

  await expect(page.locator('.attr-undo')).toHaveCount(1)
})

test('the feature key links into its ledger', async ({ page }) => {
  await stubAttribution(page, [{
    feature_id: 242, title: 'Feature ledger', provenance: 'declared',
    is_primary: true, round: 1, task_id: null,
  }])
  await openTrace(page)

  await expect(page.locator('.attr-key'))
    .toHaveAttribute('href', '/features/242')
})

test('the picker reads the feature index, and is_primary starts unchecked',
  async ({ page }) => {
    await stubAttribution(page, [])
    let asked = false
    await page.route(/\/api\/features(\?.*)?$/, (route) => {
      asked = true
      return route.fulfill({ json: {
        items: [{ key: 'CAI-242', title: 'Feature ledger', stage: 'shipped' }],
        total: 1,
        coverage: { declared_sessions: 0, all_sessions: 1,
                    declared_usd: 0, all_usd: 1 },
        unattributed_defects: 0,
      } })
    })
    await openTrace(page)

    await page.getByRole('button', { name: 'Declare' }).first().click()

    await expect(page.getByText('Declare what this session built')).toBeVisible()
    expect(asked).toBe(true)
    // Explicit, never a side effect: it moves a whole session's cost.
    await expect(page.locator('input[type=checkbox]').first()).not.toBeChecked()
  })
