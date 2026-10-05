<script setup>
// A Jupyter notebook (nbformat 4), drawn cell by cell. Markdown cells go
// through the markdown view, so they are sanitized (I2) and their relative
// images resolve against the notebook's directory. Code cells are colorized
// by Monaco, which loads here only (I4). Outputs: text, PNG/JPEG, and HTML in
// a sandboxed frame that never runs scripts.
import { onMounted, ref, shallowRef, watch } from 'vue'
import api from '../../api'
import { useTheme } from '../../composables/useTheme'
import FileMarkdownView from './FileMarkdownView.vue'
import { rawUrl } from './source'
import { monacoTheme } from './render'

const props = defineProps({
  meta: { type: Object, required: true },
  source: { type: Object, required: true },
  lines: { type: Object, default: null },
})
const emit = defineEmits(['failed', 'navigate'])

const cells = shallowRef(null)
const error = ref('')
const { theme } = useTheme()
let monacoApi = null

const joined = (v) => (Array.isArray(v) ? v.join('') : v || '')
// eslint-disable-next-line no-control-regex -- ANSI colour codes in tracebacks
const stripAnsi = (s) => s.replace(/\x1b\[[0-9;]*[A-Za-z]/g, '')

function outputView(out) {
  if (out.output_type === 'stream') return { kind: 'text', text: joined(out.text) }
  if (out.output_type === 'error') {
    const trace = (out.traceback || []).map(stripAnsi).join('\n')
    return { kind: 'text', text: trace || `${out.ename}: ${out.evalue}`, error: true }
  }
  const data = out.data || {}
  for (const mime of ['image/png', 'image/jpeg']) {
    if (data[mime]) return { kind: 'image', src: `data:${mime};base64,${joined(data[mime]).replace(/\s+/g, '')}` }
  }
  if (data['text/html']) return { kind: 'html', html: joined(data['text/html']) }
  if (data['text/plain'] != null) return { kind: 'text', text: joined(data['text/plain']) }
  return null
}

function cellView(cell, lang) {
  const text = joined(cell.source)
  if (cell.cell_type === 'markdown') return { kind: 'markdown', text }
  if (cell.cell_type !== 'code') return { kind: 'raw', text }
  return {
    kind: 'code',
    text,
    html: '',
    lang,
    count: cell.execution_count,
    outputs: (cell.outputs || []).map(outputView).filter(Boolean),
  }
}

async function colorize(list) {
  const code = list.filter((c) => c.kind === 'code')
  if (!code.length) return
  monacoApi = (await import('./monaco.js')).default
  monacoApi.editor.setTheme(monacoTheme(theme.value))
  await Promise.all(code.map(async (c) => {
    try {
      c.html = await monacoApi.editor.colorize(c.text, c.lang, { tabSize: 4 })
    } catch {
      c.html = ''
    }
  }))
}

onMounted(async () => {
  try {
    const nb = JSON.parse(await api.getText(rawUrl(props.source)))
    if (nb?.nbformat !== 4 || !Array.isArray(nb.cells)) throw new Error('Not an nbformat 4 notebook.')
    const md = nb.metadata || {}
    const lang = md.language_info?.name || md.kernelspec?.language || 'plaintext'
    const list = nb.cells.map((c) => cellView(c, lang))
    await colorize(list)
    cells.value = list
  } catch (err) {
    error.value = err.message || 'The notebook could not be loaded.'
    emit('failed', error.value)
  }
})

watch(theme, (t) => monacoApi?.editor.setTheme(monacoTheme(t)))
</script>

<template>
  <div class="fnb" data-testid="file-notebook-view">
    <p v-if="error" class="fnb-note fnb-error" role="alert">{{ error }}</p>
    <p v-else-if="!cells" class="fnb-note" role="status">Loading…</p>
    <ol v-else class="fnb-cells">
      <li v-for="(cell, i) in cells" :key="i" class="fnb-cell" :data-cell="cell.kind">
        <FileMarkdownView v-if="cell.kind === 'markdown'" :meta="meta" :source="source" :text="cell.text"
                          @navigate="(s, a) => emit('navigate', s, a)" />
        <pre v-else-if="cell.kind === 'raw'" class="fnb-pre">{{ cell.text }}</pre>
        <template v-else>
          <div class="fnb-code">
            <span class="fnb-count">[{{ cell.count ?? ' ' }}]</span>
            <!-- eslint-disable-next-line vue/no-v-html -- Monaco's colorize escapes the source -->
            <pre v-if="cell.html" class="fnb-pre fnb-src" data-testid="file-notebook-code" v-html="cell.html" />
            <pre v-else class="fnb-pre fnb-src" data-testid="file-notebook-code">{{ cell.text }}</pre>
          </div>
          <div v-for="(out, j) in cell.outputs" :key="j" class="fnb-out" :data-output="out.kind">
            <pre v-if="out.kind === 'text'" class="fnb-pre" :class="{ 'fnb-err': out.error }">{{ out.text }}</pre>
            <img v-else-if="out.kind === 'image'" :src="out.src" alt="Cell output" class="fnb-img"
                 data-testid="file-notebook-image">
            <iframe v-else-if="out.kind === 'html'" sandbox="" :srcdoc="out.html" title="Cell output"
                    referrerpolicy="no-referrer" class="fnb-frame" />
          </div>
        </template>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.fnb { flex: 1; min-height: 0; overflow: auto; }
.fnb-note { padding: 1rem 1.25rem; font-size: 13px; color: var(--color-fg-muted); }
.fnb-error { color: var(--color-danger); }
.fnb-cells {
  list-style: none;
  margin: 0;
  padding: 1rem 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.fnb-code {
  display: flex;
  gap: 0.5rem;
  align-items: flex-start;
}
.fnb-count {
  flex-shrink: 0;
  width: 3rem;
  padding-top: 0.5rem;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  color: var(--color-fg-muted);
  text-align: right;
}
.fnb-pre {
  margin: 0;
  padding: 0.5rem 0.75rem;
  font-family: var(--font-mono, monospace);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
.fnb-src {
  flex: 1;
  min-width: 0;
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md, 6px);
}
.fnb-out { margin-left: 3.5rem; }
.fnb-err { color: var(--color-danger); }
.fnb-img { max-width: 100%; }
.fnb-frame { width: 100%; min-height: 12rem; border: 1px solid var(--color-border); background: #fff; }
</style>
