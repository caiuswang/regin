/**
 * Images on the page that has the task's address, and a body you can edit there.
 *
 * The defect this covers: the detail read already carried `task.images`
 * (`_one_with_images`) and the page threw it away, so an attachment was
 * invisible everywhere except inside the edit modal that put it there.
 *
 * `![](image:N)` is DISPLAY only. A run still receives every attachment beside
 * its prompt whether the body names it or not — `runs.py` is untouched, and
 * `agent-prompt-verbatim.spec.js` is what guards that end.
 */
import { test, expect } from './auth-fixture.js'

// 1x1 transparent PNG — the smallest thing that is really an image.
const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAE'
  + 'hQGAhKmMIQAAAABJRU5ErkJggg==', 'base64')

function task(overrides = {}) {
  return {
    id: 80, title: 'a task with pictures', body: '', status: 'idea',
    source: 'web', tags: [], links: [], params: {},
    autorun: false, project_id: null, priority: 'none', priority_rank: 4,
    parent_id: null, assignee: null, assignee_name: null, rounds: 1,
    created_at: '2026-09-14', updated_at: '2026-09-14',
    external_id: null, created_by: null,
    images: [{ idx: 0, media_type: 'image/png', byte_size: PNG.length }],
    ...overrides,
  }
}

async function stub(page, body, onPatch) {
  await page.route(/\/api\/tasks\/80$/, (route) => {
    if (route.request().method() === 'PATCH') {
      onPatch?.(JSON.parse(route.request().postData() || '{}'))
      return route.fulfill({ json: { task: body, grain: { children: 0, runs: 0 } } })
    }
    return route.fulfill({ json: { task: body, grain: { children: 0, runs: 0 } } })
  })
  await page.route(/\/api\/tasks\/80\/images\/\d+$/, (route) =>
    route.fulfill({ body: PNG, contentType: 'image/png' }))
  await page.route(/\/api\/tasks\/80\/comments$/, (route) =>
    route.fulfill({ json: { threads: [] } }))
  await page.route(/\/api\/tasks\/80\/links$/, (route) =>
    route.fulfill({ json: { links: [] } }))
}

test('an attachment is visible on the detail page, named by the index a body reference uses',
  async ({ page }) => {
    await stub(page, task())
    await page.goto('/tasks/80')

    const thumbs = page.getByTestId('task-gallery-thumb')
    await expect(thumbs).toHaveCount(1)
    // Unguessable otherwise: `image:0` is the whole reference syntax.
    await expect(thumbs.first()).toContainText('image:0')
  })

test('a thumbnail opens full size', async ({ page }) => {
  await stub(page, task())
  await page.goto('/tasks/80')

  await page.getByTestId('task-gallery-thumb').first().click()
  await expect(page.locator('.tig-full')).toBeVisible()
})

test('`![](image:0)` in the body renders the attachment inline', async ({ page }) => {
  await stub(page, task({ body: 'Before.\n\n![a picture](image:0)\n\nAfter.' }))
  await page.goto('/tasks/80')

  const img = page.locator('.tbe-body img')
  await expect(img).toHaveCount(1)
  // An object URL, not the authenticated bytes route: an <img> tag cannot
  // attach the Authorization header, which is the whole reason for the rewrite.
  await expect(img).toHaveAttribute('src', /^blob:/)
  await expect(page.locator('.tbe-body')).not.toContainText('image:0')
})

test('a reference to a detached image reads as text, not as a broken image',
  async ({ page }) => {
    await stub(page, task({ body: '![gone](image:7)', images: [] }))
    await page.goto('/tasks/80')

    await expect(page.locator('.tbe-body img')).toHaveCount(0)
    await expect(page.locator('.tbe-body')).toContainText('image:7 — no longer attached')
  })

test('the body edits in place and saves the exact bytes it was given',
  async ({ page }) => {
    // The point of source-over-preview: a spec full of fences, literal `*` and
    // an HTML comment must come back byte-identical. A WYSIWYG round trip
    // rewrote 11% of lines and deleted the comment (regin:67).
    const BODY = [
      '## Spec', '', '<!-- a structural marker -->', '',
      '```bash', 'ls -la *.py', '```', '', 'A literal * star.',
    ].join('\n')
    let patched = null
    await stub(page, task({ body: BODY }), (b) => { patched = b })
    await page.goto('/tasks/80')

    await page.getByTestId('task-body-edit').click()
    await expect(page.getByTestId('task-body-area')).toHaveValue(BODY)

    await page.getByTestId('task-body-area').fill(`${BODY}\n\nAppended.`)
    await page.getByTestId('task-body-save').click()

    await expect.poll(() => patched?.body).toBe(`${BODY}\n\nAppended.`)
  })

test('cancel really cancels', async ({ page }) => {
  let patched = null
  await stub(page, task({ body: 'original' }), (b) => { patched = b })
  await page.goto('/tasks/80')

  await page.getByTestId('task-body-edit').click()
  await page.getByTestId('task-body-area').fill('thrown away')
  await page.getByTestId('task-body-cancel').click()

  await expect(page.locator('.tbe-body')).toContainText('original')
  expect(patched).toBeNull()
})
