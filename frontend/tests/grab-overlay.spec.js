import { test, expect } from './auth-fixture.js'

// The overlay is dev-only (main.js gates it behind `import.meta.env.DEV`), and
// this suite runs against `npx vite` — so it is installed here but would be
// absent from a spec pointed at the built bundle.
test.use({ permissions: ['clipboard-read', 'clipboard-write'] })

async function centreOf(locator) {
  await expect(locator).toBeVisible()
  const box = await locator.boundingBox()
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 }
}

test.describe('grab overlay (Ctrl+Shift+G)', () => {
  test('copies the element under a pointer that never moved after arming', async ({ page }) => {
    await page.goto('/')
    const { x, y } = await centreOf(page.locator('.sidebar a.sb-brand-name'))

    await page.evaluate(() => navigator.clipboard.writeText('__cleared__'))
    // Park the pointer BEFORE arming and never move it again: the chord is a
    // keystroke, so the natural gesture produces no mousemove for the overlay
    // to learn its target from.
    await page.mouse.move(x, y)
    await page.keyboard.press('Control+Shift+KeyG')
    await page.mouse.down()
    await page.mouse.up()

    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toContain('Component stack')
  })

  test('an armed click never reaches the page underneath', async ({ page }) => {
    await page.goto('/patterns')
    const { x, y } = await centreOf(page.locator('.sidebar a.sb-brand-name'))

    await page.mouse.move(x, y)
    await page.keyboard.press('Control+Shift+KeyG')
    await page.mouse.down()
    await page.mouse.up()

    await expect
      .poll(() => page.evaluate(() => navigator.clipboard.readText()))
      .toContain('Component stack')
    expect(new URL(page.url()).pathname).toBe('/patterns')
  })

  test('Escape disarms without copying', async ({ page }) => {
    await page.goto('/')
    const { x, y } = await centreOf(page.locator('.sidebar a.sb-brand-name'))

    await page.evaluate(() => navigator.clipboard.writeText('__cleared__'))
    await page.mouse.move(x, y)
    await page.keyboard.press('Control+Shift+KeyG')
    await page.keyboard.press('Escape')
    await page.mouse.down()
    await page.mouse.up()

    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('__cleared__')
  })
})
