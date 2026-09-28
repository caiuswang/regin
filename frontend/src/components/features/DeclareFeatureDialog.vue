<script setup>
// Declaring from the trace view: the trace is in hand, so a feature must be
// picked. The ergonomic direction of the pair, and the moment a human actually
// knows the answer — they have just read the session.
//
// The picker reads `GET /api/features` unchanged. No second endpoint and no
// picker variant: a list that is not the index's own list could offer a row
// the index hides, and the index exists precisely because it hides nothing.
// The attribute route takes the row's integer id, so only a clicked row can be
// declared — typed text is a search, never an id.
import { ref, watch } from 'vue'
import api from '../../api'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import { STAGE_COLOR } from '../../utils/ledgerFormat'

const props = defineProps({
  traceId: { type: String, required: true },
  declared: { type: Array, default: () => [] },
})
const emit = defineEmits(['close', 'declared'])

const query = ref('')
const chosen = ref(null)
const primary = ref(false)
const rows = ref([])
const error = ref('')
const saving = ref(false)

async function search() {
  const q = query.value.trim()
  const body = await api.get(
    `/features${q ? `?q=${encodeURIComponent(q)}` : ''}`)
  rows.value = body.items || []
  if (!rows.value.some(r => r.id === chosen.value)) chosen.value = null
}
watch(() => props.traceId, search, { immediate: true })

async function declare() {
  if (chosen.value == null) return
  saving.value = true
  error.value = ''
  // `api`'s mutations report a refusal by returning, never by throwing — and
  // the refusal that matters here is the server naming a key or trace it does
  // not hold, which a swallowed error would turn into a silent no-op.
  const result = await api.post(`/features/${chosen.value}/attribute`,
                                { trace_id: props.traceId,
                                  is_primary: primary.value })
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
    title="Declare what this session built"
    description="Every feature regin knows. Pick the one this session built."
    @update:open="emit('close')"
  >
    <Input
      v-model="query"
      class="w-full"
      placeholder="search feature title, then pick a row"
      @keydown.enter.prevent="search"
    />

    <p
      v-if="error"
      class="mt-2 rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700"
      role="alert"
    >{{ error }}</p>

    <ul class="picker mt-2">
      <li
        v-for="r in rows"
        :key="r.id"
        :class="['picker-row cursor-pointer hover:bg-slate-50 focus-visible:outline-2 focus-visible:outline-blue-500',
                 chosen === r.id ? 'is-chosen' : '',
                 declared.includes(r.id) ? 'is-declared' : '']"
        role="button"
        tabindex="0"
        :aria-pressed="chosen === r.id"
        :data-testid="`declare-feature-row-${r.id}`"
        @click="chosen = r.id"
        @keydown.enter="chosen = r.id"
      >
        <span class="w-24 flex-none font-mono text-xs font-semibold text-slate-700">#{{ r.id }}</span>
        <Badge
          v-if="r.stage"
          :color="STAGE_COLOR[r.stage] || 'gray'"
          :label="r.stage"
        />
        <span v-else class="flex-none text-[11px] italic text-slate-400">no stage</span>
        <span class="min-w-0 flex-1 truncate text-sm text-slate-700">{{ r.title || '—' }}</span>
        <span v-if="declared.includes(r.id)" class="flex-none text-[10px] text-slate-400">already declared</span>
      </li>
      <li v-if="!rows.length" class="px-2 py-3 text-xs text-slate-500">
        Nothing matches. Declare the feature first, then attribute this session to it.
      </li>
    </ul>

    <Checkbox v-model="primary" class="mt-3 items-start">
      <span class="text-xs text-slate-600">
        <strong class="font-semibold text-slate-800">Make this the session's primary feature.</strong>
        Off by default. Turning it on moves this whole session's cost —
        the figure shown above — onto the feature you picked.
      </span>
    </Checkbox>

    <p v-if="declared.length" class="mt-2 text-[11px] text-slate-500">
      This session already declares {{ declared.map(id => `#${id}`).join(', ') }}. A second
      declaration is allowed, but `regin defects blame @session` then refuses
      to guess which of the two a bug came from.
    </p>

    <template #footer>
      <Button variant="secondary" @click="emit('close')">Cancel</Button>
      <Button
        variant="primary"
        :disabled="saving || chosen == null"
        @click="declare"
      >{{ saving ? 'Declaring…' : 'Declare' }}</Button>
    </template>
  </Dialog>
</template>

<style scoped>
.picker { max-height: 18rem; overflow-y: auto; }
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
