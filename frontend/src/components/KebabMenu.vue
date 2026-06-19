<script setup>
import { ref, onBeforeUnmount } from 'vue'
import Button from './ui/Button.vue'

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
    <Button
      variant="ghost"
      size="icon"
      class="kebab-trigger"
      :aria-label="ariaLabel"
      :aria-expanded="open"
      :aria-haspopup="true"
      @click="toggle">
      <span aria-hidden="true">⋯</span>
    </Button>
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
  border: 1px solid var(--color-gray-200);
  background: var(--color-white);
  color: var(--color-slate-500);
  font-size: 1.125rem;
  line-height: 1;
}
.kebab-trigger:hover {
  background: var(--color-slate-50);
  border-color: var(--color-slate-300);
  color: var(--color-slate-900);
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
  background: var(--color-white);
  border: 1px solid var(--color-gray-200);
  border-radius: 0.5rem;
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
}
.kebab-item {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.4375rem 0.625rem;
  font-size: 0.8125rem;
  color: var(--color-slate-900);
  background: transparent;
  border: 0;
  border-radius: 0.375rem;
  cursor: pointer;
}
.kebab-item:hover { background: var(--color-slate-100); }
.kebab-item:disabled { opacity: 0.5; cursor: not-allowed; }
.kebab-item-danger { color: var(--color-red-700); }
.kebab-item-danger:hover { background: var(--color-red-50); }
</style>
