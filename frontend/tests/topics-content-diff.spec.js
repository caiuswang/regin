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

// Per-revision topic bodies: r3 (id 1) → r4 (id 2) changes the wiki AND adds
// a reference file + an alias, so the compare card exercises all three panes.
function topicForRevision(rev) {
  const byRev = {
    1: {
      id: 'webui-surface', label: 'WebUI surface',
      wiki: '# WebUI\nOld overview line\n',
      refs: [{ path: 'frontend/src/router.js', role: 'entrypoint' }],
      edges: [], aliases: ['webui'], intent: 'map the SPA',
    },
    2: {
      id: 'webui-surface', label: 'WebUI surface',
      wiki: '# WebUI\nNew overview line\nExtra detail\n',
      refs: [
        { path: 'frontend/src/router.js', role: 'entrypoint' },
        { path: 'web/app.py', role: 'api' },
      ],
      edges: [], aliases: ['webui', 'spa'], intent: 'map the SPA',
    },
  }
  return byRev[rev || '2']
}

function compareWorkspace(revisions) {
  return (route) => {
    const rev = new URL(route.request().url()).searchParams.get('revision_id')
    const topic = topicForRevision(rev)
    route.fulfill({
      json: {
        selected_run: { id: 'mock-run', title: 'webui-surface refresh', state: 'completed', provider: 'external' },
        revisions,
        proposal: { status: 'ready_to_apply', wiki: topic.wiki, topics: [topic] },
      },
    })
  }
}

const TWO_REVISIONS = [
  { id: 2, revision_number: 4, kind: 'regenerated', is_latest: true, created_at: '2026-06-19T14:39:15Z' },
  { id: 1, revision_number: 3, kind: 'regenerated', is_latest: false, created_at: '2026-06-18T10:00:00Z' },
]

test.describe('Revision compare page', () => {
  test('shows proposal context and diffs wiki + reference files per topic', async ({ page }) => {
    await page.route('**/topics/workspace/proposals**', compareWorkspace(TWO_REVISIONS))
    await page.goto('/repos/regin/topics/compare?proposal=mock-run')

    // Proposal context is present (the earlier gap: only the id showed).
    await expect(page.getByRole('heading', { name: 'webui-surface refresh' })).toBeVisible()

    const card = page.locator('[data-testid="topic-comparison-card"]')
    await expect(card).toBeVisible()
    // Wiki content diff (r3 → r4).
    await expect(card.locator('.wikidiff__row--remove').first()).toContainText('Old overview line')
    await expect(card.locator('.wikidiff__row--add').first()).toContainText('New overview line')
    // Reference files: web/app.py was added.
    await expect(card).toContainText('web/app.py')
    // Graph metadata: alias "spa" was added.
    await expect(card).toContainText('spa')
  })

  test('coerces the native-select value so switching sides stays type-correct', async ({ page }) => {
    await page.route('**/topics/workspace/proposals**', compareWorkspace(TWO_REVISIONS))
    await page.goto('/repos/regin/topics/compare?proposal=mock-run')
    const card = page.locator('[data-testid="topic-comparison-card"]')
    await expect(card).toBeVisible()
    // Set Base to r4 (id 2) too — identical sides → "No content changes"
    // (proves the string→int coercion; a string id would break the match).
    await page.locator('[data-testid="revcompare-left"]').selectOption('2')
    await expect(card.locator('[data-testid="wiki-diff-identical"]')).toBeVisible()
  })

  test('shows a too-few-revisions notice with a single revision', async ({ page }) => {
    await page.route('**/topics/workspace/proposals**', compareWorkspace([
      { id: 1, revision_number: 1, kind: 'generate', is_latest: true },
    ]))
    await page.goto('/repos/regin/topics/compare?proposal=mock-run')
    await expect(page.locator('[data-testid="revcompare-too-few"]')).toBeVisible()
  })
})
