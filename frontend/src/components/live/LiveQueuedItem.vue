<script setup>
// One row of the /live queued list. Two modes: the read-only message row the
// list has always rendered, and — only for a queue regin can actually write —
// an inline edit with a remove beside it.
//
// `editable` is per ROW, not per card: a session regin launched holds its queue
// in memory and hands each entry a stable id, while a terminal session's queue
// lives inside Claude Code, which offers no write path. So the controls follow
// the id. A row with no id renders exactly as before rather than offering a
// button that would silently change nothing.
//
// The draft is local and discarded on cancel; the parent owns the round trip,
// because a failed edit has to keep the row and say why.
import { ref, computed, nextTick } from 'vue'
import Button from '../ui/Button.vue'
import LiveQaGrowInput from './LiveQaGrowInput.vue'
import { stripMarkdown } from '../../utils/liveRows.js'

const props = defineProps({
  item: { type: Object, required: true },
  editable: { type: Boolean, default: false },
  // Dismiss-only rows exist too (a bridge steer chip): the typed keystrokes
  // can't be recalled, so it can be removed but never rewritten.
  removable: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['edit', 'remove'])

const editing = ref(false)
const draft = ref('')
const inputEl = ref(null)

// 'sdk' reads the same as 'bridge' deliberately: both are a message the
// operator sent that is waiting to be picked up.
const steer = computed(() => props.item.source === 'bridge'
  || props.item.source === 'sdk')
const label = computed(() => (steer.value ? '⧗ steering…' : '⧗ queued'))
const text = computed(() => stripMarkdown(props.item.content || ''))
const canSave = computed(() => !!draft.value.trim() && !props.busy)

function open() {
  // The RAW content, not the stripped display copy: saving the latter would
  // silently rewrite the operator's markdown into the card's rendering of it.
  draft.value = props.item.content || ''
  editing.value = true
  nextTick(() => inputEl.value?.$el?.focus?.())
}

function close() {
  editing.value = false
  draft.value = ''
}

function save() {
  if (!canSave.value) return
  emit('edit', { id: props.item.id, text: draft.value.trim(), done: close })
}
</script>

<template>
  <div
    class="live-row-msg live-queued-item"
    :class="{ 'live-queued-steer': steer, 'live-queued-editing': editing }"
    data-testid="live-queued-item"
    :title="editing ? undefined : text"
  >
    <div class="live-queued-head">
      <span class="live-msg-eyebrow">{{ label }}</span>
      <template v-if="(editable || removable) && !editing">
        <span class="live-queued-spacer" aria-hidden="true"></span>
        <Button
          v-if="editable"
          variant="link"
          size="sm"
          class="live-queued-act"
          aria-label="Edit this queued prompt"
          :disabled="busy"
          data-testid="live-queued-edit"
          @click="open"
        >edit</Button>
        <Button
          v-if="removable"
          variant="link"
          size="sm"
          class="live-queued-act live-queued-remove"
          aria-label="Remove this queued prompt"
          :disabled="busy"
          data-testid="live-queued-remove"
          @click="emit('remove', { id: item.id })"
        >✕</Button>
      </template>
    </div>

    <div v-if="!editing" class="live-msg-body">{{ text }}</div>

    <template v-else>
      <LiveQaGrowInput
        ref="inputEl"
        v-model="draft"
        aria-label="Edit this queued prompt"
        :disabled="busy"
        testid="live-queued-input"
        @enter="save"
      />
      <div class="live-queued-edit-act">
        <Button
          variant="link"
          size="sm"
          class="live-queued-act"
          :disabled="!canSave"
          data-testid="live-queued-save"
          @click="save"
        >save</Button>
        <Button
          variant="link"
          size="sm"
          class="live-queued-act live-queued-discard"
          :disabled="busy"
          data-testid="live-queued-cancel-edit"
          @click="close"
        >cancel</Button>
      </div>
    </template>
  </div>
</template>
