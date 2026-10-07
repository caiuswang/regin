/**
 * Feature #139, slice S4 — the Tasks tab under the pixel theme ("quest log"):
 * groups with sprite heads and no prototype codes, one primary key, board
 * sprite heads, `#feature` graph lanes. The data-owning components are hooked, never
 * forked, so the same rows, test ids and verbs exist under either theme.
 *
 * Every seeded task carries the test's own tag and every page is read under
 * `?tag=<mark>`: the scratch DB is shared by every spec in the run.
 */
import { execFileSync } from 'node:child_process'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { expect, test } from './auth-fixture.js'
import { contentOverflow } from './helpers/overflow.js'

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const PY = resolve(ROOT, '.venv/bin/python')
const VIEWS = ['list', 'board', 'graph']

function mark() {
  const info = test.info()
  return `pt${info.workerIndex}x${info.retry}x${Date.now().toString(36)}`
}

// Straight through the ORM: `running` and `failed` are statuses no API call
// can put a task in without launching a real agent. One task per next-step
// group, plus a defect fixed by the ready task so its FIXES tag is drawn.
function seed(tag) {
  const script = `
import json, sys
sys.path.insert(0, ".")
from lib.orm import SessionLocal
from lib.orm.models.defects import Defect
from lib.orm.models.tasks import AgentTask, AgentTaskDep, AgentTaskRun, PRIORITY_ORDER
from lib.features import store as features
tag = sys.argv[1]
feature = features.create(f"{tag} lane feature")

def task(title, status, prio="none", feature_id=None):
    with SessionLocal() as s:
        row = AgentTask(title=f"{tag} {title}", status=status, source="test",
                        tags_json=json.dumps([tag]), priority=prio,
                        priority_rank=PRIORITY_ORDER[prio], feature_id=feature_id)
        s.add(row); s.commit(); s.refresh(row)
        return row.id

ids = {"ready": task("ready", "armed", "urgent", feature.id),
       "running": task("running", "running", "high", feature.id),
       "blocked": task("blocked", "armed", "high"),
       "failed": task("failed", "failed", "medium"),
       "ideas": [task(f"idea {i}", "idea", "low") for i in range(2)],
       "done": task("done", "done", feature_id=feature.id),
       "feature": feature.id}
with SessionLocal() as s:
    s.add(AgentTaskDep(task_id=ids["blocked"], depends_on_id=ids["running"], kind="requires"))
    s.add(AgentTaskDep(task_id=ids["running"], depends_on_id=ids["done"], kind="requires"))
    s.add(AgentTaskRun(task_id=ids["running"], trace_id=f"{tag}-trace", attempt=1,
                       status="running", started_at="2026-09-01 10:00:00"))
    defect = Defect(symptom=f"{tag} defect", symptom_key=f"{tag}-defect", fix_task_id=ids["ready"])
    s.add(defect); s.commit(); s.refresh(defect)
    ids["defect"] = defect.id
print(json.dumps(ids))
`
  const out = execFileSync(PY, ['-c', script, tag], { cwd: ROOT, encoding: 'utf8' })
  return JSON.parse(out.trim().split('\n').pop())
}

const plTheme = page => page.evaluate(() => document.documentElement.getAttribute('data-pl-theme'))

async function pixelReady(page) {
  await expect.poll(() => plTheme(page)).toBe('pixel')
  await page.evaluate(() => document.fonts.ready)
}

async function switchTheme(page, label) {
  await page.getByTestId('pipeline-theme-switch').click()
  await page.getByRole('option', { name: label }).click()
  await expect(page.getByRole('listbox')).toHaveCount(0)
}

async function viewLoaded(page, view, ids) {
  if (view === 'list') await expect(page.getByTestId(`next-step-row-${ids.ready}`)).toBeVisible()
  else if (view === 'board') await expect(page.getByTestId(`board-card-${ids.ready}`)).toBeVisible()
  else await expect(page.getByTestId(`graph-node-${ids.ready}`)).toBeVisible()
}

