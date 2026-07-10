<script setup>
import { ref, computed, watch, nextTick, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api.js'

const props = defineProps({
  open: { type: Boolean, default: false },
})
const emit = defineEmits(['update:open'])

const router = useRouter()
const query = ref('')
const groups = ref([])
const loading = ref(false)
const cursor = ref(0)
const inputEl = ref(null)
let debounceTimer = null

const flatItems = computed(() => groups.value.flatMap(g => g.items))

watch(() => props.open, async (val) => {
  if (val) {
    query.value = ''
    groups.value = []
    cursor.value = 0
    await nextTick()
    inputEl.value?.focus()
  }
})

watch(query, (q) => {
  if (debounceTimer) clearTimeout(debounceTimer)
  if (!q.trim()) {
    groups.value = []
    loading.value = false
    return
  }
  loading.value = true
  debounceTimer = setTimeout(() => runSearch(q.trim()), 180)
})

async function runSearch(q) {
  try {
    const data = await api.get(`/quicksearch?q=${encodeURIComponent(q)}`)
    // Drop stale responses if the user kept typing.
    if (q !== query.value.trim()) return
    groups.value = data.groups || []
    cursor.value = 0
  } catch {
    groups.value = []
  } finally {
    loading.value = false
  }
}

function close() {
  emit('update:open', false)
}

function openItem(item) {
  if (!item) return
  router.push(item.href)
  close()
}

function moveCursor(delta) {
  const total = flatItems.value.length
  if (total === 0) return
  cursor.value = (cursor.value + delta + total) % total
  scrollCursorIntoView()
}

function scrollCursorIntoView() {
  nextTick(() => {
    const el = document.querySelector('.palette-item.is-cursor')
    if (el && typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'nearest' })
    }
  })
}

function flatIndex(groupIdx, itemIdx) {
  let base = 0
  for (let i = 0; i < groupIdx; i++) base += groups.value[i].items.length
  return base + itemIdx
}

function onKeydown(e) {
  if (e.key === 'Escape') {
    e.preventDefault()
    close()
  } else if (e.key === 'ArrowDown') {
    e.preventDefault()
    moveCursor(1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    moveCursor(-1)
  } else if (e.key === 'Enter') {
    e.preventDefault()
    openItem(flatItems.value[cursor.value])
  }
}

// Global ⌘K / Ctrl+K opener
function onGlobalKey(e) {
  const mod = e.metaKey || e.ctrlKey
  if (mod && (e.key === 'k' || e.key === 'K')) {
    e.preventDefault()
    emit('update:open', true)
  }
}

onMounted(() => window.addEventListener('keydown', onGlobalKey))
onBeforeUnmount(() => window.removeEventListener('keydown', onGlobalKey))

const iconPathByGroup = {
  patterns: 'M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z',
  skills: 'm12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z',
  trace: 'M22 12 18 12 15 21 9 3 6 12 2 12',
  rules: 'M4 7h16M4 12h10M4 17h7',
}
</script>

<template>
  <Teleport to="body">
    <Transition name="palette">
      <div v-if="open" class="palette-overlay" @mousedown.self="close">
        <div class="palette-card" role="dialog" aria-label="Quick search" @keydown="onKeydown">
          <div class="palette-input-row">
            <svg class="palette-input-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
            <input
              ref="inputEl"
              v-model="query"
              type="search"
              class="palette-input focus-visible:outline-none"
              placeholder="Search patterns, skills, sessions, rules…"
              aria-label="Quick search query"
            >
            <kbd class="palette-kbd">esc</kbd>
          </div>

          <div class="palette-results">
            <div v-if="loading" class="palette-empty">Searching…</div>
            <div v-else-if="!query.trim()" class="palette-empty">
              Type to search across <strong>patterns</strong>, <strong>skills</strong>, <strong>sessions</strong>, and <strong>rules</strong>.
            </div>
            <div v-else-if="!groups.length" class="palette-empty">No matches for "{{ query }}".</div>
            <template v-else>
              <div v-for="(group, gi) in groups" :key="group.label" class="palette-group">
                <div class="palette-group-label">{{ group.label }}</div>
                <button
                  v-for="(item, ii) in group.items"
                  :key="group.label + ii"
                  type="button"
                  class="palette-item focus-visible:outline-2 focus-visible:outline-blue-500"
                  :class="{ 'is-cursor': flatIndex(gi, ii) === cursor }"
                  @mouseenter="cursor = flatIndex(gi, ii)"
                  @click="openItem(item)"
                >
                  <svg class="palette-item-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                    <path v-if="group.icon === 'trace'" d="M22 12 18 12 15 21 9 3 6 12 2 12"/>
                    <path v-else :d="iconPathByGroup[group.icon] || iconPathByGroup.patterns"/>
                  </svg>
                  <span class="palette-item-title">{{ item.title }}</span>
                  <span v-if="item.subtitle" class="palette-item-subtitle">{{ item.subtitle }}</span>
                  <span v-if="flatIndex(gi, ii) === cursor" class="palette-item-enter">↵</span>
                </button>
              </div>
            </template>
          </div>

          <div class="palette-footer">
            <span><kbd class="palette-kbd">↑↓</kbd>navigate</span>
            <span><kbd class="palette-kbd">↵</kbd>open</span>
            <span><kbd class="palette-kbd">esc</kbd>close</span>
          </div>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.palette-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  backdrop-filter: blur(4px);
  z-index: 100;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 12vh;
  padding-left: 1rem;
  padding-right: 1rem;
}

