// Runs in the page, both as an init script (every new document) and by
// `page.evaluate` after a card replaces the document. Idempotent. The subtitle
// text and cursor position live in sessionStorage so a full navigation
// re-renders them where they were instead of blanking a frame.
export function installOverlay() {
  if (window.__promo) return
  const KEY_SUB = 'promo_sub', KEY_X = 'promo_cx', KEY_Y = 'promo_cy'
  const store = {
    get(k, d) { try { return sessionStorage.getItem(k) ?? d } catch { return d } },
    set(k, v) { try { sessionStorage.setItem(k, v) } catch { /* opaque origin */ } },
  }
  let sub = null, cursor = null
  const state = {
    text: store.get(KEY_SUB, ''),
    x: Number(store.get(KEY_X, innerWidth * 0.62)),
    y: Number(store.get(KEY_Y, innerHeight * 0.55)),
  }

  function mount() {
    if (sub?.isConnected) return
    const root = document.documentElement
    sub = document.createElement('div')
    sub.id = 'promo-subtitle'
    Object.assign(sub.style, {
      position: 'fixed', left: '0', right: '0', bottom: '34px', zIndex: '2147483647',
      display: 'flex', justifyContent: 'center', pointerEvents: 'none',
    })
    const band = document.createElement('span')
    Object.assign(band.style, {
      font: '600 27px/1.45 "PingFang SC", "Hiragino Sans GB", sans-serif',
      color: '#fff', background: 'rgba(8, 10, 14, 0.82)', padding: '8px 22px',
      borderRadius: '8px', letterSpacing: '0.02em', maxWidth: '84vw', textAlign: 'center',
      boxShadow: '0 6px 24px rgba(0,0,0,0.35)',
    })
    sub.appendChild(band)
    cursor = document.createElement('div')
    cursor.id = 'promo-cursor'
    Object.assign(cursor.style, {
      position: 'fixed', left: '0', top: '0', width: '26px', height: '26px',
      marginLeft: '-13px', marginTop: '-13px', borderRadius: '50%', zIndex: '2147483647',
      pointerEvents: 'none', border: '3px solid #fff', background: 'rgba(255, 196, 0, 0.55)',
      boxShadow: '0 0 0 2px rgba(0,0,0,0.55), 0 2px 10px rgba(0,0,0,0.5)',
    })
    root.appendChild(sub)
    root.appendChild(cursor)
    render()
  }

  function render() {
    if (!sub) return
    sub.firstChild.textContent = state.text
    sub.style.visibility = state.text ? 'visible' : 'hidden'
    cursor.style.transform = `translate(${state.x}px, ${state.y}px)`
    cursor.style.display = document.body?.dataset.testid === 'promo-card' ? 'none' : ''
  }

  window.__promo = {
    setSub(text) { state.text = text; store.set(KEY_SUB, text); mount(); render() },
    moveTo(x, y) { state.x = x; state.y = y; store.set(KEY_X, x); store.set(KEY_Y, y); mount(); render() },
    cursorCentre() {
      mount()
      const r = cursor.getBoundingClientRect()
      return { x: r.left + r.width / 2, y: r.top + r.height / 2 }
    },
    glide(x, y, steps) {
      const x0 = state.x, y0 = state.y
      return new Promise((done) => {
        let i = 0
        const tick = () => {
          i += 1
          const t = i / steps, e = t < 0.5 ? 2 * t * t : 1 - (-2 * t + 2) ** 2 / 2
          window.__promo.moveTo(x0 + (x - x0) * e, y0 + (y - y0) * e)
          if (i < steps) requestAnimationFrame(tick); else done()
        }
        requestAnimationFrame(tick)
      })
    },
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount, { once: true })
  } else {
    mount()
  }
}

export function cardHtml({ title, caption }) {
  const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
  return `<!doctype html><html><head><meta charset="utf-8"><style>
    html,body{margin:0;height:100%;background:#0b0e14;color:#e8ecf3}
    body{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:28px;
      font-family:"PingFang SC","Hiragino Sans GB",sans-serif}
    .mark{font:700 96px/1 ui-monospace,"SF Mono",Menlo,monospace;letter-spacing:.04em;color:#fff}
    .mark span{color:#f5c400}
    h1{margin:0;font-weight:600;font-size:40px;letter-spacing:.04em;color:#cfd6e3}
    p{margin:0;font:500 26px/1.4 ui-monospace,"SF Mono",Menlo,monospace;color:#8b95a7}
  </style></head><body data-testid="promo-card">
    <div class="mark">regin<span>.</span></div>
    <h1>${esc(title)}</h1>
    ${caption ? `<p>${esc(caption)}</p>` : ''}
  </body></html>`
}
