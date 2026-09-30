// One function per non-card storyboard scene, keyed by scene id. A scene runs
// its actions inside its lines (`ctx.line(i, during)`), so its length comes
// from the narration and from awaited selectors, never from a fixed wait.
//
// The film is one unbroken session in the app: each scene starts where the
// last one left off, and every change of view is a visible click on a real
// link (`ctx.follow`), dissolved rather than cut.

const tid = (id) => `[data-testid="${id}"]`

export const sceneFns = {
  async overview(ctx) {
    await ctx.line(0, async () => {
      await ctx.aim(tid('loop-tasks'))
      await ctx.aim(tid('loop-features'))
      await ctx.aim(tid('loop-defects'))
    })
    await ctx.line(1, async () => {
      await ctx.aim('#rnw-title')
      await ctx.hero()
    })
    await ctx.line(2, () => ctx.follow(tid('pipeline-tab-tasks'), tid('next-step-list')))
  },

  async queue(ctx) {
    await ctx.line(0, async () => {
      await ctx.aim('button:has-text("Group: Next step")')
      await ctx.hero()
    })
    await ctx.line(1, () => ctx.aim('button:has-text("Graph")'))
    await ctx.line(2, () => ctx.follow(tid('pipeline-tab-features'), tid(`feature-row-${ctx.pins.feature}`)))
  },

  async features(ctx) {
    await ctx.line(0, async () => {
      await ctx.aim(`${tid(`feature-row-${ctx.pins.feature}`)} a.ftt-title`)
      await ctx.hero()
    })
    await ctx.line(1, () => ctx.aim(tid('feature-stage-escaped')))
    await ctx.line(2, () => ctx.follow(tid('triage-all-defects'), tid('defects-list')))
  },

  async defects(ctx) {
    const permalink = tid(`defect-permalink-${ctx.pins.defect}`)
    await ctx.line(0, () => ctx.aim(`${tid('defects-list')} li >> nth=0 >> a.key`))
    await ctx.line(1, async () => {
      await ctx.scrollTo(permalink)
      await ctx.follow(permalink, tid('defect-detail'))
    })
    await ctx.line(2, async () => {
      await ctx.zoom(tid('defect-detail'), 1.45, { left: true })
      await ctx.aim(`${tid('defect-detail')} dt:has-text("Missing check")`)
      await ctx.hero()
    })
    await ctx.line(3, async () => {
      await ctx.zoom(null)
      await ctx.follow(`${tid('defect-detail')} a.key[href="/features/${ctx.pins.feature}"]`, tid('figure-cost'))
    })
  },

  async ledger(ctx) {
    await ctx.line(0, () => ctx.aim(tid('feature-id')))
    await ctx.line(1, async () => {
      await ctx.zoom(tid('figure-cost'), 1.35)
      await ctx.aim(tid('figure-cost'))
      await ctx.aim(tid('figure-work'))
      await ctx.zoom(null)
    })
    await ctx.line(2, () => ctx.jumpTo('#feature-spec h3:has-text("Acceptance tests")', 'start'))
    await ctx.line(3, async () => {
      await ctx.follow(tid('section-nav-slices'), tid('feature-slices'))
      await ctx.hero()
    })
    await ctx.line(4, () => ctx.follow(tid('slice-task-S4'), tid('task-detail-runs')))
  },

  async task(ctx) {
    await ctx.line(0, () => ctx.aim('h2:has-text("Delivers")'))
    await ctx.line(1, async () => {
      await ctx.scrollTo('h2:has-text("验收测试名")', 'start')
      await ctx.aim('h2:has-text("验收测试名") + ul code >> nth=0')
    })
    await ctx.line(2, async () => {
      await ctx.follow(tid('task-detail-runs'), '[role="dialog"]')
      await ctx.aim('[role="dialog"] :text("verify passed")')
      await ctx.hero()
    })
    await ctx.line(3, () => ctx.follow(tid('task-run-trace-1'), tid('trace-vitals-strip')))
  },

  async trace(ctx) {
    await ctx.line(0, async () => {
      await ctx.zoom(tid('trace-header-facts'), 1.4)
      await ctx.aim(tid('trace-header-facts'))
    })
    await ctx.line(1, async () => {
      await ctx.zoom(null)
      await ctx.aim(tid('trace-vitals-strip'))
    })
    await ctx.line(2, async () => {
      await ctx.follow(tid('header-details-toggle'), tid('trace-conversation-feed'))
      await ctx.hero()
    })
    await ctx.line(3, () => ctx.scrollTo(`${tid('trace-conversation-feed')} :text("TOOL FAILURE")`))
    await ctx.line(4, async () => {
      await ctx.follow(tid('trace-agents-btn'), ':text-is("goal-verifier") >> visible=true')
      await ctx.aim(':text-is("goal-verifier") >> visible=true')
    })
    await ctx.line(5, async () => {
      await ctx.point(tid('view-mode-menu'))
      await ctx.follow('[role="menuitemradio"]:has-text("Timeline")', tid('spine-row'))
    })
    await ctx.line(6, async () => {
      await ctx.point(`${tid('spine-row')}:has(${tid('spine-toggle')}) ${tid('spine-toggle')}`)
      // A row click also scrolls the rail's own turn list to that turn, past
      // the span's details; the rail is put back to its top beneath the plate.
      await ctx.follow(`${tid('spine-row')}:has-text("tool.Read")`,
        'aside.trace-detail-rail > .card:first-child :text-is("tool.Read")',
        () => ctx.scrollTo('aside.trace-detail-rail h2:has-text("Span details")', 'start', { instant: true }))
    })
  },

  async messages(ctx) {
    await ctx.line(0, async () => {
      await ctx.point(tid('view-mode-menu'))
      await ctx.follow('[role="menuitemradio"]:has-text("Messages")', 'li.scroll-mt-28')
      await ctx.hero()
    })
    await ctx.line(1, () => ctx.scrollTo('li.scroll-mt-28 >> nth=1', 'start'))
  },

  async sessions(ctx) {
    const id = ctx.pins.sessions[0]
    await ctx.line(0, () => ctx.follow('a[href="/trace/sessions"]', '[aria-label^="Copy session id"]'))
    await ctx.line(1, async () => {
      // The list opens on Today; a pinned session from an earlier day is only
      // on it once the range is widened.
      await ctx.point('[aria-label="Filter by last activity time range"]')
      await ctx.follow('[aria-label="Preset time ranges"] button:has-text("All time")', `[aria-label="Copy session id ${id}"]`)
      await ctx.scrollTo(`[aria-label="Copy session id ${id}"]`)
      await ctx.aim(`a.srow__title[href="${ctx.route}/${id}"]`)
      await ctx.hero()
    })
    await ctx.line(2)
  },
}
