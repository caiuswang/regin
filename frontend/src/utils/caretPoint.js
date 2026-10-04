// Where a character offset of a textarea sits on screen.
//
// A textarea exposes no geometry for its own text, so a hidden mirror copies
// its box and text metrics, holds the text up to the offset, and measures a
// marker placed there: the technique of component/textarea-caret-position.

const MIRRORED = [
  'direction', 'boxSizing',
  'borderTopWidth', 'borderRightWidth', 'borderBottomWidth', 'borderLeftWidth', 'borderStyle',
  'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft',
  'fontStyle', 'fontVariant', 'fontWeight', 'fontStretch', 'fontSize', 'fontSizeAdjust',
  'fontFamily', 'fontFeatureSettings', 'lineHeight',
  'textAlign', 'textTransform', 'textIndent', 'letterSpacing', 'wordSpacing', 'tabSize',
  'wordBreak', 'overflowWrap',
]

const px = (value) => Number.parseFloat(value) || 0

/** `{ left, top, height }` of the line box at `index` of `el`, in viewport pixels. */
export function caretPoint(el, index) {
  const style = window.getComputedStyle(el)
  const mirror = document.createElement('div')
  for (const prop of MIRRORED) mirror.style[prop] = style[prop]
  // `clientWidth` leaves out a vertical scrollbar, which narrows the lines
  // the textarea actually wraps.
  const borders = px(style.borderLeftWidth) + px(style.borderRightWidth)
  Object.assign(mirror.style, {
    position: 'absolute', top: '0', left: '-9999px', visibility: 'hidden',
    boxSizing: 'border-box', width: `${el.clientWidth + borders}px`, height: 'auto',
    overflow: 'hidden', whiteSpace: 'pre-wrap',
  })
  mirror.textContent = el.value.slice(0, index)
  const marker = document.createElement('span')
  marker.textContent = el.value.slice(index) || '.'
  mirror.appendChild(marker)
  document.body.appendChild(mirror)
  const box = el.getBoundingClientRect()
  const point = {
    left: box.left + px(style.borderLeftWidth) + marker.offsetLeft - el.scrollLeft,
    top: box.top + px(style.borderTopWidth) + marker.offsetTop - el.scrollTop,
    height: px(style.lineHeight) || px(style.fontSize) * 1.2,
  }
  mirror.remove()
  return point
}
