/**
 * The Terminal tab end to end: xterm ↔ WebSocket ↔ a PTY on a real tmux pane.
 *
 * Nothing here is mocked below the browser. The spec stands up its own tmux
 * server on a scratch socket, runs `node` in it (an allowlisted
 * `pane_current_command`, so `delivery._verify_identity` passes for real),
 * registers that pane in `bridge_panes`, and then drives the UI. What it
 * pins is what a mock could not:
 *
 *   - typed bytes reach the pane and its echo comes back to xterm,
 *   - the browser's geometry reaches the tmux client,
 *   - the client lands on a SEPARATE, grouped session rather than joining the
 *     operator's, and
 *   - leaving the tab reaps it — no orphaned client per page view.
 *
 * Serial: one tmux server and one pane row are shared by the whole file.
 */
import { execFileSync } from 'node:child_process'
import { randomUUID } from 'node:crypto'
import { join } from 'node:path'
import { test, expect } from './auth-fixture.js'
import { SCRATCH_DIR, TEST_USER } from '../e2e-env.js'
import { API_BASE } from './helpers/api-base.js'
import { selectViewMode } from './helpers/viewMode.js'

test.describe.configure({ mode: 'serial' })

const SOCKET = join(SCRATCH_DIR, 'tmux-e2e.sock')
const TARGET = 'e2e-target'
const PY = join(process.cwd(), '..', '.venv', 'bin', 'python')

const traceId = randomUUID()

// `-f /dev/null`: the throwaway server must not read the developer's
// ~/.tmux.conf. A `default-shell` there replaces the pane command outright,
// which makes `pane_current_command` the login shell and the identity guard
// refuse — a real 40-minute detour the first time this spec ran.
function tmux(...args) {
  return execFileSync('tmux', ['-f', '/dev/null', '-S', SOCKET, ...args],
    { encoding: 'utf8' }).trim()
}

function killServer() {
  try { tmux('kill-server') } catch { /* already gone */ }
}

test.beforeAll(async ({ request }) => {
  const { token } = await (await request.post(
    `${API_BASE}/api/auth/login`, { data: TEST_USER })).json()
  // The trace page needs a session to render at all; one prompt span is it.
  const seeded = await request.post(`${API_BASE}/api/session-spans`, {
    headers: { Authorization: `Bearer ${token}` },
    data: [{
      trace_id: traceId, span_id: `prompt-${traceId.slice(0, 8)}`,
      parent_id: null, name: 'prompt',
      start_time: '2026-05-03T10:00:00', duration_ms: 0,
      attributes: { text: 'open a terminal on my pane', is_test: true },
    }],
  })
  expect(seeded.ok()).toBeTruthy()

  killServer()
  // An echoing `node` — allowlisted as a pane command, and its tty echo is
  // what makes a keystroke observable without the pane interpreting it.
  tmux('new-session', '-d', '-s', TARGET, '-x', '80', '-y', '24',
    'node -e "process.stdin.resume()"')
  const [paneId, serverPid, panePid] =
    tmux('display-message', '-p', '-t', TARGET,
      '#{pane_id}\t#{pid}\t#{pane_pid}').split('\t')
  execFileSync(PY, ['-c', `
from lib.orm.engine import get_connection
conn = get_connection()
conn.execute(
    "INSERT INTO bridge_panes (trace_id, pane_id, tmux_server_pid, pane_pid, "
    "tmux_socket, reachable, cwd) VALUES (?, ?, ?, ?, ?, 1, '/work')",
    (${JSON.stringify(traceId)}, ${JSON.stringify(paneId)},
     ${Number(serverPid)}, ${Number(panePid)}, ${JSON.stringify(SOCKET)}))
conn.commit()
conn.close()
`], { cwd: join(process.cwd(), '..'), encoding: 'utf8' })
})

test.afterAll(killServer)

// The grouped sessions regin opened, as `<name> <cols>x<rows>` of their
// client — the client size IS the browser's geometry, arriving over the
// socket as a resize control frame.
function groupedClients() {
  return tmux('list-clients', '-F',
    '#{client_session} #{client_width}x#{client_height}')
    .split('\n')
    .filter(line => line.startsWith('regin-term-'))
}

