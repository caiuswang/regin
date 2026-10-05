import { expect, test } from 'claude-code/testing'

import { parseInbox } from './register'

const ROW = (id: number, title: string) => ({
  id, title, body: `${title} body`, msg_type: 'warning', created_at: '2026-10-05T09:41:12.000000',
})
const TWO = JSON.stringify([ROW(7, 'deploy blocked'), ROW(5, 'tests red')])
const ok = (stdout: string) => ({ value: {
  exitCode: 0, stdout, stderr: '', isStdoutTruncated: false, isStderrTruncated: false,
} })
const PANE = { component: 'Pane', requestId: 'regin-inbox' } as const
const PANE_PROPS = { title: 'regin inbox', isFocused: false, bodyColumns: 80, placement: 'dock' } as never
const BAND_PROPS = { hasSurvey: false, isWorking: false } as never
const START = { cwd: '/tmp', surface: 'terminal', isInteractive: true } as never

// A fake `regin messages` CLI: `read --all` empties the unread list.
function fakeRegin(argvs: string[][]) {
  let unread = TWO
  return (_$: unknown, e: { argv: readonly string[] }) => {
    const argv = e.argv.slice(2)
    argvs.push(argv)
    if (argv[1] === 'read') {
      unread = '[]'
      return ok('marked 2 message(s) read\n')
    }
    return ok(unread)
  }
}

test('parses `regin messages list --json` into the inbox', async () => {
  expect(parseInbox(TWO)).toEqual({
    unread: 2,
    messages: [
      { id: 7, title: 'deploy blocked', type: 'warning', createdAt: '2026-10-05T09:41:12.000000' },
      { id: 5, title: 'tests red', type: 'warning', createdAt: '2026-10-05T09:41:12.000000' },
    ],
  })
  expect(parseInbox(JSON.stringify([{ ...ROW(1, ''), title: null }]))?.messages[0]?.title).toBe(' body')
  expect(parseInbox('garbage')).toBe(null)
})

test('test_regin_inbox_command_opens_the_inbox_pane', async ($, on) => {
  const commands: string[] = []
  const opened: string[] = []
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => { commands.push(e.name); return { value: { command: e.name } } })
  on('process.run', fakeRegin([]))
  on('ui.open', (_$, e) => { opened.push(e.id); return { value: { isPlaced: true } } })
  on('ui.render', ($, e) => { const { Box } = $.ui.resolve(e); return <Box key="engine" /> })
  await $.session.start(START)

  expect(commands).toEqual(['regin-inbox'])
  await $.command.run({ command: 'regin-inbox' } as never)
  expect(opened).toEqual(['regin-inbox'])

  for (const surface of ['terminal', 'desktop'] as const) {
    const ui = await $.ui.mount({ plugin: 'regin-band', surface, ...PANE, props: PANE_PROPS })
    expect(await ui.find({ type: 'Text', text: /deploy blocked/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /tests red/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /10-05 09:41 warning/ })).toBeDefined()
    await ui.unmount()
  }
})

test('test_mark_all_read_clears_the_pane', async ($, on) => {
  const argvs: string[][] = []
  let fake = fakeRegin(argvs)
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('process.run', (_$, e) => fake(_$, e))
  on('ui.render', ($, e) => { const { Box } = $.ui.resolve(e); return <Box key="engine" /> })

  for (const surface of ['terminal', 'desktop'] as const) {
    argvs.length = 0
    fake = fakeRegin(argvs)
    await $.session.start(START)

    const pane = await $.ui.mount({ plugin: 'regin-band', surface, ...PANE, props: PANE_PROPS })
    const band = await $.ui.mount({ plugin: 'regin-band', surface, component: 'AbovePrompt', props: BAND_PROPS })
    expect(await pane.find({ type: 'Text', text: /deploy blocked/ })).toBeDefined()
    expect(await band.find({ type: 'Text', text: /regin inbox: 2 unread/ })).toBeDefined()

    await pane.press({ key: 'mark-all-read' })

    expect(argvs).toContainEqual(['messages', 'read', '--all'])
    expect(await pane.find({ type: 'Text', text: /No unread messages/ })).toBeDefined()
    expect(await pane.find({ type: 'Text', text: /deploy blocked/ })).toBeUndefined()
    expect(await band.find({ type: 'Text', text: /regin inbox/ })).toBeUndefined()
    await pane.unmount()
    await band.unmount()
  }
})

test('band button opens the inbox pane', async ($, on) => {
  const commands: string[] = []
  const opened: string[] = []
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => { commands.push(e.name); return { value: { command: e.name } } })
  on('process.run', fakeRegin([]))
  on('ui.open', (_$, e) => { opened.push(e.id); return { value: { isPlaced: true } } })
  on('ui.render', ($, e) => { const { Box } = $.ui.resolve(e); return <Box key="engine" /> })
  await $.session.start(START)
  const band = await $.ui.mount({ plugin: 'regin-band', surface: 'terminal', component: 'AbovePrompt', props: BAND_PROPS })
  await band.press({ key: 'open-inbox' })
  expect(opened).toEqual(['regin-inbox'])
  await band.unmount()
})

test('band is empty when inbox is clear', async ($, on) => {
  const commands: string[] = []
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => { commands.push(e.name); return { value: { command: e.name } } })
  on('process.run', () => ok('[]'))
  on('ui.render', ($, e) => { const { Box } = $.ui.resolve(e); return <Box key="engine" /> })
  await $.session.start(START)
  const ui = await $.ui.mount({ plugin: 'regin-band', surface: 'terminal', component: 'AbovePrompt', props: BAND_PROPS })
  expect(await ui.find({ type: 'Text', text: /regin inbox/ })).toBeUndefined()
  await ui.unmount()
})
