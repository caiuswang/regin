/**
 * Feature #119 S6 — defect verbs and the `/defects` list (D2, D6, R5).
 * The API halves live in tests/defects/test_web_defect_verbs.py.
 */
import { test, expect } from './auth-fixture.js'

const PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=',
  'base64')

function defect(id, over = {}) {
  return {
    id, feature_id: null, feature_title: null, symptom: `symptom ${id}`,
    status: 'open', round: 0, blamed_event_kind: null, missing_check: null,
    suspect_paths: [], fix_task_id: null, fix_state: null,
    triage_state: null, triage_reason: null, queue_position: 'queue',
    images: [], opened_at: '2026-09-14 12:00:00', updated_at: '2026-09-14 12:00:00',
    closed_at: null, ...over,
  }
}

const ATTRIBUTED = { feature_id: 81, feature_title: 'The ledger', queue_position: null,
                     blamed_event_kind: 'started' }
const FEATURES = [{ id: 44, title: 'Project progress rollups', stage: 'building' },
                  { id: 81, title: 'The ledger', stage: 'building' }]

function page0(items, total = items.length, extra = {}) {
  return { items, pagination: { total, page: 0, size: 50, has_next: total > items.length,
                                has_prev: false }, population: total, ...extra }
}

/** One defect on its own page; writes are recorded and answered by `onPost`. */
async function stubDetail(page, state, onPost = () => ({ defect: state.row })) {
  const posts = []
  await page.route(/\/api\/defects\/\d+(\/[a-z-]+)?$/, async (route) => {
    const req = route.request()
    const path = new URL(req.url()).pathname
    if (req.method() === 'GET') {
      const id = Number(path.match(/\/defects\/(\d+)/)[1])
      return route.fulfill({ json: { defect: state.rows?.[id] || state.row } })
    }
    posts.push({ method: req.method(), path, body: req.postDataJSON() })
    const answer = await onPost(path, req.postDataJSON())
    return route.fulfill(answer.status ? answer : { json: answer })
  })
  return posts
}

async function menuItems(page, id) {
  await page.getByTestId(`defect-more-${id}`).click()
  const names = await page.getByRole('menuitem').allInnerTexts()
  await page.keyboard.press('Escape')
  return names.map(n => n.trim())
}

test('test_defects_list_page_filters_and_pages', async ({ page }) => {
  const asked = []
  const all = Array.from({ length: 60 }, (_, i) => defect(200 - i, ATTRIBUTED))
  const declined = defect(150, { status: 'closed', triage_state: 'declined',
                                 queue_position: 'declined', triage_reason: 'dup of a note' })
  await page.route(/\/api\/defects\?/, (route) => {
    const q = new URL(route.request().url()).searchParams
    asked.push(Object.fromEntries(q))
    if (q.get('triage') === 'declined') return route.fulfill({ json: page0([declined], 1, { population: 61 }) })
    const at = Number(q.get('page') || 0)
    const rows = all.slice(at * 50, at * 50 + 50)
    return route.fulfill({ json: { items: rows, population: 61,
      pagination: { total: 61, page: at, size: 50, has_next: at === 0, has_prev: at > 0 } } })
  })
  const posts = await stubDetail(page, { row: declined }, () => ({
    defect: { ...declined, status: 'open', triage_state: null, queue_position: 'queue' } }))

  await page.goto('/defects')
  await expect(page.getByTestId('defects-list-page')).toBeVisible()
  await expect(page.getByTestId('pipeline-tab-features')).toHaveAttribute('aria-current', 'page')
  await expect(page.getByTestId('defects-list').locator('> li')).toHaveCount(50)
  await expect(page.getByTestId('list-count')).toHaveText('61 defects')

  // Paging is the server's: page 2 is a request, not a slice of a loaded array.
  await page.getByRole('button', { name: /next/i }).first().click()
  await expect(page).toHaveURL(/page=1/)
  await expect.poll(() => asked.at(-1)?.page).toBe('1')
  await expect(page.getByTestId('defects-list').locator('> li')).toHaveCount(10)

  // Search commits on Enter and rides the URL.
  await page.getByTestId('list-search').fill('gutter')
  await page.getByTestId('list-search').press('Enter')
  await expect.poll(() => asked.at(-1)?.q).toBe('gutter')
  await expect(page).not.toHaveURL(/page=1/)

  // A declined defect is reachable and offers Restore.
  await page.goto('/defects?triage=declined')
  await expect.poll(() => asked.at(-1)?.triage).toBe('declined')
  await expect(page.getByTestId('list-count')).toHaveText('1 of 61 defects')
  await expect(page.getByTestId('defect-triage-150')).toHaveText('declined')
  await expect(page.getByTestId('defect-primary-150')).toHaveText('Restore')
  await page.getByTestId('defect-primary-150').click()
  await expect.poll(() => posts.map(p => p.path)).toEqual(['/api/defects/150/restore'])
  await expect(page.getByRole('status')).toContainText('#150 restored to the queue')

  // The facet control writes the same parameter.
  await page.goto('/defects')
  await page.getByTestId('list-facet-triage').click()
  await page.getByRole('option', { name: 'Snoozed' }).click()
  await expect(page).toHaveURL(/triage=snoozed/)
  await expect.poll(() => asked.at(-1)?.triage).toBe('snoozed')

  // A feature's "full list" arrives with feature_id, shown as a chip Clear keeps.
  await page.goto('/defects?feature_id=81&status=open')
  await expect.poll(() => asked.at(-1)?.feature_id).toBe('81')
  await expect(page.getByTestId('defects-feature-chip')).toContainText('feature #81')
  await page.getByTestId('list-clear').click()
  await expect(page).toHaveURL(/feature_id=81/)
  await expect(page).not.toHaveURL(/status=open/)
  await page.getByTestId('defects-feature-clear').click()
  await expect(page).not.toHaveURL(/feature_id/)
})

