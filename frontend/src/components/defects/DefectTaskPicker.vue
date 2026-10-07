<script setup>
// Point a defect at a task that already exists — chosen from this feature's
// unfinished tasks, or typed as an id when the right one lives elsewhere.
// Both controls write the one value the submit posts, so picking a task and
// then correcting its id by hand cannot send two different answers.
import { computed, onMounted, ref } from 'vue'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import { FORMS, linkableTasks } from './defectForms'

const props = defineProps({
  defect: { type: Object, required: true },
  busy: { type: Boolean, default: false },
})
const value = defineModel({ type: String, default: '' })
const emit = defineEmits(['submit'])

const spec = FORMS['fix-item']
const tasks = ref([])
const loaded = ref(false)
const error = ref('')

const problem = computed(() => spec.invalid(value.value))
const options = computed(() => tasks.value
  .filter(t => t.id !== props.defect.fix_task_id)
  .map(t => ({ value: String(t.id), label: `#${t.id} ${t.title}` })))
const picked = computed({
  get: () => (options.value.some(o => o.value === value.value.trim()) ? value.value.trim() : ''),
  set: (id) => { value.value = id },
})

onMounted(async () => {
  try {
    tasks.value = await linkableTasks(props.defect.feature_id)
  } catch (e) {
    error.value = e?.message || String(e)
  } finally {
    loaded.value = true
  }
})
</script>

<template>
  <form class="dtp" @submit.prevent="!problem && emit('submit')">
    <Select
      v-if="options.length"
      v-model="picked"
      class="dtp-grow"
      :options="options"
      :disabled="busy"
      placeholder="A task of this feature"
      :aria-label="`Task to fix defect #${defect.id}`"
      :data-testid="`defect-fix-item-picker-${defect.id}`"
    />
    <label class="sr-only" :for="`f-${defect.id}`">{{ spec.placeholder }}</label>
    <Input
      :id="`f-${defect.id}`"
      v-model="value"
      class="dtp-id"
      :placeholder="spec.placeholder"
      :disabled="busy"
      :error="!!problem && !!value"
      :data-testid="`defect-fix-item-field-${defect.id}`"
    />
    <Button type="submit" size="sm" :disabled="busy || !!problem"
            :data-testid="`defect-fix-item-submit-${defect.id}`">
      {{ spec.label }} #{{ defect.id }}
    </Button>
    <p v-if="error" class="dtp-problem" role="alert">{{ error }}</p>
    <p v-else-if="loaded && !options.length" class="dtp-hint"
       :data-testid="`defect-fix-item-none-${defect.id}`">
      This feature has no unfinished task to pick — enter an id.
    </p>
    <p class="dtp-hint">{{ spec.hint }}</p>
    <p v-if="problem && value" class="dtp-problem" role="alert"
       :data-testid="`defect-fix-item-problem-${defect.id}`">{{ problem }}</p>
  </form>
</template>

<style scoped>
.dtp {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem;
    max-width: 44rem;
}
.dtp .dtp-grow { flex: 1 1 16rem; min-width: 0; }
.dtp .dtp-id { flex: 0 1 9rem; }
.dtp-hint { flex-basis: 100%; font-size: 11px; color: var(--color-fg-faint); }
.dtp-problem {
    flex-basis: 100%;
    border-radius: var(--radius-lg); background: var(--color-red-50);
    padding: 0.35rem 0.6rem; font-size: 11.5px; color: var(--color-red-700);
}
</style>
