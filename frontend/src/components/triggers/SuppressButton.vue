<script setup>
import { ref, computed, nextTick } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'

/**
 * Three-state inline control for marking a rule_trigger event as noise.
 *
 *   default    →  🔇 button (small, transparent)
 *   editing    →  text input "why?" + Save / Cancel — focus is auto-grabbed
 *   suppressed →  ↺ button (un-mark)
 *
 * The component owns its own network calls but emits `changed` after a
 * successful flip so the parent can refetch aggregates / metrics.
 */
const props = defineProps({
  triggerId: { type: Number, required: true },
  suppressed: { type: Boolean, default: false },
  // When false, the button is hidden entirely (used to gate non-violated
  // rules — there's no fire to suppress).
  enabled: { type: Boolean, default: true },
})
const emit = defineEmits(['changed'])

const editing = ref(false)
const reason = ref('')
const busy = ref(false)
const inputEl = ref(null)

const visible = computed(() => props.enabled || props.suppressed)

async function startEditing() {
  editing.value = true
  reason.value = ''
  await nextTick()
  inputEl.value?.focus()
}
function cancelEditing() {
  editing.value = false
  reason.value = ''
}
async function submitSuppress() {
  if (busy.value) return
  busy.value = true
  try {
    const res = await api.post(`/triggers/${props.triggerId}/suppress`, {
      reason: reason.value.trim() || null,
    })
    if (res?.ok) {
      editing.value = false
      reason.value = ''
      emit('changed')
    }
  } finally {
    busy.value = false
  }
}
async function unsuppress() {
  if (busy.value) return
  busy.value = true
  try {
    const res = await api.del(`/triggers/${props.triggerId}/suppress`)
    if (res?.ok) emit('changed')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <span v-if="visible" class="suppress-btn-root">
    <!-- Suppressed: show ↺ to undo. -->
    <Button
      v-if="suppressed"
      variant="ghost"
      size="sm"
      class="suppress-btn"
      :disabled="busy"
      :title="'Un-mark as noise'"
      aria-label="Un-mark as noise"
      @click.stop="unsuppress"
    >↺</Button>

    <!-- Default: open the reason input. The button always renders; the
         input shows as an overlay underneath it so we don't disturb
         the surrounding row layout. -->
    <Button
      v-else
      variant="ghost"
      size="sm"
      class="suppress-btn"
      :disabled="busy"
      title="Mark as noise"
      aria-label="Mark as noise"
      @click.stop="startEditing"
    >🔇</Button>

    <!-- Reason popover. Anchored to the root so it floats below the
         button without consuming row width. Click-stops on the overlay
         so clicks inside (input, buttons) don't bubble. -->
    <span
      v-if="editing"
      class="suppress-btn__pop"
      role="dialog"
      aria-label="Mark as noise — reason"
      @click.stop
    >
      <input
        ref="inputEl"
        v-model="reason"
        type="text"
        class="suppress-btn__input focus-visible:outline-2 focus-visible:outline-blue-500"
        placeholder="why? (optional)"
        maxlength="200"
        @keydown.enter.prevent="submitSuppress"
        @keydown.esc.prevent="cancelEditing"
      />
      <Button
        variant="primary"
        size="sm"
        class="suppress-btn__save"
        :disabled="busy"
        :title="busy ? 'Saving…' : 'Save (Enter)'"
        @click.stop="submitSuppress"
      >Save</Button>
      <Button
        variant="ghost"
        size="sm"
        class="suppress-btn"
        :disabled="busy"
        title="Cancel (Esc)"
        aria-label="Cancel"
        @click.stop="cancelEditing"
      >×</Button>
    </span>
  </span>
</template>

<style scoped>
.suppress-btn-root {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  position: relative;
}
.suppress-btn__pop {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 4px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 6px;
  background: var(--color-white);
  border: 1px solid var(--color-slate-300);
  border-radius: 6px;
  box-shadow: 0 4px 12px rgba(15, 23, 42, 0.08);
  z-index: 20;
  white-space: nowrap;
}
.suppress-btn {
  background: transparent;
  border: 1px solid var(--color-slate-200);
  border-radius: 4px;
  padding: 1px 6px;
  cursor: pointer;
  font-size: 12px;
  line-height: 1.4;
  color: var(--color-slate-600);
}
.suppress-btn:hover:not(:disabled) {
  background: var(--color-slate-100);
  border-color: var(--color-slate-300);
}
.suppress-btn:disabled {
  opacity: 0.4;
  cursor: wait;
}
.suppress-btn__input {
  font-size: 11px;
  padding: 1px 6px;
  border: 1px solid var(--color-slate-300);
  border-radius: 4px;
  width: 140px;
  background: var(--color-white);
}
.suppress-btn__save {
  font-size: 11px;
  padding: 1px 8px;
  background: var(--color-blue-800);
  color: var(--color-white);
  border: 1px solid var(--color-cyan-900);
  border-radius: 4px;
  cursor: pointer;
}
.suppress-btn__save:hover:not(:disabled) { background: var(--color-cyan-900); }
.suppress-btn__save:disabled { opacity: 0.5; cursor: wait; }
</style>
