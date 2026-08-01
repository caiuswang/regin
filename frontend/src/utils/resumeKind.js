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

// An unknown kind falls back to the conservative reading: a fresh trace is what
// an id regin has no run for gets, so it is also the safe thing to promise.
export const resumeKindLabel = (kind) => KINDS[kind] || KINDS.session