test('test_defect_detail_menu_offers_verbs_by_state', async ({ page }) => {
  const cases = [
    [defect(7), 'Attribute',
      ['Attribute', 'Merge into…', 'Decline…', 'Snooze', 'Edit symptom', 'Edit suspect paths', 'Attach image…']],
    [defect(7, { triage_state: 'snoozed', queue_position: 'snoozed' }), 'Unsnooze',
      ['Unsnooze', 'Attribute', 'Merge into…', 'Decline…', 'Edit symptom', 'Edit suspect paths']],
    [defect(7, ATTRIBUTED), 'Close',
      ['Re-attribute', 'Close', 'Create fix task', 'Link existing task', 'Merge into…',
       'Absorb duplicates…', 'Edit symptom', 'Edit suspect paths', 'Attach image…']],
    [defect(7, { ...ATTRIBUTED, fix_task_id: 300 }), 'Close',
      ['Re-attribute', 'Close', 'Re-point fix task', 'Clear fix task', 'Merge into…',
       'Absorb duplicates…', 'Edit symptom', 'Edit suspect paths', 'Attach image…']],
    [defect(7, { status: 'closed', triage_state: 'declined', queue_position: 'declined' }), 'Restore',
      ['Restore', 'Edit symptom']],
    [defect(7, { ...ATTRIBUTED, status: 'closed', missing_check: 'test_x' }), 'Reopen',
      ['Reopen', 'Edit symptom', 'Edit suspect paths', 'Absorb duplicates…']],
  ]
  const state = { row: cases[0][0] }
  const posts = await stubDetail(page, state)
  for (const [row, primary, menu] of cases) {
    state.row = row
    await page.goto('/defects/7')
    await expect(page.getByTestId('defect-primary-7')).toHaveText(primary)
    expect(await menuItems(page, 7)).toEqual(menu)
  }

  // Unsnooze is one click; the edit of suspect paths sends the list.
  state.row = cases[1][0]
  await page.goto('/defects/7')
  await page.getByTestId('defect-primary-7').click()
  await expect.poll(() => posts.at(-1)?.path).toBe('/api/defects/7/unsnooze')
  await page.getByTestId('defect-more-7').click()
  await page.getByRole('menuitem', { name: 'Edit suspect paths' }).click()
  await page.getByTestId('defect-paths-field-7').fill('lib/a.py\nweb/b.vue')
  await page.getByTestId('defect-paths-submit-7').click()
  await expect.poll(() => posts.at(-1)).toEqual({
    method: 'PATCH', path: '/api/defects/7', body: { suspect_paths: ['lib/a.py', 'web/b.vue'] } })
})

