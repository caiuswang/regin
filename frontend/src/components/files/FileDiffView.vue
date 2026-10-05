<script setup>
// Changes vs HEAD (R3): Monaco's diff editor, the committed bytes on the left
// and the worktree on the right, whichever rev the viewer itself is showing.
// The `#L` anchor and "Copy link" count lines on the worktree side.
import { onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue'
import api from '../../api'
import { useTheme } from '../../composables/useTheme'
import { rawUrl } from './source'
import { monacoTheme } from './render'
import { lineHighlighter, onUserSelection } from './editorLines'

const props = defineProps({
  meta: { type: Object, required: true },
  source: { type: Object, required: true },
  lines: { type: Object, default: null },
  layout: { type: String, default: 'side' },
})
const emit = defineEmits(['failed', 'select'])

const host = ref(null)
const ready = ref(false)
const error = ref('')
const diff = shallowRef(null)
let highlight = null
let monacoApi = null
let disposed = false

const { theme } = useTheme()

function at(rev) {
  return rawUrl({ ...props.source, rev })
}

onMounted(async () => {
  let head
  let work
  try {
    const [mod, a, b] = await Promise.all([
      import('./monaco.js'),
      api.getText(at('HEAD')),
      api.getText(at('')),
    ])
    monacoApi = mod.default
    head = a
    work = b
  } catch (err) {
    error.value = err.message || 'The file could not be loaded.'
    emit('failed', error.value)
    return
  }
  if (disposed || !host.value) return
  const language = props.meta.language || 'plaintext'
  diff.value = monacoApi.editor.createDiffEditor(host.value, {
    readOnly: true,
    originalEditable: false,
    domReadOnly: true,
    automaticLayout: true,
    scrollBeyondLastLine: false,
    renderSideBySide: props.layout !== 'inline',
    useInlineViewWhenSpaceIsLimited: false,
    theme: monacoTheme(theme.value),
    fontSize: 13,
  })
  diff.value.setModel({
    original: monacoApi.editor.createModel(head, language),
    modified: monacoApi.editor.createModel(work, language),
  })
  const modified = diff.value.getModifiedEditor()
  highlight = lineHighlighter(monacoApi, modified)
  onUserSelection(modified, 1, (lines) => emit('select', lines))
  highlight(props.lines)
  ready.value = true
})

watch(() => props.lines, (lines) => highlight?.(lines))
watch(() => props.layout, (layout) => diff.value?.updateOptions({ renderSideBySide: layout !== 'inline' }))
watch(theme, (t) => monacoApi?.editor.setTheme(monacoTheme(t)))

onBeforeUnmount(() => {
  disposed = true
  const ed = diff.value
  if (!ed) return
  const model = ed.getModel()
  ed.dispose()
  model?.original.dispose()
  model?.modified.dispose()
})
</script>

<template>
  <div class="fdv" data-testid="file-diff-view" :data-ready="ready ? 'true' : 'false'"
       :data-layout="layout === 'inline' ? 'inline' : 'side'">
    <p v-if="error" class="fdv-note fdv-error" role="alert">{{ error }}</p>
    <p v-else-if="!ready" class="fdv-note" role="status">Loading diff…</p>
    <div ref="host" class="fdv-host" />
  </div>
</template>

<style scoped>
.fdv {
  position: relative;
  flex: 1;
  min-height: 20rem;
  display: flex;
  flex-direction: column;
}
.fdv-host { flex: 1; min-height: 0; }
.fdv-note {
  position: absolute;
  inset: 0 auto auto 0;
  padding: 1rem 1.25rem;
  font-size: 13px;
  color: var(--color-fg-muted);
}
.fdv-error { color: var(--color-danger); }
</style>
