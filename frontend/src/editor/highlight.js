// The one lowlight instance both faces highlight with: the read view's
// `CodeBlock.vue` and the rich face's `CodeBlockLowlight` decorations, so code
// looks the same before and after Save.
//
// A language nobody registered is left as plain text rather than guessed:
// `highlightAuto` would colour a `mermaid` or `unknownlang` block as whatever
// it resembles, and the two faces would disagree with the language the writer
// named.

import { createLowlight } from 'lowlight'
import bash from 'highlight.js/lib/languages/bash'
import diff from 'highlight.js/lib/languages/diff'
import gherkin from 'highlight.js/lib/languages/gherkin'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'
import { LANGUAGES } from './languages.js'

export { LANGUAGES }

export const lowlight = createLowlight({
  bash, diff, gherkin, javascript, json, markdown, python, sql, typescript, xml, yaml,
})
lowlight.registerAlias({ xml: ['vue'] })

const plain = (code) => ({ type: 'root', children: [{ type: 'text', value: code }] })

export function isHighlighted(language) {
  return !!language && lowlight.registered(language)
}

export function highlightTree(code, language) {
  return isHighlighted(language) ? lowlight.highlight(language, code) : plain(code)
}

// What `CodeBlockLowlight` is handed. Its plugin asks highlight.js's global
// registry as well as this instance whether a language is known, and falls
// back to `highlightAuto` when neither does; both routes end in `highlightTree`.
export const editorLowlight = {
  listLanguages: () => lowlight.listLanguages(),
  registered: (language) => lowlight.registered(language),
  highlight: (language, code) => highlightTree(code, language),
  highlightAuto: (code) => plain(code),
}

const ESCAPES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }
const escape = (text) => text.replace(/[&<>"]/g, (c) => ESCAPES[c])

function toHtml(node) {
  if (node.type === 'text') return escape(node.value)
  const inner = (node.children ?? []).map(toHtml).join('')
  if (node.type !== 'element') return inner
  const cls = node.properties?.className?.join(' ')
  return cls ? `<span class="${escape(cls)}">${inner}</span>` : inner
}

export function highlightHtml(code, language) {
  return toHtml(highlightTree(code, language))
}
