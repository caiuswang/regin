import { writeFileSync } from 'node:fs'
import { join } from 'node:path'

import { tc, timeline } from './storyboard.mjs'

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])
// Words that are part of the picture rather than subtitles: title cards,
// the ident and logo, a card laid over a scene, the sentence typed on screen.
function cardText(shot) {
  const p = shot.params ?? {}
  if (shot.world === 'mark') return p.mode === 'ident' ? `${p.word ?? 'regin'} ${p.sub ?? '出品'}` : `regin / ${p.sub ?? '未来 AI 工作台'}`
  if (shot.world === 'sentence') return p.text
  if (shot.world === 'decide') return `${p.status ?? '已完成 · 已验证'} / ${p.title} / ${p.button ?? '合并'}`
  return [...(p.lines ?? []), ...(p.card?.lines ?? [])].map((l) => l.text).join(' / ')
}

const CSS = `
:root { color-scheme: dark; --bg: #07080b; --panel: #0e1015; --line: #1d212b; --ink: #e9e6df; --dim: #8b8f99; --warm: #f2b26b; --cool: #6fd3f5; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.75 "PingFang SC", system-ui, sans-serif; }
.wrap { max-width: 1480px; margin: 0 auto; padding: 56px 28px 120px; }
header { border-bottom: 1px solid var(--line); padding-bottom: 36px; margin-bottom: 40px; }
.kicker { color: var(--dim); letter-spacing: .18em; font-size: 12px; }
h1 { font: 400 64px/1.1 "Songti SC", serif; letter-spacing: .3em; margin: 18px 0 4px; }
.en { color: var(--dim); letter-spacing: .9em; font: 200 13px "Avenir Next", sans-serif; }
.meta { margin-top: 22px; color: var(--dim); font-size: 13px; letter-spacing: .06em; }
.logline { max-width: 820px; margin: 22px 0 0; color: #cfcac0; }
h2 { font: 400 26px "Songti SC", serif; letter-spacing: .2em; margin: 64px 0 22px; color: var(--warm); }
.script { background: var(--panel); border: 1px solid var(--line); border-radius: 14px; padding: 26px 30px; columns: 2 420px; column-gap: 48px; }
.script .act-name { color: var(--warm); font-size: 13px; letter-spacing: .2em; margin: 10px 0 6px; break-after: avoid; }
.script .row { display: grid; grid-template-columns: 64px 1fr; gap: 12px; break-inside: avoid; }
.script .t { color: var(--dim); font: 12px/2.2 Menlo, monospace; }
.script .card { color: var(--warm); }
.shot { display: grid; grid-template-columns: minmax(0, 1.9fr) minmax(300px, 1fr); gap: 28px; padding: 28px 0; border-top: 1px solid var(--line); }
.frames { display: grid; gap: 12px; }
figure { margin: 0; }
figure img { width: 100%; display: block; border-radius: 6px; background: #000; }
figcaption { color: var(--dim); font-size: 12px; margin-top: 6px; letter-spacing: .04em; }
.id { font: 12px Menlo, monospace; color: var(--dim); letter-spacing: .05em; }
.name { font: 400 22px "Songti SC", serif; letter-spacing: .12em; margin: 6px 0 14px; }
h3 { font-size: 12px; letter-spacing: .24em; color: var(--dim); margin: 16px 0 4px; font-weight: 400; }
p { margin: 0; }
ol { list-style: none; padding: 0; margin: 0; }
li { display: grid; grid-template-columns: 112px 1fr; gap: 10px; }
li .t { color: var(--dim); font: 12px/2.1 Menlo, monospace; }
.cardline { color: var(--warm); font: 18px "Songti SC", serif; letter-spacing: .12em; }
@media (max-width: 900px) { .shot { grid-template-columns: 1fr; } h1 { font-size: 44px; } }
`

function scriptRows(board, rows) {
  const out = []
  let act = null
  for (const { shot, start } of rows) {
    if (shot.act !== act) {
      act = shot.act
      out.push(`<div class="act-name">${esc(board.acts.find((a) => a.id === act)?.name)}</div>`)
    }
    const card = cardText(shot)
    if (card) out.push(`<div class="row"><span class="t">${tc(start)}</span><span class="card">【${shot.world === 'sentence' || shot.world === 'decide' ? '画面字' : '字卡'}】${esc(card)}</span></div>`)
    for (const l of shot.lines ?? []) {
      out.push(`<div class="row"><span class="t">${tc(start + l.in)}</span><span>${esc(l.text)}</span></div>`)
    }
  }
  return out.join('\n')
}

function shotBlock(manifest, { shot, start, end }, n) {
  const stills = manifest.shots[shot.id] ?? []
  const figs = stills.map((s) => `<figure><img loading="lazy" src="${esc(s.jpg)}" alt="${esc(shot.name)} ${esc(s.tag)}">` +
    `<figcaption>${tc(start + s.t)}${s.subtitle ? ` · 字幕「${esc(s.subtitle)}」` : ''}</figcaption></figure>`).join('')
  const lines = (shot.lines ?? []).map((l) =>
    `<li><span class="t">${tc(start + l.in)}–${tc(start + l.out)}</span><span>${esc(l.text)}</span></li>`).join('')
  const card = cardText(shot)
  return `<article class="shot" id="${esc(shot.id)}">
  <div class="frames">${figs || '<p class="id">（未渲染）</p>'}</div>
  <div class="notes">
    <div class="id">${String(n).padStart(2, '0')} · ${tc(start)}–${tc(end)} · ${shot.dur.toFixed(1)}s</div>
    <div class="name">${esc(shot.name)}</div>
    <h3>画面</h3><p>${esc(shot.picture)}</p>
    ${shot.camera ? `<h3>镜头</h3><p>${esc(shot.camera)}</p>` : ''}
    ${card ? `<h3>${shot.world === 'sentence' || shot.world === 'decide' ? '画面字' : '字卡'}</h3><p class="cardline">${esc(card)}</p>` : ''}
    ${lines ? `<h3>字幕</h3><ol>${lines}</ol>` : ''}
    ${shot.transition ? `<h3>转场</h3><p>${esc(shot.transition)}</p>` : ''}
  </div>
</article>`
}

export function writeReview(board, manifest, outDir) {
  const rows = timeline(board)
  const runtime = rows.at(-1).end
  const sections = []
  let act = null
  rows.forEach((row, i) => {
    if (row.shot.act !== act) {
      act = row.shot.act
      sections.push(`<h2>${esc(board.acts.find((a) => a.id === act)?.name)}</h2>`)
    }
    sections.push(shotBlock(manifest, row, i + 1))
  })
  const html = `<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>《${esc(board.title)}》分镜 · regin</title><style>${CSS}</style></head>
<body><div class="wrap">
<header>
  <div class="kicker">${esc(board.brand)} · 概念片分镜 · 画面与字幕（暂无配音）</div>
  <h1>${esc(board.title)}</h1>
  <div class="en">${esc(board.title_en)}</div>
  <div class="meta">片长 ${tc(runtime)} · ${rows.length} 个镜头 · 画幅 2.39:1 · 母版 3840×2160 · 每句字幕配一张定格</div>
  <p class="logline">${esc(board.logline)}</p>
</header>
<h2>文案全文</h2>
<div class="script">${scriptRows(board, rows)}</div>
${sections.join('\n')}
</div></body></html>`
  writeFileSync(join(outDir, 'storyboard.html'), html)
}
