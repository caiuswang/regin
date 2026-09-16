<script setup>
// The runs of one feature, as nested rows under it.
//
// Fetched from the same feed the list pages through, narrowed by
// `parent_id`, so a run reads here exactly as it reads when "Show runs" lists
// it beside its feature — one serializer, one set of controls.
import { onMounted, ref, watch } from 'vue'
import api from '../../api'
import TaskRow from './TaskRow.vue'

const props = defineProps({
  parent: { type: Object, required: true },
  liveness: { type: Object, default: () => ({}) },
  refusals: { type: Object, default: () => ({}) },
  busyId: { type: Number, default: null },
  projectFor: { type: Function, default: () => null },
})
const emit = defineEmits(['open', 'control', 'focus', 'delete'])

const children = ref([])
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.get(`/tasks?parent_id=${props.parent.id}&size=100`)
    children.value = data.items || []
  } catch (e) {
    error.value = e.message || String(e)
  } finally {
    loading.value = false
  }
}

// A run starting or settling changes the parent's row, and that is the
// signal to re-read the runs beneath it — never a timer.
watch(() => [props.parent.status, props.parent.child_count], load)
onMounted(load)
</script>

<template>
  <div class="tc" :data-testid="`task-children-${parent.id}`">
    <p v-if="loading" class="tc-note">reading runs…</p>
    <p v-else-if="error" class="tc-note" role="alert">{{ error }}</p>
    <p v-else-if="!children.length" class="tc-note">
      Nothing is a run of this task.
    </p>
    <TaskRow
      v-for="child in children"
      v-else
      :key="child.id"
      :task="child"
      nested
      :liveness="liveness[child.id] || null"
      :refusal="refusals[child.id] || null"
      :project="projectFor(child)"
      :busy="busyId === child.id"
      @open="(kind, t) => emit('open', kind, t)"
      @control="(t, action) => emit('control', t, action)"
      @focus="id => emit('focus', id)"
      @delete="t => emit('delete', t)"
    />
  </div>
</template>

<style scoped>
.tc {
    border-bottom: 1px solid var(--color-border-subtle);
    background: var(--color-surface-2);
}
.tc-note {
    margin: 0;
    padding: 0.375rem 0.75rem 0.375rem 2.75rem;
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
}
</style>