// Everything a spec or a reader can address in the tab: test ids with their
// text, and every control's accessible name and state.
function surface() {
  const panel = document.querySelector('[data-testid="task-toolbar"]').closest('[data-pl-part="panel"]')
  // Text nodes only, not an <svg><title>: a sprite stands where a status
  // icon stood, and the icon's name is compared through `images` instead.
  // A MenuField words its pick per theme ("Group: Next step" / "GROUP · Next
  // step ▼"), so it reads as the value it holds.
  const MENU = '[role="combobox"][data-value]'
  const text = (el) => {
    if (el.matches(MENU)) return `@${el.dataset.value}`
    let out = ''
    const walker = document.createTreeWalker(el, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, (n) => {
      if (n.nodeType === Node.TEXT_NODE) return NodeFilter.FILTER_ACCEPT
      if (n.matches('svg')) return NodeFilter.FILTER_REJECT
      if (n.matches(MENU)) { out += ` @${n.dataset.value} `; return NodeFilter.FILTER_REJECT }
      return NodeFilter.FILTER_SKIP
    })
    for (let n = walker.nextNode(); n; n = walker.nextNode()) out += n.textContent
    return out.replace(/\s+/g, ' ').trim()
  }
  const ids = [...panel.querySelectorAll('[data-testid]')].map(el => `${el.getAttribute('data-testid')}=${text(el)}`)
  const images = [...panel.querySelectorAll('[role="img"]')].map(el => el.getAttribute('aria-label'))
  const controls = [...panel.querySelectorAll('button, a[href], input, [role="tab"]')].map(el =>
    [el.tagName, el.getAttribute('aria-label') || (el.textContent || '').replace(/\s+/g, ' ').trim(),
     el.getAttribute('href') || '', el.disabled ? 'disabled' : '', el.getAttribute('aria-expanded') || '',
     el.getAttribute('aria-selected') || ''].join('|'))
  return { ids, controls, images }
}

