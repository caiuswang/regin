// How a resumable session is described, for the picker row and for the launch
// sheet's summary of what was picked. Shared rather than written twice: the two
// surfaces sit side by side in one flow, and wording that drifted between them
// would read as two different distinctions.
//
// Both halves are stated on every row — what the session *is*, then what
// picking it does — because the effect alone ("same trace") names no referent:
// the picker lists sessions unrelated to whatever the card is open on.
const KINDS = {
  run: 'regin run · keeps its trace',
  session: 'terminal session · new trace',
}
// A terminal launch runs `claude --resume` under the CLI's own id, so a run
// continues as its child conversation and a terminal session keeps its trace.
const TERMINAL_KINDS = {
  run: 'regin run · reopens its conversation in a pane',
  session: 'terminal session · keeps its trace',
}

// An unknown kind falls back to the conservative reading: a fresh trace is what
// an id regin has no run for gets, so it is also the safe thing to promise.
export const resumeKindLabel = (kind, launchKind = 'sdk') => {
  const table = launchKind === 'terminal' ? TERMINAL_KINDS : KINDS
  return table[kind] || table.session
}
