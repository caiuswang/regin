<script setup>
// Modal dialog built on Reka's DialogRoot: focus-trap, scroll-lock,
// Escape-to-close, click-outside, and aria-modal/labelledby are all handled
// by the primitive — the gaps the audit found in all 4 hand-rolled modals.
// Usage: <Dialog v-model:open="x" title="…"> body </Dialog> with optional
// #trigger and #footer slots.
import { DialogRoot, DialogTrigger, DialogPortal, DialogOverlay, DialogContent, DialogTitle, DialogDescription, DialogClose } from 'reka-ui'

const open = defineModel('open', { type: Boolean, default: false })
// Reka's own auto-focus stays prevented; a consumer that wants a specific
// field focused listens for this instead of racing the portal's mount.
defineEmits(['open-auto-focus'])
defineProps({
  title: { type: String, default: '' },
  description: { type: String, default: '' },
  // Opt-in wider shell for bodies that carry their own wide content — a
  // terminal capture, a diff — which the default reading width squeezes into
  // a horizontal-scroll ribbon.
  wide: { type: Boolean, default: false },
})

// A `[data-ref-menu]` popup is teleported to <body> from a field inside this
// dialog, so reka reads a click on it as a click outside and dismisses the
// dialog, and the draft with it.
function keepForRefMenu(event) {
  const target = event.detail?.originalEvent?.target ?? event.target
  if (target instanceof Element && target.closest('[data-ref-menu]')) event.preventDefault()
}
</script>

<template>
  <DialogRoot v-model:open="open">
    <DialogTrigger v-if="$slots.trigger" as-child>
      <slot name="trigger" />
    </DialogTrigger>
    <DialogPortal>
      <DialogOverlay class="ds-dialog-overlay" />
      <DialogContent
        class="ds-dialog"
        :class="{ 'ds-dialog-wide': wide }"
        @open-auto-focus.prevent="$emit('open-auto-focus', $event)"
        @pointer-down-outside="keepForRefMenu"
        @focus-outside="keepForRefMenu"
        @interact-outside="keepForRefMenu"
      >
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
  background: rgba(var(--scrim-rgb), 0.45);
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
.ds-dialog-wide { width: min(72rem, calc(100vw - 3rem)); }
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
