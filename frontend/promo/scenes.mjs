// One function per non-card storyboard scene, keyed by scene id. A scene runs
// its actions inside its lines (`ctx.line(i, during)`), so its length comes
// from the narration and from awaited selectors, never from a fixed wait.

const tid = (id) => `[data-testid="${id}"]`

// Resolves once no scroll container has moved for a run of animation frames:
// a smooth scroll (ours or the app's own section jump) has no promise to await.
async function settle(page) {
  await page.evaluate(() => new Promise((done) => {
    const boxes = [...document.querySelectorAll('*')].filter((el) => el.scrollHeight > el.clientHeight)
    const pos = () => boxes.map((el) => el.scrollTop).join(',') + `|${scrollY}`
    let last = pos(), still = 0
    const tick = () => {
      const now = pos()
      still = now === last ? still + 1 : 0
      last = now
      if (still >= 8) done(); else requestAnimationFrame(tick)
    }
    requestAnimationFrame(tick)
  }))
}

// Re-aims until the target is on screen: content still rendering above it can
// push it back down after a scroll has already come to rest.
async function glideTo(page, selector, block = 'center') {
  const target = page.locator(selector).first()
  await target.waitFor({ state: 'attached' })
  for (let aim = 0; aim < 6; aim += 1) {
    await target.evaluate((el, b) => el.scrollIntoView({ behavior: 'smooth', block: b }), block)
    await settle(page)
    const onScreen = await target.evaluate((el) => {
      const r = el.getBoundingClientRect()
      return r.top >= 0 && r.top < innerHeight * 0.6
    })
    if (onScreen) return
  }
  throw new Error(`glideTo(${selector}): target never came to rest on screen`)
}

async function open(page, ctx, path, ready) {
  await page.goto(ctx.url(path))
  await page.locator(ready).first().waitFor({ state: 'visible' })
}