test('a refusal that names merge offers it as a button', async ({ page }) => {
  const state = { row: defect(7), rows: { 9: defect(9, { ...ATTRIBUTED, symptom: 'the keeper' }) } }
  await page.route(/\/api\/features\?/, route => route.fulfill({ json: page0(FEATURES) }))
  const posts = await stubDetail(page, state, (path) => (path.endsWith('/attribute')
    ? { status: 409, json: { error: '#9 already reports this symptom for feature 81 — `regin defects merge 7 --into 9` if they are the same bug',
                             remedy: { verb: 'merge', args: { defect_id: 7, into: 9 } } } }
    : { defect: state.rows[9] }))

  await page.goto('/defects/7')
  await page.getByTestId('defect-primary-7').click()
  await page.getByTestId('defect-attribute-picker-7').click()
  await page.getByTestId('defect-attribute-picker-7-option-81').click()
  await page.getByTestId('defect-attribute-submit-7').click()
  const remedy = page.getByTestId('defect-refusal-7-remedy')
  await expect(remedy).toHaveText('Merge #7 into #9')
  await remedy.click()
  expect(posts.map(p => [p.path, p.body])).toEqual([
    ['/api/defects/7/attribute', { feature_id: 81 }],
    ['/api/defects/7/merge', { into: 9 }],
  ])
  await expect(page).toHaveURL(/\/defects\/9$/)
})

test('test_defect_merge_picker_previews_and_confirms', async ({ page }) => {
  const keeper = defect(9, { ...ATTRIBUTED, symptom: 'the keeper', suspect_paths: ['lib/ledger.py'] })
  const state = { row: defect(7, { ...ATTRIBUTED, symptom: 'the duplicate' }), rows: { 9: keeper } }
  const asked = []
  await page.route(/\/api\/defects\?/, (route) => {
    const q = new URL(route.request().url()).searchParams
    asked.push(Object.fromEntries(q))
    if (q.get('feature_id') === '81') return route.fulfill({ json: page0([state.row, keeper]) })
    if (q.get('q') === 'palette') return route.fulfill({ json: page0([defect(12, { symptom: 'palette is stale' })]) })
    return route.fulfill({ json: page0([state.row, keeper, defect(12, { symptom: 'palette is stale' })], 40) })
  })
  const posts = await stubDetail(page, state, () => ({ defect: keeper }))

  await page.goto('/defects/7')
  await page.getByTestId('defect-more-7').click()
  await page.getByRole('menuitem', { name: 'Merge into…' }).click()
  const picker = page.getByTestId('merge-picker')
  // Same feature first, open only, never itself.
  await expect(picker.getByText('Same feature')).toBeVisible()
  await expect(picker.getByTestId('merge-option-9')).toBeVisible()
  await expect(picker.getByTestId('merge-option-7')).toHaveCount(0)
  expect(asked[0]).toMatchObject({ status: 'open', feature_id: '81' })

  // The search asks the server.
  await picker.getByTestId('merge-search').fill('palette')
  await expect.poll(() => asked.at(-1)?.q).toBe('palette')
  await expect(picker.getByTestId('merge-option-12')).toBeVisible()

  await picker.getByTestId('merge-option-9').click()
  const preview = page.getByTestId('merge-preview')
  await expect(preview).toContainText('the keeper')
  await expect(preview).toContainText('lib/ledger.py')
  await expect(page.getByTestId('merge-confirm')).toHaveText('Delete #7, keep #9')
  expect(posts).toEqual([])

  await page.getByTestId('merge-confirm').click()
  expect(posts.map(p => [p.path, p.body])).toEqual([['/api/defects/7/merge', { into: 9 }]])
  await expect(page).toHaveURL(/\/defects\/9$/)
  await expect(page.getByRole('status')).toContainText('#7 folded into #9')
})

test('test_defect_attach_image_on_detail', async ({ page }) => {
  const state = { row: defect(7, ATTRIBUTED) }
  const posts = await stubDetail(page, state, () => {
    state.row = { ...state.row, images: [{ idx: 1 }] }
    return { status: 201, json: { image: { idx: 1 } } }
  })
  await page.route('**/api/defects/7/images/1', route =>
    route.fulfill({ body: PNG, contentType: 'image/png' }))

  await page.goto('/defects/7')
  await expect(page.getByTestId('defect-images')).toHaveCount(0)
  await page.getByTestId('defect-more-7').click()
  const chooser = page.waitForEvent('filechooser')
  await page.getByRole('menuitem', { name: 'Attach image…' }).click()
  await (await chooser).setFiles({ name: 'shot.png', mimeType: 'image/png', buffer: PNG })

  await expect.poll(() => posts.length).toBe(1)
  expect(posts[0].path).toBe('/api/defects/7/images')
  expect(posts[0].body.data_b64).toBeTruthy()
  await expect(page.getByTestId('defect-image-thumb')).toHaveCount(1)
  await expect(page.getByRole('status')).toContainText('1 image attached')
})

