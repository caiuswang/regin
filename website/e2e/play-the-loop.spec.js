import { test, expect } from '@playwright/test'

// Level 0's timers run on Playwright's fake clock: a stage's animation is
// advanced explicitly with `settle`, so "busy" states are observable without racing.
const LONGEST_TIMER_MS = 8 * 650 + 100

const play = (page) => page.locator('#play')
const actKey = (page) => play(page).locator('.play-act')
const hud = (page) => play(page).locator('.play-hud')
const trackKeys = (page) => play(page).getByRole('list', { name: 'Stages' }).getByRole('button')
const kicker = (page) => play(page).locator('.play-kicker')
const settle = (page) => page.clock.runFor(LONGEST_TIMER_MS)

async function press(page, label) {
  await expect(actKey(page)).toHaveText(label)
  await actKey(page).click()
  await settle(page)
}

async function clearFeature(page) {
  await press(page, '+ NEW FEATURE')
  await press(page, 'WRITE SPEC')
}

async function reachDefects(page) {
  await clearFeature(page)
  await press(page, 'NEXT: TASKS ▶')
  await press(page, 'TRANSCRIBE SLICES')
  await press(page, 'NEXT: GRAPH ▶')
  await press(page, '▶ RUN READY TASKS')
  await press(page, 'NEXT: DEFECTS ▶')
}

test.beforeEach(async ({ page }) => {
  await page.clock.install()
  await page.goto('/')
  await play(page).scrollIntoViewIfNeeded()
})

test('playing the loop end to end', async ({ page }) => {
  await expect(hud(page)).toContainText('STAGE 1/5')
  for (const i of [1, 2, 3, 4]) await expect(trackKeys(page).nth(i)).toBeDisabled()
  await expect(trackKeys(page).nth(0)).toHaveAttribute('aria-current', 'step')

  const passed = []
  for (let guard = 0; guard < 20; guard += 1) {
    const label = (await actKey(page).textContent()).trim()
    if (label === '↺ PLAY AGAIN') break
    const k = (await kicker(page).textContent()).trim()
    if (passed.at(-1) !== k) passed.push(k)
    await actKey(page).click()
    await settle(page)
    if (label === '▶ RUN READY TASKS') await expect(hud(page)).toContainText('TASKS DONE 7/7')
    if (label === 'FIX NEXT DEFECT' && k === 'STAGE 4 · COUNT') {
      const open = await play(page).locator('.play-defect:not(.is-fixed)').count()
      if (open === 0) await expect(hud(page)).toContainText('OPEN DEFECTS 0')
    }
  }

  expect(passed).toEqual(['STAGE 1 · SPEC', 'STAGE 2 · SLICE', 'STAGE 3 · RUN', 'STAGE 4 · COUNT', 'STAGE 5 · SHIP'])
  await expect(actKey(page)).toHaveText('↺ PLAY AGAIN')
  await expect(hud(page)).toContainText('OPEN DEFECTS 0')
  await expect(play(page).locator('.play-clear')).toContainText('★ LEVEL CLEAR')
  await expect(play(page).locator('.play-clear')).toContainText('ROUNDS 1 · ESCAPES 0 · NEVER WALKED BACK')
  await expect(play(page).locator('.play-ledger-line')).toHaveCount(8)
  for (const i of [0, 1, 2, 3, 4]) await expect(trackKeys(page).nth(i)).toContainText('★ 0-')
})

test('the first stage types the title, then writes the spec', async ({ page }) => {
  await actKey(page).click()
  await expect(actKey(page)).toHaveText('· · ·')
  await page.clock.runFor(70 * 5)
  await expect(play(page).locator('.play-field-text')).toHaveText('重新设计P')
  await settle(page)
  await expect(play(page).locator('.play-field-text')).toHaveText('重新设计Pipeline操作界面-游戏像素风格')
  await expect(play(page).locator('.play-spec').first()).not.toHaveClass(/is-specced/)
  await press(page, 'WRITE SPEC')
  await expect(play(page).locator('.play-spec').first()).toHaveClass(/is-specced/)
  await expect(actKey(page)).toHaveText('NEXT: TASKS ▶')
})

test('clicking a bug fixes that defect', async ({ page }) => {
  await reachDefects(page)
  await expect(hud(page)).toContainText('OPEN DEFECTS 3')
  const defects = play(page).locator('.play-defect')
  await expect(defects).toHaveCount(3)

  await defects.nth(1).getByRole('button', { name: /^Fix defect:/ }).click()

  await expect(defects.nth(1)).toContainText('CLOSED · CHECK NAMED')
  await expect(defects.nth(1).getByRole('button')).toBeDisabled()
  await expect(hud(page)).toContainText('OPEN DEFECTS 2')
  await expect(defects.nth(0)).toContainText('OPEN · CLICK THE BUG')
  await expect(defects.nth(2)).toContainText('OPEN · CLICK THE BUG')

  await press(page, 'FIX NEXT DEFECT')
  await expect(defects.nth(0)).toContainText('CLOSED · CHECK NAMED')
  await expect(defects.nth(2)).toContainText('OPEN · CLICK THE BUG')
})

test('the track cannot skip ahead or interrupt', async ({ page }) => {
  await clearFeature(page)
  await press(page, 'NEXT: TASKS ▶')
  await expect(hud(page)).toContainText('STAGE 2/5')

  await actKey(page).click()
  await expect(actKey(page)).toHaveText('· · ·')
  await expect(actKey(page)).toBeDisabled()
  await trackKeys(page).nth(0).click()
  await expect(hud(page)).toContainText('STAGE 2/5')
  await expect(kicker(page)).toHaveText('STAGE 2 · SLICE')
  await expect(trackKeys(page).nth(2)).toBeDisabled()

  await settle(page)
  await expect(play(page).locator('.play-task.is-shown')).toHaveCount(9)
  await trackKeys(page).nth(0).click()
  await expect(hud(page)).toContainText('STAGE 1/5')
})

test('play again resets every stage', async ({ page }) => {
  await reachDefects(page)
  await press(page, 'FIX NEXT DEFECT')
  await press(page, 'FIX NEXT DEFECT')
  await press(page, 'FIX NEXT DEFECT')
  await press(page, 'NEXT: SHIP ▶')
  await press(page, 'SHIP #139')
  await press(page, '↺ PLAY AGAIN')

  await expect(hud(page)).toContainText('STAGE 1/5')
  await expect(hud(page)).toContainText('TASKS DONE 0/7')
  await expect(hud(page)).toContainText('OPEN DEFECTS 0')
  for (const i of [1, 2, 3, 4]) await expect(trackKeys(page).nth(i)).toBeDisabled()
  await expect(play(page).locator('.play-field-text')).toHaveText('')
  await expect(actKey(page)).toHaveText('+ NEW FEATURE')
})

test('the hero robot walks when motion is allowed', async ({ page }) => {
  const scene = page.getByRole('img', { name: 'A pixel robot walks the loop: spec, run, watch, then counts a defect.' })
  await expect(scene).toBeVisible()
  await expect(scene.locator('.rg-robot')).toHaveCSS('animation-name', 'rg-walk')
  expect(await page.locator('.hero-scene [data-rg-anim]').evaluateAll(
    (els) => els.every((el) => el.getAnimations().length > 0),
  )).toBe(true)
})
