/**
 * Session trace view smoke test.
 *
 * Opens the first available session from /trace/sessions and asserts the
 * timeline table renders with spans. Confirms the new read-only GET
 * /api/sessions/:id handler still produces a usable tree for the UI.
 *
 * The read-only contract itself (GET never mutates, POST /materialize
 * does) is covered by tests/test_trace_api.py — that's where we exercise
 * the DB side-effect assertion, since Playwright can't see sqlite state.
 */
import { test, expect } from './auth-fixture.js'

test.describe('Session Trace View', () => {
  test('renders session timeline for a real session', async ({ page }) => {
    await page.goto('/trace/sessions')

    // Wait for the sessions list to populate.
    const firstLink = page.locator('a[href^="/trace/sessions/"]').first()
    await expect(firstLink).toBeVisible({ timeout: 10_000 })

    await firstLink.click()

    await expect(page.getByRole('heading', { name: 'Session Timeline' }))
      .toBeVisible({ timeout: 10_000 })

    // At least one span row should render in the TreeTable.
    const spanRows = page.locator('[role="row"][aria-level]')
    await expect(spanRows.first()).toBeVisible({ timeout: 10_000 })
    expect(await spanRows.count()).toBeGreaterThanOrEqual(1)

    // Span details panel should populate for the auto-selected span.
    await expect(page.getByRole('heading', { name: 'Span Details' }))
      .toBeVisible()
  })

  test('turns sidebar links turns to spans', async ({ page }) => {
    await page.goto('/trace/sessions')
    const firstLink = page.locator('a[href^="/trace/sessions/"]').first()
    await expect(firstLink).toBeVisible({ timeout: 10_000 })
    await firstLink.click()
    await expect(page.getByRole('heading', { name: 'Session Timeline' }))
      .toBeVisible({ timeout: 10_000 })

    // The panel starts closed with a "load" button — the cost is per-turn
    // aggregation on the backend, so we defer until the user asks.
    const loadBtn = page.getByRole('button', { name: /^load$/i })
    await expect(loadBtn).toBeVisible({ timeout: 10_000 })
    await loadBtn.click()

    // The count text ("N turns") replaces the button once the fetch
    // settles. The regex covers both the populated path and the
    // "0 turns" edge case for old sessions.
    const countLabel = page.getByText(/\d+ turns/i)
    await expect(countLabel).toBeVisible({ timeout: 10_000 })

    const countText = await countLabel.textContent()
    const match = (countText || '').match(/^(\d+) turns/)
    const turnCount = match ? parseInt(match[1], 10) : 0
    test.skip(turnCount === 0,
      'session has no turn_usage rows — exercised in unit tests instead')

    // At least one row should now render with a HH:MM:SS local time.
    // `turn_usage.timestamp` is UTC in the DB; the view reformats to
    // local — regression guard against the format slipping back to ISO.
    const firstRow = page.locator('ul > li').filter({ hasText: /\d{2}:\d{2}:\d{2}/ }).first()
    await expect(firstRow).toBeVisible({ timeout: 10_000 })

    // Clicking a turn row dims the overview-strip bars that don't
    // belong to that turn. We observe this via the opacity class the
    // component toggles. At least one dimmed bar is enough to prove
    // the cross-highlight pipeline is wired (if the session only has
    // one turn, every bar matches — skip the assertion in that case).
    await firstRow.click()
    if (turnCount > 1) {
      const dimmed = page.locator('.opacity-20')
      await expect(dimmed.first()).toBeVisible({ timeout: 5_000 })
    }
  })

  test('consecutive loads are idempotent', async ({ page }) => {
    // The previous implementation mutated DB state on every GET. Now two
    // consecutive loads of the same session should return the same payload
    // shape (same number of spans).
    await page.goto('/trace/sessions')
    const firstLink = page.locator('a[href^="/trace/sessions/"]').first()
    await expect(firstLink).toBeVisible({ timeout: 10_000 })
    const href = await firstLink.getAttribute('href')
    expect(href).toBeTruthy()

    const fetchCount = async () => {
      const resp = await page.request.get(`/api${href}`)
      expect(resp.ok()).toBeTruthy()
      const body = await resp.json()
      return body.spans.length
    }

    const first = await fetchCount()
    const second = await fetchCount()
    const third = await fetchCount()
    expect(second).toBe(first)
    expect(third).toBe(first)
  })
})
