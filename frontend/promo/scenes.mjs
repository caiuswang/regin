// One function per non-card storyboard scene, keyed by scene id. A scene runs
// its actions inside its lines (`ctx.line(i, during)`), so its length comes
// from the narration and from awaited selectors, never from a fixed wait.

export const sceneFns = {
  async overview(page, ctx) {
    await ctx.line(0, async () => {
      await page.goto(ctx.url(ctx.route))
      await page.locator('[data-testid="overview-loop"]').waitFor()
    })
    await ctx.line(1, async () => {
      await ctx.point('[data-testid="loop-features"] .ol-stage__label')
      await ctx.hero()
    })
  },
}
