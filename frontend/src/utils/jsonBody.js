// An agent handed an output schema answers with ONE JSON object — regin's own
// memory-curate skill ends its fan-out prompt with "OUTPUT exactly one JSON
// object, no prose" so step 6 can assemble the replies into a plan.json. Put
// that through a markdown renderer and 8 KB of payload reflows into a single
// paragraph. Every surface that shows an agent's body asks here instead.
export function prettyJson(text) {
  if (typeof text !== 'string') return ''
  const trimmed = text.trim()
  if (!trimmed.startsWith('{') && !trimmed.startsWith('[')) return ''
  try {
    const parsed = JSON.parse(trimmed)
    // A scalar is not a document: "123" and a bare quoted string parse fine and
    // gain nothing from a code block.
    if (!parsed || typeof parsed !== 'object') return ''
    return JSON.stringify(parsed, null, 2)
  } catch (e) {
    // Truncated by capture (`result_preview` is a 200-char cut with a trailing
    // ellipsis) — unparseable by construction. Leave it on the prose path.
    return ''
  }
}

// Markdown surfaces need it fenced. The fence is longer than any backtick run
// in the payload, so a run inside a string value cannot break out.
export function jsonFence(pretty) {
  const longestRun = (pretty.match(/`+/g) || []).reduce((n, r) => Math.max(n, r.length), 0)
  const fence = '`'.repeat(Math.max(3, longestRun + 1))
  return `${fence}json\n${pretty}\n${fence}`
}
