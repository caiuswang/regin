/**
 * Presentation mode: a prompt's attached images, and the dev overlay's hotkey.
 *
 * The reader is where the payload is shown WHOLE, so an image that the feed
 * could only thumbnail has to render here at document scale — and in reading
 * order, where its `[Image #N]` marker sat. The invariants below are the ones a
 * screenshot cannot prove: that a marker with no attachment behind it stays
 * literal text, that an image whose marker the hook's truncation ate is still
 * rendered rather than silently dropped, and that the reader's
 * keyboard-ownership rule stops at modifier chords — it swallowed Ctrl+Shift+G,
 * which is how `grab` arms, so the only debug tool for this surface was dead
 * exactly where it was needed.
 */
import { randomUUID } from 'node:crypto'
import { test, expect } from './auth-fixture.js'

// 1×1 red PNG. The bytes only have to decode; the assertions are about which
// images reach the DOM and in what order.
const PNG_1PX = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='

const reader = page => page.getByTestId('span-present-reader')

async function seed(page, { text, indices, attached }) {
  const traceId = randomUUID()
  const promptId = `p-${traceId.slice(0, 8)}`
  await page.goto('/trace/sessions')
  const token = await page.evaluate(() => localStorage.getItem('regin_auth_token'))
  const auth = { Authorization: `Bearer ${token}` }

  expect((await page.request.post('/api/session-spans', {
    headers: auth,
    data: [{
      trace_id: traceId, span_id: promptId, parent_id: null, name: 'prompt',
      start_time: '2026-05-08T12:00:00',
      attributes: { text, image_indices: indices, is_test: true },
    }, {
      trace_id: traceId, span_id: `r-${traceId.slice(0, 8)}`, parent_id: promptId,
      name: 'assistant_response', start_time: '2026-05-08T12:00:01',
      attributes: { text: 'A plain response, no images anywhere.', is_test: true },
    }],
  })).ok()).toBeTruthy()

  if (attached.length) {
    expect((await page.request.post('/api/prompt-images', {
      headers: auth,
      data: attached.map(idx => ({
        trace_id: traceId, prompt_span_id: promptId, idx,
        media_type: 'image/png', data_b64: PNG_1PX,
      })),
    })).ok()).toBeTruthy()
  }

  await page.goto(`/trace/sessions/${traceId}?view=conversation`)
  await expect(page.locator('.present-open').first()).toBeVisible({ timeout: 30_000 })
  return { traceId, promptId }
}

async function openPrompt(page) {
  await page.locator('.present-open').first().click()
  await expect(reader(page)).toBeVisible()
  await expect(reader(page).locator('.present-name')).toHaveText('prompt')
}

test('a prompt renders its attached images inline, in marker order', async ({ page }) => {
  await seed(page, {
    text: 'Before. [Image #1] Between. [Image #2] After.',
    indices: [1, 2],
    attached: [1, 2],
  })
  await openPrompt(page)

  const figures = reader(page).locator('.present-figure')
  await expect(figures).toHaveCount(2)
  await expect(figures.nth(0).locator('.present-figcaption')).toHaveText('Image #1')
  await expect(figures.nth(1).locator('.present-figcaption')).toHaveText('Image #2')

  // The bytes actually arrived — a blob URL that 404s decodes to naturalWidth 0.
  await expect.poll(async () => page.evaluate(() =>
    [...document.querySelectorAll('.present-image')].map(i => i.naturalWidth),
  )).toEqual([1, 1])

  // The marker became the image, so it is no longer sitting there as text.
  await expect(reader(page).locator('.present-prose')).not.toContainText('[Image #')
  // Reading order survives: prose, image, prose, image, prose.
  const order = await reader(page).locator('.present-prose').innerText()
  expect(order.indexOf('Before.')).toBeLessThan(order.indexOf('Image #1'))
  expect(order.indexOf('Image #1')).toBeLessThan(order.indexOf('Between.'))
  expect(order.indexOf('Between.')).toBeLessThan(order.indexOf('Image #2'))
  expect(order.indexOf('Image #2')).toBeLessThan(order.indexOf('After.'))
})

test('a marker with nothing attached behind it stays literal text', async ({ page }) => {
  await seed(page, {
    text: 'Real: [Image #1]. Typed by hand: [Image #99].',
    indices: [1],
    attached: [1],
  })
  await openPrompt(page)

  await expect(reader(page).locator('.present-figure')).toHaveCount(1)
  await expect(reader(page).locator('.present-prose')).toContainText('[Image #99]')
  await expect(reader(page).locator('.present-prose')).not.toContainText('[Image #1]')
})

test('an image whose marker the truncation ate still renders', async ({ page }) => {
  // The hook clips long prompt text with a `…`, and the cut can take a trailing
  // marker with it. The image is attached all the same.
  await seed(page, {
    text: 'A long prompt that was clipped before its second marker…',
    indices: [1, 2],
    attached: [1, 2],
  })
  await openPrompt(page)

  const figures = reader(page).locator('.present-figure')
  await expect(figures).toHaveCount(2)
  await expect(figures.nth(1).locator('.present-figcaption')).toHaveText('Image #2')
})

test('a span with no images renders prose exactly as before', async ({ page }) => {
  await seed(page, { text: 'No attachments here at all.', indices: [], attached: [] })
  await openPrompt(page)

  await expect(reader(page).locator('.present-figure')).toHaveCount(0)
  await expect(reader(page).locator('.present-prose')).toContainText('No attachments here at all.')

  // …and so does a span kind the image endpoint knows nothing about.
  await page.keyboard.press('ArrowDown')
  await expect(reader(page).locator('.present-name')).toHaveText('assistant_response')
  await expect(reader(page).locator('.present-figure')).toHaveCount(0)
})

