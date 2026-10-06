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
import { readFileSync } from 'node:fs'
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
  test(`test_classic_is_unchanged at ${vp.name}`, async ({ page }) => {
    test.skip(RECORD, 'recording')
    await page.routeFromHAR(HAR, { url: '**/api/**', notFound: 'abort' })
    await page.clock.setFixedTime(recordedAt())
    await visitAll(page, vp, async (path) => {
      await expect(page).toHaveScreenshot(`classic${path.replace('/', '-')}-${vp.name}.png`, {
        fullPage: true,
        animations: 'disabled',
        // The switch is new chrome in space that was empty before the seam.
        style: '[data-testid="pipeline-theme-switch"] { visibility: hidden !important; }',
      })
    })
  })
}
