import { expect, test } from 'claude-code/testing'

import { parseUnread } from './register'

const STATS = 'total:     736\nunread:    5\ndismissed: 63\n'

test('parses the unread line of `regin messages stats`', async () => {
  expect(parseUnread(STATS)).toBe(5)
  expect(parseUnread('garbage')).toBe(null)
})

test('unread count shows above the prompt', async ($, on) => {
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('process.run', () => ({ value: {
    exitCode: 0, stdout: STATS, stderr: '', isStdoutTruncated: false, isStderrTruncated: false,
  } }))
  on('ui.render', ($, e) => { const { Box } = $.ui.resolve(e); return <Box key="engine" /> })
  await $.session.start({ cwd: '/tmp', surface: 'terminal', isInteractive: true } as never)
  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({
      plugin: 'regin-band', surface, component: 'AbovePrompt',
      props: { hasSurvey: false, isWorking: false } as never,
    })
    expect(await ui.find({ type: 'Text', text: /regin inbox: 5 unread/ })).toBeDefined()
    await ui.unmount()
  }
})

test('band is empty when inbox is clear', async ($, on) => {
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('process.run', () => ({ value: {
    exitCode: 0, stdout: 'unread:    0\n', stderr: '', isStdoutTruncated: false, isStderrTruncated: false,
  } }))
  on('ui.render', ($, e) => { const { Box } = $.ui.resolve(e); return <Box key="engine" /> })
  await $.session.start({ cwd: '/tmp', surface: 'terminal', isInteractive: true } as never)
  const ui = await $.ui.mount({
    plugin: 'regin-band', surface: 'terminal', component: 'AbovePrompt',
    props: { hasSurvey: false, isWorking: false } as never,
  })
  expect(await ui.find({ type: 'Text', text: /regin inbox/ })).toBeUndefined()
  await ui.unmount()
})
