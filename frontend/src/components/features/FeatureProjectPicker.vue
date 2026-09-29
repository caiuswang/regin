<script setup>
// Where the feature lives, as one field on the stage line — the same kind of
// metadata as the id and the reference beside it, so it is not a card.
//
// A repo IS the project, the same thing the task surface means by it, so this
// reads `useProjects` rather than growing a second registry. Saving appends no
// event: a project is where a feature lives, not a step in its life.
import { computed, onMounted, ref } from 'vue'
import api from '../../api'
import { useProjects } from '../../composables/useProjects'
import Select from '../ui/Select.vue'

const props = defineProps({
  featureId: { type: Number, required: true },
  projectId: { type: Number, default: null },
  projectName: { type: String, default: null },
  canWrite: { type: Boolean, default: false },
})
const emit = defineEmits(['saved'])

const { items: projects, ensure, error: listError } = useProjects()
onMounted(ensure)

const saving = ref(false)
const error = ref('')
// Bumped per save so the "Saved" mark re-mounts and its fade restarts; the
// fade is CSS, so nothing on a clock has to take it down.
const savedTick = ref(0)

// "No project" is a value, not the absence of one — the same scope the tabs
// call `none`. A Select with no option for it could only ever file a feature,
// never unfile one.
const NONE = 'none'
const options = computed(() => [
  { value: NONE, label: 'No project' },
  ...projects.value.map(p => ({ value: String(p.id), label: p.name })),
])
const current = computed(() =>
  props.projectId == null ? NONE : String(props.projectId))

// A project id the repo list does not answer to is named rather than rendered
// as "No project". Gated on HAVING a list: `useProjects` starts empty, and an
// ungated check calls every valid project unknown for the frame before
// `/repos` answers.
const answered = computed(() => projects.value.length > 0 || !!listError.value)
const unknown = computed(() => props.projectId != null && answered.value
  && !projects.value.some(p => p.id === props.projectId))

async function choose(value) {
  if (value === current.value) return
  saving.value = true
  error.value = ''
  const body = { project_id: value === NONE ? null : Number(value) }
  const res = await api.patch(`/features/${props.featureId}`, body)
  saving.value = false
  if (res?.ok === false) {
    error.value = res.msg || 'the project could not be changed'
    return
  }
  savedTick.value += 1
  // The parent re-seats the whole ledger from the response, so the picker
  // reads back what the server stored rather than what was clicked.
  emit('saved', res)
}
</script>

<template>
  <span class="fpp" data-testid="stage-project">
    <span class="fpp-label">Project</span>
    <Select
      v-if="canWrite"
      class="fpp-select"
      :model-value="current"
      :options="options"
      :disabled="saving"
      aria-label="Feature project"
      data-testid="feature-project-select"
      @update:model-value="choose"
    />
    <span v-else class="fpp-static" data-testid="feature-project-static">
      {{ projectName || 'No project' }}
    </span>
    <span v-if="saving" class="fpp-note">Saving…</span>
    <span
      v-else-if="savedTick && !error" :key="savedTick"
      class="fpp-saved" role="status" data-testid="feature-project-saved"
    >Saved</span>
    <span v-if="unknown" class="fpp-note" data-testid="feature-project-unknown">
      #{{ projectId }} is not a registered repo{{ listError ? ' — the list could not be read' : '' }}
    </span>
    <span v-if="error" class="fpp-error" role="alert" data-testid="feature-project-error">
      {{ error }}
    </span>
  </span>
</template>

<style scoped>
.fpp {
    display: inline-flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;
    min-width: 0; font-size: 11.5px; color: var(--color-fg-subtle);
}
.fpp-select { font-size: 0.8125rem; }
.fpp-static { font-size: 0.8125rem; color: var(--color-fg-muted); }
.fpp-note { color: var(--color-fg-subtle); }
.fpp-saved {
    color: var(--color-green-700, #15803d);
    animation: fpp-fade 2.4s ease-in forwards;
}
@keyframes fpp-fade { 0%, 70% { opacity: 1; } 100% { opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
    .fpp-saved { animation: none; }
}
.fpp-error { color: var(--color-danger); overflow-wrap: anywhere; }
</style>
