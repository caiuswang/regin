/**
 * Feature #139, slice S6 — the secret stage (R12): a triple click on the ALL
 * stage tab, read off the click's own `detail`, opens a pause menu whose HIGH
 * SCORE is the loop payload's settled totals for the shell's scope.
 */
import { test, expect } from './auth-fixture.js'

const LOOP = {
  stages: [
    { label: 'Tasks', total: 12, parts: [
      { key: 'idea', label: 'Ideas', n: 4 }, { key: 'done', label: 'Done', n: 5 },
      { key: 'failed', label: 'Failed', n: 3 }] },
    { label: 'Features', total: 6, parts: [
      { key: 'spec', label: 'Spec', n: 3 }, { key: 'shipped', label: 'Shipped', n: 3 }] },
    { label: 'Defects', total: 9, parts: [
      { key: 'open', label: 'Open', n: 2 }, { key: 'closed', label: 'Closed', n: 7 }] },
  ],
  fix_tasks: 0,
}

async function stubOverview(page) {
  const asked = []
  await page.route(/\/api\/pipeline\/overview(\?|$)/, (route) => {
    asked.push(new URL(route.request().url()).search)
    return route.fulfill({ json: {
      scope: 'all', loop: LOOP, running: { total: 0, rows: [] },
      run_next: { total: 0, ready: 0, blocked: 0, rows: [] }, needs_decision: { total: 0, rows: [] },
    } })
  })
  return asked
}

const plTheme = page => page.evaluate(() => document.documentElement.getAttribute('data-pl-theme'))

async function openFeatures(page, theme) {
  if (theme === 'pixel') await page.addInitScript(() => localStorage.setItem('regin_pipeline_theme', 'pixel'))
  await page.goto('/features')
  await expect(page.getByTestId('feature-stage-all')).toBeVisible()
  await expect.poll(() => plTheme(page)).toBe(theme)
}

// Animations running inside the secret stage's dialog — the open, the bot's
// run and hop, the coins.
const dialogAnimations = page => page.evaluate(() => {
  const dialog = document.querySelector('[data-testid="secret-stage"]')
  return document.getAnimations().filter(a => dialog?.contains(a.effect?.target)).length
})

test.describe('secret stage', () => {
  test('test_secret_stage', async ({ page }) => {
    const asked = await stubOverview(page)
    await openFeatures(page, 'pixel')

    await page.getByTestId('feature-stage-all').click({ clickCount: 3 })
    const dialog = page.getByRole('dialog', { name: 'Secret stage' })
    await expect(dialog).toBeVisible()
    await expect(page.getByTestId('secret-continue')).toBeFocused()
    await expect(dialog.getByRole('menuitem')).toHaveText(['Continue', 'High score', 'Credits'])
    expect(await dialogAnimations(page)).toBeGreaterThan(0)

    // ↑↓ walk the menu (and wrap), Enter picks.
    await page.keyboard.press('ArrowUp')
    await expect(page.getByTestId('secret-credits')).toBeFocused()
    await page.keyboard.press('ArrowDown')
    await page.keyboard.press('ArrowDown')
    await expect(page.getByTestId('secret-score')).toBeFocused()
    await page.keyboard.press('Enter')

    // The settled totals of the loop payload, labelled with the scope.
    const board = dialog.getByTestId('secret-board')
    await expect(board).toContainText('High score · All projects')
    await expect(page.getByTestId('secret-score-shipped')).toHaveText('3')
    await expect(page.getByTestId('secret-score-done')).toHaveText('5')
    await expect(page.getByTestId('secret-score-closed')).toHaveText('7')
    await expect(board).not.toContainText('week')
    expect(asked).toEqual([''])
    await expect(page.getByTestId('secret-back')).toBeFocused()
    await page.keyboard.press('Enter')
    await expect(page.getByTestId('secret-continue')).toBeFocused()

    await page.keyboard.press('Escape')
    await expect(dialog).toHaveCount(0)
    await expect(page.getByTestId('feature-stage-all')).toHaveAttribute('aria-selected', 'true')
    expect(new URL(page.url()).searchParams.get('stage')).toBeNull()

    // Two clicks are two clicks, not a stage.
    await page.getByTestId('feature-stage-all').click({ clickCount: 2 })
    await expect(page.getByRole('dialog')).toHaveCount(0)
  })

  test('test_secret_stage under reduced motion: the menu is there without frames', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' })
    await stubOverview(page)
    await openFeatures(page, 'pixel')
    await page.getByTestId('feature-stage-all').click({ clickCount: 3 })
    await expect(page.getByRole('dialog', { name: 'Secret stage' })).toBeVisible()
    await expect(page.getByTestId('secret-continue')).toBeFocused()
    expect(await dialogAnimations(page)).toBe(0)
  })

  test('test_secret_stage is not in the classic theme', async ({ page }) => {
    const asked = await stubOverview(page)
    await openFeatures(page, 'classic')
    await page.getByTestId('feature-stage-all').click({ clickCount: 3 })
    await expect(page.getByTestId('feature-stage-all')).toHaveAttribute('aria-selected', 'true')
    await expect(page.getByRole('dialog')).toHaveCount(0)
    expect(asked).toEqual([])
  })
})
