import { test, expect } from '@playwright/test'

test('the quest logs feature events as the robot walks', async ({ page }) => {
  await page.goto('/pipeline')
  await page.locator('.quest').scrollIntoViewIfNeeded()
  await expect(page.locator('.quest-log li').first()).toContainText('specced')
  await expect(page.locator('.quest-log li').nth(1)).toContainText('build_started', { timeout: 20000 })
})

test('dragging a level moves it; a click without a drag reads it', async ({ page }) => {
  await page.goto('/pipeline')
  const level = page.locator('.quest-level', { hasText: 'Transcribe' })
  await level.scrollIntoViewIfNeeded()
  const before = await level.boundingBox()
  await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2)
  await page.mouse.down()
  await page.mouse.move(before.x + before.width / 2, before.y + before.height / 2 + 150, { steps: 10 })
  await page.mouse.up()
  const after = await level.boundingBox()
  expect(after.y - before.y).toBeGreaterThan(100)
  await expect(page.locator('.quest-kicker').first()).not.toContainText('Transcribe')

  await page.locator('.quest-level', { hasText: 'Ship' }).click()
  await expect(page.locator('.quest-kicker').first()).toContainText('1-7 · Ship')
  await expect(page.locator('.quest-line')).toContainText('done-claim')
})

test('arrow keys move a focused level by one tile', async ({ page }) => {
  await page.goto('/pipeline')
  const level = page.locator('.quest-level', { hasText: 'Spec' })
  await level.scrollIntoViewIfNeeded()
  const before = await level.boundingBox()
  await level.focus()
  await page.keyboard.press('ArrowRight')
  await page.keyboard.press('ArrowRight')
  const after = await level.boundingBox()
  expect(after.x).toBeGreaterThan(before.x)
})

test('under reduced motion the quest waits for Play', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/pipeline')
  await page.locator('.quest').scrollIntoViewIfNeeded()
  await expect(page.locator('.quest-controls .key-primary')).toHaveText('▶ PLAY')
  await expect(page.locator('.quest-log li')).toHaveText(['#1 specced'])
})
