<script setup>
import { ref, onBeforeUnmount } from 'vue'

const props = defineProps({
  items: { type: Array, required: true },
  ariaLabel: { type: String, default: 'More actions' },
})

const open = ref(false)
const rootEl = ref(null)

function toggle() {
  open.value = !open.value
}

function close() {
  open.value = false
}

function onItemClick(item) {
  close()
  item.action?.()
}

function onDocClick(e) {
  if (!open.value) return
  if (rootEl.value && !rootEl.value.contains(e.target)) close()
}

function onKey(e) {
  if (e.key === 'Escape') close()
}

document.addEventListener('mousedown', onDocClick)
document.addEventListener('keydown', onKey)
onBeforeUnmount(() => {
  document.removeEventListener('mousedown', onDocClick)
  document.removeEventListener('keydown', onKey)
})
</script>

<template>
  <div ref="rootEl" class="relative inline-block">
    <button
      type="button"
      class="kebab-trigger focus-visible:outline-2 focus-visible:outline-blue-500"
      :aria-label="ariaLabel"
      :aria-expanded="open"
      :aria-haspopup="true"
      @click="toggle">
      <span aria-hidden="true">⋯</span>
    </button>
    <ul
      v-if="open"
      role="menu"
      class="kebab-menu">
      <li v-for="it in items" :key="it.label" role="none">
        <button
          type="button"
          role="menuitem"
          class="kebab-item focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="{ 'kebab-item-danger': it.danger }"
          :disabled="it.disabled"
          @click="onItemClick(it)">
          {{ it.label }}
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.kebab-trigger {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 0.5rem;
  border: 1px solid #e5e7eb;
  background: #fff;
  color: #64748b;
  font-size: 1.125rem;
  line-height: 1;
  cursor: pointer;
  transition: background-color 150ms, border-color 150ms;
}
.kebab-trigger:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
  color: #0f172a;
}
.kebab-menu {
  position: absolute;
  right: 0;
  top: calc(100% + 4px);
  z-index: 20;
  min-width: 180px;
  list-style: none;
  margin: 0;
  padding: 0.25rem;
  background: #fff;
  border: 1px solid #e5e7eb;
  border-radius: 0.5rem;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
}
.kebab-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.4375rem 0.625rem;
  font-size: 0.8125rem;
  color: #0f172a;
  background: transparent;
  border: 0;
  border-radius: 0.375rem;
  cursor: pointer;
}
.kebab-item:hover { background: #f1f5f9; }
.kebab-item:disabled { opacity: 0.5; cursor: not-allowed; }
.kebab-item-danger { color: #b91c1c; }
.kebab-item-danger:hover { background: #fef2f2; }
</style>