test.describe('pixel theme — tasks', () => {
  test('test_behaviour_is_theme_independent', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const tag = mark()
    const ids = seed(tag)
    // A sibling worker's write raises a `pipeline` frame on every open page,
    // and the tab re-reads its counts and rows on it — mid-switch, that read
    // is indistinguishable from one the theme caused. With the ticket held no
    // stream opens (same reasoning as pipeline-theme-seam's switch test).
    await page.route('**/api/auth/stream-ticket', () => {})

    for (const view of VIEWS) {
      await page.goto(`/tasks?tag=${tag}&view=${view}`)
      await viewLoaded(page, view, ids)
      await expect.poll(() => plTheme(page)).toBe('classic')
      const classic = await page.evaluate(surface)
      const url = page.url()

      const requests = []
      // The app's notification stream subscribes on its own schedule after
      // every page load; it is app chrome, not something a theme fetches.
      const onRequest = (r) => {
        const url = r.url()
        if (url.includes('/api/') && !/\/api\/(notifications\/stream|auth\/stream-ticket)/.test(url)) requests.push(url)
      }
      page.on('request', onRequest)
      await switchTheme(page, 'Pixel')
      await pixelReady(page)
      const pixel = await page.evaluate(surface)
      page.off('request', onRequest)

      expect(page.url()).toBe(url)
      expect(requests).toEqual([])
      expect(classic.ids.length).toBeGreaterThan(5)
      expect(pixel).toEqual(classic)
      await switchTheme(page, 'Classic')
      await expect.poll(() => plTheme(page)).toBe('classic')
    }

    // The verbs still answer under pixel: a group folds, RUN ALL asks first.
    await page.addInitScript(() => localStorage.setItem('regin_pipeline_theme', 'pixel'))
    await page.goto(`/tasks?tag=${tag}`)
    await pixelReady(page)
    const toggle = page.getByTestId('next-step-toggle-ideas')
    await expect(toggle).toHaveAttribute('aria-expanded', 'true')
    await toggle.click()
    await expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await expect(page.getByTestId('next-step-group-ideas').getByTestId('next-step-row-' + ids.ideas[0])).toHaveCount(0)
    const runs = []
    await page.route('**/api/tasks/*/run', (route) => {
      runs.push(Number(route.request().url().match(/tasks\/(\d+)\/run/)[1]))
      return route.fulfill({ json: { run: { attempt: 1 } } })
    })
    await page.getByTestId('next-step-run-all').click()
    await page.getByRole('button', { name: 'Run all ready tasks', exact: true }).click()
    await expect.poll(() => runs).toEqual([ids.ready])
  })

  test('the quest log draws titles, never prototype codes', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const tag = mark()
    const ids = seed(tag)
    await page.addInitScript(() => localStorage.setItem('regin_pipeline_theme', 'pixel'))

    await page.goto(`/tasks?tag=${tag}`)
    await viewLoaded(page, 'list', ids)
    await pixelReady(page)

    const panelTitle = await page.getByTestId('task-toolbar')
      .evaluate(el => getComputedStyle(el, '::before').content)
    expect(panelTitle).toMatch(/^"Quest log"/)

    // Group heads carry their sprite and label, no 2-0n code.
    const heads = await page.locator('[data-testid^="next-step-group-"]').evaluateAll(groups => groups.map(g => {
      const label = g.querySelector('[data-pl-part="group-label"]')
      return {
        key: g.getAttribute('data-testid').replace('next-step-group-', ''),
        before: getComputedStyle(label, '::before').content,
        transform: getComputedStyle(label).textTransform,
        font: getComputedStyle(label).fontFamily,
        sprite: g.querySelector('[data-pl-part="group-head"] [data-pl-glyph]')?.getAttribute('data-sprite') ?? null,
      }
    }))
    expect(heads.map(h => h.key)).toEqual(['ready', 'running', 'blocked', 'failed', 'ideas'])
    for (const h of heads) expect(h.before, h.key).toBe('none')
    expect(heads.map(h => h.sprite)).toEqual(['bot', 'bot.running', 'lock', 'bot.failed', 'bulb'])
    for (const h of heads) {
      expect(h.transform).toBe('uppercase')
      expect(h.font).toMatch(/^"?Silkscreen/)
    }

    // RUN ALL is the panel's only primary key; row verbs are ink outlines.
    await expect(page.locator('[data-pl-part="panel"]:has([data-testid="task-toolbar"]) [data-pl-part="key--primary"]')).toHaveCount(1)
    await expect(page.getByTestId('next-step-run-all')).toHaveAttribute('data-pl-part', 'key--primary')
    const fixes = page.getByTestId(`next-step-row-${ids.ready}`).getByTestId('task-fixes')
    await expect(fixes).toHaveText(`fixes D-${ids.defect}`)
    expect(await fixes.evaluate(el => getComputedStyle(el).textTransform)).toBe('uppercase')
    expect(await page.getByTestId(`next-step-row-${ids.ready}`).locator('[data-pl-part="row-id"]')
      .evaluate(el => getComputedStyle(el).fontFamily)).toMatch(/^"?Silkscreen/)

    // Grouped by status, every group head has its status sprite and no code.
    await page.goto(`/tasks?tag=${tag}&view=list&group=status`)
    await expect(page.locator('[data-testid^="task-group-toggle-"]').first()).toBeVisible()
    const statusHeads = await page.locator('[data-testid^="task-group-toggle-"]').evaluateAll(heads => heads.map(h => [
      h.getAttribute('data-pl-group'),
      h.querySelector('[data-pl-glyph]')?.getAttribute('data-sprite') ?? null,
      getComputedStyle(h.querySelector('[data-pl-part="group-label"]'), '::before').content,
    ]))
    expect(statusHeads.length).toBeGreaterThanOrEqual(5)
    statusHeads.forEach(([group, sprite, before]) => {
      expect(sprite, group).not.toBeNull()
      expect(before, group).toBe('none')
    })
    expect(Object.fromEntries(statusHeads.map(([g, s]) => [g, s]))).toMatchObject({
      idea: 'bulb', armed: 'bot', running: 'bot.running', done: 'check', failed: 'bot.failed' })

    // Board: every full column has a sprite head.
    await page.goto(`/tasks?tag=${tag}&view=board`)
    await viewLoaded(page, 'board', ids)
    const columns = await page.locator('[data-testid^="board-column-"][data-kind="full"]').evaluateAll(cols =>
      cols.map(c => [c.getAttribute('data-testid'), c.querySelector('header [data-pl-glyph]')?.getAttribute('data-sprite') ?? null]))
    expect(columns.length).toBeGreaterThanOrEqual(3)
    for (const [, sprite] of columns) expect(sprite).not.toBeNull()
    expect(Object.fromEntries(columns)['board-column-running']).toBe('bot.running')

    // Graph: lanes are #feature, with no L.0n code.
    await page.goto(`/tasks?tag=${tag}&view=graph`)
    await viewLoaded(page, 'graph', ids)
    const lanes = await page.locator('[data-testid^="graph-lane-"]:not([data-testid="graph-lane-count"])').evaluateAll(ls => ls.map((l) => {
      const ref = l.querySelector('[data-pl-part="meta-label"]')
      return getComputedStyle(ref, '::before').content + ' ' + ref.textContent.trim()
    }))
    expect(lanes.length).toBeGreaterThanOrEqual(2)
    lanes.forEach(lane => expect(lane).toMatch(/^none (#\d+|no feature)$/))
    expect(lanes.some(l => l.endsWith(`#${ids.feature}`))).toBe(true)
    expect(lanes.some(l => l.endsWith('no feature'))).toBe(true)
  })

  // The prototype draws only the next-step grouping; every other list shape
  // has to read as the same quest log, not as classic's rounded cards.
  for (const appearance of ['light', 'dark']) {
    test(`every grouping reads as the quest log (${appearance})`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 })
      const tag = mark()
      const ids = seed(tag)
      await page.addInitScript((t) => {
        localStorage.setItem('regin_theme', t)
        localStorage.setItem('regin_pipeline_theme', 'pixel')
      }, appearance)

      const SPRITES = {
        status: { idea: 'bulb', armed: 'bot', running: 'bot.running', failed: 'bot.failed', done: 'check' },
        priority: { urgent: 'bars.4', high: 'bars.3', medium: 'bars.2', low: 'bars.1', none: 'bars.0' },
        project: { none: 'tray' },
      }
      const failures = []
      for (const group of ['none', 'status', 'priority', 'project']) {
        await page.goto(`/tasks?tag=${tag}&view=list&group=${group}`)
        await expect(page.locator(`#task-${ids.ready}`)).toBeVisible()
        await pixelReady(page)
        await page.mouse.move(0, 0)

        const boxes = await page.locator('[data-pl-part="group"], [data-pl-part="list"]').evaluateAll(els =>
          els.map((el) => {
            const cs = getComputedStyle(el)
            const head = el.querySelector(':scope > [data-pl-part="group-head"], :scope > [data-pl-part="list-head"]')
            return [cs.borderTopWidth, cs.borderTopLeftRadius, head ? getComputedStyle(head).backgroundColor : 'none']
          }))
        expect(boxes.length, group).toBeGreaterThan(0)
        for (const box of boxes) expect(box, group).toEqual(['0px', '0px', 'rgba(0, 0, 0, 0)'])

        if (group === 'none') {
          const head = await page.getByTestId('task-day-head').boundingBox()
          const day = await page.locator(`#task-${ids.running}`).getByTestId('task-day').boundingBox()
          expect(Math.abs((head.x + head.width) - (day.x + day.width)), 'date head over the date column').toBeLessThan(2)
        } else {
          const heads = await page.locator('[data-testid^="task-group-toggle-"]').evaluateAll(hs => hs.map(h => [
            h.getAttribute('data-testid').replace('task-group-toggle-', ''),
            h.querySelector('[data-pl-glyph]')?.getAttribute('data-sprite') ?? null,
            getComputedStyle(h.querySelector('[data-pl-part="group-label"]')).fontFamily,
          ]))
          for (const [key, sprite, font] of heads) {
            expect(sprite, `${group}/${key}`).not.toBeNull()
            expect(font).toMatch(/^"?Silkscreen/)
          }
          expect(Object.fromEntries(heads.map(([k, s]) => [k, s]))).toMatchObject(SPRITES[group])
        }

        const row = page.locator(`#task-${ids.running}`)
        for (const part of [row.getByTestId('task-day'), row.getByTestId('task-run-count'), row.locator('.badge').first()]) {
          // Polled: the row re-renders when its liveness answer lands.
          await expect.poll(() => part.evaluate(el => getComputedStyle(el).fontFamily), { message: group })
            .toMatch(/^"?Silkscreen/)
        }

        // A row's chips are bare words, not a run of outlined boxes.
        const boxed = await page.locator('[data-testid="task-row"] [data-pl-part="tag"]').evaluateAll(tags => tags
          .filter((el) => {
            const cs = getComputedStyle(el)
            return cs.boxShadow !== 'none' || cs.borderTopWidth !== '0px'
          })
          .map(el => el.textContent.trim()))
        expect(boxed, group).toEqual([])

        // One ground, the shell's, as under next step: nothing between a row
        // and the shell paints a background of its own.
        const grounds = await page.locator('[data-testid="task-row"]').evaluateAll(rows => rows.flatMap((row) => {
          const painted = []
          for (let n = row; n && !n.classList.contains('pl-shell'); n = n.parentElement) {
            const cs = getComputedStyle(n)
            if (cs.backgroundColor !== 'rgba(0, 0, 0, 0)' || cs.backgroundImage !== 'none') {
              painted.push(`${n.getAttribute('data-pl-part') ?? n.className} ${cs.backgroundColor}`)
            }
          }
          return painted
        }))
        expect(grounds, group).toEqual([])

        const audit = await page.evaluate(colourAudit)
        failures.push(...audit.failures.map(f => `group=${group}: ${f}`))
      }
      expect(failures).toEqual([])
    })
  }

  test('classic draws no sprites', async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 })
    const tag = mark()
    const ids = seed(tag)
    await page.goto(`/tasks?tag=${tag}`)
    await viewLoaded(page, 'list', ids)
    await expect.poll(() => plTheme(page)).toBe('classic')
    const drawn = await page.locator('.pl-shell').evaluate(shell => ({
      glyphs: shell.querySelectorAll('[data-pl-glyph]').length,
    }))
    expect(drawn).toEqual({ glyphs: 0 })
  })

  // R7: on the Tasks tab, a chromatic fill is a primary key (one per panel),
  // a meter cell, or a sprite pixel — nothing else, in light or dark.
  for (const appearance of ['light', 'dark']) {
    test(`test_colour_budget (${appearance})`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 })
      const tag = mark()
      const ids = seed(tag)
      await page.addInitScript((t) => {
        localStorage.setItem('regin_theme', t)
        localStorage.setItem('regin_pipeline_theme', 'pixel')
      }, appearance)

      const failures = []
      for (const view of VIEWS) {
        await page.goto(`/tasks?tag=${tag}&view=${view}`)
        await viewLoaded(page, view, ids)
        await pixelReady(page)
        await page.mouse.move(0, 0)
        const audit = await page.evaluate(colourAudit)
        expect(audit.checked, `${view}: elements audited`).toBeGreaterThan(30)
        failures.push(...audit.failures.map(f => `${view}: ${f}`))
      }
      expect(failures).toEqual([])
    })
  }

  test('test_phone', async ({ page }) => {
    await page.setViewportSize({ width: 400, height: 860 })
    const tag = mark()
    const ids = seed(tag)
    await page.addInitScript(() => localStorage.setItem('regin_pipeline_theme', 'pixel'))

    // A stored board view still lands on the grouped list at phone width.
    await page.goto(`/tasks?tag=${tag}&view=board`)
    await viewLoaded(page, 'list', ids)
    await pixelReady(page)
    await expect(page.getByTestId('list-views')).toHaveCount(0)

    // Segmented tabs: three side by side on one row, each a 44px target.
    const tabs = await page.locator('[data-pl-part="tab"]').evaluateAll(els => els.map((el) => {
      const r = el.getBoundingClientRect()
      return { top: Math.round(r.top), h: r.height }
    }))
    expect(tabs).toHaveLength(3)
    expect(new Set(tabs.map(t => t.top)).size).toBe(1)
    for (const t of tabs) expect(t.h).toBeGreaterThanOrEqual(44)

    // Create is the floating key.
    const create = page.locator('#pipeline-actions [data-testid="task-new"]')
    await expect(create).toBeVisible()
    expect(await page.locator('#pipeline-actions').evaluate(el => getComputedStyle(el).position)).toBe('fixed')
    const box = await create.boundingBox()
    expect(box.width).toBeGreaterThanOrEqual(44)
    expect(box.height).toBeGreaterThanOrEqual(44)

    // The quest log keeps classic's compact rows and 44px group toggles.
    for (const key of ['ready', 'running', 'blocked', 'failed', 'ideas']) {
      const t = await page.getByTestId(`next-step-toggle-${key}`).boundingBox()
      expect(t.height, key).toBeGreaterThanOrEqual(44)
    }
    expect(await page.getByTestId('next-step-group-ready').locator('[data-pl-part="group-label"]')
      .evaluate(el => getComputedStyle(el, '::before').content)).toBe('none')

    const overflow = await contentOverflow(page)
    expect(overflow.pane).toBe(true)
    expect(overflow.offenders, `scrollWidth ${overflow.scrollWidth} > ${overflow.clientWidth}`).toEqual([])
    expect(overflow.scrollWidth).toBeLessThanOrEqual(overflow.clientWidth + 1)
  })
})

