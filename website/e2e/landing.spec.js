import { test, expect } from '@playwright/test'

const QUICK_START = 'git clone https://github.com/caiuswang/regin\ncd regin && ./scripts/setup.sh'
const PLAY = 'Level 0 Play the loop'
const SECTIONS = [
  'Hero',
  PLAY,
  'Level 1-1 Workbench',
  'Level 1-2 Runs',
  'Level 1-3 Observability',
  'Level 1-4 Guidance',
  'Continue',
]

const LEVELS = [
  { id: 'workbench', h2: 'A feature is a row, and done is a claim.', link: 'HOW THE PIPELINE WORKS →', to: '/pipeline', cap: 'QUEST LOG · F139 SLICES AS A TASK GRAPH' },
  { id: 'runs', h2: 'Launch the run, then steer it.', link: 'HOW RUNS ARE LAUNCHED →', to: '/runs', cap: 'QUEST LOG · IDEAS → ARMED → RUNNING → DONE' },
  { id: 'observability', h2: 'A session viewer, not a scrollback.', link: 'HOW CAPTURE WORKS →', to: '/dashboard', cap: 'SESSION TRACE · REPLAY TURN BY TURN' },
  { id: 'guidance', h2: 'Rules the agent can\'t drift past.', link: 'PATTERNS, RULES AND MEMORY →', to: '/guidance', cap: '46 HOOK HANDLERS · GATE · TRACE · ENRICH' },
]

const copyKey = (page) => page.getByRole('button', { name: 'Copy quick-start commands' })

test('the landing renders its sections in order, footer last', async ({ page }) => {
  await page.goto('/')
  const labels = await page.locator('main [data-screen-label]').evaluateAll((els) => els.map((el) => el.dataset.screenLabel))
  // Level 0 is drawn by its own slice; every other section must be here, in
  // order, and Level 0 — once present — sits second.
  expect(labels).toEqual(SECTIONS.filter((s) => s !== PLAY || labels.includes(PLAY)))
  const lastIsFooter = await page.evaluate(() => {
    const footer = document.querySelector('footer.site-footer')
    const cont = document.querySelector('[data-screen-label="Continue"]')
    return !!footer && !!(cont.compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING)
  })
  expect(lastIsFooter).toBe(true)
  await expect(page.locator('footer.site-footer')).toContainText('REGIN · RUNS LOCALLY · NO HOSTED SERVICE')
  await expect(page.locator('footer.site-footer a')).toHaveText('GITHUB.COM/CAIUSWANG/REGIN')
})

test('the hero HUD, copy and CTAs are the prototype\'s', async ({ page }) => {
  await page.goto('/')
  const hero = page.locator('#top')
  const hud = hero.locator('.land-hud')
  await expect(hud).toContainText('WORLD 1 · REGIN BUILDS REGIN')
  for (const stat of ['FEATURES 109', 'TASKS 378', 'DEFECTS 139', 'SHIPPED 30%']) {
    await expect(hud.locator('.land-hud-stat', { hasText: stat })).toHaveCount(1)
  }
  await expect(hero.locator('.land-kicker')).toHaveText('▶ AI AGENT WORKBENCH')
  await expect(page.locator('h1')).toHaveText('Plan the feature, run the agents, see what they did, count what they got wrong.')
  await expect(hero.getByRole('link', { name: '▶ CLONE ON GITHUB' })).toHaveAttribute('href', 'https://github.com/caiuswang/regin')
  await expect(hero.getByRole('link', { name: 'Play the loop ↓' })).toHaveAttribute('href', '/#play')
  await expect(hero.locator('.quick-start-pre')).toHaveText('$ git clone https://github.com/caiuswang/regin\n$ cd regin && ./scripts/setup.sh')
  await expect(hero.locator('figcaption')).toHaveText('PIPELINE OVERVIEW · THE REPO THAT BUILDS REGIN, LIVE')
  await expect(hero.locator('figure img')).toHaveAttribute('src', /overview.*\.webp$/)
})

test('loop tiles name the four layers in order', async ({ page }) => {
  await page.goto('/')
  const tiles = page.getByRole('navigation', { name: 'The regin loop' }).getByRole('link')
  await expect(tiles).toHaveCount(4)
  await expect(tiles.locator('.loop-tile-name')).toHaveText(['Workbench', 'Runs', 'Observability', 'Guidance'])
  await expect(tiles.locator('.loop-tile-head .tone-green')).toHaveText(['1-1 SPEC', '1-2 RUN', '1-3 WATCH', '1-4 COUNT'])
  const hrefs = await tiles.evaluateAll((els) => els.map((el) => el.getAttribute('href')))
  expect(hrefs).toEqual(['/#workbench', '/#runs', '/#observability', '/#guidance'])
})

