<script setup>
// Where the feature lives — the one field the Pipeline filters on that the
// browser could never write.
//
// `features.project_id` shipped filterable (`lib/features/index.py`) and with
// a scope picker above the tabs, but no create or edit path ever set it, so
// every feature was `NULL` and every per-project scope was empty. The filter
// was reading a column only a hand-written UPDATE could fill.
//
// A repo IS the project, the same thing the task surface means by it, so this
// reads `useProjects` rather than growing a second registry. Saving appends no
// event: a project is where a feature lives, not a step in its life.
import { computed, onMounted, ref } from 'vue'
import api from '../../api'
import { useProjects } from '../../composables/useProjects'
import Card from '../Card.vue'
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
const saved = ref(false)

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

// A project id the repo list does not answer to: the repo was dropped, or the
// list failed to load. Named rather than silently rendered as "No project",
// which would be a picker lying about where the feature is filed.
//
// Gated on HAVING a list, because `useProjects` starts empty and fetches in
// `onMounted`: an ungated check calls every valid project unknown for the one
// frame before `/repos` answers — a false accusation on every cold load.
const answered = computed(() => projects.value.length > 0 || !!listError.value)
const unknown = computed(() => props.projectId != null && answered.value
  && !projects.value.some(p => p.id === props.projectId))

async function choose(value) {
  if (value === current.value) return
  saving.value = true
  error.value = ''
  saved.value = false
  const body = { project_id: value === NONE ? null : Number(value) }
  const res = await api.patch(`/features/${props.featureId}`, body)
  saving.value = false
  if (res?.ok === false) {
    error.value = res.msg || 'the project could not be changed'
    return
  }
  saved.value = true
  // The parent re-seats the whole ledger from the response, so the picker
  // reads back what the server stored rather than what was clicked.
  emit('saved', res)
}
</script>

<template>
  <Card data-testid="feature-project">
    <div class="fp">
      <div class="fp-label">
        <h2 class="card-header fp-title">Project</h2>
        <p class="fp-sub">the registered repo this feature is filed under</p>
      </div>
      <div class="fp-control">
        <Select
          v-if="canWrite"
          :model-value="current"
          :options="options"
          :disabled="saving"
          aria-label="Feature project"
          data-testid="feature-project-select"
          @update:model-value="choose"
        />
        <span v-else class="fp-static" data-testid="feature-project-static">
          {{ projectName || 'No project' }}
        </span>
      </div>
    </div>
    <p v-if="unknown" class="fp-note" data-testid="feature-project-unknown">
      Filed under project #{{ projectId }}, which the repo list does not
      answer to{{ listError ? ' — the list could not be read' : '' }}.
    </p>
    <p v-if="error" class="fp-error" role="alert" data-testid="feature-project-error">
      {{ error }}
    </p>
    <p v-else-if="saved" class="fp-note" data-testid="feature-project-saved">
      Saved. This changes which Pipeline scope the feature appears under.
    </p>
  </Card>
</template>

<style scoped>
.fp {
    display: flex; align-items: flex-start; justify-content: space-between;
    gap: 0.75rem; flex-wrap: wrap;
}
.fp-title { margin-bottom: 0; }
.fp-sub { font-size: 11.5px; color: var(--color-fg-subtle); }
.fp-control { display: flex; align-items: center; gap: 0.5rem; }
.fp-static { font-size: 0.8125rem; color: var(--color-fg-muted); }
.fp-note { margin-top: 0.5rem; font-size: 11.5px; color: var(--color-fg-subtle); }
.fp-error {
    margin-top: 0.5rem; font-size: 0.8125rem; color: var(--color-danger);
    overflow-wrap: anywhere;
}
</style>
