import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { chromium } from 'playwright'

import { serveDir } from './serve.mjs'

const FILM_DIR = join(dirname(fileURLToPath(import.meta.url)), '..')
export const SIZES = { '4k': [3840, 2160], '1080': [1920, 1080] }
// expose-gc: the film's frames are uploaded as Blobs, see main.js.
const GPU_ARGS = ['--use-angle=metal', '--enable-gpu', '--ignore-gpu-blocklist', '--js-flags=--expose-gc']

// The stage in headless Chromium on the GPU, served from the film directory.
export async function openStage(size, { post } = {}) {
  const server = await serveDir(FILM_DIR, { post })
  const browser = await chromium.launch({ headless: true, args: GPU_ARGS })
  const page = await browser.newPage({ viewport: { width: 1280, height: 720 } })
  const errors = []
  page.on('pageerror', (e) => errors.push(`pageerror: ${e.message}`))
  page.on('console', (m) => { if (m.type() === 'error') errors.push(`console: ${m.text()}`) })
  const [W, H] = SIZES[size]
  await page.goto(`${server.url}/stage/index.html?w=${W}&h=${H}`)
  await page.waitForFunction(() => window.film, null, { timeout: 30_000 }).catch(() => {
    throw new Error(`stage did not start:\n${errors.join('\n')}`)
  })
  const renderer = await page.evaluate(() => {
    const gl = document.createElement('canvas').getContext('webgl2')
    const ext = gl.getExtension('WEBGL_debug_renderer_info')
    return gl.getParameter(ext ? ext.UNMASKED_RENDERER_WEBGL : gl.RENDERER)
  })
  if (/SwiftShader/i.test(renderer)) console.warn(`warning: software WebGL (${renderer}); frames are correct but slow`)
  const close = async () => { await browser.close(); await server.close() }
  return { page, errors, close, renderer, url: server.url, W, H }
}
