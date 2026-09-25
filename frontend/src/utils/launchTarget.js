// A terminal-kind launch lands on the browser terminal attached to its pane,
// wherever the form was opened from: the TUI is only reachable there.
export function terminalRoute({ traceId, kind }) {
  return kind === 'terminal' && traceId ? `/terminal/${traceId}` : null
}