// Runs in the page. Colours go through a 1×1 canvas so every notation comes
// back as sRGB bytes; "chromatic" is a channel spread a soft tint still has.
function colourAudit() {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const ctx = canvas.getContext('2d', { willReadFrequently: true })
  const rgba = (color) => {
    ctx.clearRect(0, 0, 1, 1)
    ctx.fillStyle = 'rgba(0,0,0,0)'
    ctx.fillStyle = color
    ctx.fillRect(0, 0, 1, 1)
    const d = ctx.getImageData(0, 0, 1, 1).data
    return [d[0], d[1], d[2], d[3] / 255]
  }
  // The neutrals a surface may be: the pixel inks and papers, and the app's
  // own (slightly blue) surface and border ramp that classic chrome keeps.
  const root = getComputedStyle(document.documentElement)
  const NEUTRALS = ['--pl-page', '--pl-page-top', '--pl-paper', '--pl-paper-2', '--pl-paper-3', '--pl-sunk', '--pl-text', '--pl-text-2',
    '--pl-text-3', '--pl-muted', '--pl-faint', '--pl-frame', '--pl-hair', '--pl-rule', '--pl-bevel', '--pl-drop',
    '--pl-cell-off', '--pl-cell-flight', '--pl-cell-settled',
    '--color-surface', '--color-surface-2', '--color-surface-3', '--color-border',
    '--color-border-subtle', '--color-border-strong']
  const neutral = new Set(NEUTRALS.map(t => rgba(root.getPropertyValue(t).trim()).slice(0, 3).join()))
  const chromatic = (color) => {
    const [r, g, b, a] = rgba(color)
    return a >= 0.1 && Math.max(r, g, b) - Math.min(r, g, b) >= 8 && !neutral.has([r, g, b].join())
  }
  const gradientColours = image => [...image.matchAll(/(?:rgba?|oklch|oklab|color|hsla?)\([^()]*\)|#[0-9a-f]{3,8}\b/gi)].map(m => m[0])
  const visible = (el) => {
    const r = el.getBoundingClientRect()
    return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'
  }
  const describe = (el, pseudo) => `${el.tagName.toLowerCase()}${pseudo || ''}[${el.getAttribute('data-pl-part') ?? el.getAttribute('data-testid') ?? el.className?.baseVal ?? el.className ?? ''}] "${(el.textContent || '').trim().slice(0, 30)}"`

  const shell = document.querySelector('.pl-shell')
  const failures = []
  const primaries = new Map()
  let checked = 0
  for (const el of [shell, ...shell.querySelectorAll('*')]) {
    if (!visible(el) || el.closest('svg[data-sprite]')) continue
    checked++
    for (const pseudo of ['', '::before', '::after']) {
      const cs = getComputedStyle(el, pseudo || null)
      if (pseudo && (cs.content === 'none' || cs.content === 'normal' || cs.display === 'none')) continue
      const fills = [cs.backgroundColor, ...gradientColours(cs.backgroundImage)]
      if (!fills.some(chromatic)) continue
      if (!pseudo && el.matches('[data-pl-part="key--primary"]')) {
        const panel = el.closest('[data-pl-part="panel"]') ?? shell
        primaries.set(panel, (primaries.get(panel) ?? 0) + 1)
        continue
      }
      if (el.matches('[data-pl-part="meter"], [data-pl-part="meter-cell"]')) continue
      failures.push(`chromatic fill ${fills.find(chromatic)} on ${describe(el, pseudo)}`)
    }
  }
  for (const [panel, n] of primaries) {
    if (n > 1) failures.push(`${n} primary keys in one panel ${describe(panel)}`)
  }
  return { failures, checked }
}
