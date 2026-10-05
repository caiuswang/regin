import { expect, test } from 'claude-code/testing'

const LINK = 'https://claude.ai/code/session_01AbCdEf'

test('test_commit_attribution_drops_the_session_trailer', async ($, on) => {
  on('attribution.text', () => ({
    text: `Co-Authored-By: Claude <noreply@anthropic.com>\nClaude-Session: ${LINK}`,
  }))

  const { text } = await $.attribution.text({ kind: 'commit', text: 'as asked' })

  expect(text).toBe('Co-Authored-By: Claude <noreply@anthropic.com>')
})

test('test_pr_attribution_drops_bare_session_links', async ($, on) => {
  on('attribution.text', () => ({
    text: `🤖 Generated with [Claude Code](https://claude.com/claude-code)\n\n${LINK}\n`,
  }))

  const { text } = await $.attribution.text({ kind: 'pr', text: 'as asked' })

  expect(text).toBe('🤖 Generated with [Claude Code](https://claude.com/claude-code)\n')
})

test('test_pr_attribution_keeps_the_rest_of_a_line_with_an_inline_session_link', async ($, on) => {
  on('attribution.text', () => ({ text: `Built in ${LINK} by  Claude` }))

  const { text } = await $.attribution.text({ kind: 'pr', text: 'as asked' })

  expect(text).toBe('Built in by  Claude')
})

test('test_pr_attribution_keeps_punctuation_after_a_session_link', async ($, on) => {
  on('attribution.text', () => ({ text: `See ${LINK}, then ${LINK}?utm=x.` }))

  const { text } = await $.attribution.text({ kind: 'pr', text: 'as asked' })

  expect(text).toBe('See, then.')
})

test('test_commit_attribution_drops_a_session_key_in_any_case_or_position', async ($, on) => {
  on('attribution.text', () => ({ text: `Body\r\n\r\nTrailers Claude-Session: ${LINK}\r\nclaude-session: ${LINK}\r\n` }))

  const { text } = await $.attribution.text({ kind: 'commit', text: 'as asked' })

  expect(text).toBe('Body\r\n\r\nTrailers\r\n')
})

for (const kind of ['exemption', 'remedy'] as const) {
  test(`test_other_attribution_text_passes_untouched (${kind})`, async ($, on) => {
    const composed = `Claude-Session: ${LINK}\n\n${LINK}\n`
    const reached: unknown[] = []
    on('attribution.text', (_$, e) => { reached.push(e); return { text: composed } })

    const { text } = await $.attribution.text({ kind, text: composed })

    expect(text).toBe(composed)
    expect(reached).toEqual([{ kind, text: composed }])
  })
}