test('the reader swallows bare page shortcuts but lets the grab chord through', async ({ page }) => {
  await seed(page, { text: 'Anything readable.', indices: [], attached: [] })
  await openPrompt(page)

  // `grab` listens on `window`; the reader used to stopPropagation() every key
  // but Escape/Tab, so the chord never got there.
  await page.evaluate(() => {
    window.__chord = false
    window.addEventListener('keydown', (e) => {
      if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'g') window.__chord = true
    })
    window.__bare = false
    window.addEventListener('keydown', (e) => {
      if (e.key === 'f' && !e.ctrlKey && !e.metaKey && !e.altKey) window.__bare = true
    })
  })

  await page.keyboard.press('Control+Shift+G')
  expect(await page.evaluate(() => window.__chord)).toBe(true)

  await page.keyboard.press('f')
  expect(await page.evaluate(() => window.__bare)).toBe(false)
  await expect(reader(page)).toBeVisible()
})

test('an attached image the persistence caps dropped degrades to one marker', async ({ page }) => {
  // `image_indices` deliberately lists images that were NOT stored, so the UI
  // knows they existed (hook_manager/.../span_posters.py). The fetch 404s, and
  // the reader must not then print the marker twice — once as the placeholder
  // and once as the caption underneath it.
  await seed(page, { text: 'Capped: [Image #1].', indices: [1], attached: [] })
  await openPrompt(page)

  await expect(reader(page).locator('.present-image')).toHaveCount(0)
  await expect(reader(page).locator('.present-figcaption')).toHaveCount(0)
  await expect(reader(page).locator('.present-image-pending')).toHaveText('[Image #1]')
})

test('a chord the page binds is still swallowed — only grab gets through', async ({ page }) => {
  // Regression: exempting EVERY modifier chord let Ctrl+K open the command
  // palette on top of the reader, inside its focus trap — un-typeable, and the
  // first Escape retired the reader beneath it instead of the palette.
  await seed(page, { text: 'Anything readable.', indices: [], attached: [] })
  await openPrompt(page)

  await page.keyboard.press('Control+k')
  await expect(page.locator('.palette-card')).toHaveCount(0)
  await expect(reader(page)).toBeVisible()

  await page.keyboard.press('Control+Shift+k')
  await expect(page.locator('.palette-card')).toHaveCount(0)
  await expect(reader(page)).toBeVisible()
})

test('the reader declares how grab should reopen it', async ({ page }) => {
  await seed(page, { text: 'Anything readable.', indices: [], attached: [] })
  await openPrompt(page)

  // grab relays this verbatim into a `--reveal 'click:…'` step; without it the
  // emitted dom-measure command measures a closed overlay.
  const selector = await reader(page).getAttribute('data-grab-reveal')
  expect(selector).toBe('.present-open')
  await page.keyboard.press('Escape')
  await expect(page.locator(selector).first()).toBeVisible()
})

// A prompt is the user's INPUT, not model markdown, and the reader used to run
// it through the markdown pipeline: `import java.util.*;` painted as
// `import java.util.;` and a `/* … */` comment collapsed into emphasis, because
// every mark-shaped character was consumed. Pasted source is the common case
// for a prompt, so the reader renders it literally — the conversation card
// (`PromptBody`) always has.
const JAVA = `look at this

import java.util.*;
/*
 class ListNode { int val; }
*/
public class Solution {
    int x = 1;  // two-space indent below
        int y = 2;
}
_not italics_ and **not bold**`

test('a pasted prompt keeps every mark-shaped character and its indentation',
  async ({ page }) => {
    await seed(page, { text: JAVA, indices: [], attached: [] })
    await openPrompt(page)

    const well = reader(page).locator('pre.present-verbatim')
    await expect(well).toHaveCount(1)
    const shown = await well.innerText()
    for (const literal of ['import java.util.*;', '/*', '*/', '_not italics_',
                           '**not bold**', '        int y = 2;']) {
      expect(shown).toContain(literal)
    }
    // The well wraps; a 62KB file behind a horizontal scrollbar is not a
    // reading surface, and the markdown `pre` rule (white-space: pre) must not
    // take this element over.
    expect(await well.evaluate(el => getComputedStyle(el).whiteSpace))
      .toBe('pre-wrap')
    expect(await well.evaluate(el => el.scrollWidth <= el.clientWidth + 1))
      .toBe(true)
  })

test('a verbatim prompt still inlines its attached images at the marker',
  async ({ page }) => {
    // Rendering literally must not cost the reader its image inlining — the
    // body still goes through PresentProse, only the text runs change.
    await seed(page, {
      text: 'code *stars* stay\n[Image #1]\nafter',
      indices: [1], attached: [1],
    })
    await openPrompt(page)
    await expect(reader(page).locator('.present-image')).toHaveCount(1)
    await expect(reader(page).locator('pre.present-verbatim').first())
      .toContainText('code *stars* stay')
  })

test('a marker with nothing behind it does not split a verbatim body into plates',
  async ({ page }) => {
    // `imageSegments` splits on every marker, attached or not. For literal text
    // the pieces have to be sewn back up: the conversation card paints one
    // continuous block, and three stacked 18px-margined wells is not that.
    await seed(page, {
      text: 'line one\n[Image #5] typed literally\nline three',
      indices: [], attached: [],
    })
    await openPrompt(page)
    const wells = reader(page).locator('pre.present-verbatim')
    await expect(wells).toHaveCount(1)
    await expect(wells.first()).toContainText('[Image #5] typed literally')
    await expect(reader(page).locator('.present-figure')).toHaveCount(0)
  })
