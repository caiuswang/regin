import { test, expect } from './auth-fixture.js'

// Two surfaces added on top of the proposal pipeline:
//  1. a wiki-content diff inside the Apply panel (DiffPanel), fed by the
//     server /diff response's wiki_before / wiki_after;
//  2. a standalone /topics/compare page that diffs two revisions' wiki.
// Both are driven off mocked API responses (no real proposal seeding),
// matching proposal-apply-all.spec.js.

function draftTopic(id, label) {
  return {
    id,
    label,
    review_status: 'pending',
    intent_preview: `intent for ${id}`,
    evidence_count: 1,
    proposed_ref_count: 2,
    feedback_thread_count: 0,
    wiki: '# Doc A\nProposed line one\nProposed line two\n',
  }
}

function workspaceWithSelectedTopic() {
  const topic = draftTopic('doc-a', 'Doc A')
  return {
    runs: [{ id: 'mock-run', state: 'completed', review_state: 'ready_to_apply' }],
    selected_proposal_id: 'mock-run',
    selected_run: { id: 'mock-run', state: 'completed', review_state: 'ready_to_apply' },
    draft_topics: [{ ...topic }],
    selected_draft_topic: { ...topic, aliases: [], refs: [], edges: [] },
    selected_draft_topic_id: 'doc-a',
    selected_revision: { id: 2, revision_number: 2, is_latest: true },
    selected_revision_id: 2,
    revisions: [
      { id: 2, revision_number: 2, kind: 'regenerate', is_latest: true },
      { id: 1, revision_number: 1, kind: 'generate', is_latest: false },
    ],
    proposal: { status: 'ready_to_apply', wiki: '', topics: [{ ...topic }] },
    feedback_threads: [],
    providers: [],
    buckets: [],
  }
}

async function gotoTopics(page) {
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.goto('/repos')
  await page.locator('table.tbl').first().locator('tbody tr a').first().click()
  await page.locator('a.btn', { hasText: 'Topics' }).click()
  await page.getByRole('button', { name: /Proposals/ }).click()
  await expect(page).toHaveURL(/tab=proposals/)
}

test.describe('Apply panel wiki-content diff', () => {
  test('renders added/removed wiki lines from the /diff response', async ({ page }) => {
    await page.route('**/topics/workspace/proposals**', (route) =>
      route.fulfill({ json: workspaceWithSelectedTopic() }))
    await page.route('**/topics/proposals/**/topics/**/diff', (route) =>
      route.fulfill({
        json: {
          ok: true,
          diff: {
            strategy: 'create',
            proposed_topic_id: 'doc-a',
            is_applyable: true,
            topic_deltas: [],
            graph_warnings: [],
            introduced_errors: [],
            valid_strategies_by_topic: { 'doc-a': ['create'] },
          },
          dropped_items: { orphan_edges: [], dead_refs: [], duplicate_aliases: [] },
          raw_introduced_errors: [],
          wiki_before: '# Doc A\nOld line one\n',
          wiki_after: '# Doc A\nProposed line one\nProposed line two\n',
        },
      }))

    await gotoTopics(page)
    await page.goto(page.url() + '&proposal=mock-run')

    await page.locator('[data-testid="apply-proposed-topic"]').click()
    const wikiDiff = page.locator('[data-testid="wiki-content-diff"]')
    await expect(wikiDiff).toBeVisible()
    // The removed old line and both added proposed lines must show.
    await expect(wikiDiff.locator('.wikidiff__row--remove')).toContainText('Old line one')
    await expect(wikiDiff.locator('.wikidiff__row--add')).toContainText(['Proposed line one', 'Proposed line two'])
  })

  test('shows "No content changes" when before equals after', async ({ page }) => {
    const same = '# Doc A\nSame body\n'
    await page.route('**/topics/workspace/proposals**', (route) =>
      route.fulfill({ json: workspaceWithSelectedTopic() }))
    await page.route('**/topics/proposals/**/topics/**/diff', (route) =>
      route.fulfill({
        json: {
          ok: true,
          diff: {
            strategy: 'create', proposed_topic_id: 'doc-a', is_applyable: true,
            topic_deltas: [], graph_warnings: [], introduced_errors: [],
            valid_strategies_by_topic: { 'doc-a': ['create'] },
          },
          dropped_items: { orphan_edges: [], dead_refs: [], duplicate_aliases: [] },
          raw_introduced_errors: [],
          wiki_before: same,
          wiki_after: same,
        },
      }))

    await gotoTopics(page)
    await page.goto(page.url() + '&proposal=mock-run')
    await page.locator('[data-testid="apply-proposed-topic"]').click()
    await expect(page.locator('[data-testid="wiki-diff-identical"]')).toBeVisible()
  })
})

test.describe('Revision compare page', () => {
  test('diffs two revisions and switches sides', async ({ page }) => {
    await page.route('**/topics/workspace/proposals**', (route) => {
      const url = new URL(route.request().url())
      const rev = url.searchParams.get('revision_id')
      const payload = workspaceWithSelectedTopic()
      const wikiByRev = { 1: '# Doc A\nRevision one body\n', 2: '# Doc A\nRevision two body\n' }
      payload.proposal = { status: 'ready_to_apply', wiki: rev ? wikiByRev[rev] : wikiByRev['2'], topics: [] }
      route.fulfill({ json: payload })
    })

    await page.goto('/repos/regin/topics/compare?proposal=mock-run')
    const wikiDiff = page.locator('[data-testid="wiki-content-diff"]')
    await expect(wikiDiff).toBeVisible()
    await expect(wikiDiff.locator('.wikidiff__row--remove')).toContainText('Revision one body')
    await expect(wikiDiff.locator('.wikidiff__row--add')).toContainText('Revision two body')

    // Default header is r1 → r2. Change the Base dropdown — the native
    // <select> emits a string, so the header label must still resolve to a
    // revision (not collapse to "—") after coercion.
    await page.locator('[data-testid="revcompare-left"]').selectOption('2')
    await expect(wikiDiff.locator('.wikidiff__title')).toHaveText('r2 → r2')
  })

  test('shows a too-few-revisions notice with a single revision', async ({ page }) => {
    await page.route('**/topics/workspace/proposals**', (route) => {
      const payload = workspaceWithSelectedTopic()
      payload.revisions = [{ id: 1, revision_number: 1, kind: 'generate', is_latest: true }]
      route.fulfill({ json: payload })
    })
    await page.goto('/repos/regin/topics/compare?proposal=mock-run')
    await expect(page.locator('[data-testid="revcompare-too-few"]')).toBeVisible()
  })
})
