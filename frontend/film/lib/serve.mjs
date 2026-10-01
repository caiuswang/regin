import { readFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { extname, join, normalize, sep } from 'node:path'

const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.json': 'application/json' }

// The stage is ES modules, which Chromium refuses over file://. Port 0: the
// OS picks a free one, so a render never collides with the dev or promo stacks.
// `post` receives the stage's uploads (the film's frames).
export function serveDir(root, { post } = {}) {
  const server = createServer(async (req, res) => {
    if (req.method === 'POST') {
      if (!post) { res.writeHead(405).end(); return }
      try { await post(req, res) } catch (e) { res.writeHead(500).end(String(e.message ?? e)) }
      return
    }
    const rel = normalize(decodeURIComponent(new URL(req.url, 'http://x').pathname)).replace(/^[/\\]+/, '')
    const path = join(root, rel || 'index.html')
    if (!path.startsWith(root + sep) && path !== root) { res.writeHead(403).end(); return }
    try {
      const body = await readFile(path)
      res.writeHead(200, { 'content-type': TYPES[extname(path)] ?? 'application/octet-stream', 'cache-control': 'no-store' })
      res.end(body)
    } catch {
      res.writeHead(404).end()
    }
  })
  return new Promise((ok) => server.listen(0, '127.0.0.1', () => ok({
    url: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise((done) => server.close(done)),
  })))
}