/** Paste a PNG and/or text into `locator`; resolves false when a handler claimed it. */
async function pasteInto(locator, { png = null, text = null }) {
  return locator.evaluate((el, { b64, text }) => {
    const dt = new DataTransfer()
    if (b64) {
      const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0))
      dt.items.add(new File([bytes], 'shot.png', { type: 'image/png' }))
    }
    if (text) dt.setData('text/plain', text)
    return el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }))
  }, { b64: png ? png.toString('base64') : null, text })
}

test('test_defect_edit_paste_attaches_image', async ({ page }) => {
  const state = { row: defect(7, { ...ATTRIBUTED, images: [{ idx: 1 }] }) }
  const posts = await stubDetail(page, state, (path) => {
    if (!path.endsWith('/images')) return { defect: state.row }
    const idx = state.row.images.length + 1
    state.row = { ...state.row, images: [...state.row.images, { idx }] }
    return { status: 201, json: { image: { idx } } }
  })
  let imageGets = 0
  await page.route(/\/api\/defects\/7\/images\/\d+$/, (route) => {
    imageGets += 1
    return route.fulfill({ body: PNG, contentType: 'image/png' })
  })

  await page.goto('/defects/7')
  await expect(page.getByTestId('defect-image-thumb')).toHaveCount(1)
  await page.getByTestId('defect-more-7').click()
  await page.getByRole('menuitem', { name: 'Edit symptom' }).click()
  const field = page.getByTestId('defect-edit-field-7')
  await field.fill('reworded, not saved yet')

  expect(await pasteInto(field, { png: PNG })).toBe(false)
  await expect.poll(() => posts.length).toBe(1)
  expect(posts[0].path).toBe('/api/defects/7/images')
  expect(posts[0].body.data_b64).toBeTruthy()
  await expect(page.getByTestId('defect-image-thumb')).toHaveCount(2)
  await expect(page.getByRole('status')).toContainText('1 image attached')
  await expect(field).toHaveValue('reworded, not saved yet')

  // A reload that brings back the same images must not re-pull them.
  const before = imageGets
  await page.getByTestId('defect-edit-submit-7').click()
  await expect(page.getByRole('status')).toContainText('#7 edited')
  await expect(page.getByTestId('defect-image-thumb')).toHaveCount(2)
  expect(imageGets).toBe(before)
})

test('test_defect_paste_text_or_other_form_posts_nothing', async ({ page }) => {
  const state = { row: defect(7, ATTRIBUTED) }
  const posts = await stubDetail(page, state)
  await page.goto('/defects/7')

  await page.getByTestId('defect-more-7').click()
  await page.getByRole('menuitem', { name: 'Edit symptom' }).click()
  expect(await pasteInto(page.getByTestId('defect-edit-field-7'), { text: 'plain words' })).toBe(true)

  await page.getByTestId('defect-more-7').click()
  await page.getByRole('menuitem', { name: 'Edit suspect paths' }).click()
  expect(await pasteInto(page.getByTestId('defect-paths-field-7'), { png: PNG })).toBe(true)
  expect(posts).toEqual([])
})

/** The Features tab with a stubbed queue; everything else hits the suite's server. */
async function stubTriage(page, queue) {
  const state = { queue, posts: [] }
  await page.route(/\/api\/defects(\?.*|\/.*)?$/, (route) => {
    const req = route.request()
    const url = new URL(req.url())
    if (req.method() === 'POST') {
      state.posts.push({ path: url.pathname, body: req.postDataJSON() })
      const id = Number(url.pathname.match(/\/defects\/(\d+)/)[1])
      if (/attribute|merge|decline|snooze/.test(url.pathname)) state.queue = state.queue.filter(d => d.id !== id)
      return route.fulfill({ json: { defect: defect(id, { ...ATTRIBUTED, fix_task_id: 500 + id }) } })
    }
    const img = url.pathname.match(/\/defects\/\d+\/images\/\d+$/)
    if (img) return route.fulfill({ body: PNG, contentType: 'image/png' })
    const one = url.pathname.match(/\/defects\/(\d+)$/)
    if (one) return route.fulfill({ json: { defect: defect(Number(one[1])) } })
    if (url.searchParams.get('scope') === 'triage') return route.fulfill({ json: page0(state.queue) })
    return route.fulfill({ json: page0([]) })
  })
  // The picker's search gets the features; the tab's own list stays empty, so
  // the rows it draws need no more fields than this spec cares about.
  await page.route(/\/api\/features(\?.*)?$/, (route) => {
    const picker = new URL(route.request().url()).searchParams.get('size') === '20'
    return route.fulfill({ json: {
      ...page0(picker ? FEATURES : []),
      extras: { stage_counts: {}, unattributed_defects: state.queue.length } } })
  })
  return state
}

