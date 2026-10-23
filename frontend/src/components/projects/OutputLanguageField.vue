<script setup>
// The language a task run under this project is told to write in. A project's
// own value lives on its repo row; `/projects/default` edits the global
// fallback, which is a setting rather than a row.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import { useFlash } from '../../composables/useFlash'

const props = defineProps({
  projectId: { type: [String, Number], default: null },
  canWrite: { type: Boolean, default: false },
})

const MAX = 64
const { flash } = useFlash()
const value = ref('')
const saved = ref('')
const source = ref('')
const effective = ref('')
const error = ref('')
const busy = ref(false)

const isDefault = computed(() => props.projectId == null)
const tooLong = computed(() => value.value.trim().length > MAX)
const dirty = computed(() => value.value.trim() !== saved.value)
const fallbackNote = computed(() => {
  if (isDefault.value || saved.value) return ''
  return source.value === 'default'
    ? `unset — runs use the default, ${effective.value}`
    : 'unset — and no default either, so runs are told nothing'
})

async function load() {
  const sentFor = props.projectId
  error.value = ''
  try {
    if (isDefault.value) {
      const body = await api.get('/settings/tasks')
      const field = (body.fields || []).find(f => f.key === 'output_language')
      if (sentFor !== props.projectId) return
      saved.value = field?.value || ''
    } else {
      const body = await api.get(`/projects/${props.projectId}/output-language`)
      if (sentFor !== props.projectId) return
      saved.value = body.output_language || ''
      source.value = body.source
      effective.value = body.effective
    }
    value.value = saved.value
  } catch (e) {
    if (sentFor === props.projectId) error.value = e?.message || String(e)
  }
}

watch(() => props.projectId, load, { immediate: true })

async function save() {
  if (busy.value || tooLong.value) return
  busy.value = true
  try {
    const payload = { output_language: value.value.trim() }
    const res = isDefault.value
      ? await api.put('/settings/tasks', payload)
      : await api.patch(`/projects/${props.projectId}/output-language`, payload)
    if (res?.ok === false) {
      flash(res.msg || (res.errors || []).join('; ') || 'the language was refused', 'error')
      return
    }
    flash(payload.output_language ? `Output language: ${payload.output_language}` : 'Output language cleared', 'success')
    await load()
  } catch (e) {
    flash(e?.message || String(e), 'error')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="olf" data-testid="project-output-language">
    <label class="olf-label" for="olf-input" data-pl-part="meta-label">Output language</label>
    <p v-if="error" class="olf-fail" role="alert" data-testid="project-output-language-error">{{ error }}</p>
    <form v-else class="olf-row" @submit.prevent="save">
      <Input
        id="olf-input"
        v-model="value"
        class="olf-input"
        :disabled="!canWrite || busy"
        :placeholder="isDefault ? 'e.g. English — empty says nothing' : 'e.g. English — empty uses the default'"
        data-testid="project-output-language-input"
      />
      <Button v-if="canWrite" type="submit" size="sm" :disabled="busy || tooLong || !dirty" data-testid="project-output-language-save">
        Save
      </Button>
    </form>
    <p v-if="tooLong" class="olf-fail" role="alert">At most {{ MAX }} characters.</p>
    <p v-else-if="fallbackNote" class="olf-note" data-testid="project-output-language-fallback">{{ fallbackNote }}</p>
    <p v-else class="olf-note">
      Every task run {{ isDefault ? 'without a project language' : 'in this project' }} writes its replies,
      messages, specs and finish report in this language. Code and commands stay as they are.
    </p>
  </div>
</template>

<style scoped>
.olf { display: flex; flex-direction: column; gap: 0.35rem; min-width: 0; }
.olf-label { font-size: 11px; font-weight: 600; color: var(--color-fg-subtle); }
.olf-row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; min-width: 0; }
.olf-input { flex: 1 1 14rem; min-width: 0; max-width: 24rem; }
.olf-note { font-size: 12px; color: var(--color-fg-muted); }
.olf-fail { font-size: 12px; color: var(--color-danger); }
</style>
