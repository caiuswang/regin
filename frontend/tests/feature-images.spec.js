/**
 * The images a feature carries, on the ledger page.
 *
 * Stubbed rather than seeded, for the reason `feature-ledger.spec.js` states:
 * every assertion here is about the strip surviving a render, and binding it
 * to live data would make a rendering regression look like an empty database.
 */
import { test, expect } from './auth-fixture.js'

const PNG_B64 =
  'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmM' +
  'IQAAAABJRU5ErkJggg=='

function ledger(images) {
  return {
    id: 242, title: 'A feature with a mockup', origin: 'native',
    external_ref: null, external_url: null, sync_error: null, body: 'thin prose',
    stage: {
      stage: 'building', blurb: 'being built', rounds: 1,
      claimed_at: null, open_defects: 0, escapes: 0,
    },
    synced_at: null, stage_live: true, images,
    cost: { usd: 1, sessions: 1, recalled: 0, shared: 0, gaps: {} },
    work_time: { active_work_ms: 1000, gaps: {} },
    tokens: { input: 1, output: 1 },
    sessions: { rows: [], gaps: { traceless_runs: 0 } },
    defects: { open: 0, total: 0, escapes: 0 },
    coverage: {
      declared_sessions: 1, all_sessions: 2,
      declared_usd: 1, all_usd: 2,
    },
  }
}

function summary(idx) {
  return { id: idx, idx, media_type: 'image/png', byte_size: 68 }
}

async function stub(page, images) {
  await page.route(/\/api\/features\/242\/images\/\d+$/, (route) => {
    if (route.request().method() === 'DELETE') {
      return route.fulfill({ json: { removed: true } })
    }
    return route.fulfill({
      contentType: 'image/png', body: Buffer.from(PNG_B64, 'base64'),
    })
  })
  await page.route(/\/api\/features\/242\/images$/, (route) =>
    route.fulfill({ status: 201, json: { image: summary(3) } }))
  await page.route(/\/api\/features\/242$/, (route) =>
    route.fulfill({ json: ledger(images) }))
  await page.route(/\/api\/features\/242\/(tasks|defects)/, (route) =>
    route.fulfill({ json: { items: [] } }))
}

test('the feature renders every image it carries', async ({ page }) => {
  await stub(page, [summary(1), summary(2)])
  await page.goto('/features/242')

  const card = page.getByTestId('feature-images')
  await expect(card).toContainText('delivered with every run cut from this feature')
  await expect(card.locator('.task-image-thumb')).toHaveCount(2)
})

test('a feature with no images says what the absence costs', async ({ page }) => {
  await stub(page, [])
  await page.goto('/features/242')

  await expect(page.getByTestId('feature-images')
    .locator('.task-image-thumb')).toHaveCount(0)
  await expect(page.getByTestId('feature-images'))
    .toContainText('leaves the agent nothing to match')
})

test('attaching one posts it to the feature and shows it at once',
  async ({ page }) => {
    await stub(page, [summary(1)])
    await page.goto('/features/242')

    const card = page.getByTestId('feature-images')
    const posted = page.waitForRequest((req) =>
      req.url().endsWith('/api/features/242/images') && req.method() === 'POST')

    await card.getByTestId('task-image-file').setInputFiles({
      name: 'mockup.png', mimeType: 'image/png',
      buffer: Buffer.from(PNG_B64, 'base64'),
    })

    await posted
    await expect(card.locator('.task-image-thumb')).toHaveCount(2)
  })

test('removing one is immediate, and hits the feature route', async ({ page }) => {
  await stub(page, [summary(1), summary(2)])
  await page.goto('/features/242')

  const card = page.getByTestId('feature-images')
  await expect(card.locator('.task-image-thumb')).toHaveCount(2)
  const deleted = page.waitForRequest((req) =>
    /\/api\/features\/242\/images\/1$/.test(req.url())
      && req.method() === 'DELETE')

  await card.locator('.task-image-x').first().click()

  await deleted
  await expect(card.locator('.task-image-thumb')).toHaveCount(1)
})
