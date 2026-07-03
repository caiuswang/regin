<script setup>
import { ref, computed, watch, onBeforeUnmount } from 'vue'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import Badge from '../Badge.vue'

const props = defineProps({
  topics: { type: Array, default: () => [] },
  driftTopicIds: { type: Array, default: () => [] },
  disabled: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})

// `regenerate` carries the chosen topic ids; an empty array means "all"
// (the backend treats absent/empty scope as a full re-draft).
const emit = defineEmits(['regenerate'])

const open = ref(false)
const rootEl = ref(null)
const selected = ref(new Set())

const topicIds = computed(() => props.topics.map((t) => t.id).filter(Boolean))
const driftSet = computed(() => new Set(props.driftTopicIds))
const allSelected = computed(
  () => topicIds.value.length > 0 && topicIds.value.every((id) => selected.value.has(id)),
)
const selectedCount = computed(() => topicIds.value.filter((id) => selected.value.has(id)).length)

function selectAll() {
  selected.value = new Set(topicIds.value)
}

function toggleId(id) {
  const next = new Set(selected.value)
  if (next.has(id)) next.delete(id)
  else next.add(id)
  selected.value = next
}

function isChecked(id) {
  return selected.value.has(id)
}

function toggleOpen() {
  if (props.disabled) return
  // No topics to choose from → behave like a plain full regenerate.
  if (topicIds.value.length === 0) {
    emit('regenerate', [])
    return
  }
  if (!open.value) selectAll() // fresh selection each time the menu opens
  open.value = !open.value
}

function close() {
  open.value = false
}

function confirm() {
  const ids = topicIds.value.filter((id) => selected.value.has(id))
  if (ids.length === 0) return
  // All selected ⇒ send [] so the backend takes the plain full-redraft path.
  emit('regenerate', allSelected.value ? [] : ids)
  close()
}

function onDocClick(e) {
  if (!open.value) return
  if (rootEl.value && !rootEl.value.contains(e.target)) close()
}

function onKey(e) {
  if (e.key === 'Escape') close()
}

// Keep the selection valid if the topic set changes underneath an open menu.
watch(topicIds, (ids) => {
  const valid = new Set(ids)
  selected.value = new Set([...selected.value].filter((id) => valid.has(id)))
})

document.addEventListener('mousedown', onDocClick)
document.addEventListener('keydown', onKey)
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div ref="rootEl" class="regen-scope">
    <Button
      variant="secondary"
      :disabled="disabled"
      :aria-expanded="open"
      :aria-haspopup="true"
      @click="toggleOpen"
    >{{ busy ? 'Regenerating…' : 'Regenerate' }}<span v-if="topics.length" aria-hidden="true"> ▾</span></Button>

    <div v-if="open" role="dialog" aria-label="Choose topic wikis to regenerate" class="regen-scope-menu">
      <div class="regen-scope-head">
        <span class="regen-scope-title">Topics to regenerate</span>
        <Button variant="link" size="sm" :disabled="allSelected" @click="selectAll">All</Button>
      </div>
      <ul class="regen-scope-list">
        <li v-for="t in topics" :key="t.id" class="regen-scope-item">
          <Checkbox
            class="regen-scope-check"
            :model-value="isChecked(t.id)"
            @update:model-value="toggleId(t.id)"
          >
            <span class="regen-scope-name" :title="t.label">{{ t.label || t.id }}</span>
            <Badge v-if="driftSet.has(t.id)" color="yellow" label="drift" />
          </Checkbox>
        </li>
      </ul>
      <div class="regen-scope-foot">
        <Button
          variant="primary"
          size="sm"
          :disabled="selectedCount === 0 || busy"
          @click="confirm"
        >Regenerate {{ allSelected ? 'all' : selectedCount }}</Button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.regen-scope {
  position: relative;
  display: inline-block;
}
.regen-scope-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 4px);
  z-index: 20;
  width: 260px;
  padding: 0.5rem;
  background: var(--color-white);
  border: 1px solid var(--color-gray-200);
  border-radius: 0.5rem;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
}
.regen-scope-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0.125rem 0.25rem 0.375rem;
}
.regen-scope-title {
  font-size: 0.75rem;
  font-weight: 600;
  color: var(--color-slate-500);
  text-transform: uppercase;
  letter-spacing: 0.03em;
}
.regen-scope-list {
  list-style: none;
  margin: 0;
  padding: 0;
  max-height: 240px;
  overflow-y: auto;
}
.regen-scope-item {
  border-radius: 0.375rem;
}
.regen-scope-item:hover {
  background: var(--color-slate-50);
}
.regen-scope-check {
  display: flex;
  width: 100%;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0.25rem;
}
.regen-scope-name {
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.regen-scope-foot {
  display: flex;
  justify-content: flex-end;
  padding-top: 0.5rem;
  margin-top: 0.25rem;
  border-top: 1px solid var(--color-gray-100);
}
</style>