async function blame(page, testid) {
  await page.getByTestId(testid).click()
  await page.getByTestId(`${testid}-option-44`).click()
}

test('test_triage_accept_same_on_rail_and_sheet', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  let state = await stubTriage(page, [defect(94, { images: [{ idx: 1 }] }), defect(93), defect(92)])
  await page.goto('/features')
  await page.getByTestId('triage-unfold').click()

  // Triage cards carry the report's screenshots.
  await expect(page.getByTestId('triage-card-94').getByTestId('defect-image-thumb')).toHaveCount(1)

  // A on the rail: attribute AND create the fix task.
  await page.getByTestId('triage-card-94').click()
  await blame(page, 'triage-blame')
  await page.keyboard.press('a')
  await expect(page.getByTestId('triage-card-94')).toHaveCount(0)
  expect(state.posts).toEqual([
    { path: '/api/defects/94/attribute', body: { feature_id: 44 } },
    { path: '/api/defects/94/fix-item', body: { create: true } },
  ])

  // ⇧A on the rail: attribute only.
  await page.getByTestId('triage-card-93').click()
  await blame(page, 'triage-blame')
  await page.keyboard.press('Shift+A')
  await expect(page.getByTestId('triage-card-93')).toHaveCount(0)
  expect(state.posts.slice(2)).toEqual([{ path: '/api/defects/93/attribute', body: { feature_id: 44 } }])

  // The sheet: the same two effects under the same two names.
  await page.unrouteAll({ behavior: 'ignoreErrors' })
  state = await stubTriage(page, [defect(91), defect(90)])
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/features')
  await page.getByTestId('features-triage-open').click()
  await blame(page, 'triage-sheet-blame')
  await page.getByTestId('triage-sheet-accept').click()
  await expect(page.getByTestId('triage-sheet-position')).toHaveText('1 of 1')
  await blame(page, 'triage-sheet-blame')
  await page.getByTestId('triage-sheet-accept-only').click()
  await expect.poll(() => state.posts.length).toBe(3)
  expect(state.posts).toEqual([
    { path: '/api/defects/91/attribute', body: { feature_id: 44 } },
    { path: '/api/defects/91/fix-item', body: { create: true } },
    { path: '/api/defects/90/attribute', body: { feature_id: 44 } },
  ])
})

test('test_triage_arrival_focuses_card', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const state = await stubTriage(page, [defect(94), defect(93)])
  await page.goto('/features?triage=93')
  const card = page.getByTestId('triage-card-93')
  await expect(card).toHaveAttribute('aria-current', 'true')
  await expect(card).toBeFocused()
  // Keys work at once, with no click first.
  await page.keyboard.press('x')
  await expect(card.getByTestId('triage-prompt-decline')).toBeVisible()
  await page.keyboard.press('Escape')

  // An id past the loaded page is read directly and focused all the same.
  await page.goto('/features?triage=77')
  await expect(page.getByTestId('triage-card-77')).toBeFocused()
  await page.keyboard.press('s')
  await expect.poll(() => state.posts.at(-1)?.path).toBe('/api/defects/77/snooze')
})

