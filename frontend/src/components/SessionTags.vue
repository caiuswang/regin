<script setup>
// One session's tags rendered as chips, plus a compact add-tag control.
// Builtin category chips (user / topic-proposal / system) are colored and
// fixed; custom chips are neutral and carry an ✕ to remove. The "＋" reveals
// an inline slug input. Purely presentational — mutations are emitted up to
// SessionsView, which owns the API calls and row patching.
import { ref, nextTick } from 'vue'

defineProps({
  tags: { type: Array, default: () => [] },
  traceId: { type: String, required: true },
})
const emit = defineEmits(['add', 'remove'])

// Per-builtin tone. Custom tags share one neutral tone so they read as a
// distinct class from the intrinsic categories.
const BUILTIN_TONE = {
  user: 'tag-chip--user',
  'topic-proposal': 'tag-chip--proposal',
  system: 'tag-chip--system',
}
const BUILTIN_LABEL = {
  user: 'user',
  'topic-proposal': 'proposal',
  system: 'system',
}

function chipClass(tag) {
  if (tag.builtin) return BUILTIN_TONE[tag.slug] || 'tag-chip--user'
  return 'tag-chip--custom'
}

function chipLabel(tag) {
  return tag.builtin ? (BUILTIN_LABEL[tag.slug] || tag.slug) : tag.slug
}

const adding = ref(false)
const draft = ref('')
const inputEl = ref(null)

async function openAdd() {
  adding.value = true
  await nextTick()
  if (inputEl.value) inputEl.value.focus()
}

function cancelAdd() {
  adding.value = false
  draft.value = ''
}

function submitAdd() {
  const slug = draft.value.trim().toLowerCase()
  if (slug) emit('add', slug)
  cancelAdd()
}
</script>

<template>
  <span class="tag-chips">
    <span
      v-for="tag in tags"
      :key="tag.slug"
      class="tag-chip"
      :class="chipClass(tag)"
      :title="tag.builtin ? `${tag.slug} (builtin category)` : `custom tag: ${tag.slug}`"
    >
      {{ chipLabel(tag) }}
      <button
        v-if="!tag.builtin"
        type="button"
        class="tag-chip__x focus-visible:outline-2 focus-visible:outline-blue-500"
        :aria-label="`Remove tag ${tag.slug}`"
        :title="`Remove tag ${tag.slug}`"
        @click.stop="emit('remove', tag.slug)"
      >×</button>
    </span>

    <span v-if="adding" class="tag-add" @click.stop>
      <input
        ref="inputEl"
        v-model="draft"
        type="text"
        class="tag-add__input focus-visible:outline-2 focus-visible:outline-blue-500"
        placeholder="tag…"
        aria-label="New tag slug"
        maxlength="40"
        @keydown.enter.prevent="submitAdd"
        @keydown.esc.prevent="cancelAdd"
        @blur="cancelAdd"
      >
    </span>
    <button
      v-else
      type="button"
      class="tag-add__btn focus-visible:outline-2 focus-visible:outline-blue-500"
      aria-label="Add a tag to this session"
      title="Add a tag"
      @click.stop="openAdd"
    >+ tag</button>
  </span>
</template>

<style scoped>
.tag-chips {
  align-items: center;
  display: inline-flex;
  flex-wrap: wrap;
  gap: 0.25rem;
}
.tag-chip {
  align-items: center;
  border-radius: 0.25rem;
  display: inline-flex;
  font-size: 10px;
  font-weight: 600;
  gap: 0.125rem;
  letter-spacing: 0.02em;
  line-height: 1;
  padding: 0.15rem 0.35rem;
  text-transform: uppercase;
}
.tag-chip--user {
  background: var(--color-slate-100);
  color: var(--color-slate-600);
}
.tag-chip--proposal {
  background: var(--color-violet-100);
  color: var(--color-violet-800);
}
.tag-chip--system {
  background: var(--color-sky-100);
  color: var(--color-sky-800);
}
.tag-chip--custom {
  background: var(--color-amber-100);
  color: var(--color-amber-800);
  text-transform: none;
  letter-spacing: normal;
}
.tag-chip__x {
  color: currentColor;
  cursor: pointer;
  font-size: 12px;
  line-height: 1;
  opacity: 0.6;
}
.tag-chip__x:hover {
  opacity: 1;
}
.tag-add__btn {
  border: 1px dashed var(--color-gray-300);
  border-radius: 0.25rem;
  color: var(--color-gray-500);
  cursor: pointer;
  font-size: 10px;
  line-height: 1;
  padding: 0.15rem 0.35rem;
}
.tag-add__btn:hover {
  border-color: var(--color-gray-400);
  color: var(--color-gray-700);
}
.tag-add__input {
  border: 1px solid var(--color-blue-300);
  border-radius: 0.25rem;
  font-size: 11px;
  padding: 0.1rem 0.3rem;
  width: 6rem;
}
</style>
