import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { Inbox, InboxMessage } from '../types'

const PANE = 'regin-inbox'
const LIMIT = 200
const inbox = atom({ plugin: 'regin-band', key: 'inbox' } as const, null)

type Row = { id: number; title: string | null; body: string | null; msg_type: string; created_at: string }

export function parseInbox(stdout: string): Inbox | null {
  let rows: Row[]
  try {
    rows = JSON.parse(stdout)
  } catch {
    return null
  }
  if (!Array.isArray(rows)) return null
  const messages = rows.map((r): InboxMessage => ({
    id: r.id,
    title: r.title || (r.body ?? '').split('\n')[0]!.slice(0, 80),
    type: r.msg_type,
    createdAt: r.created_at,
  }))
  return { unread: messages.length, messages }
}

// Same resolution as regin-agents/bin/regin-mcp.sh: $REGIN_HOME, else ~/regin.
async function reginHome($: EngineInterface): Promise<string> {
  return (await $.env.get('REGIN_HOME')) || `${await $.env.get('HOME')}/regin`
}

async function regin($: EngineInterface, ...args: string[]) {
  const home = await reginHome($)
  return $.process.run([`${home}/.venv/bin/python`, `${home}/cli/regin.py`, ...args], { cwd: home })
}

// Refreshed on session start, at each turn's end and after an action rather
// than on a timer: the count only matters when the person is about to read it.
// The list's scope (non-test, non-dismissed) is the one `read --all` clears;
// `messages stats` also counts test rows, so it never reached 0 after the press.
async function refresh($: EngineInterface) {
  const { exitCode, stdout, isStdoutTruncated } = await regin(
    $, 'messages', 'list', '--unread', '--json', '--limit', String(LIMIT + 1),
  )
  const box = exitCode === 0 && !isStdoutTruncated ? parseInbox(stdout) : null
  await update($, inbox, (): Inbox | null => box)
}

async function openPane($: EngineInterface) {
  await refresh($)
  return $.ui.open({ id: PANE, title: 'regin inbox' })
}

async function markAllRead($: EngineInterface) {
  await regin($, 'messages', 'read', '--all')
  await refresh($)
}

function count(box: Inbox) {
  return box.unread > LIMIT ? `${LIMIT}+` : String(box.unread)
}

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    const started = await next(e)
    await $.command.register({ name: PANE, description: 'Show unread regin inbox messages in a pane' })
    await refresh($)
    return started
  })

  on('turn.complete', async ($, e, next) => {
    const done = await next(e)
    await refresh($)
    return done
  })

  on('command.run', { command: PANE }, async $ => {
    await openPane($)
    return { text: 'regin inbox opened.' }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const box = await read($, inbox)
    if (e.props.hasSurvey || box === null || box.unread === 0) return next(e)

    const { Box, Button, Text } = $.ui.resolve(e)
    return (
      <Box>
        <Text dimColor>regin inbox: {count(box)} unread </Text>
        <Button key="open-inbox" label="Open" onPress={() => void openPane($)} />
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Button, Text } = $.ui.resolve(e)
    const box = await read($, inbox)
    if (box === null) return <Text dimColor>regin inbox unavailable.</Text>
    if (box.unread === 0) return <Text dimColor>No unread messages.</Text>

    const room = Math.max(1, (e.viewport?.rows ?? 24) - 6)
    return (
      <Box flexDirection="column">
        <Box>
          <Text>{count(box)} unread </Text>
          <Button key="mark-all-read" label="Mark all read" onPress={() => markAllRead($)} />
        </Box>
        {box.messages.slice(0, room).map(m => (
          <Box key={`m${m.id}`}>
            <Text dimColor>{m.createdAt.slice(5, 16).replace('T', ' ')} {m.type.padEnd(8)} </Text>
            <Text wrap="truncate">{m.title}</Text>
          </Box>
        ))}
      </Box>
    )
  })
}
