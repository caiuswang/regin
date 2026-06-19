<script setup>
// Modal dialog built on Reka's DialogRoot: focus-trap, scroll-lock,
// Escape-to-close, click-outside, and aria-modal/labelledby are all handled
// by the primitive — the gaps the audit found in all 4 hand-rolled modals.
// Usage: <Dialog v-model:open="x" title="…"> body </Dialog> with optional
// #trigger and #footer slots.
import { DialogRoot, DialogTrigger, DialogPortal, DialogOverlay, DialogContent, DialogTitle, DialogDescription, DialogClose } from 'reka-ui'

const open = defineModel('open', { type: Boolean, default: false })
defineProps({
  title: { type: String, default: '' },
  description: { type: String, default: '' },
})
</script>

<template>
  <DialogRoot v-model:open="open">
    <DialogTrigger v-if="$slots.trigger" as-child>
      <slot name="trigger" />
    </DialogTrigger>
    <DialogPortal>
      <DialogOverlay class="ds-dialog-overlay" />
      <DialogContent class="ds-dialog" @open-auto-focus.prevent>
        <DialogTitle v-if="title" class="ds-dialog-title">{{ title }}</DialogTitle>
        <DialogDescription v-if="description" class="ds-dialog-desc">
          {{ description }}
        </DialogDescription>
        <div class="ds-dialog-body"><slot /></div>
        <div v-if="$slots.footer" class="ds-dialog-footer"><slot name="footer" /></div>
        <DialogClose class="ds-dialog-x" aria-label="Close">✕</DialogClose>
      </DialogContent>
    </DialogPortal>
  </DialogRoot>
</template>

<!-- NOT scoped: Reka portals this content to <body>, beyond scoped data-v. -->
<style>
.ds-dialog-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal);
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(2px);
}
.ds-dialog {
  position: fixed;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%);
  z-index: var(--z-modal);
  width: min(32rem, calc(100vw - 2rem));
  max-height: calc(100vh - 4rem);
  overflow-y: auto;
  background: var(--color-surface);
  color: var(--color-fg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--ds-shadow-lg);
  padding: 1.25rem;
}
.ds-dialog-title { font-size: 1rem; font-weight: 600; color: var(--color-fg); }
.ds-dialog-desc { margin-top: 0.25rem; font-size: 0.8125rem; color: var(--color-fg-muted); }
.ds-dialog-body { margin-top: 0.875rem; font-size: 0.8125rem; color: var(--color-fg); }
.ds-dialog-footer {
  margin-top: 1.25rem;
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
.ds-dialog-x {
  position: absolute;
  top: 0.75rem;
  right: 0.875rem;
  border: 0;
  background: transparent;
  color: var(--color-fg-subtle);
  cursor: pointer;
  font-size: 0.875rem;
  line-height: 1;
  border-radius: var(--radius-md);
}
.ds-dialog-x:hover { color: var(--color-fg); }
.ds-dialog-x:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
</style>
