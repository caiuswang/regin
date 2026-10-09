import { test, expect } from '@playwright/test'

import { DOCS_ROUTES, NOT_FOUND_ROUTE } from './routes.js'

const worldMap = (page) => page.getByRole('complementary', { name: 'World map' })
const current = (page) => worldMap(page).locator('[aria-current="page"]')
const cardHud = (page) => page.locator('.doc-card-label')
const prevNext = (page) => page.getByRole('navigation', { name: 'Previous and next' })

const CURRENT_PAGE = [
  { path: '/getting-started', code: '0-0', label: 'LEVEL 0-0 · GETTING STARTED', title: 'Getting Started' },
  { path: '/pipeline', code: '1-1', label: 'LEVEL 1-1 · WORKBENCH · SPEC', title: 'Pipeline' },
  { path: '/cli', code: 'R-3', label: 'REF R-3 · CLI REFERENCE', title: 'CLI Reference' },
  { path: '/topics/routing', code: 'R-4.1', label: 'REF R-4.1 · TOPIC ROUTING', title: 'Routing' },
]

for (const row of CURRENT_PAGE) {
  test(`world map marks ${row.path} as current`, async ({ page }) => {
    await page.goto(row.path)
    await expect(current(page)).toHaveCount(1)
    await expect(current(page).locator('.world-map-code')).toHaveText(row.code)
    await expect(cardHud(page)).toHaveText(row.label)
    await expect(page.locator('h1')).toHaveText(row.title)
    await expect(page).toHaveTitle(`${row.title} · regin`)
  })
}

test('every docs route has exactly one current entry; the landing and 404 have none', async ({ page }) => {
  for (const path of DOCS_ROUTES) {
    await page.goto(path)
    await expect(current(page), path).toHaveCount(1)
    await expect(page.locator('.doc-card-label'), path).not.toContainText('GAME OVER')
  }
  await page.goto('/')
  await expect(page.locator('[aria-current="page"]').filter({ has: page.locator('.world-map-code') })).toHaveCount(0)
  await page.goto(NOT_FOUND_ROUTE)
  await expect(current(page)).toHaveCount(0)
})

test('topic sub-pages appear only inside Topics', async ({ page }) => {
  await page.goto('/runs')
  await expect(worldMap(page).locator('a')).toHaveCount(9)
  await page.goto('/topics')
  const entries = worldMap(page).locator('a')
  await expect(entries).toHaveCount(13)
  const codes = await entries.locator('.world-map-code').allTextContents()
  expect(codes.slice(8)).toEqual(['R-4', 'R-4.1', 'R-4.2', 'R-4.3', 'R-4.4'])
  const parentLeft = await entries.nth(8).locator('.world-map-code').evaluate((el) => el.getBoundingClientRect().left)
  for (let i = 9; i < 13; i++) {
    const left = await entries.nth(i).locator('.world-map-code').evaluate((el) => el.getBoundingClientRect().left)
    expect(left - parentLeft, `entry ${codes[i]} is indented 12px`).toBe(12)
  }
})

test('STAGE SELECT counts and jumps', async ({ page }) => {
  await page.goto('/pipeline')
  await expect(page.locator('.doc-card-status')).toHaveText('STAGES 9 · READY')
  const chips = page.locator('.stage-chip')
  await expect(chips).toHaveCount(9)
  const chip = chips.filter({ hasText: '06Defects' })
  await expect(chip).toHaveText('06Defects')
  await chip.click()
  const defects = page.locator('#defects')
  await expect.poll(() => defects.evaluate((el) => Math.round(el.getBoundingClientRect().top))).toBe(76)
})

test('PREV and NEXT walk the world map', async ({ page }) => {
  await page.goto('/getting-started')
  await expect(prevNext(page).locator('.is-prev')).toHaveCount(0)
  await expect(prevNext(page).locator('.is-next')).toHaveText(/NEXT · 1-1 ▶\s*Pipeline/)

  await page.goto('/topics/memory')
  await expect(prevNext(page).locator('.is-prev .prev-next-kicker')).toHaveText('◀ PREV · R-4.3')
  await expect(prevNext(page).locator('.is-next')).toHaveCount(0)

  await page.goto('/topics')
  await expect(prevNext(page).locator('.is-next')).toHaveText(/NEXT · R-4.1 ▶\s*Routing/)
  await prevNext(page).locator('.is-next').click()
  await expect(page).toHaveURL(/\/topics\/routing$/)
})

test('404', async ({ page }) => {
  await page.goto(NOT_FOUND_ROUTE)
  await expect(cardHud(page)).toHaveText('GAME OVER · 404')
  await expect(page.locator('h1')).toHaveText('Page not found')
  await expect(page.locator('.doc-card-lead')).toContainText(NOT_FOUND_ROUTE)
  await expect(page).toHaveTitle('Page not found · regin')
  await expect(current(page)).toHaveCount(0)
  await page.getByRole('link', { name: '▶ CONTINUE' }).click()
  await expect(page).toHaveURL(/\/$/)
})

test('header variants', async ({ page }) => {
  await page.goto('/')
  const header = page.locator('.site-header')
  await expect(header.locator('.brand-badge')).toHaveText('BETA')
  await expect(header.getByRole('link', { name: 'Guide' })).toHaveAttribute('href', '/getting-started')
  await expect(page.locator('.site-footer')).toContainText('REGIN · RUNS LOCALLY · NO HOSTED SERVICE')

  await page.goto('/runs')
  await expect(header.locator('.brand-badge')).toHaveText('DOCS')
  await expect(header.getByRole('link', { name: 'Home' })).toHaveAttribute('href', '/')
  await expect(header.getByRole('link', { name: 'GITHUB' })).toHaveAttribute('href', 'https://github.com/caiuswang/regin')
  const box = await header.boundingBox()
  expect(box.height).toBe(58)
})

test('links and buttons show the green focus ring', async ({ page }) => {
  await page.goto('/pipeline')
  await page.locator('.stage-chip').first().focus()
  await page.keyboard.press('Tab')
  const ring = await page.evaluate(() => {
    const s = getComputedStyle(document.activeElement)
    return `${s.outlineStyle} ${s.outlineWidth} ${s.outlineColor} ${s.outlineOffset}`
  })
  expect(ring).toBe('solid 2px rgb(43, 222, 94) 2px')
})

test('dark only: no theme toggle, no data-theme', async ({ page }) => {
  await page.goto('/')
  expect(await page.evaluate(() => document.documentElement.hasAttribute('data-theme'))).toBe(false)
  await expect(page.getByRole('button', { name: /theme/i })).toHaveCount(0)
})

test('a hash deep link lands its section 76px below the viewport top', async ({ page }) => {
  await page.goto('/pipeline#defects')
  const defects = page.locator('#defects')
  await expect.poll(() => defects.evaluate((el) => Math.round(el.getBoundingClientRect().top))).toBe(76)
})
