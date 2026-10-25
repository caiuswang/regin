<script setup>
// The head's list of what the editor's keys do (`editor/shortcuts.js`). `?`
// is not bound to it: in a body `?` is a character. Closing hands the focus
// back to the face the writer was in, not to this button.
//
// The panel has nothing focusable in it, so Reka leaves the focus where it
// was; it is moved in by hand, or Esc would go to the face rather than here.
import { nextTick, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Popover from '../ui/Popover.vue'
import { SHORTCUTS, isMac, shortcutLabel } from '../../editor/shortcuts.js'

const emit = defineEmits(['closed'])

const open = ref(false)
const panel = ref(null)
watch(open, (isOpen) => { if (isOpen) nextTick(() => panel.value?.focus()) })
const mac = isMac()
const rows = SHORTCUTS.map((row) => ({ action: row.action, keys: shortcutLabel(row, mac) }))

function onClosed(event) {
  event.preventDefault()
  emit('closed')
}
</script>

<template>
  <Popover v-model:open="open" side="bottom" align="end" @close-auto-focus="onClosed">
    <template #trigger>
      <Button
        variant="ghost" size="sm" data-testid="editor-shortcuts"
        aria-label="Shortcuts" title="Shortcuts" @mousedown.prevent
      >
        <Icon name="keyboard" :size="13" />
      </Button>
    </template>
    <div ref="panel" tabindex="-1" class="es-panel">
      <table class="es-table" data-testid="editor-shortcuts-list">
        <caption class="es-caption">Shortcuts</caption>
        <tbody>
          <tr v-for="row in rows" :key="row.action">
            <th scope="row" class="es-action">{{ row.action }}</th>
            <td class="es-keys"><kbd>{{ row.keys }}</kbd></td>
          </tr>
        </tbody>
      </table>
    </div>
  </Popover>
</template>

<!-- Not scoped: the popover is portalled to <body>, beyond the scope id. -->
<style>
.es-panel { outline: none; }
.es-table { border-collapse: collapse; font-size: 0.75rem; }
.es-caption {
    caption-side: top; text-align: left;
    padding-bottom: 0.375rem;
    font-weight: 600; color: var(--color-fg);
}
.es-action {
    padding: 0.1875rem 0.75rem 0.1875rem 0;
    font-weight: 400; text-align: left; color: var(--color-fg-muted);
}
/* `.ds-popover` caps the panel at 20rem; the long rows wrap between chords
   (a chord has no space in it) rather than spilling out of the box. */
.es-keys { padding: 0.1875rem 0; text-align: right; }
.es-keys kbd { font-family: var(--font-mono, ui-monospace, monospace); color: var(--color-fg); }
</style>
