// A terminal-kind launch lands on the session's detail page, wherever the form
// was opened from: the pane is reachable from there, and the trace is what the
// operator asked to follow.
export function terminalLaunchRoute({ traceId, kind }) {
  return kind === 'terminal' && traceId ? `/trace/sessions/${traceId}` : null
}
