<script setup>
// Pick one feature by searching the server's feature index.
//
// The search is `GET /api/features?q=` — the index's own predicate, which
// leaves abandoned features out — so a feature on page 5 of a 200-row list is
// one word away rather than unreachable. Nothing is preselected: every
// caller is collecting a human's assertion, and a default would be a guess
// wearing their signature.
//
// Every keystroke asks; a response that arrives after a newer one is dropped
// by its sequence number rather than by waiting a fixed time before asking.
import { computed, nextTick, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'

const props = defineProps({
  placeholder: { type: String, default: 'Search features…' },
  disabled: { type: Boolean, default: false },
  label: { type: String, default: 'Blame a feature' },
  testid: { type: String, default: 'feature-picker' },
  size: { type: Number, default: 20 },
  // The feature the model already names, so an edit form opens on its chip
  // rather than on an empty search that reads as "no feature".
  current: { type: Object, default: null },
})
const model = defineModel({ type: String, default: '' })
const emit = defineEmits(['picked'])

const query = ref('')
const open = ref(false)
const rows = ref([])
const total = ref(0)
const error = ref('')
const loading = ref(false)
const active = ref(0)
const chosen = ref(props.current && model.value ? props.current : null)
const field = ref(null)
let issued = 0

const listId = `fp-${Math.random().toString(36).slice(2, 9)}`
const more = computed(() => Math.max(0, total.value - rows.value.length))

async function search() {
  const mine = ++issued
  loading.value = true
  error.value = ''
  const params = new URLSearchParams({ size: String(props.size), sort: 'activity:desc' })
  const q = query.value.trim()
  if (q) params.set('q', q)
  try {
    const body = await api.get(`/features?${params}`)
    if (mine !== issued) return
    rows.value = body.items || []
    total.value = body.pagination?.total ?? rows.value.length
    active.value = 0
  } catch (e) {
    if (mine === issued) error.value = e?.message || String(e)
  } finally {
    if (mine === issued) loading.value = false
  }
}

function reveal() {
  if (props.disabled) return
  if (!open.value) search()
  open.value = true
}

function onInput() {
  open.value = true
  search()
}

function choose(row) {
  chosen.value = row
  model.value = String(row.id)
  open.value = false
  query.value = ''
  emit('picked', row)
}

async function clear() {
  chosen.value = null
  model.value = ''
  await nextTick()
  field.value?.focus()
}

function onKeydown(event) {
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault()
    if (!open.value) { reveal(); return }
    const step = event.key === 'ArrowDown' ? 1 : -1
    active.value = Math.min(Math.max(0, active.value + step), rows.value.length - 1)
  } else if (event.key === 'Enter' && open.value && rows.value[active.value]) {
    event.preventDefault()
    choose(rows.value[active.value])
  } else if (event.key === 'Escape' && open.value) {
    event.preventDefault()
    event.stopPropagation()
    open.value = false
  }
}

// A parent that resets the model (a triage cursor move) resets the chip too.
watch(model, (v) => { if (!v) chosen.value = null })

// The current feature may be fetched after the picker mounted, or the model
// may be set to match an already-known current; update the chip only when the
// two agree, without overriding a user pick.
watch([() => props.current, model], ([cur, id]) => {
  if (cur && id === String(cur.id) && (!chosen.value || String(chosen.value.id) !== id)) {
    chosen.value = cur
  }
})

defineExpose({ focus: () => field.value?.focus() })
</script>

<template>
  <div class="fp">
    <div v-if="chosen && model" class="fp-chosen" :data-testid="`${testid}-chosen`" data-pl-part="input">
      <span class="fp-chosen__label">#{{ chosen.id }} {{ chosen.title }}</span>
      <Button
        variant="ghost" size="icon" class="fp-clear" :disabled="disabled"
        :aria-label="`Change feature (now #${chosen.id})`"
        :data-testid="`${testid}-clear`"
        @click.stop="clear"
      ><Icon name="x" :size="12" /></Button>
    </div>
    <template v-else>
      <Input
        ref="field"
        v-model="query"
        type="text"
        role="combobox"
        autocomplete="off"
        :aria-label="label"
        :aria-expanded="open"
        :aria-controls="listId"
        :aria-activedescendant="open && rows[active] ? `${listId}-${rows[active].id}` : undefined"
        :placeholder="placeholder"
        :disabled="disabled"
        :data-testid="testid"
        data-pl-part="input"
        @focus="reveal"
        @click="reveal"
        @input="onInput"
        @keydown="onKeydown"
      />
      <ul
        v-if="open"
        :id="listId"
        class="fp-list"
        role="listbox"
        :aria-label="label"
        :data-testid="`${testid}-list`"
        data-pl-part="picker"
      >
        <li v-if="error" class="fp-note fp-note--error" role="alert">{{ error }}</li>
        <li v-else-if="!rows.length" class="fp-note">
          {{ loading ? 'Searching…' : 'No live feature matches.' }}
        </li>
        <li
          v-for="(row, i) in rows"
          :id="`${listId}-${row.id}`"
          :key="row.id"
          role="option"
          class="fp-option"
          :class="{ 'is-active': i === active }"
          :aria-selected="i === active"
          :data-testid="`${testid}-option-${row.id}`"
          data-pl-part="picker-row"
          @mousedown.prevent
          @click.stop="choose(row)"
        >#{{ row.id }} {{ row.title }}</li>
        <li v-if="more" class="fp-note" :data-testid="`${testid}-more`">
          {{ more }} more — keep typing to narrow
        </li>
      </ul>
    </template>
  </div>
</template>

<style scoped>
.fp { display: flex; flex-direction: column; gap: 0.25rem; min-width: 0; }
.fp-chosen {
    display: flex; align-items: center; gap: 0.35rem; min-width: 0;
    border: 1px solid var(--color-border); border-radius: var(--radius-lg);
    background: var(--color-surface-2); padding: 0.2rem 0.25rem 0.2rem 0.6rem;
    font-size: 12.5px; color: var(--color-fg);
}
.fp-chosen__label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fp-chosen .fp-clear { width: 24px; height: 24px; flex-shrink: 0; }
.fp-list {
    display: flex; flex-direction: column; max-height: 14rem; overflow-y: auto;
    border: 1px solid var(--color-border); border-radius: var(--radius-lg);
    background: var(--color-surface); padding: 0.2rem;
}
.fp-option {
    padding: 0.35rem 0.5rem; border-radius: var(--radius-md); cursor: pointer;
    font-size: 12.5px; color: var(--color-fg); overflow-wrap: anywhere;
}
.fp-option.is-active, .fp-option:hover { background: var(--color-surface-2); }
.fp-note { padding: 0.35rem 0.5rem; font-size: 11.5px; color: var(--color-fg-muted); }
.fp-note--error { color: var(--color-danger); }
</style>