function sessionNames() {
  return tmux('list-sessions', '-F', '#{session_name}').split('\n')
}

async function takeControl(page) {
  await page.getByRole('button', { name: 'Take control' }).click()
  await expect(page.getByRole('button', { name: 'Release control' })).toBeVisible()
}

async function openTerminalTab(page) {
  await page.goto(`/trace/sessions/${traceId}`)
  await selectViewMode(page, 'Terminal')
  await expect(page.locator('[data-terminal-pane] .xterm-screen')).toBeVisible()
  // The strip renders only while the socket is NOT open, and carries the
  // server's refusal reason — so its absence is the assertion, not the text.
  await expect(page.getByTestId('terminal-status')).toHaveCount(0)
}

test('read-only by default: typing changes nothing until control is taken', async ({ page }) => {
  await openTerminalTab(page)
  await expect(page.getByText('Read-only: live output, no input.')).toBeVisible()
  await page.locator('[data-terminal-pane] .xterm-helper-textarea').focus()
  await page.keyboard.type('must-not-arrive')
  await page.waitForTimeout(1000)
  expect(tmux('capture-pane', '-p', '-t', TARGET)).not.toContain('must-not-arrive')
})

test('typed bytes reach the pane and its echo comes back', async ({ page }) => {
  await openTerminalTab(page)
  await takeControl(page)
  await page.keyboard.type('regin-echo-probe')
  await expect(page.locator('[data-terminal-pane]'))
    .toContainText('regin-echo-probe', { timeout: 10_000 })
})

test('the client gets its own grouped session, not the target', async ({ page }) => {
  await openTerminalTab(page)
  await expect.poll(() => groupedClients().length, { timeout: 10_000 }).toBe(1)
  // The target keeps existing as its own session, with no client of its own:
  // regin joined a session it made, not the one the operator would be in.
  expect(sessionNames()).toContain(TARGET)
  expect(groupedClients()[0].split(' ')[0]).not.toBe(TARGET)
})

test('the browser geometry propagates to the tmux client', async ({ page }) => {
  // Height, not width: below `lg` the trace chrome drops its rails and the
  // pane gets WIDER, so a narrower viewport is not a narrower terminal.
  // Follow THIS page's client by its session name: the previous test's client
  // shares the socket until its reap lands, and list order is not ours.
  // 1400, not 1000: the pane sits ~470px down and clamps at a ~29-row minimum,
  // so at 1000 it clears the clamp by two rows — any extra chrome above it on
  // the shared server (a notification banner) makes 1000 and 500 the same.
  const clients = () => groupedClients().map(line => line.split(' '))
  const before = new Set(clients().map(([name]) => name))
  await page.setViewportSize({ width: 1280, height: 1400 })
  await openTerminalTab(page)
  let mine
  await expect.poll(() => {
    mine = clients().find(([name]) => !before.has(name))?.[0]
    return mine
  }, { timeout: 10_000 }).toBeTruthy()
  const sizeOfMine = () => clients().find(([name]) => name === mine)?.[1]
  const rowsOf = (size) => Number(size?.split('x')[1])
  // The client is born at the connect URL's geometry, which predates the
  // page's settled layout — the resize to it lands later. Read `tall` once the
  // client has caught up with the page's own terminal, not at first sight.
  const pageRows = () => page.locator('[data-terminal-pane] .xterm-rows')
    .evaluate(el => el.children.length)
  await expect.poll(async () => rowsOf(sizeOfMine()) === await pageRows(),
    { timeout: 10_000 }).toBe(true)
  const tall = sizeOfMine()

  await page.setViewportSize({ width: 1280, height: 500 })
  await expect.poll(sizeOfMine, { timeout: 10_000 }).not.toBe(tall)
  expect(rowsOf(sizeOfMine())).toBeLessThan(rowsOf(tall))
})