.palette-card {
  width: 100%;
  max-width: 38rem;
  background: var(--color-white);
  border-radius: 18px;
  box-shadow: 0 24px 64px rgba(15, 23, 42, 0.25);
  display: flex;
  flex-direction: column;
  overflow: hidden;
  max-height: 70vh;
}

.palette-input-row {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.875rem 1rem;
  border-bottom: 1px solid var(--color-slate-100);
}

.palette-input-icon {
  width: 20px;
  height: 20px;
  color: var(--color-slate-400);
  flex-shrink: 0;
}

.palette-input {
  flex: 1;
  font-size: 0.95rem;
  background: transparent;
  border: 0;
  outline: 0;
  color: var(--color-slate-900);
  min-width: 0;
}

.palette-input::placeholder { color: var(--color-slate-400); }

/* iOS Safari zooms the viewport in on focusing a field with computed
   font-size < 16px and never zooms back — bump the search box to 16px on
   touch devices. `any-pointer` (not `pointer`) so a touch device paired with
   a trackpad/mouse, whose primary pointer reports as fine, is still covered. */
@media (any-pointer: coarse) {
  .palette-input { font-size: 16px; }
}

.palette-kbd {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.6875rem;
  background: var(--color-slate-100);
  border: 1px solid var(--color-slate-200);
  color: var(--color-slate-500);
  padding: 0.125rem 0.375rem;
  border-radius: 0.25rem;
}

.palette-results {
  flex: 1;
  overflow-y: auto;
  padding: 0.5rem 0.5rem 0.5rem;
}

.palette-empty {
  padding: 2rem 1.5rem;
  text-align: center;
  color: var(--color-slate-500);
  font-size: 0.875rem;
}

.palette-empty strong { color: var(--color-slate-900); font-weight: 600; }

.palette-group { margin-bottom: 0.25rem; }

.palette-group-label {
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: var(--color-slate-400);
  font-weight: 600;
  padding: 0.75rem 0.75rem 0.25rem;
}

.palette-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  width: 100%;
  padding: 0.625rem 0.75rem;
  border-radius: 0.625rem;
  border: 0;
  background: transparent;
  cursor: pointer;
  text-align: left;
  font-size: 0.875rem;
  color: var(--color-slate-800);
  transition: background-color 100ms;
}

.palette-item.is-cursor {
  background: var(--color-blue-50);
  color: var(--color-blue-800);
}

.palette-item-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--color-slate-500);
}

.palette-item.is-cursor .palette-item-icon { color: var(--color-blue-800); }

.palette-item-title {
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  flex-shrink: 1;
  min-width: 0;
}

.palette-item-subtitle {
  font-size: 0.75rem;
  color: var(--color-slate-400);
  margin-left: auto;
  padding-left: 0.75rem;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 50%;
}

.palette-item.is-cursor .palette-item-subtitle { color: var(--color-blue-500); }

.palette-item-enter {
  margin-left: 0.5rem;
  font-size: 0.875rem;
  color: var(--color-blue-500);
}

.palette-footer {
  display: flex;
  align-items: center;
  gap: 1rem;
  padding: 0.625rem 1rem;
  border-top: 1px solid var(--color-slate-100);
  font-size: 0.6875rem;
  color: var(--color-slate-500);
}

.palette-footer span { display: inline-flex; align-items: center; gap: 0.375rem; }

/* Transitions */
.palette-enter-active, .palette-leave-active { transition: opacity 150ms; }
.palette-enter-from, .palette-leave-to { opacity: 0; }
.palette-enter-active .palette-card, .palette-leave-active .palette-card {
  transition: transform 180ms cubic-bezier(0.16, 1, 0.3, 1);
}
.palette-enter-from .palette-card { transform: translateY(-8px); }
.palette-leave-to .palette-card { transform: translateY(-4px); }

.palette-results::-webkit-scrollbar { width: 8px; }
.palette-results::-webkit-scrollbar-thumb { background: var(--color-slate-300); border-radius: 4px; }
</style>
