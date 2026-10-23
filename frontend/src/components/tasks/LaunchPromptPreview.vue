<script setup>
// The exact text the task's next run would be launched with, read from the
// same builder the launch calls. Fetched only once opened: assembling it
// resolves every reference, which is not free.
import { computed, ref, watch } from 'vue'
import api from '../../api'

const props = defineProps({
  taskId: { type: Number, required: true },
  // Anything that changes what the run would be told (a re-arm, an edit).
  refreshKey: { type: String, default: '' },
  startOpen: { type: Boolean, default: false },
})

const SOURCE_LABEL = { project: 'project', default: 'default', none: 'not set' }

const open = ref(props.startOpen)
const loading = ref(false)
const error = ref('')
const preview = ref(null)
let seq = 0

async function load() {
  const mine = ++seq
  loading.value = true
  error.value = ''
  try {
    const body = await api.get(`/tasks/${props.taskId}/prompt-preview`)
    if (mine === seq) preview.value = body
  } catch (e) {
    if (mine === seq) { preview.value = null; error.value = e?.message || String(e) }
  } finally {
    if (mine === seq) loading.value = false
  }
}

watch(() => [open.value, props.taskId, props.refreshKey], ([isOpen]) => {
  if (isOpen) load()
}, { immediate: true })

const languageLine = computed(() => {
  const p = preview.value
  if (!p) return ''
  return p.language_source === 'none' ? 'no output language' : p.language
})

function onToggle(event) { open.value = event.target.open }
</script>

<template>
  <details class="lpp" data-testid="launch-prompt" :open="open" @toggle="onToggle">
    <summary class="lpp-summary">
      <span class="lpp-title">Launch prompt</span>
      <template v-if="preview">
        <span class="lpp-lang" data-testid="launch-prompt-language">{{ languageLine }}</span>
        <span class="lpp-tag" :data-source="preview.language_source" data-testid="launch-prompt-source">
          {{ SOURCE_LABEL[preview.language_source] || preview.language_source }}
        </span>
      </template>
    </summary>
    <p v-if="error" class="lpp-error" role="alert" data-testid="launch-prompt-error">
      Could not build the preview: {{ error }}
    </p>
    <p v-else-if="loading && !preview" class="lpp-note">Building…</p>
    <template v-else-if="preview">
      <p v-if="preview.run_id_placeholder" class="lpp-note" data-testid="launch-prompt-placeholder">
        Not armed yet — the finish command's run id is filled in when it is.
      </p>
      <pre class="lpp-text" data-testid="launch-prompt-text">{{ preview.prompt }}</pre>
    </template>
  </details>
</template>

<style scoped>
.lpp {
    border: 1px solid var(--color-border);
    border-radius: var(--radius-md);
    background: var(--color-bg-elevated, var(--color-bg));
    min-width: 0;
}
.lpp-summary {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.5rem;
    padding: 0.6rem 0.9rem;
    cursor: pointer;
    font-size: 0.8125rem;
}
.lpp-title { font-weight: 600; color: var(--color-fg); }
.lpp-lang { color: var(--color-fg-muted); overflow-wrap: anywhere; }
.lpp-tag {
    font-size: 11px;
    padding: 0 0.45rem;
    border: 1px solid var(--color-border);
    border-radius: var(--radius-sm);
    color: var(--color-fg-muted);
}
.lpp-tag[data-source="none"] { border-style: dashed; }
.lpp-note, .lpp-error {
    padding: 0 0.9rem 0.6rem;
    font-size: 0.8125rem;
    color: var(--color-fg-muted);
}
.lpp-error { color: var(--color-danger); }
.lpp-text {
    margin: 0;
    padding: 0.6rem 0.9rem 0.8rem;
    border-top: 1px solid var(--color-border-subtle);
    max-height: 28rem;
    overflow: auto;
    font-size: 12px;
    line-height: 1.5;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
}
</style>