export const sceneFns = {
  async overview(page, ctx) {
    await ctx.line(0, () => open(page, ctx, ctx.route, tid('overview-loop')))
    await ctx.line(1, async () => {
      await ctx.point(`${tid('loop-features')} .ol-stage__label`)
      await ctx.point(`${tid('loop-defects')} .ol-stage__label`)
    })
    await ctx.line(2, async () => {
      await ctx.point('#rnw-title')
      await ctx.hero()
    })
    await ctx.line(3)
  },

  async features(page, ctx) {
    await ctx.line(0, () => open(page, ctx, ctx.route, tid(`feature-row-${ctx.pins.feature}`)))
    await ctx.line(1, async () => {
      await ctx.point(tid('feature-stage-building'))
      await page.locator(tid(`feature-row-${ctx.pins.feature}`)).waitFor()
      await ctx.hero()
    })
    await ctx.line(2)
    await ctx.line(3, async () => {
      await ctx.point(`${tid(`feature-row-${ctx.pins.feature}`)} a.ftt-title`)
      await page.locator(tid('figure-cost')).waitFor()
    })
  },

  async ledger(page, ctx) {
    await ctx.line(0, async () => {
      if (new URL(page.url()).pathname !== ctx.route) await page.goto(ctx.url(ctx.route))
      await page.locator(tid('figure-cost')).waitFor()
      await ctx.point(tid('feature-id'))
    })
    await ctx.line(1, () => ctx.point(tid('figure-cost')))
    await ctx.line(2, async () => {
      await ctx.point(tid('section-nav-spec'))
      await settle(page)
    })
    await ctx.line(3, () => glideTo(page, '#feature-spec h3:has-text("Acceptance tests")', 'start'))
    await ctx.line(4, async () => {
      await ctx.point(tid('section-nav-slices'))
      await settle(page)
      await ctx.hero()
    })
    await ctx.line(5, async () => {
      await ctx.point(tid('section-nav-tasks'))
      await settle(page)
    })
    await ctx.line(6, async () => {
      await ctx.point(tid('section-nav-defects'))
      await settle(page)
    })
  },

  async tasks(page, ctx) {
    await ctx.line(0, () => open(page, ctx, ctx.route, tid('next-step-list')))
    await ctx.line(1, () => ctx.point(tid('list-count')))
    await ctx.line(2, () => open(page, ctx, `/tasks/${ctx.pins.task}`, tid('task-detail-feature')))
    await ctx.line(3, async () => {
      await ctx.point(tid('task-detail-runs'))
      await page.locator('[role="dialog"]').waitFor({ state: 'visible' })
    })
    await ctx.line(4, () => ctx.hero())
  },

  async defects(page, ctx) {
    await ctx.line(0, () => open(page, ctx, ctx.route, tid('defects-list')))
    await ctx.line(1, async () => {
      await ctx.point(tid(`defect-permalink-${ctx.pins.defect}`))
      await page.locator(tid('defect-detail')).waitFor()
    })
    await ctx.line(2, () => ctx.hero())
  },

  async bridge(page, ctx) {
    await ctx.line(0, async () => {
      await open(page, ctx, ctx.route, '#feature-sessions')
      // The ledger's own hash reveal lands short of the section when arriving
      // from another page (defect #223), so the camera finishes the move.
      await glideTo(page, '#feature-sessions', 'start')
      await ctx.hero()
    })
    await ctx.line(1, async () => {
      await ctx.point('button[aria-label="Trace"]')
      await page.locator('[aria-label^="Copy session id"]').first().waitFor()
    })
  },

  async sessions(page, ctx) {
    const id = ctx.pins.sessions[0]
    await ctx.line(0, async () => {
      if (new URL(page.url()).pathname !== ctx.route) await open(page, ctx, ctx.route, '[aria-label^="Copy session id"]')
      // The list opens on Today; a pinned session from an earlier day is only
      // on it once the range is widened.
      await ctx.point('[aria-label="Filter by last activity time range"]')
      await ctx.point('[aria-label="Preset time ranges"] button:has-text("All time")')
      await page.locator(`[aria-label="Copy session id ${id}"]`).waitFor({ state: 'attached' })
    })
    await ctx.line(1, async () => {
      await glideTo(page, `[aria-label="Copy session id ${id}"]`)
      await ctx.hero()
    })
    await ctx.line(2, () => ctx.point(`code.srow__id[title="${id}"]`))
  },

  async trace(page, ctx) {
    await ctx.line(0, async () => {
      await ctx.point(`a.srow__title[href="${ctx.route}"]`)
      await page.locator(tid('trace-vitals-strip')).waitFor()
      await ctx.point(tid('trace-header-facts'))
    })
    await ctx.line(1, () => ctx.point(tid('trace-vitals-strip')))
    await ctx.line(2, async () => {
      await ctx.point(tid('header-details-toggle'))
      await page.locator(tid('trace-header-digest')).waitFor()
      await ctx.hero()
    })
    await ctx.line(3, () => glideTo(page, `${tid('trace-conversation-feed')} :text("TOOL FAILURE")`))
    await ctx.line(4, async () => {
      await ctx.point(tid('trace-agents-btn'))
      await page.getByText('goal-verifier', { exact: true }).filter({ visible: true }).first().waitFor()
    })
    await ctx.line(5, async () => {
      await ctx.point(tid('view-mode-menu'))
      await ctx.point('[role="menuitemradio"]:has-text("Timeline")')
      await page.locator(tid('spine-row')).first().waitFor()
    })
    await ctx.line(6, async () => {
      await ctx.point(`${tid('spine-row')}:has(${tid('spine-toggle')}) ${tid('spine-toggle')}`)
      await ctx.point(`${tid('spine-row')}:has-text("tool.Read")`)
    })
  },

  async messages(page, ctx) {
    await ctx.line(0, async () => {
      await ctx.point(tid('view-mode-menu'))
      await ctx.point('[role="menuitemradio"]:has-text("Messages")')
      await page.locator('li.scroll-mt-28').first().waitFor()
      await ctx.hero()
    })
    await ctx.line(1, () => glideTo(page, 'li.scroll-mt-28 >> nth=1', 'start'))
  },
}
