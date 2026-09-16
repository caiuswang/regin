<script setup>
// Declaring from the feature page: the key is in hand, so a *trace* must be
// named. The awkward direction of the pair, and deliberately kept — the trace
// view is admin-only, so removing this would make declaring admin-only in
// practice, a role decision taken by deleting a control rather than deciding
// it.
//
// The session list is itself admin-only, so an editor who is not an admin gets
// the paste field alone. That is a component decision from what the endpoint
// actually answered, never a role flag in a payload: the real guard is the
// server refusing a trace regin never captured, by name.
//
// `primary` is an explicit checkbox and never a side effect — turning it on
// moves a whole session's cost between two features.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import { money, shortDate } from '../../utils/ledgerFormat'

const props = defineProps({
  featureKey: { type: String, required: true },
  declared: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'declared'])

const query = ref('')
const chosen = ref('')
const primary = ref(false)
const rows = ref([])
const listable = ref(true)
const error = ref('')
const saving = ref(false)

const declaredIds = computed(
  () => new Set(props.declared.map(r => r.trace_id)))

// The endpoint asks for a title and an id down two different params: `q`
// matches the title, `trace_id` prefix-matches the id. One field serves both,
// so the field has to pick. Eight is the length of the short id the picker
// itself prints, so anything a reader could have copied off a row is asked
// for by id.
function idFilter(q) {
  return /^[0-9a-f][0-9a-f-]{7,}$/i.test(q) ? 'trace_id' : 'q'
}

async function search() {
  const q = query.value.trim()
  try {
    const body = await api.get(
      `/sessions?limit=20&kind=all${
        q ? `&${idFilter(q)}=${encodeURIComponent(q)}` : ''}`)
    rows.value = body.sessions || body.items || []
  } catch {
    // 403 for a non-admin editor. The paste field is the whole dialog then.
    listable.value = false
    rows.value = []
  }
}
watch(() => props.featureKey, search, { immediate: true })

async function declare() {
  const traceId = (chosen.value || query.value).trim()
  if (!traceId) return
  saving.value = true
  error.value = ''
  const result = await api.post(
    `/features/${encodeURIComponent(props.featureKey)}/attribute`,
    { trace_id: traceId, is_primary: primary.value })
  saving.value = false
  if (result?.ok === false) {
    error.value = result.msg
    return
  }
  emit('declared')
}
</script>

<template>
  <Dialog
    :open="true"
    wide
    :title="`Declare a session built ${featureKey}`"
    description="Canonical conversations only — a run id is collapsed onto the conversation it drove, so what you pick is what gets stored."
    @update:open="emit('close')"
  >
    <Input
      v-model="query"
      class="w-full"
      :placeholder="listable ? 'search title, or paste a trace id'
                             : 'paste a trace id'"
      @keydown.enter.prevent="search"
    />

    <p v-if="!listable" class="mt-2 text-xs text-slate-500">
      The session list is admin-only, so paste the id. Nothing is declared
      until regin confirms it has that session.
    </p>

    <p
      v-if="error"
      class="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700"
      role="alert"
    >{{ error }}</p>

    <ul v-if="rows.length" class="picker mt-2">
      <li
        v-for="r in rows"
        :key="r.trace_id"
        :class="['picker-row cursor-pointer hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500',
                 chosen === r.trace_id ? 'is-chosen' : '',
                 declaredIds.has(r.trace_id) ? 'is-declared' : '']"
        role="button"
        tabindex="0"
        @click="chosen = r.trace_id"
        @keydown.enter="chosen = r.trace_id"
      >
        <span class="font-mono text-[11px] text-slate-400">{{ shortDate(r.started_at) }}</span>
        <span class="font-mono text-[11px] text-slate-500">{{ r.trace_id.slice(0, 8) }}</span>
        <span class="min-w-0 flex-1 truncate text-sm text-slate-700">{{ r.title || '—' }}</span>
        <span
          class="w-20 text-right font-mono text-xs tabular-nums"
          :class="r.cost_usd === null || r.cost_usd === undefined
                    ? 'text-slate-400 italic' : 'text-slate-600'"
        >{{ r.cost_usd === null || r.cost_usd === undefined
              ? 'no usage' : money(r.cost_usd) }}</span>
      </li>
    </ul>

    <Checkbox v-model="primary" class="mt-3 items-start">
      <span class="text-xs text-slate-600">
        <strong class="font-semibold text-slate-800">Make this the session's primary feature.</strong>
        Off by default. Turning it on moves the whole session's cost onto
        {{ featureKey }} and demotes whichever feature holds it now.
      </span>
    </Checkbox>

    <template #footer>
      <Button variant="secondary" @click="emit('close')">Cancel</Button>
      <Button
        variant="primary"
        :disabled="saving || !(chosen || query.trim())"
        @click="declare"
      >{{ saving ? 'Declaring…' : 'Declare' }}</Button>
    </template>
  </Dialog>
</template>

<style scoped>
.picker { max-height: 16rem; overflow-y: auto; }
.picker-row {
    display: flex; align-items: center; gap: 0.6rem; cursor: pointer;
    padding: 0.4rem 0.5rem; border-radius: 0.5rem;
    border: 1px solid transparent;
    transition: background 150ms, border-color 150ms;
}
.picker-row:hover { background: var(--color-slate-50); }
.picker-row:focus-visible { outline: 2px solid var(--color-blue-500); }
.picker-row.is-chosen {
    border-color: var(--color-blue-300); background: var(--color-blue-50);
}
.picker-row.is-declared { opacity: 0.55; }
</style>
