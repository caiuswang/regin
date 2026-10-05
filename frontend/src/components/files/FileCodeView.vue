<script setup>
// Source view: Monaco, read-only (D1). Monaco is imported here, on mount, and
// nowhere else, so no other route pays for it (I4). Line numbers follow the
// file, not the buffer: a Read span's content starts at its offset, so the
// gutter and the `#L` anchor both count from `meta.first_line`.
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import api from '../../api'
import { useTheme } from '../../composables/useTheme'
import { rawUrl } from './source'
import { monacoTheme } from './render'

const props = defineProps({
  meta: { type: Object, required: true },
  source: { type: Object, required: true },
  lines: { type: Object, default: null },
})
const emit = defineEmits(['failed'])

const host = ref(null)
const ready = ref(false)
const error = ref('')
const editor = shallowRef(null)
let decorations = null
let monacoApi = null
let disposed = false

const { theme } = useTheme()

function toBufferLine(fileLine) {
  return fileLine - (props.meta.first_line || 1) + 1
}

function applyLines() {
  const ed = editor.value
  if (!ed || !decorations) return
  const model = ed.getModel()
  if (!props.lines || !model) {
    decorations.clear()
    return
  }
  const count = model.getLineCount()
  const first = toBufferLine(props.lines.start)
  const last = toBufferLine(props.lines.end)
  // An anchor outside the buffer (a span that captured lines 100–150 asked
  // for #L10) names no line here; clamping would point at the wrong one.
  if (last < 1 || first > count) {
    decorations.clear()
    return
  }
  const start = Math.max(first, 1)
  const end = Math.min(last, count)
  decorations.set([{
    range: new monacoApi.Range(start, 1, end, 1),
    options: {
      isWholeLine: true,
      className: 'fv-line-highlight',
      linesDecorationsClassName: 'fv-line-highlight-gutter',
    },
  }])
  ed.revealLinesInCenter(start, end)
}

onMounted(async () => {
  let text
  try {
    const [mod, body] = await Promise.all([
      import('./monaco.js'),
      api.getText(rawUrl(props.source)),
    ])
    monacoApi = mod.default
    text = body
  } catch (err) {
    error.value = err.message || 'The file could not be loaded.'
    emit('failed', error.value)
    return
  }
  if (disposed || !host.value) return
  const offset = (props.meta.first_line || 1) - 1
  editor.value = monacoApi.editor.create(host.value, {
    value: text,
    language: props.meta.language || 'plaintext',
    readOnly: true,
    domReadOnly: true,
    automaticLayout: true,
    scrollBeyondLastLine: false,
    renderWhitespace: 'selection',
    theme: monacoTheme(theme.value),
    lineNumbers: (n) => String(n + offset),
    fontSize: 13,
  })
  decorations = editor.value.createDecorationsCollection()
  applyLines()
  ready.value = true
})

watch(() => props.lines, applyLines)
watch(theme, (t) => monacoApi?.editor.setTheme(monacoTheme(t)))

onBeforeUnmount(() => {
  disposed = true
  const ed = editor.value
  if (!ed) return
  ed.getModel()?.dispose()
  ed.dispose()
})
</script>

<template>
  <div class="fcv" data-testid="file-code-view" :data-ready="ready ? 'true' : 'false'"
       :data-language="meta.language || 'plaintext'">
    <p v-if="error" class="fcv-error" role="alert">{{ error }}</p>
    <p v-else-if="!ready" class="fcv-note" role="status">Loading editor…</p>
    <div ref="host" class="fcv-host" />
  </div>
</template>

<style scoped>
.fcv {
  position: relative;
  flex: 1;
  min-height: 20rem;
  display: flex;
  flex-direction: column;
}
.fcv-host { flex: 1; min-height: 0; }
.fcv-note, .fcv-error {
  position: absolute;
  inset: 0 auto auto 0;
  padding: 1rem 1.25rem;
  font-size: 13px;
  color: var(--color-fg-muted);
}
.fcv-error { color: var(--color-danger); }
</style>

<!-- NOT scoped: Monaco renders its own DOM, which carries no scoped data-v. -->
<style>
.fv-line-highlight { background: rgba(250, 204, 21, 0.18); }
.fv-line-highlight-gutter { background: rgb(234, 179, 8); width: 3px !important; margin-left: 3px; }
</style>
