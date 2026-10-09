/**
 * Feature #139, slice S1 — the pipeline theme seam.
 *
 * `classic is unchanged` compares against screenshots captured from the build
 * BEFORE the seam existed. Both runs read the same recorded API traffic
 * (`fixtures/pipeline-theme-classic.har`) under the clock it was recorded at,
 * so a diff is the frontend's, never the data's. Re-record with
 * `PL_THEME_RECORD=1` (seeds the scratch DB, rewrites the HAR) — and then the
 * baselines must be recaptured from a pre-seam checkout, or the test proves
 * nothing.
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

const HAR = fileURLToPath(new URL('./fixtures/pipeline-theme-classic.har', import.meta.url))
const RECORD = process.env.PL_THEME_RECORD === '1'

const SCREENS = ['/overview', '/tasks', '/features']
// Seeded rows, so a screen is read only once its own payload has landed — the
// notification stream never lets the network go idle.
const READY = {
  '/overview': '[data-testid="needs-decision"]',
  '/tasks': 'text=Stream span events into the trace spine',
  '/features': 'text=Cost attribution in the ledger',
}
const VIEWPORTS = [
  { name: 'desktop', width: 1440, height: 900 },
  { name: 'phone', width: 400, height: 860 },
]

async function seed(request) {
  const { token } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const headers = { Authorization: `Bearer ${token}` }
  const post = async (path, data) => {
    const res = await request.post(`${API_BASE}${path}`, { headers, data })
    if (!res.ok()) throw new Error(`${path}: ${res.status()} ${await res.text()}`)
    return res.json()
  }
  const live = await post('/api/features', { title: 'Live trace follow' })
  const ledger = await post('/api/features', { title: 'Cost attribution in the ledger' })
  await post('/api/features', { title: 'Pipeline tab memory' })
  await post('/api/tasks', { title: 'Stream span events into the trace spine', feature_id: live.id, priority: 'high' })
  await post('/api/tasks', { title: 'Auto-follow the newest span', feature_id: live.id })
  await post('/api/tasks', { title: 'Show attribution share in the Features subline', feature_id: ledger.id })
  await post('/api/tasks', { title: 'Add escape count to the project picker rows' })
  await post('/api/defects', { symptom: 'Trace spine loses the live node after a compaction divider' })
  await post('/api/defects', { symptom: 'Ledger double-counts a resumed run', feature_id: ledger.id })
}

function recordedAt() {
  const entries = JSON.parse(readFileSync(HAR, 'utf8')).log.entries
  return Date.parse(entries[0].startedDateTime)
}

// The HAR keeps the origin it was recorded on; replay it on this run's port.
function harForThisPort(testInfo, baseURL) {
  const origin = new URL(baseURL).origin
  const path = testInfo.outputPath('classic.har')
  writeFileSync(path, readFileSync(HAR, 'utf8').replace(/http:\/\/localhost:\d+/g, origin))
  return path
}

async function visitAll(page, vp, onScreen) {
  await page.setViewportSize({ width: vp.width, height: vp.height })
  for (const path of SCREENS) {
    await page.goto(path)
    await expect(page.getByTestId('pipeline-tabs')).toBeVisible()
    await expect(page.locator(READY[path]).first()).toBeVisible()
    if (vp.width >= 768) await expect(page.getByTestId('pipeline-count-tasks')).toBeVisible()
    await onScreen(path)
  }
}

// One context records every screen at every width: the HAR is written when
// its context closes, so two recording tests would overwrite each other.
test('record the classic fixture', async ({ page, request }) => {
  test.skip(!RECORD, 'set PL_THEME_RECORD=1 to re-record')
  await seed(request)
  await page.routeFromHAR(HAR, { url: '**/api/**', update: true, updateContent: 'embed' })
  for (const vp of VIEWPORTS) await visitAll(page, vp, async () => {})
})

for (const vp of VIEWPORTS) {
  test(`test_classic_is_unchanged at ${vp.name}`, async ({ page, baseURL }, testInfo) => {
    test.skip(RECORD, 'recording')
    await page.routeFromHAR(harForThisPort(testInfo, baseURL), { url: '**/api/**', notFound: 'abort' })
    await page.clock.setFixedTime(recordedAt())
    await visitAll(page, vp, async (path) => {
      // The switch is new chrome in space that was empty before the seam.
      await page.getByTestId('pipeline-theme-switch').evaluate((el) => { el.style.visibility = 'hidden' })
      await expect(page).toHaveScreenshot(`classic${path.replace('/', '-')}-${vp.name}.png`, {
        fullPage: true,
        animations: 'disabled',
      })
    })
  })
}

const PIXEL_BYTES = /\/themes\/pixel\/|pixel-[\w-]+\.js|silkscreen/i

async function plTheme(page) {
  return page.evaluate(() => document.documentElement.getAttribute('data-pl-theme'))
}

async function storeTheme(page, id) {
  await page.addInitScript((value) => {
    // Once per test, so a reload reads what the page itself last stored.
    if (!sessionStorage.getItem('pl-theme-seeded')) {
      localStorage.setItem('regin_pipeline_theme', value)
      sessionStorage.setItem('pl-theme-seeded', '1')
    }
  }, id)
}

async function pickTheme(page, label) {
  await page.getByTestId('pipeline-theme-switch').click()
  await page.getByRole('option', { name: label }).click()
}

async function create(request, path, data) {
  const { token } = await (await request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  const res = await request.post(`${API_BASE}${path}`, { headers: { Authorization: `Bearer ${token}` }, data })
  expect(res.ok()).toBeTruthy()
  return res.json()
}

test.describe('theme seam', () => {
  test.skip(RECORD, 'recording')

  test('test_unknown_stored_value', async ({ page }) => {
    await storeTheme(page, 'neon')
    await page.goto('/overview')
    await expect(page.getByTestId('overview')).toBeVisible()
    await expect.poll(() => plTheme(page)).toBe('pixel')
    await expect(page.getByTestId('pipeline-theme-switch')).toHaveText(/Pixel/)
  })

  test('test_switching_keeps_place', async ({ page, request }) => {
    const feature = await create(request, '/api/features', { title: `Seam switch ${Date.now()}` })
    const url = `/features?stage=spec&q=${encodeURIComponent(feature.title)}`
    // Realtime frames are broadcast to every open page, so a sibling worker's
    // write re-reads the lists mid-switch. With the ticket held, no stream opens;
    // useRealtime's own re-read is then the fallback poll, whose first tick
    // (four ticket timeouts plus backoff, then FALLBACK_POLL_MS) lands past the
    // 30s test timeout — so the switch is the only thing that could read.
    await page.route('**/api/auth/stream-ticket', () => {})
    await page.goto(url)
    const toggle = page.getByTestId(`feature-toggle-${feature.id}`)
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    const before = page.url()
    const dataReads = []
    page.on('request', (r) => {
      if (/\/api\/(features|tasks|defects|pipeline)\b/.test(r.url())) dataReads.push(r.url())
    })

    await pickTheme(page, 'Pixel')

    await expect.poll(() => plTheme(page)).toBe('pixel')
    expect(page.url()).toBe(before)
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    expect(dataReads).toEqual([])
    expect(await page.evaluate(() => localStorage.getItem('regin_pipeline_theme'))).toBe('pixel')

    await page.reload()
    await expect(page.getByTestId(`feature-toggle-${feature.id}`)).toBeVisible()
    await expect.poll(() => plTheme(page)).toBe('pixel')
  })

  test('test_theme_attribute_is_scoped_to_the_shell', async ({ page, request }) => {
    const { task } = await create(request, '/api/tasks', { title: `Seam scope ${Date.now()}`, priority: 'urgent' })
    const feature = await create(request, '/api/features', { title: `Seam scope ${Date.now()}` })
    await storeTheme(page, 'pixel')

    await page.goto(`/features?q=${encodeURIComponent(feature.title)}`)
    await expect.poll(() => plTheme(page)).toBe('pixel')
    await page.locator(`a[href="/features/${feature.id}"]`).first().click()
    await expect(page).toHaveURL(new RegExp(`/features/${feature.id}$`))
    await expect.poll(() => plTheme(page)).toBeNull()

    await page.getByTestId('pipeline-tab-tasks').click()
    await expect.poll(() => plTheme(page)).toBe('pixel')
    // The quest log folds a group past its first rows, so any other urgent idea
    // in the DB can push this one out of sight.
    await page.getByTestId('list-search').fill(task.title)
    await page.getByTestId('list-search').press('Enter')
    await page.locator(`a[href="/tasks/${task.id}"]`).first().click()
    await expect(page).toHaveURL(new RegExp(`/tasks/${task.id}$`))
    await expect.poll(() => plTheme(page)).toBeNull()
  })

  test('test_a_theme_chunk_that_fails_to_load', async ({ page }) => {
    await page.route(PIXEL_BYTES, r => r.abort())
    await storeTheme(page, 'pixel')
    await page.goto('/overview')
    await expect(page.getByTestId('overview')).toBeVisible()
    await expect.poll(() => plTheme(page)).toBe('classic')
    await expect(page.getByTestId('flash')).toContainText('Pixel theme failed to load — showing Classic')
    expect(await page.evaluate(() => localStorage.getItem('regin_pipeline_theme'))).toBe('pixel')
    // The switch names what is on screen, not the preference that failed.
    await expect(page.getByTestId('pipeline-theme-switch')).toHaveText(/Classic/)
  })
})

test.describe('no stored theme', () => {
  test.skip(RECORD, 'recording')
  test.use({ pipelineTheme: null })

  test('test_default_is_pixel', async ({ page }) => {
    await page.goto('/overview')
    await expect(page.getByTestId('overview')).toBeVisible()
    await expect.poll(() => plTheme(page)).toBe('pixel')
    await expect(page.getByTestId('pipeline-theme-switch')).toHaveText(/Pixel/)
    expect(await page.evaluate(() => localStorage.getItem('regin_pipeline_theme'))).toBeNull()
  })
})

test.describe('the pixel project stores the pixel theme', () => {
  test.skip(RECORD, 'recording')
  test.use({ pipelineTheme: 'pixel' })

  // The behaviour suites themselves run under the `pixel` Playwright project
  // (playwright.config.js); this pins that its option actually lands.
  test('test_behaviour_is_theme_independent', async ({ page }) => {
    await page.goto('/overview')
    await expect(page.getByTestId('overview')).toBeVisible()
    await expect.poll(() => plTheme(page)).toBe('pixel')
  })
})
