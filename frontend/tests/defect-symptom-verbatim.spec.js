/**
 * A defect's symptom is QUOTED INPUT, not an authored document.
 *
 * `defects.symptom` is whoever opened it, in their words, and `missing_check`
 * names a test — neither is prose to be styled. They are the two "stays plain"
 * rows of feature #75's survey, the same pairing `agent-prompt-verbatim`
 * already makes for a launch brief. This spec is the watch that says so out
 * loud, so that the pass which widened the shared markdown stylesheet — and
 * any future pass that "fixes" the last unrendered field on the Pipeline —
 * fails here instead of silently eating a reporter's asterisks and folding the
 * line breaks that carried their reproduction steps.
 */
import { test, expect } from './auth-fixture.js'

// Every mark the widened stylesheet now styles somewhere else, in one string:
// emphasis, a path in backticks, a heading, a bullet, and the blank-line block
// break that a renderer would consume.
const SYMPTOM = [
  'the gutter folds when **not bold** is typed',
  '`lib/orm/engine.py` returns a row the list never shows',
  '## not a heading',
  '- not a bullet',
].join('\n')

const MISSING_CHECK = 'test_symptom_is_quoted[**literal**] in `tests/test_defects.py`'

function defect(over = {}) {
  return {
    id: 7, feature_id: null, feature_title: null,
    symptom: SYMPTOM, status: 'closed',
    round: 1, blamed_against_state: null, missing_check: MISSING_CHECK,
    suspect_paths: [], fix_provider: null, fix_external_key: null,
    opened_at: '2026-09-14 12:00:00', closed_at: '2026-09-15 09:00:00',
    ...over,
  }
}

// One handler for both shapes, dispatching on the path it was asked for. Two
// patterns, because a glob `*` stops at a slash: the list and the permalink do
// not share one.
async function stubDefects(page) {
  const answer = (route) => {
    if (route.request().method() !== 'GET') return route.fallback()
    const { pathname } = new URL(route.request().url())
    if (/\/api\/defects\/\d+$/.test(pathname)) {
      return route.fulfill({ json: { defect: defect() } })
    }
    return route.fulfill({ json: { items: [defect()], scope: 'all' } })
  }
  await page.route('**/api/defects*', answer)
  await page.route('**/api/defects/**', answer)
}

// "Prints literally" is not "contains the word": the element holds exactly the
// bytes that were reported, and holds them as text. A renderer that consumed
// one mark would leave an element child behind — so zero children is the
// tightest way to state it, and it stays true no matter which tag the
// stylesheet learns to style next.
async function expectVerbatim(locator, source) {
  await expect(locator).toHaveText(source)
  expect(await locator.evaluate((el) => el.children.length)).toBe(0)
}

test('a symptom is quoted, never rendered, and keeps its line breaks',
  async ({ page }) => {
    await stubDefects(page)

    await page.goto('/defects')
    // Expanded, the row carries the symptom unclamped.
    await page.locator('li.card').getByRole('button', { name: /Show details/ }).click()
    const symptom = page.locator('li.card .symptom')
    await expect(symptom).toBeVisible()

    await expectVerbatim(symptom, SYMPTOM)

    // The breaks are the reporter's own structure — four authored lines must
    // occupy four lines on screen. Asserting the box, not the class: a rule
    // that collapsed whitespace would leave the text rendered as one run.
    const { lines, whiteSpace } = await symptom.evaluate((el) => {
      const cs = getComputedStyle(el)
      return {
        lines: Math.round(el.getBoundingClientRect().height /
          parseFloat(cs.lineHeight)),
        whiteSpace: cs.whiteSpace,
      }
    })
    expect(whiteSpace).toMatch(/^pre/)
    expect(lines).toBeGreaterThanOrEqual(4)
  })

test('the permalink quotes the symptom and the missing check too',
  async ({ page }) => {
    await stubDefects(page)

    await page.goto('/defects/7')
    const detail = page.getByTestId('defect-detail')
    await expect(detail).toBeVisible()

    await expectVerbatim(detail.locator('.symptom'), SYMPTOM)

    // The check names a test. Its brackets and backticks are part of the name
    // a reader has to be able to paste into a shell.
    const check = detail.locator('dt', { hasText: 'missing check' })
      .locator('xpath=following-sibling::dd[1]')
    await expectVerbatim(check, MISSING_CHECK)
  })
