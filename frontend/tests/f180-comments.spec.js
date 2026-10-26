/**
 * F180 #3 — a task comment is written in the rich editor: a pasted image is
 * held in the browser and posted in the comment's own request, a reference
 * renders as a live chip, and `image:N` resolves among that comment's images
 * alone.
 *
 * The paste and discard scenarios run against the suite's server, so the
 * images landing on (or never reaching) the comment is the backend's answer;
 * the rendering scenarios are stubbed.
 */
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='
const PNG = Buffer.from(PNG_B64, 'base64')

const unique = (label) => `${label} ${Date.now()}-${Math.random().toString(36).slice(2, 7)}`

async function api(request, method, path, data) {
  const { token } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const res = await request[method](`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` }, data,
  })
  expect(res.ok(), `${method} ${path}`).toBeTruthy()
  return res.json()
}

async function pasteImages(locator, count = 1) {
  await locator.evaluate((el, { b64, count }) => {
    const bin = atob(b64)
    const bytes = new Uint8Array(bin.length)
    for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i)
    const dt = new DataTransfer()
    for (let i = 0; i < count; i += 1) {
      dt.items.add(new File([bytes], `pasted-${i}.png`, { type: 'image/png' }))
    }
    el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }, { b64: PNG_B64, count })
}

// Every request the page sends that carries comment or image bytes.
function watchWrites(page) {
  const seen = { posts: [], images: [] }
  page.on('request', (req) => {
    const path = new URL(req.url()).pathname
    if (req.method() === 'POST' && /\/api\/tasks\/\d+\/comments$/.test(path)) {
      seen.posts.push(req.postDataJSON())
    }
    if (req.method() !== 'GET' && /\/images(\/|$)/.test(path)) seen.images.push(path)
  })
  return seen
}

const draft = (page) => page.getByTestId('task-comment-draft-rich')

test.use({ viewport: { width: 1280, height: 900 } })

test('test_comment_pasted_image_is_cited_and_drawn', async ({ page, request }) => {
  const { task } = await api(request, 'post', '/api/tasks', { title: unique('commented') })
  const seen = watchWrites(page)
  await page.goto(`/tasks/${task.id}`)

  await draft(page).click()
  await pasteImages(draft(page))
  await expect(draft(page).locator('img[data-idx="1"]')).toHaveCount(1)
  await page.keyboard.type('按钮错位')
  // Held in the browser until Post.
  expect(seen.images).toEqual([])

  await page.getByTestId('task-comment-submit').click()
  const comment = page.locator('[data-testid^="task-thread-"] .tc-comment').first()
  await expect(comment).toContainText('按钮错位')
  await expect(comment.locator('.tc-body img')).toHaveAttribute('src', /^blob:/)
  await expect(comment.locator('.tc-body img')).toHaveJSProperty('complete', true)

  expect(seen.posts).toHaveLength(1)
  expect(seen.posts[0].body).toContain('![](image:1)')
  expect(seen.posts[0].images.map((i) => i.idx)).toEqual([1])
  expect(seen.images).toEqual([])

  const { threads } = await api(request, 'get', `/api/tasks/${task.id}/comments`)
  expect(threads).toHaveLength(1)
  const [posted] = threads[0].comments
  expect(posted.images).toEqual([expect.objectContaining({ idx: 1, media_type: 'image/png' })])

  // The draft is empty again, its held image gone with it.
  await expect(page.getByTestId('task-comment-discard')).toHaveCount(0)
  await expect(page.getByTestId('task-image-strip')).toHaveCount(0)
})

test('test_comment_discarded_draft_uploads_nothing', async ({ page, request }) => {
  const { task } = await api(request, 'post', '/api/tasks', { title: unique('discarded') })
  const seen = watchWrites(page)
  await page.goto(`/tasks/${task.id}`)

  await draft(page).click()
  await pasteImages(draft(page), 2)
  await expect(draft(page).locator('img[data-idx]')).toHaveCount(2)
  await expect(page.getByTestId('task-image-thumb')).toHaveCount(2)

  // Removing a held image takes its citation out of the draft with it.
  await page.getByRole('button', { name: 'Remove pasted-0.png' }).click()
  await expect(page.getByTestId('task-image-thumb')).toHaveCount(1)
  await expect(draft(page).locator('img[data-idx="1"]')).toHaveCount(0)
  await expect(draft(page).locator('img[data-idx="2"]')).toHaveCount(1)

  await page.getByTestId('task-comment-discard').click()
  await expect(draft(page).locator('img')).toHaveCount(0)
  await expect(page.getByTestId('task-image-strip')).toHaveCount(0)

  expect(seen.posts).toEqual([])
  expect(seen.images).toEqual([])
  const { threads } = await api(request, 'get', `/api/tasks/${task.id}/comments`)
  expect(threads).toEqual([])
})

const TASK = 81

