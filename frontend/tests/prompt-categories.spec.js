import { test, expect } from './auth-fixture.js'
import { TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'

async function apiRows(page, kind) {
  const login = await page.request.post(`${API_BASE}/api/auth/login`, { data: TEST_USER })
  const { token } = await login.json()
  const res = await page.request.get(`${API_BASE}/api/prompt-templates?kind=${kind}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  return (await res.json()).templates
}

function countByArea(rows) {
  const out = {}
  for (const r of rows) out[r.area] = (out[r.area] || 0) + 1
  return out
}

const bar = (page) => page.getByRole('group', { name: 'Filter by category' })
const chip = (page, area) => bar(page).locator(`[data-category="${area}"]`)
const cards = (page) => page.locator('.prompt-grid .prompt-card')

async function chipCount(locator) {
  const m = (await locator.innerText()).match(/\((\d+)\)\s*$/)
  return Number(m[1])
}

test('skeleton tab: one chip per area, counts match the API', async ({ page }) => {
  const rows = await apiRows(page, 'skeleton')
  const byArea = countByArea(rows)
  await page.goto('/prompt-templates')
  await expect(cards(page)).toHaveCount(rows.length)

  await expect(bar(page).locator('[data-category]:not([data-category="all"])'))
    .toHaveCount(Object.keys(byArea).length)
  let sum = 0
  for (const [area, n] of Object.entries(byArea)) {
    expect(await chipCount(chip(page, area))).toBe(n)
    sum += n
  }
  expect(await chipCount(chip(page, 'all'))).toBe(sum)
  expect(sum).toBe(rows.length)
})

test('filtering by a chip narrows the grid and survives reload', async ({ page }) => {
  const rows = await apiRows(page, 'skeleton')
  const memory = rows.filter((r) => r.area === 'memory').length
  await page.goto('/prompt-templates')

  await chip(page, 'memory').click()
  await expect(page).toHaveURL(/[?&]category=memory\b/)
  await expect(cards(page)).toHaveCount(memory)
  expect(await chipCount(chip(page, 'memory'))).toBe(memory)
  await expect(cards(page).locator('.card-eyebrow .badge:first-child'))
    .toHaveText(Array(memory).fill('Memory'))

  await page.reload()
  await expect(chip(page, 'memory')).toHaveAttribute('aria-pressed', 'true')
  await expect(cards(page)).toHaveCount(memory)

  await chip(page, 'all').click()
  await expect(cards(page)).toHaveCount(rows.length)
  await expect(page).not.toHaveURL(/category=/)
})

test('an unknown category reads as All', async ({ page }) => {
  const rows = await apiRows(page, 'skeleton')
  await page.goto('/prompt-templates?category=nope')
  await expect(chip(page, 'all')).toHaveAttribute('aria-pressed', 'true')
  await expect(cards(page)).toHaveCount(rows.length)
})

test('a surface deep link switches the category to reveal it', async ({ page }) => {
  await page.goto('/prompt-templates?category=memory&surface=grader-process')
  await expect(chip(page, 'grader')).toHaveAttribute('aria-pressed', 'true')
  await expect(page).toHaveURL(/[?&]category=grader\b/)
  const card = page.locator('#sk-grader-process')
  await expect(card.locator('.skeleton-editor')).toBeVisible()
  await expect(card).toBeInViewport()
})

test('fragments tab filters the table rows and tab switch resets the category', async ({ page }) => {
  const rows = await apiRows(page, 'fragment')
  const byArea = countByArea(rows)
  test.skip(Object.keys(byArea).length < 2, 'fragments span a single area in this DB')
  const [area, n] = Object.entries(byArea)[0]

  await page.goto('/prompt-templates?tab=fragments')
  await chip(page, area).click()
  await expect(page.locator('table.tbl tbody tr:not(:has(.empty-row))')).toHaveCount(n)
  await expect(page).toHaveURL(new RegExp(`category=${area}\\b`))

  await page.getByRole('tab', { name: 'Agent prompts' }).click()
  await expect(page).not.toHaveURL(/category=/)
  await expect(chip(page, 'all')).toHaveAttribute('aria-pressed', 'true')
})
