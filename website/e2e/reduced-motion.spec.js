import { test, expect } from '@playwright/test'

// G9. The fake clock is installed and never advanced: anything that still shows
// up did so without a timer firing.
const play = (page) => page.locator('#play')
const actKey = (page) => play(page).locator('.play-act')

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' })
  await page.clock.install()
  await page.goto('/')
})

test('the hero robot has no running animation', async ({ page }) => {
  const animated = page.locator('.hero-scene [data-rg-anim]')
  await expect(animated.first()).toBeAttached()
  expect(await animated.evaluateAll((els) => els.map((el) => [
    getComputedStyle(el).animationName, el.getAnimations().length,
  ]))).toEqual(Array.from({ length: await animated.count() }, () => ['none', 0]))
  const scene = await page.locator('.hero-scene').boundingBox()
  const robot = await page.locator('.rg-robot').boundingBox()
  expect(robot.x).toBeGreaterThanOrEqual(scene.x)
})

test('Level 0 timers complete instantly', async ({ page }) => {
  await actKey(page).click()
  await expect(play(page).locator('.play-field-text')).toHaveText('重新设计Pipeline操作界面-游戏像素风格')
  await expect(actKey(page)).toHaveText('WRITE SPEC')
  await actKey(page).click()
  await actKey(page).click()
  await expect(actKey(page)).toHaveText('TRANSCRIBE SLICES')

  await actKey(page).click()

  await expect(play(page).locator('.play-task.is-shown')).toHaveCount(9)
  const fade = await play(page).locator('.play-task').last().evaluate((el) => getComputedStyle(el).transitionDuration)
  for (const d of fade.split(',')) expect(parseFloat(d)).toBeLessThan(0.001)
  await expect(actKey(page)).toHaveText('NEXT: GRAPH ▶')
  await expect(actKey(page)).toBeEnabled()
})

test('smooth scroll becomes instant', async ({ page }) => {
  await expect(page.locator('html')).toHaveCSS('scroll-behavior', 'auto')
})
