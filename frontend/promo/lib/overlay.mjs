// The camera's own layer, installed into the page: subtitle band, pointer,
// title card, and the freeze plate a dissolve fades out. The plate is the
// top of the layer and is a still of the whole frame, subtitle and pointer
// included: the live ones beneath it are the same pixels, so neither ever
// blinks while it is up or fading. It sits on <html>,
// outside <body>, so the app never re-renders it and the camera's zoom (a
// transform on <body>) never scales it. Runs as an init script on every new
// document and again by `page.evaluate`; idempotent.
//
// Every animation is a Web Animation, so each move resolves on its own
// `finished` promise rather than on a guessed delay.
export function installOverlay(boot) {
  if (window.__promo) return
  const SUB_FADE = 180
  const TEXT_FADE = 110
  const TIP = { x: 5, y: 3 }
  let layer = null, plate = null, card = null, sub = null, band = null, cursor = null
  const state = { x: innerWidth * 0.62, y: innerHeight * 0.55, cam: { k: 1, tx: 0, ty: 0 } }
  let bootCard = null
  try {
    if (boot?.card && !sessionStorage.getItem('promo_booted')) bootCard = boot.card
    sessionStorage.setItem('promo_booted', '1')
  } catch { /* about:blank has an opaque origin */ }

  const css = `
    html{overflow:hidden!important}
    #promo-layer{position:fixed;inset:0;pointer-events:none;z-index:2147483647}
    #promo-layer *{pointer-events:none}
    #promo-plate{position:absolute;inset:0;width:100%;height:100%;opacity:0}
    #promo-card{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;
      justify-content:center;gap:22px;background:radial-gradient(120% 90% at 50% 38%,#151b27 0%,#0b0e14 62%);
      color:#e8ecf3;font-family:"PingFang SC","Hiragino Sans GB",sans-serif;opacity:0}
    #promo-card .mark{font:700 84px/1 ui-monospace,"SF Mono",Menlo,monospace;letter-spacing:.03em;color:#fff}
    #promo-card .mark span{color:#f5c400}
    #promo-card h1{margin:0;font-weight:600;font-size:34px;letter-spacing:.06em;color:#cfd6e3}
    #promo-card p{margin:6px 0 0;padding:8px 18px;border:1px solid #2a3243;border-radius:10px;
      font:500 22px/1.2 ui-monospace,"SF Mono",Menlo,monospace;color:#9aa6ba;background:#10151f}
    #promo-card p::before{content:"$ ";color:#5b6679}
    #promo-sub{position:absolute;left:0;right:0;bottom:30px;display:flex;justify-content:center;opacity:0}
    #promo-sub span{font:600 24px/1.5 "PingFang SC","Hiragino Sans GB",sans-serif;color:#fff;
      background:rgba(7,9,13,.86);padding:7px 20px;border-radius:10px;letter-spacing:.03em;
      max-width:86vw;text-align:center;box-shadow:0 8px 28px rgba(0,0,0,.4)}
    #promo-sub b{font-weight:inherit}
    #promo-cursor{position:absolute;left:0;top:0;width:30px;height:34px;will-change:transform}
    #promo-cursor svg{position:absolute;left:0;top:0;filter:drop-shadow(0 2px 4px rgba(0,0,0,.55))}
    #promo-cursor i{position:absolute;left:${TIP.x - 18}px;top:${TIP.y - 18}px;width:36px;height:36px;
      border-radius:50%;border:2px solid rgba(255,208,64,.95);opacity:0}
  `
  const arrow = '<svg width="30" height="34" viewBox="0 0 30 34"><path d="M5 3 L5 27 L11.2 21.4 L15.4 30.6 ' +
    'L19.6 28.7 L15.5 19.7 L23.6 19.4 Z" fill="#fff" stroke="#10131a" stroke-width="1.6" stroke-linejoin="round"/></svg>'

  function mount() {
    if (layer?.isConnected) return
    const root = document.documentElement
    if (!root) return
    const style = document.createElement('style')
    style.textContent = css
    layer = document.createElement('div')
    layer.id = 'promo-layer'
    layer.innerHTML =
      '<div id="promo-card"><div class="mark">regin<span>.</span></div><h1></h1><p hidden></p></div>' +
      `<div id="promo-sub"><span><b></b></span></div><div id="promo-cursor">${arrow}<i></i></div>` +
      '<img id="promo-plate" alt="">'
    layer.prepend(style)
    root.appendChild(layer)
    plate = layer.querySelector('#promo-plate')
    card = layer.querySelector('#promo-card')
    sub = layer.querySelector('#promo-sub')
    band = sub.querySelector('b')
    cursor = layer.querySelector('#promo-cursor')
    place()
    if (bootCard) { fillCard(bootCard); card.style.opacity = '1'; cursor.style.opacity = '0'; bootCard = null }
  }

  function place() {
    cursor.style.transform = `translate(${state.x - TIP.x}px, ${state.y - TIP.y}px)`
  }

  function fillCard(c) {
    card.querySelector('h1').textContent = c.title ?? ''
    const p = card.querySelector('p')
    p.textContent = c.caption ?? ''
    p.hidden = !c.caption
  }

  const shown = (el) => Number(getComputedStyle(el).opacity) > 0

  async function fade(el, to, ms) {
    const from = getComputedStyle(el).opacity
    if (Number(from) === to) return
    const a = el.animate([{ opacity: from }, { opacity: String(to) }], { duration: ms, easing: 'ease-in-out', fill: 'forwards' })
    await a.finished
    el.style.opacity = String(to)
    a.cancel()
  }

  const frames = (n) => new Promise((done) => {
    const tick = () => { n -= 1; if (n <= 0) done((performance.timeOrigin + performance.now()) / 1000); else requestAnimationFrame(tick) }
    requestAnimationFrame(tick)
  })

  const camTransform = ({ k, tx, ty }) =>
    (k === 1 && tx === 0 && ty === 0 ? 'none' : `translate(${tx}px, ${ty}px) scale(${k})`)

  window.__promo = {
    // Resolves, in epoch seconds, once `n` more frames have been produced.
    frames,
    // A new line while the band is up swaps only its text, so consecutive
    // lines of one scene never blink the band off and on.
    async sub(text) {
      mount()
      if (!text) return fade(sub, 0, SUB_FADE)
      if (shown(sub)) {
        await fade(band, 0, TEXT_FADE)
        band.textContent = text
        await fade(band, 1, TEXT_FADE)
      } else {
        band.textContent = text
        band.style.opacity = '1'
        await fade(sub, 1, SUB_FADE)
      }
    },
    subLayers: () => document.querySelectorAll('#promo-sub').length,
    // The band's box while a line shows, in CSS pixels: where a frame check
    // looks for the line.
    subBox() {
      const r = sub.querySelector('span').getBoundingClientRect()
      return { x: r.left, y: r.top, w: r.width, h: r.height }
    },
    async card(c, ms) {
      mount()
      if (c) {
        fillCard(c)
        await Promise.all([fade(card, 1, ms), fade(cursor, 0, ms)])
      } else {
        await Promise.all([fade(card, 0, ms), fade(cursor, 1, ms)])
      }
    },
    cardUp: () => { mount(); return shown(card) },
    async freeze(url) {
      mount()
      plate.src = url
      await plate.decode()
      plate.style.opacity = '1'
    },
    async thaw(ms) {
      await fade(plate, 0, ms)
      plate.removeAttribute('src')
    },
    hotspot() {
      mount()
      const r = cursor.getBoundingClientRect()
      return { x: r.left + TIP.x, y: r.top + TIP.y }
    },
    async glide(x, y) {
      mount()
      const dist = Math.hypot(x - state.x, y - state.y)
      if (dist < 1) return
      const from = `translate(${state.x - TIP.x}px, ${state.y - TIP.y}px)`
      const to = `translate(${x - TIP.x}px, ${y - TIP.y}px)`
      state.x = x; state.y = y
      const ms = Math.round(Math.min(950, Math.max(420, 260 + dist * 0.9)))
      const a = cursor.animate([{ transform: from }, { transform: to }],
        { duration: ms, easing: 'cubic-bezier(.5,0,.2,1)', fill: 'forwards' })
      await a.finished
      place()
      a.cancel()
    },
    async ripple() {
      const ring = cursor.querySelector('i')
      const a = ring.animate([{ opacity: 0.95, transform: 'scale(.35)' }, { opacity: 0, transform: 'scale(1.25)' }],
        { duration: 340, easing: 'ease-out' })
      await a.finished
    },
    // Zooms the page (a transform on <body>) so `el` is centred at scale k —
    // or, with `left`, so its left edge sits a margin in from the frame's; a
    // null `el` returns to the full frame. Clamped so no edge of the page
    // ever comes into the frame. The pointer rides along, staying on the
    // spot of the page it rested on.
    async camera(el, k, ms, left = false) {
      const cur = state.cam
      let next = { k: 1, tx: 0, ty: 0 }
      if (el) {
        const r = el.getBoundingClientRect()
        const u = { x: (r.left - cur.tx) / cur.k, y: (r.top - cur.ty) / cur.k, w: r.width / cur.k, h: r.height / cur.k }
        const W = innerWidth, H = innerHeight
        const clamp = (v, lo) => Math.min(0, Math.max(lo, v))
        const tx = left ? 48 - k * u.x : W / 2 - k * (u.x + u.w / 2)
        next = { k, tx: clamp(tx, W - k * W), ty: clamp(H / 2 - k * (u.y + u.h / 2), H - k * H) }
      }
      const body = document.body
      body.style.transformOrigin = '0 0'
      const easing = 'cubic-bezier(.45,0,.15,1)'
      const px = next.tx + next.k * (state.x - cur.tx) / cur.k, py = next.ty + next.k * (state.y - cur.ty) / cur.k
      const a = body.animate([{ transform: camTransform(cur) }, { transform: camTransform(next) }],
        { duration: ms, easing, fill: 'forwards' })
      const b = cursor.animate([{ transform: `translate(${state.x - TIP.x}px, ${state.y - TIP.y}px)` },
        { transform: `translate(${px - TIP.x}px, ${py - TIP.y}px)` }], { duration: ms, easing, fill: 'forwards' })
      await Promise.all([a.finished, b.finished])
      body.style.transform = camTransform(next)
      state.x = px; state.y = py
      place()
      a.cancel(); b.cancel()
      state.cam = next
    },
    cameraReset() { document.body.style.transform = 'none'; state.cam = { k: 1, tx: 0, ty: 0 } },
    zoomed: () => state.cam.k !== 1,
  }
  if (document.documentElement) mount()
  else {
    new MutationObserver((_, obs) => { if (document.documentElement) { obs.disconnect(); mount() } })
      .observe(document, { childList: true })
  }
  document.addEventListener('DOMContentLoaded', mount, { once: true })
}
