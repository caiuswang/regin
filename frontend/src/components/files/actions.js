// The toolbar's non-visual half (R3): what each action produces, kept apart
// from the buttons so the binary card's Download and the toolbar's are one.
import api from '../../api'
import { rawUrl } from './source'

export const TEXT_BACKED = new Set(['code', 'markdown', 'html', 'svg', 'csv', 'notebook'])

// `vscode://file/` opens the path on the browser's machine, which is the
// server's only when the browser is local (D9). `[::1]` is how a browser
// reports the IPv6 loopback in `location.hostname`.
const LOOPBACK = new Set(['localhost', '127.0.0.1', '::1', '[::1]'])

export function isLoopbackHost(hostname = window.location.hostname) {
  return LOOPBACK.has(hostname)
}

export function vscodeHref(absPath, line) {
  const path = absPath.split('/').map(encodeURIComponent).join('/')
  return `vscode://file${path.startsWith('/') ? '' : '/'}${path}:${line || 1}`
}

export function lineAnchor(lines) {
  if (!lines) return ''
  return lines.start === lines.end ? `#L${lines.start}` : `#L${lines.start}-L${lines.end}`
}

// The raw route is behind a bearer token, which a plain <a href> cannot send.
// The blob URL lives until the next download: revoking it right after the
// click can cancel a download the browser has not started reading yet.
let lastUrl = ''

export async function downloadFile(source, name) {
  const url = await api.getBlobUrl(rawUrl(source, { download: '1' }))
  if (lastUrl) URL.revokeObjectURL(lastUrl)
  lastUrl = url
  const a = document.createElement('a')
  a.href = url
  a.download = name
  a.click()
}
