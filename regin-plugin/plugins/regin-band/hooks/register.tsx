import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Inbox } from '../types'

const REGIN = '/Users/taowang/regin'
const inbox = atom({ plugin: 'regin-band', key: 'inbox' } as const, null)

export function parseUnread(stdout: string): number | null {
  const m = /^unread:\s+(\d+)/m.exec(stdout)
  return m ? Number(m[1]) : null
}

// Refreshed on session start and at each turn's end rather than on a timer:
// the count only matters when the person is about to read the prompt.
async function refresh($: EngineInterface) {
  const { exitCode, stdout } = await $.process.run(
    [`${REGIN}/.venv/bin/python`, `${REGIN}/cli/regin.py`, 'messages', 'stats'],
    { cwd: REGIN },
  )
  const unread = exitCode === 0 ? parseUnread(stdout) : null
  await update($, inbox, (): Inbox | null => (unread === null ? null : { unread }))
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await refresh($)
    return started
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    await refresh($)
    return done
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const box = await read($, inbox)
    if (e.props.hasSurvey || box === null || box.unread === 0) return next(e)

    const { Box, Text } = $.ui.resolve(e)
    return (
      <Box>
        <Text dimColor>regin inbox: {box.unread} unread</Text>
      </Box>
    )
  })
}
