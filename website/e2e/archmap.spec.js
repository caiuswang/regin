import { test, expect } from '@playwright/test'

test('How it works shows the layered diagram, not ASCII', async ({ page }) => {
  await page.goto('/architecture')
  const section = page.locator('#s-how-it-works')
  await expect(section.locator('.arch-band-label')).toHaveCount(4)
  await expect(section.locator('.arch-system .arch-agent, .arch-system .arch-serve')).toHaveCount(2)
  await expect(section.locator('.arch-part')).toHaveCount(15)
  await expect(section.locator('pre')).toHaveCount(0)
})

test('a flow lights its path in order and dims the rest', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.goto('/architecture')
  await page.locator('.arch-flow', { hasText: 'Guard an edit' }).click()
  await expect(page.locator('.arch-steps li')).toHaveCount(4)
  await expect(page.locator('.arch-part.is-lit')).toHaveCount(3)
  await expect(page.locator('.arch-part', { hasText: 'Your agent' }).locator('.arch-badges b')).toHaveText(['1', '4'])
  await expect(page.locator('.arch-part', { hasText: 'Rule engines' }).locator('.arch-badges b')).toHaveText(['3'])
  await page.locator('.arch-clear').click()
  await expect(page.locator('.arch-part.is-lit')).toHaveCount(0)
})

test('steps reveal one at a time when motion is allowed', async ({ page }) => {
  await page.goto('/architecture')
  await page.locator('.arch-flow', { hasText: 'Launch a task' }).click()
  await expect(page.locator('.arch-steps li')).toHaveCount(1)
  await expect(page.locator('.arch-steps li')).toHaveCount(4, { timeout: 10000 })
})

test('clicking a part reads it', async ({ page }) => {
  await page.goto('/architecture')
  await page.locator('.arch-part', { hasText: 'Mods' }).click()
  await expect(page.locator('.arch-panel-kicker')).toHaveText('Mods')
  await expect(page.locator('.arch-panel-text')).toContainText('subagent lineage')
})
