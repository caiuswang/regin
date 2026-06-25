import { test, expect } from './auth-fixture.js'

// Phase 2: a review_note feedback thread renders as an "Automated review"
// card with a recommendation badge + Regenerate/Dismiss actions, wired to the
// regenerate endpoint. We fully mock the workspace-proposals payload (so a run
// is always selected and carries our review_note) and open the detail view via
// the ?proposal= query param that gates ProposalRunDetail.
const REVIEW_NOTE = {
  id: 999999,
  proposal_topic_id: null,
  kind: 'review_note',
  anchor_kind: 'general',
  resolution_state: 'open',
  metadata: { recommendation: 'REGENERATE' },
  revision_number: 1,
  updated_at: '2026-01-01T00:00:00+00:00',
  comments: [{
    id: 1,
    author_kind: 'agent',
    created_at: '2026-01-01T00:00:00+00:00',
    body: 'Automated review — recommendation: REGENERATE\n\nCoverage looks thin.',
  }],
}

const MOCK_PROPOSALS = {
  runs: [{ id: 'mock-run', state: 'completed', review_state: 'pending_review' }],
  selected_proposal_id: 'mock-run',
  selected_run: { id: 'mock-run', state: 'completed', review_state: 'pending_review' },
  selected_draft_topic: null,
  selected_draft_topic_id: null,
  selected_revision: { id: 1, is_latest: true },
  proposal: { status: 'pending_review' },
  feedback_threads: [REVIEW_NOTE],
  providers: [],
}

test.describe('Proposal review note card', () => {
  test('renders recommendation badge + actions and wires Regenerate', async ({ page }) => {
    let regenerateCalled = false

    await page.route('**/topics/workspace/proposals**', async (route) => {
      await route.fulfill({ json: MOCK_PROPOSALS })
    })
    await page.route('**/topics/proposals/**/regenerate', async (route) => {
      regenerateCalled = true
      await route.fulfill({ json: { ok: true } })
    })

    await page.setViewportSize({ width: 1440, height: 1000 })
    await page.goto('/repos')
    await page.locator('table.tbl').first().locator('tbody tr a').first().click()
    await page.locator('a.btn', { hasText: 'Topics' }).click()
    await page.getByRole('button', { name: /Proposals/ }).click()
    await expect(page).toHaveURL(/tab=proposals/)
    // Open the detail view (ProposalRunDetail renders only with ?proposal=).
    await page.goto(page.url() + '&proposal=mock-run')

    const card = page.locator('[data-testid="review-note-card"]').first()
    await expect(card).toBeVisible()
    await expect(card.locator('[data-testid="review-note-recommendation"]')).toHaveText('REGENERATE')
    await expect(card.getByText('Automated review', { exact: true })).toBeVisible()
    await expect(card.getByRole('button', { name: 'Dismiss' })).toBeVisible()

    const regenerateBtn = card.getByRole('button', { name: 'Regenerate' })
    await expect(regenerateBtn).toBeVisible()
    await regenerateBtn.click()
    await expect.poll(() => regenerateCalled).toBe(true)
  })
})
