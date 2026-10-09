// InlineText is the prototype's segment grammar: **bold**, `code`,
// [label](to) and *emphasis* (rendered plain, as the prototype does).
const SEG_RE = /(`[^`]+`|\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\)|\*[^*]+\*)/g
const LINK_RE = /^\[([^\]]+)\]\(([^)]+)\)$/

// The prototype is two static files linked by hash routes; the site uses
// history routes (G3).
export function siteHref(href) {
  if (href === 'Regin Landing.dc.html') return '/'
  return href.replace(/^Regin Docs\.dc\.html#/, '')
}

export function segments(text) {
  return text.split(SEG_RE).filter(Boolean).map((t) => {
    if (t.startsWith('`')) return { kind: 'code', t: t.slice(1, -1) }
    if (t.startsWith('**')) return { kind: 'bold', t: t.slice(2, -2) }
    const link = t.match(LINK_RE)
    if (link) return { kind: 'link', t: link[1], href: siteHref(link[2]) }
    if (t.startsWith('*') && t.endsWith('*') && t.length > 2) return { kind: 'plain', t: t.slice(1, -1) }
    return { kind: 'plain', t }
  })
}
