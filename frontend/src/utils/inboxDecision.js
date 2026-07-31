// Recover the choices a parked agent offered from the flattened message body.
//
// Only `_format_question` (lib/agent_messages/event_notify.py) lists options,
// and it emits exactly one shape: a "• " bullet per option. Recovery is
// therefore restricted to that glyph. Accepting `-`/`*`/`+` as well looked
// more permissive but was actively wrong: `_format_permission` puts arbitrary
// operator text on line 1, so a permission body listing shell steps turned
// `- npm publish` into a selectable "answer", and a `* * *` rule became an
// option labelled "* *".
//
// Lines inside a fenced block are skipped even so, since a quoted command may
// legitimately use "•" in output.
const OPTION_BULLET = /^\s*•\s+(.*\S)\s*$/

export function parseDecisionBody(body) {
  const options = []
  const prose = []
  let fenced = false
  for (const line of (body || '').split('\n')) {
    if (/^\s*(```|~~~)/.test(line)) { fenced = !fenced; prose.push(line); continue }
    const bullet = fenced ? null : OPTION_BULLET.exec(line)
    if (bullet) options.push(bullet[1])
    else prose.push(line)   // blank lines included: dropping them merged paragraphs
  }
  return { options, prose: prose.join('\n').trim() }
}

// True when the panel recovered real choices and so speaks for the body; the
// detail pane suppresses its markdown only in that case.
export function decisionOwnsBody(message) {
  if (!message || message.msg_key === 'plan-pending') return false
  return parseDecisionBody(message.body).options.length > 0
}
