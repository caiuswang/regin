import { test, expect } from './auth-fixture.js'

// "Apply All" bulk-applies every pending draft topic in a proposal through the
// per-topic /apply endpoint. We mock the workspace-proposals payload so a run
// with 3 pending draft topics is selected and the proposal is ready to apply,
// then capture the per-topic apply calls the button fans out.
function draftTopic(id, label, review_status = 'pending') {
  return {
    id,
    label,
    review_status,
    intent_preview: `intent for ${id}`,
    evidence_count: 1,
    proposed_ref_count: 2,
    feedback_thread_count: 0,
  }
}

function mockProposals(reviewState, runState = 'completed') {
  return {
    runs: [{ id: 'mock-run', state: runState, review_state: reviewState }],
    selected_proposal_id: 'mock-run',
    selected_run: { id: 'mock-run', state: runState, review_state: reviewState },
    draft_topics: [
      draftTopic('doc-a', 'Doc A'),
      draftTopic('doc-b', 'Doc B'),
      draftTopic('doc-c', 'Doc C'),
    ],
    selected_draft_topic: null,
    selected_draft_topic_id: null,
    selected_revision: { id: 1, is_latest: true },
    proposal: { status: reviewState },
    feedback_threads: [],
    providers: [],
  }
}

async function openProposalDetail(page, reviewState, runState = 'completed') {
  await page.route('**/topics/workspace/proposals**', async (route) => {
    await route.fulfill({ json: mockProposals(reviewState, runState) })
  })
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/repos')
  await page.locator('table.tbl').first().locator('tbody tr a').first().click()
  await page.locator('a.btn', { hasText: 'Topics' }).click()
  await page.getByRole('button', { name: /Proposals/ }).click()
  await expect(page).toHaveURL(/tab=proposals/)
  await page.goto(page.url() + '&proposal=mock-run')
}

test.describe('Proposal Apply All', () => {
  test('applies every pending draft topic via the per-topic endpoint', async ({ page }) => {
    const applied = []
    await page.route('**/topics/proposals/**/topics/**/apply', async (route) => {
      const url = route.request().url()
      const id = url.match(/\/topics\/([^/]+)\/apply/)[1]
      const body = route.request().postDataJSON()
      applied.push({ id, strategy: body.strategy })
      await route.fulfill({ json: { ok: true, snapshot_id: 1 } })
    })

    await openProposalDetail(page, 'ready_to_apply')

    const applyAll = page.locator('[data-testid="apply-all-topics"]')
    await expect(applyAll).toHaveText('Apply All (3)')

    await applyAll.click()
    // In-app ConfirmDialog — its confirm button is labelled with the title.
    await page.getByRole('button', { name: 'Apply all draft topics' }).click()

    await expect.poll(() => applied.length).toBe(3)
    expect(applied.map((a) => a.id).sort()).toEqual(['doc-a', 'doc-b', 'doc-c'])
    // No topic collides with an approved id, so all auto-pick 'create'.
    expect(applied.every((a) => a.strategy === 'create')).toBe(true)
  })

  test('surfaces a partial failure that survives the post-apply refresh', async ({ page }) => {
    // doc-b fails (400); the others succeed. The report must still be on
    // screen after the refresh onDone triggers — regression guard for the
    // banner being wiped same-frame by loadProposals clearing proposalError.
    await page.route('**/topics/proposals/**/topics/**/apply', async (route) => {
      const id = route.request().url().match(/\/topics\/([^/]+)\/apply/)[1]
      if (id === 'doc-b') {
        await route.fulfill({ status: 400, json: { ok: false, error: 'unresolvable_errors' } })
      } else {
        await route.fulfill({ json: { ok: true, snapshot_id: 1 } })
      }
    })

    await openProposalDetail(page, 'ready_to_apply')
    await page.locator('[data-testid="apply-all-topics"]').click()
    await page.getByRole('button', { name: 'Apply all draft topics' }).click()

    const banner = page.locator('.alert-info')
    await expect(banner).toContainText('Applied 2 of 3')
    await expect(banner).toContainText('doc-b')
  })

  test('hides Apply All until the proposal is marked ready', async ({ page }) => {
    await openProposalDetail(page, 'pending_review')
    await expect(page.locator('[data-testid="apply-all-topics"]')).toHaveCount(0)
    // The draft topics table still renders (3 rows), just without the button.
    await expect(page.getByRole('heading', { name: 'Draft Topics' })).toBeVisible()
  })

  test('hides Apply All while the run is still active', async ({ page }) => {
    // Even a ready-to-apply proposal must not offer Apply All while its run is
    // in flight — clicking against a not-yet-refetched revision no-ops. The
    // button only appears once the run has settled.
    await openProposalDetail(page, 'ready_to_apply', 'running')
    await expect(page.locator('[data-testid="apply-all-topics"]')).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'Draft Topics' })).toBeVisible()
  })
})
