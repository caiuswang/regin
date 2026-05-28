import fs from 'node:fs'

const COMMENT_PATTERN = /\/\*[\s\S]*?\*\/|\/\/.*$/gm
const WINDOW_CONFIRM_PATTERN = /\bwindow\.confirm\s*\(/g
const BARE_CONFIRM_PATTERN = /(^|[^\w$.])confirm\s*\(/g
const LOCAL_CONFIRM_PATTERN = /\b(?:function\s+confirm\s*\(|const\s+confirm\s*=|let\s+confirm\s*=|var\s+confirm\s*=|confirm\s*[:=]\s*(?:async\s*)?\()/
const USE_CONFIRM_DESTRUCTURE_PATTERN = /\b(?:const|let|var)\s*\{[^}]*\bconfirm\b[^}]*\}\s*=\s*useConfirm\s*\(\s*\)/

export function run({ filePath }) {
  const source = fs.readFileSync(filePath, 'utf8')
  const scrubbed = source.replace(COMMENT_PATTERN, '')
  const details = []

  let match
  while ((match = WINDOW_CONFIRM_PATTERN.exec(scrubbed)) !== null) {
    const line = scrubbed.slice(0, match.index).split('\n').length
    details.push(`native confirm at line ${line}; use useConfirm/ConfirmDialog for confirmation prompts`)
  }

  const hasLocalConfirm =
    LOCAL_CONFIRM_PATTERN.test(scrubbed) ||
    USE_CONFIRM_DESTRUCTURE_PATTERN.test(scrubbed)
  if (!hasLocalConfirm) {
    while ((match = BARE_CONFIRM_PATTERN.exec(scrubbed)) !== null) {
      const line = scrubbed.slice(0, match.index + match[1].length).split('\n').length
      details.push(`native confirm at line ${line}; use useConfirm/ConfirmDialog for confirmation prompts`)
    }
  }

  return { matches: details.length, details }
}
