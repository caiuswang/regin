import { test, expect } from '@playwright/test'

import { ALL_ROUTES } from './routes.js'

const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]'])

function leavesLocalhost(url) {
  const { protocol, hostname } = new URL(url)
  if (protocol === 'data:' || protocol === 'blob:' || protocol === 'about:') return false
  return !LOCAL_HOSTS.has(hostname)
}

for (const route of ALL_ROUTES) {
  test(`${route} makes no third-party request`, async ({ page }) => {
    const requested = []
    const external = []
    page.on('request', (req) => {
      requested.push(req.url())
      if (leavesLocalhost(req.url())) external.push(req.url())
    })

    await page.goto(route, { waitUntil: 'networkidle' })
    // A lazy image below the fold is never requested at 1440×900; force every
    // image to load so a third-party src further down the page is caught too.
    await page.evaluate(() => {
      for (const img of document.images) img.loading = 'eager'
    })
    await page.waitForFunction(() => [...document.images].every((img) => img.complete))
    await page.evaluate(() => document.fonts.ready)

    expect(requested.length, 'the page loaded something').toBeGreaterThan(0)
    expect(external).toEqual([])
  })
}