test('the pane terminal leaves no orphaned tmux client behind', async ({ page }) => {
  await openTerminalTab(page)
  await expect.poll(() => groupedClients().length, { timeout: 10_000 }).toBe(1)
  // Leaving the tab unmounts the component, which closes the socket; the
  // route reaps the PTY child and kills the session it created.
  await selectViewMode(page, 'Conversation')
  await expect.poll(() => groupedClients().length, { timeout: 10_000 }).toBe(0)
  await expect.poll(() => sessionNames().filter(n => n.startsWith('regin-term-')).length,
    { timeout: 10_000 }).toBe(0)
})

// ── The terminal must behave like a terminal, not like a page ────────

test('the span detail rail does not steal the width, or appear uncloseable', async ({ page }) => {
  // Select a span on the Log tab first: that is what used to leave the rail
  // pinned open on the terminal, where its close control is not rendered.
  // At the default 720px the Log card is ~195px tall under a ~140px sticky
  // filter bar, so a little extra chrome scrolls the row beneath the bar and
  // the click never lands.
  await page.setViewportSize({ width: 1280, height: 1000 })
  await page.goto(`/trace/sessions/${traceId}`)
  await selectViewMode(page, 'Log')
  await page.locator('tr[data-span-id]').first().click()
  await expect(page.locator('.trace-detail-rail')).toBeVisible()

  await selectViewMode(page, 'Terminal')
  await expect(page.locator('.trace-detail-rail')).toHaveCount(0)
})

test('control is what hands the keyboard over, and giving it back restores the chords', async ({ page }) => {
  const PANEL = '[role="dialog"][aria-label="Full-text search"]'
  await openTerminalTab(page)

  // Read-only: the app still owns its chords.
  await page.keyboard.press('ControlOrMeta+k')
  await expect(page.locator(PANEL)).toBeVisible()
  await page.keyboard.press('Escape')
  await expect(page.locator(PANEL)).toHaveCount(0)

  // In control: the pane owns them — a terminal that eats ⌘K is not one.
  await takeControl(page)
  await page.keyboard.press('ControlOrMeta+k')
  await expect(page.locator(PANEL)).toHaveCount(0)

  // And the way back out is the same button, not a mystery.
  await page.getByRole('button', { name: 'Release control' }).click()
  await page.keyboard.press('ControlOrMeta+k')
  await expect(page.locator(PANEL)).toBeVisible()
})

test('the full-page terminal opens chromeless, in control, and attaches', async ({ page }) => {
  await openTerminalTab(page)
  await page.getByRole('link', { name: /Open full page/ }).click()
  await expect(page).toHaveURL(new RegExp(`/terminal/${traceId}$`))

  // Chromeless: no app sidebar competing for width or keyboard.
  await expect(page.locator('.sidebar')).toHaveCount(0)
  await expect(page.getByTestId('terminal-status')).toHaveCount(0)
  await expect(page.locator('[data-terminal-pane] .xterm-screen')).toBeVisible()
  await expect.poll(() => groupedClients().length, { timeout: 10_000 }).toBe(1)

  // In control already: you came here to operate the pane, not to read it.
  await expect(page.getByRole('button', { name: 'Release control' })).toBeVisible()
  await page.keyboard.type('full-page-probe')
  await expect(page.locator('[data-terminal-pane]'))
    .toContainText('full-page-probe', { timeout: 10_000 })
})

test('a direct deep link lands in control and typing works immediately', async ({ page }) => {
  await page.goto(`/terminal/${traceId}`)
  await expect(page.locator('[data-terminal-pane] .xterm-screen')).toBeVisible()
  await expect(page.getByTestId('terminal-status')).toHaveCount(0)
  await expect(page.getByRole('link', { name: '← Trace' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Release control' })).toBeVisible()

  await page.keyboard.type('deep-link-probe')
  await expect(page.locator('[data-terminal-pane]'))
    .toContainText('deep-link-probe', { timeout: 10_000 })

  // And the way out is still there: releasing gives the app its chords back.
  await page.getByRole('button', { name: 'Release control' }).click()
  await expect(page.getByRole('button', { name: 'Take control' })).toBeVisible()
})

test('the in-trace tab still starts read-only, where the chords matter', async ({ page }) => {
  await openTerminalTab(page)
  await expect(page.getByRole('button', { name: 'Take control' })).toBeVisible()
  await expect(page.getByText('Read-only: live output, no input.')).toBeVisible()
})

