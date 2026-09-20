<script setup>
import NavIcon from './NavIcon.vue'

defineProps({
  // Viewport coordinates of the rail row this card is previewing. The card is
  // teleported to <body> because the rail itself clips its overflow.
  anchor: { type: Object, required: true },
  group: { type: String, default: '' },
  label: { type: String, required: true },
  icon: { type: String, default: '' },
  badge: { type: [Number, String], default: null },
  tone: { type: String, default: 'info' },
  active: { type: Boolean, default: false },
})
</script>

<template>
  <Teleport to="body">
    <div
      class="sb-preview"
      :class="{ 'is-active': active }"
      :style="{ left: `${anchor.left}px`, top: `${anchor.top}px` }"
      role="tooltip"
      aria-hidden="true"
    >
      <NavIcon v-if="icon" :name="icon" :size="17" class="sb-preview-icon" />
      <div class="sb-preview-text">
        <div v-if="group" class="sb-preview-group">{{ group }}</div>
        <div class="sb-preview-label">{{ label }}</div>
      </div>
      <span v-if="badge" class="sb-preview-badge" :class="`sb-preview-badge-${tone}`">{{ badge }}</span>
    </div>
  </Teleport>
</template>

<style scoped>
.sb-preview {
  position: fixed;
  z-index: 60;
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 0.4375rem 0.75rem 0.4375rem 0.625rem;
  border-radius: 0.625rem;
  background: var(--color-surface);
  border: 1px solid var(--color-slate-200);
  box-shadow: 0 10px 28px rgba(15, 23, 42, 0.16);
  pointer-events: none;
  white-space: nowrap;
  transform: translateY(-50%);
  /* The card follows the cursor down a column of rows, so it slides rather
     than blinking out and back in at each one. */
  transition: top 120ms cubic-bezier(0.4, 0, 0.2, 1);
}

@media (prefers-reduced-motion: reduce) {
  .sb-preview { transition: none; }
}

/* The rail itself is gone below 768px, and the card lives outside it (teleport),
   so nothing else would take it down when the viewport narrows. */
@media (max-width: 767px) {
  .sb-preview { display: none; }
}

.sb-preview-icon { color: var(--color-slate-400); }
.sb-preview.is-active .sb-preview-icon { color: var(--color-blue-600); }

.sb-preview-group {
  font-size: 0.5625rem;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  font-weight: 700;
  color: var(--color-slate-400);
  line-height: 1.2;
}

.sb-preview-label {
  font-size: 0.8125rem;
  font-weight: 600;
  color: var(--color-slate-900);
  line-height: 1.3;
}

.sb-preview-badge {
  background: var(--color-blue-100);
  color: var(--color-blue-800);
  font-size: 0.625rem;
  font-weight: 600;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.625rem;
  min-width: 1.125rem;
  text-align: center;
  font-variant-numeric: tabular-nums;
}

.sb-preview-badge-warning { background: var(--color-amber-100); color: var(--color-amber-800); }
.sb-preview-badge-danger { background: var(--color-red-100); color: var(--color-red-800); }
</style>
