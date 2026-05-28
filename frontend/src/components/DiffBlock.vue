<script setup>
import { computed } from 'vue'
import hljs from 'highlight.js/lib/common'
// Side-effect import: hljs CSS classes (.hljs-keyword, .hljs-string, …)
// gain colors. Loaded globally so the diff renders against the dark
// slate-900 card the parent wraps it in.
import 'highlight.js/styles/github-dark.css'

const props = defineProps({
  diff: { type: String, required: true },
  filePath: { type: String, default: '' },
})

// Map common file extensions to hljs language ids. Anything not in the
// map (or whose hljs registration isn't loaded) falls through to
// auto-detection, which handles most languages well enough for short
// diff hunks.
const EXT_TO_LANG = {
  js: 'javascript', cjs: 'javascript', mjs: 'javascript', jsx: 'javascript',
  ts: 'typescript', tsx: 'typescript',
  py: 'python', pyi: 'python',
  rb: 'ruby', go: 'go', rs: 'rust',
  java: 'java', kt: 'kotlin', scala: 'scala', swift: 'swift',
  c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', cxx: 'cpp', hpp: 'cpp',
  cs: 'csharp', php: 'php',
  sh: 'bash', bash: 'bash', zsh: 'bash', fish: 'bash',
  yaml: 'yaml', yml: 'yaml',
  json: 'json', json5: 'json',
  toml: 'ini', ini: 'ini',
  html: 'xml', htm: 'xml', xml: 'xml', svg: 'xml',
  // .vue / .svelte / .astro mix template + script + style — let
  // auto-detection pick the dominant language per-diff so a
  // script-only hunk gets JS highlighting instead of XML's "no
  // matches because there are no tags" silence.
  css: 'css', scss: 'scss', less: 'less',
  md: 'markdown', markdown: 'markdown',
  sql: 'sql',
  dockerfile: 'dockerfile',
  makefile: 'makefile', mk: 'makefile',
}

// Strip diff metadata (hunk headers, `\` no-newline markers) so the
// language detector sees just code. Used as the auto-detection sample
// and is small enough to recompute cheaply on every render.
const codeSample = computed(() => {
  return (props.diff || '')
    .split('\n')
    .filter(l => l && (l[0] === '+' || l[0] === '-' || l[0] === ' '))
    .map(l => l.slice(1))
    .join('\n')
})

const language = computed(() => {
  const fp = props.filePath || ''
  const base = fp.split('/').pop() || ''
  const lower = base.toLowerCase()
  const dot = lower.lastIndexOf('.')
  const ext = dot >= 0 ? lower.slice(dot + 1) : lower
  const id = EXT_TO_LANG[ext]
  if (id && hljs.getLanguage(id)) return id
  // Unknown / mixed-language file — ask hljs to guess from the actual
  // diff content. Cheap because hljs.highlightAuto runs on the small
  // sample we computed above, not the rendered diff each time.
  const sample = codeSample.value
  if (sample.length < 5) return null
  try {
    const result = hljs.highlightAuto(sample)
    return result.language || null
  } catch {
    return null
  }
})

function escapeHtml(s) {
  return s.replace(/[&<>"']/g, m => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[m]))
}

function highlightCode(code) {
  if (!code) return ''
  try {
    if (language.value) {
      return hljs.highlight(code, { language: language.value, ignoreIllegals: true }).value
    }
    return hljs.highlightAuto(code).value
  } catch {
    return escapeHtml(code)
  }
}

// Each line gets a kind that drives both the prefix gutter and the
// background tint. `add`/`del` are the only kinds that highlight code
// — `hunk` headers and metadata stay unstyled so they don't compete
// for attention with the actual change.
const lines = computed(() => {
  return (props.diff || '').split('\n').map(raw => {
    if (!raw.length) return { kind: 'ctx', prefix: ' ', html: '' }
    const c = raw[0]
    // `\` lines like "\ No newline at end of file" are diff metadata.
    if (c === '@') return { kind: 'hunk', prefix: '', html: escapeHtml(raw) }
    if (c === '\\') return { kind: 'meta', prefix: '', html: escapeHtml(raw) }
    if (c === '+') return { kind: 'add', prefix: '+', html: highlightCode(raw.slice(1)) }
    if (c === '-') return { kind: 'del', prefix: '-', html: highlightCode(raw.slice(1)) }
    return { kind: 'ctx', prefix: ' ', html: highlightCode(raw.slice(1)) }
  })
})
</script>

<template>
  <pre class="diff-block text-[12px] font-mono leading-snug max-h-[32rem] overflow-auto py-2"><span
    v-for="(line, lineNo) in lines"
    :key="lineNo"
    class="block px-3"
    :class="{
      'bg-emerald-500/10 text-emerald-100': line.kind === 'add',
      'bg-red-500/10 text-red-100': line.kind === 'del',
      'text-cyan-300': line.kind === 'hunk',
      'text-slate-400': line.kind === 'meta',
      'text-slate-300': line.kind === 'ctx',
    }"
  ><span class="select-none mr-2 w-3 inline-block text-center" :class="{
      'text-emerald-300/70': line.kind === 'add',
      'text-red-300/70': line.kind === 'del',
      'text-slate-500': line.kind === 'ctx',
    }">{{ line.prefix }}</span><span v-html="line.html || '&nbsp;'"></span></span></pre>
</template>

<style scoped>
/* Push hljs colors slightly toward the line's tint so highlighted
   tokens still read as "added" or "removed" instead of looking like
   stray editor chrome. */
.diff-block :deep(.hljs-comment),
.diff-block :deep(.hljs-quote) { font-style: italic; }
</style>