test('a refused fix task after Accept is reported about its own defect', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 })
  const state = await stubTriage(page, [defect(94), defect(93)])
  await page.route('**/api/defects/94/fix-item', (route) => {
    state.posts.push({ path: '/api/defects/94/fix-item', body: route.request().postDataJSON() })
    return route.fulfill({ status: 409, json: {
      error: '#94 already points at task #7, which was not created from it' } })
  })
  await page.goto('/features')
  await page.getByTestId('triage-unfold').click()
  await page.getByTestId('triage-card-94').click({ position: { x: 8, y: 8 } })
  await blame(page, 'triage-blame')
  await page.keyboard.press('a')

  await expect(page.getByTestId('triage-card-94')).toHaveCount(0)
  await expect(page.getByTestId('triage-notice'))
    .toContainText('#94 attributed, but the fix task was refused — #94 already points at task #7')
  // Never on the card the cursor moved to.
  await expect(page.getByTestId('triage-refusal-93')).toHaveCount(0)
  // The next verb retires the notice.
  await page.getByTestId('triage-card-93').click({ position: { x: 8, y: 8 } })
  await page.keyboard.press('s')
  await expect(page.getByTestId('triage-notice')).toHaveCount(0)
})

test('test_merge_picker_more_excludes_self_and_shown', async ({ page }) => {
  const keeper = defect(9, { ...ATTRIBUTED, symptom: 'the keeper' })
  const state = { row: defect(7, { ...ATTRIBUTED, symptom: 'the duplicate' }), rows: { 9: keeper } }
  await page.route(/\/api\/defects\?/, (route) => {
    const q = new URL(route.request().url()).searchParams
    if (q.get('feature_id') === '81') return route.fulfill({ json: page0([state.row, keeper]) })
    // The server counts this defect and the same-feature row it already listed.
    if (q.get('q') === 'the') return route.fulfill({ json: page0([state.row, keeper], 2) })
    if (q.get('q') === 'palette') return route.fulfill({ json: page0([defect(12, { symptom: 'palette is stale' })], 5) })
    return route.fulfill({ json: page0([state.row, keeper, defect(12, { symptom: 'palette is stale' })], 40) })
  })
  await stubDetail(page, state)

  await page.goto('/defects/7')
  await page.getByTestId('defect-more-7').click()
  await page.getByRole('menuitem', { name: 'Merge into…' }).click()
  const picker = page.getByTestId('merge-picker')
  // 40 open, minus #7 itself, #9 above and #12 below.
  await expect(picker.getByTestId('merge-more')).toHaveText(/^37 more/)

  // Every match is already shown or is this defect: nothing more, and no contradiction.
  await picker.getByTestId('merge-search').fill('the')
  await expect(picker.getByTestId('merge-option-9')).toBeVisible()
  await expect(picker.getByTestId('merge-more')).toHaveCount(0)

  await picker.getByTestId('merge-search').fill('palette')
  await expect(picker.getByTestId('merge-option-12')).toBeVisible()
  await expect(picker.getByTestId('merge-more')).toHaveText(/^4 more/)
})

// Feature #125 S5: the tag names where the row sits now. A snoozed report that
// woke keeps `triage_state = snoozed` but is back in the queue.
test('test_defect_row_tag_uses_queue_position', async ({ page }) => {
  const state = { row: defect(7, { triage_state: 'snoozed', queue_position: 'snoozed' }) }
  await stubDetail(page, state)

  await page.goto('/defects/7')
  await expect(page.getByTestId('defect-triage-7')).toHaveText('snoozed')

  state.row = defect(7, { triage_state: 'snoozed', queue_position: 'queue' })
  await page.goto('/defects/7')
  await expect(page.getByTestId('defect-primary-7')).toHaveText('Attribute')
  await expect(page.getByTestId('defect-triage-7')).toHaveCount(0)

  state.row = defect(7, { status: 'closed', triage_state: 'declined', queue_position: 'declined' })
  await page.goto('/defects/7')
  await expect(page.getByTestId('defect-triage-7')).toHaveText('declined')
})

// Feature #125 V5: the note points at Attribute, so it is drawn only where the
// row's verb set holds Attribute.
test('test_attribute_note_follows_verb_set', async ({ page }) => {
  const state = { row: defect(7) }
  await stubDetail(page, state)
  const note = page.getByTestId('defect-fix-create-blocked-7')

  for (const [row, shown] of [
    [defect(7), true],
    [defect(7, { triage_state: 'snoozed', queue_position: 'snoozed' }), true],
    [defect(7, { status: 'closed', triage_state: 'declined', queue_position: 'declined' }), false],
    [defect(7, { status: 'closed', queue_position: null }), false],
    [defect(7, ATTRIBUTED), false],
  ]) {
    state.row = row
    await page.goto('/defects/7')
    await expect(page.getByTestId('defect-primary-7')).toBeVisible()
    await expect(note).toHaveCount(shown ? 1 : 0)
  }
})
