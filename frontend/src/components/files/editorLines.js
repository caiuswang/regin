// Line anchors on a Monaco editor, shared by the code view and the modified
// side of the diff view. Lines are counted in the file, not the buffer: a
// Read span's content starts at its offset (`first_line`).

// Only a selection the user made names a line: the cursor Monaco parks at 1:1
// on mount would otherwise turn "Copy link" into `#L1` and drop the anchor
// the page was opened at.
const USER_SOURCES = new Set(['mouse', 'keyboard'])

export function lineHighlighter(monaco, editor, firstLine = 1) {
  const decorations = editor.createDecorationsCollection()
  const toBuffer = (line) => line - firstLine + 1

  return function apply(lines) {
    const model = editor.getModel()
    if (!lines || !model) {
      decorations.clear()
      return
    }
    const count = model.getLineCount()
    const first = toBuffer(lines.start)
    const last = toBuffer(lines.end)
    // An anchor outside the buffer (a span that captured lines 100–150 asked
    // for #L10) names no line here; clamping would point at the wrong one.
    if (last < 1 || first > count) {
      decorations.clear()
      return
    }
    const start = Math.max(first, 1)
    const end = Math.min(last, count)
    decorations.set([{
      range: new monaco.Range(start, 1, end, 1),
      options: {
        isWholeLine: true,
        className: 'fv-line-highlight',
        linesDecorationsClassName: 'fv-line-highlight-gutter',
      },
    }])
    editor.revealLinesInCenter(start, end)
  }
}

export function onUserSelection(editor, firstLine, callback) {
  const offset = firstLine - 1
  return editor.onDidChangeCursorSelection((event) => {
    if (!USER_SOURCES.has(event.source)) return
    const sel = event.selection
    let end = sel.endLineNumber
    // A drag that ends at column 1 of the next line selected none of it.
    if (end > sel.startLineNumber && sel.endColumn === 1) end -= 1
    callback({ start: sel.startLineNumber + offset, end: end + offset })
  })
}
