// Helpers shared by the rendered views. Markdown from a file is untrusted, so
// it always passes DOMPurify before any v-html (I2). The purifier is a local
// instance: its hooks must not leak into other DOMPurify users.
import DOMPurify from 'dompurify'
import { marked } from 'marked'

const purifier = DOMPurify(window)

// A scheme (`https:`, `data:`, `mailto:`), a protocol-relative `//host`, or a
// bare fragment is not a path inside the file's root.
const NOT_RELATIVE = /^(?:[a-z][a-z0-9+.-]*:|\/\/|#)/i

export function isRelativeRef(ref) {
  return !!ref && !NOT_RELATIVE.test(ref)
}

purifier.addHook('afterSanitizeAttributes', (node) => {
  // srcset and poster fetch outside the same-source path; nothing resolves them.
  node.removeAttribute('srcset')
  node.removeAttribute('poster')
  if (node.tagName === 'IMG') {
    const src = node.getAttribute('src')
    // Left in place, a relative src would be requested against regin's own
    // origin before the view could resolve it through the file's source.
    if (isRelativeRef(src)) {
      node.setAttribute('data-fv-src', src)
      node.removeAttribute('src')
    }
  } else if (node.tagName === 'A') {
    const href = node.getAttribute('href')
    if (isRelativeRef(href)) {
      // The raw href would open as a regin URL (or, as `/\\host`, off-site) on a
      // middle- or cmd-click; only the viewer's own resolution may follow it.
      node.setAttribute('data-fv-href', href)
      node.setAttribute('href', '#')
    } else if (/^https?:/i.test(href || '')) {
      node.setAttribute('target', '_blank')
      node.setAttribute('rel', 'noopener noreferrer')
    }
  }
})

export function sanitizeMarkdown(text) {
  // The output lands in regin's own page, not a frame: no forms (a fake
  // re-login posting off-site) and no inline style (a full-screen overlay).
  return purifier.sanitize(marked.parse(text || '', { breaks: false }), {
    ADD_ATTR: ['target'],
    FORBID_TAGS: ['form', 'input', 'button', 'textarea', 'select', 'style'],
    FORBID_ATTR: ['style'],
  })
}

function normalize(parts) {
  const out = []
  for (const part of parts) {
    if (part === '' || part === '.') continue
    if (part === '..') {
      if (out.length && out[out.length - 1] !== '..') out.pop()
      else out.push('..')
    } else {
      out.push(part)
    }
  }
  return out
}

function dirParts(path) {
  const parts = (path || '').split('/')
  parts.pop()
  return parts
}

// Resolve a relative reference found in the file `source` names, returning the
// source and `#L` anchor it points at, or null when it names nothing the same
// root can serve. A project path that climbs above the repo is refused here,
// so it is never fetched; a session path is absolute, and the server's R1
// check decides it. A span source has no directory to resolve against.
export function resolveRelative(source, ref) {
  if (!source?.path || !isRelativeRef(ref)) return null
  const hashAt = ref.indexOf('#')
  const anchor = hashAt >= 0 ? ref.slice(hashAt) : ''
  let target = hashAt >= 0 ? ref.slice(0, hashAt) : ref
  target = target.split('?')[0]
  try { target = decodeURIComponent(target) } catch { /* keep it as written */ }
  if (!target) return null

  const absolute = target.startsWith('/')
  const isSession = !!source.session
  if (absolute && !isSession) return null
  const parts = normalize([...(absolute ? [] : dirParts(source.path)), ...target.split('/')])
  if (!parts.length || parts[0] === '..') return null

  const next = { ...source, path: (isSession ? '/' : '') + parts.join('/') }
  delete next.span
  return { source: next, anchor: /^#L\d/.test(anchor) ? anchor : '' }
}

export const monacoTheme = (t) => (t === 'dark' ? 'vs-dark' : 'vs')