function stubTask(overrides = {}) {
  return {
    id: TASK, title: 'a commented task', body: null, status: 'idea',
    source: 'web', tags: [], links: [], params: {},
    autorun: false, project_id: null, priority: 'none', priority_rank: 4,
    parent_id: null, assignee: null, assignee_name: null, rounds: 1,
    created_at: '2026-10-26', updated_at: '2026-10-26',
    external_id: null, created_by: null, images: [], ...overrides,
  }
}

function stubComment(id, body, images = []) {
  return { id, author_kind: 'human', created_at: '2026-10-26T00:00:00+00:00', body, images }
}

async function stubPage(page, threads) {
  const fetched = []
  await page.route(new RegExp(`/api/tasks/${TASK}$`), (route) =>
    route.fulfill({ json: { task: stubTask(), grain: { children: 0, runs: 0 } } }))
  await page.route(new RegExp(`/api/tasks/${TASK}/links$`), (route) =>
    route.fulfill({ json: { links: [] } }))
  await page.route(/\/api\/(tasks|comments)\/\d+\/images\/\d+$/, (route) => {
    fetched.push(new URL(route.request().url()).pathname)
    return route.fulfill({ body: PNG, contentType: 'image/png' })
  })
  await page.route(new RegExp(`/api/tasks/${TASK}/comments$`), (route) => {
    if (route.request().method() === 'POST') {
      const sent = route.request().postDataJSON()
      threads.push({
        id: 9, resolution_state: 'open', created_at: '2026-10-26T00:00:00+00:00',
        comments: [stubComment(900, sent.body)],
      })
      return route.fulfill({ status: 201, json: { thread: threads.at(-1), comment: threads.at(-1).comments[0] } })
    }
    return route.fulfill({ json: { threads } })
  })
  return { fetched }
}

test('test_comment_image_ref_resolves_to_its_own_comment', async ({ page }) => {
  const { fetched } = await stubPage(page, [{
    id: 1, resolution_state: 'open', created_at: '2026-10-26T00:00:00+00:00',
    comments: [
      stubComment(101, 'mine\n\n![](image:1)', [{ idx: 1, media_type: 'image/png', byte_size: PNG.length }]),
      stubComment(102, 'theirs\n\n![](image:1)'),
    ],
  }])
  // The task holds an image 1 too; neither comment may draw it.
  await page.route(new RegExp(`/api/tasks/${TASK}$`), (route) => route.fulfill({
    json: { task: stubTask({ images: [{ idx: 1, media_type: 'image/png', byte_size: PNG.length }] }),
            grain: { children: 0, runs: 0 } },
  }))
  await page.goto(`/tasks/${TASK}`)

  const [c1, c2] = [0, 1].map((i) => page.locator('[data-testid="task-thread-1"] .tc-comment').nth(i))
  await expect(c1.locator('.tc-body img')).toHaveAttribute('src', /^blob:/)
  await expect(c2).toContainText('(image:1 — no longer attached)')
  await expect(c2.locator('.tc-body img')).toHaveCount(0)
  expect(fetched.filter((p) => p.startsWith('/api/comments/'))).toEqual(['/api/comments/101/images/1'])
})

test('test_comment_mention_inserts_reference_chip', async ({ page }) => {
  const FEATURE = {
    ref: 'feature:180', kind: 'feature', key: '180', label: 'Pipeline 九期',
    title: 'Pipeline 九期', status: 'build', project_id: null, project: null,
    feature_id: 180, feature_title: 'Pipeline 九期', url: '/features/180',
  }
  await page.route((url) => url.pathname === '/api/refs', (route) => {
    const q = new URL(route.request().url()).searchParams.get('q') || ''
    return route.fulfill({
      json: { query: q, kind: null, context: [], groups: [{ kind: 'feature', total: 1, items: [FEATURE] }] },
    })
  })
  await page.route((url) => url.pathname === '/api/refs/resolve', (route) =>
    route.fulfill({ json: { refs: { [FEATURE.ref]: FEATURE } } }))
  const threads = []
  const posts = []
  page.on('request', (req) => {
    if (req.method() === 'POST' && req.url().endsWith(`/api/tasks/${TASK}/comments`)) posts.push(req.postDataJSON())
  })
  await stubPage(page, threads)
  await page.goto(`/tasks/${TASK}`)

  await draft(page).click()
  await page.keyboard.type('@F180')
  const menu = page.getByTestId('ref-menu')
  await expect(menu).toHaveAttribute('data-query', 'F180')
  await expect(menu).not.toHaveClass(/is-stale/)
  await page.keyboard.press('Enter')
  await expect(menu).toHaveCount(0)
  await expect(draft(page).locator('[data-testid="ref-chip"][data-ref="feature:180"]')).toBeVisible()

  await page.getByTestId('task-comment-submit').click()
  const posted = page.locator('[data-testid="task-thread-9"] .tc-body')
  const chip = posted.locator('[data-testid="ref-chip"][data-ref="feature:180"]')
  await expect(chip).toContainText('Pipeline 九期')
  await expect(posted.locator('a[href="feature:180"]')).toHaveCount(0)

  expect(posts).toHaveLength(1)
  expect(posts[0].body).toMatch(/\[[^\]]+\]\(feature:180\)/)
})