test('loop tiles jump to their level', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('link', { name: /1-3 WATCH/ }).click()
  await expect(page).toHaveURL(/#observability$/)
  await expect.poll(
    () => page.locator('#observability').evaluate((el) => Math.round(el.getBoundingClientRect().top)),
    { timeout: 5000 },
  ).toBe(76)
  const headerBottom = await page.locator('.site-header').evaluate((el) => el.getBoundingClientRect().bottom)
  expect(headerBottom).toBeLessThanOrEqual(76)
})

test('the four level sections carry their copy, links, shots and pips', async ({ page }) => {
  await page.goto('/')
  for (const [i, level] of LEVELS.entries()) {
    const section = page.locator(`#${level.id}`)
    await expect(section.locator('.land-hud')).toContainText(`LEVEL 1-${i + 1}`)
    await expect(section.locator('.level-pip.is-current')).toHaveCount(1)
    await expect(section.locator('.level-pip.is-passed')).toHaveCount(i)
    await expect(section.locator('h2')).toHaveText(level.h2)
    await expect(section.getByRole('link', { name: level.link })).toHaveAttribute('href', level.to)
    await expect(section.locator('figcaption')).toHaveText(level.cap)
    await expect(section.locator('figure img')).toHaveAttribute('src', /\.webp$/)
  }
  await expect(page.locator('#workbench .land-hud')).toContainText('F139 ROUNDS 1')
  await expect(page.locator('#workbench .land-hud')).toContainText('COST $140.43')
  await expect(page.locator('#workbench pre')).toContainText('shipped  rounds=1  open_defects=0  escapes=0')
  await expect(page.locator('#runs .land-hud')).toContainText('AGENTS CLAUDE · KIMI')
  await expect(page.locator('#runs pre .tone-gold')).toHaveText(['armed  ', 'armed  '])
  await expect(page.locator('#observability .land-hud')).toContainText('TRACE 3B3ADBBC · TURN 8')
  await expect(page.locator('#observability .level-tile-value')).toHaveText(['552', '16', '$5.59 OF $55.14'])
  await expect(page.locator('#guidance pre')).toContainText('use the <Button> primitive, not a raw <button>')
})

test('the cost meter shows the tool-attributed share', async ({ page }) => {
  await page.goto('/')
  const share = await page.locator('#observability .level-meter').evaluate((meter) => {
    const part = meter.querySelector('.level-meter-part').getBoundingClientRect().width
    return part / meter.getBoundingClientRect().width
  })
  expect(share).toBeGreaterThan(0.08)
  expect(share).toBeLessThan(0.12)
})

test('the Continue box closes World 1', async ({ page }) => {
  await page.goto('/')
  const box = page.locator('[data-screen-label="Continue"]')
  await expect(box.locator('.land-hud')).toContainText('WORLD 1 CLEAR')
  await expect(box.locator('.land-hud')).toContainText('CONTINUE? ▶ YES')
  await expect(box.locator('h2')).toHaveText('Still here? Then it\'s probably time to run it.')
  await expect(box.getByRole('link', { name: '▶ CLONE ON GITHUB' })).toHaveAttribute('href', 'https://github.com/caiuswang/regin')
  await expect(box.getByRole('link', { name: 'Read the guide first →' })).toHaveAttribute('href', '/getting-started')
  await expect(box.locator('h3')).toHaveText(['WHAT REGIN IS NOT', 'SUPPORTED AGENTS'])
  await expect(box).toHaveCSS('border-top-color', 'rgb(43, 222, 94)')
})

test('copy quick start', async ({ page, context }) => {
  await context.grantPermissions(['clipboard-read', 'clipboard-write'])
  await page.goto('/')
  await expect(copyKey(page)).toHaveText('COPY')
  await copyKey(page).click()
  await expect(copyKey(page)).toHaveText('COPIED ✓')
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(QUICK_START)
  await page.waitForTimeout(1500)
  await expect(copyKey(page)).toHaveText('COPIED ✓')
  await expect(copyKey(page)).toHaveText('COPY', { timeout: 2000 })
})

test('copy without clipboard permission', async ({ page }) => {
  await page.addInitScript(() => {
    navigator.clipboard.writeText = () => Promise.reject(new DOMException('denied', 'NotAllowedError'))
  })
  await page.goto('/')
  await copyKey(page).click()
  await expect(copyKey(page)).toHaveText('COPY FAILED')
  await expect(copyKey(page)).toHaveText('COPY', { timeout: 4000 })
})

test('copy without a Clipboard API', async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(Navigator.prototype, 'clipboard', { get: () => undefined, configurable: true })
  })
  await page.goto('/')
  await copyKey(page).click()
  await expect(copyKey(page)).toHaveText('COPY FAILED')
  await expect(copyKey(page)).toHaveText('COPY', { timeout: 4000 })
})

test('the landing does not scroll sideways at 1440 px', async ({ page }) => {
  await page.goto('/')
  const fits = await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)
  expect(fits).toBe(true)
})
