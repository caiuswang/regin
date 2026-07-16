import { test, expect } from './auth-fixture.js'

// A review_note whose body is free-form markdown (heading + bullet list + inline
// code) — the reviewer's verbatim output. The card must render it as HTML, not
// show the raw `##`/`-` source, and the Copy button must hand back the raw
// markdown source unchanged.
const MD_BODY = [
  '**Automated review — recommendation: REGENERATE**',
  '',
  '## Summary',
  '',
  '- Coverage looks thin',
  '- `lib/foo.py` is not referenced',
  '- The wiki restates a sibling topic instead of ceding it',
  '- Refs are mis-tiered: a central file is marked `reference`',
  '- A pointer-only example file is left `primary`',
  '- The intent line duplicates the label',
  '- No `[[id]]` link to the overlapping neighbour',
  '- Several sections read as a file-by-file catalog',
  '',
  'RECOMMENDATION: REGENERATE',
].join('\n')

const REVIEW_NOTE = {
  id: 999998,
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
    body: MD_BODY,
  }],
}

const DRAFT_TOPIC = { id: 'dt1', label: 'Draft Topic', intent: 'A drafted topic.', review_status: null }

const MOCK_PROPOSALS = {
  runs: [{ id: 'mock-run', state: 'completed', review_state: 'pending_review' }],
  selected_proposal_id: 'mock-run',
  selected_run: { id: 'mock-run', state: 'completed', review_state: 'pending_review' },
  draft_topics: [DRAFT_TOPIC],
  selected_draft_topic: DRAFT_TOPIC,
  selected_draft_topic_id: 'dt1',
  selected_revision: { id: 1, is_latest: true },
  proposal: { status: 'pending_review' },
  feedback_threads: [REVIEW_NOTE],
  providers: [],
}

async function openProposalDetail(page) {
  await page.route('**/topics/workspace/proposals**', async (route) => {
    await route.fulfill({ json: MOCK_PROPOSALS })
  })
  await page.goto('/repos')
  await page.locator('table.tbl').first().locator('tbody tr a').first().click()
  await page.locator('a.btn', { hasText: 'Topics' }).click()
  await page.getByRole('button', { name: /Proposals/ }).click()
  await expect(page).toHaveURL(/tab=proposals/)
  await page.goto(page.url() + '&proposal=mock-run')
}

test.describe('Review note markdown render + copy', () => {
  test('revision-overview card renders markdown and copies raw source', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write'])
    await page.setViewportSize({ width: 1440, height: 1000 })
    await openProposalDetail(page)

    const card = page.locator('[data-testid="review-overview-card"]').first()
    await expect(card).toBeVisible()
    // Markdown was parsed to HTML — heading/list/code elements exist.
    await expect(card.locator('.pattern-content h2')).toHaveText('Summary')
    await expect(card.locator('.pattern-content ul li')).toHaveCount(8)
    await expect(card.locator('.pattern-content code').first()).toHaveText('lib/foo.py')
    // The raw markdown source is NOT shown literally.
    await expect(card.getByText('## Summary', { exact: false })).toHaveCount(0)

    // A long note is collapsed by default: ClampedText renders its Show more
    // toggle only when the clamp measured scrollHeight > clientHeight — i.e. the
    // 6-line clamp actually limited the rendered markdown (not just plain text).
    const showMore = card.getByRole('button', { name: 'Show more' })
    await expect(showMore).toBeVisible()
    await card.screenshot({ path: 'test-results/review-md-collapsed.png' })
    await showMore.click()
    await expect(card.getByRole('button', { name: 'Show less' })).toBeVisible()

    // Copy hands back the verbatim source, not the rendered text.
    await card.locator('[data-testid="review-overview-copy"]').click()
    await expect(page.getByText('Copied!')).toBeVisible()
    const clip = await page.evaluate(() => navigator.clipboard.readText())
    expect(clip).toBe(MD_BODY)

    // Sidebar automated-review card renders the same body as markdown + has copy.
    const side = page.locator('[data-testid="review-note-card"]').first()
    await expect(side.locator('.pattern-content h2')).toHaveText('Summary')
    await expect(side.locator('[data-testid="review-note-copy"]')).toBeVisible()

    await page.screenshot({ path: 'test-results/review-md-desktop.png' })
  })

  test('no horizontal overflow at 390px mobile', async ({ page }) => {
    // Navigate at desktop width (the mobile nav collapses the tab controls),
    // then shrink to a phone viewport to measure the rendered card's overflow.
    await page.setViewportSize({ width: 1440, height: 1000 })
    await openProposalDetail(page)
    await expect(page.locator('[data-testid="review-overview-card"]').first()).toBeVisible()

    await page.setViewportSize({ width: 390, height: 800 })
    const noOverflow = await page.evaluate(() =>
      document.documentElement.scrollWidth <= document.documentElement.clientWidth)
    expect(noOverflow).toBe(true)
    await page.screenshot({ path: 'test-results/review-md-mobile.png' })
  })
})
