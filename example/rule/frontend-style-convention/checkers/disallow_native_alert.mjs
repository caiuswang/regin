import fs from 'node:fs'

const ALERT_PATTERN = /\b(?:window\.)?alert\s*\(/g
const COMMENT_PATTERN = /\/\*[\s\S]*?\*\/|\/\/.*$/gm

export function run({ filePath }) {
  const source = fs.readFileSync(filePath, 'utf8')
  const scrubbed = source.replace(COMMENT_PATTERN, '')
  const details = []

  let match
  while ((match = ALERT_PATTERN.exec(scrubbed)) !== null) {
    const line = scrubbed.slice(0, match.index).split('\n').length
    details.push(
      `native alert at line ${line}; use useFlash/FlashMessage for notices or useConfirm/ConfirmDialog for user prompts`
    )
  }

  return { matches: details.length, details }
}
