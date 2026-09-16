<script setup>
// Capture by reference: paste the issue the work already lives in, and the
// composer fills itself from it.
//
// A paste IS the interaction. Reading is triggered by the paste itself and by
// leaving the field after typing — never by a button the operator has to
// notice — because a capture that still needs a click and then a typed title
// is the retyping this field exists to remove. The button stays for the
// keyboard path and for a retry after a failed read.
//
// Resolving is a read, never a write. The task that would hold the link does
// not exist yet, so a dialog closed after a paste leaves nothing behind — the
// link is attached only once the capture lands.
//
// Nothing here names a platform. The field asks the server which trackers are
// configured and what each wants pasted, so a second adapter shows up with no
// edit to this file.
import { onMounted, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import { useTaskLinks } from '../../composables/useTaskLinks.js'

const emit = defineEmits(['resolved'])

const text = defineModel({ type: String, default: '' })

const { configured, placeholder, error, busyId, loadProviders, preview } =
  useTaskLinks()

const resolved = ref(null)
// The text that produced `resolved`. Editing away from it drops the issue
// rather than leaving a card that describes a reference no longer in the box.
const resolvedFor = ref('')

watch(text, (value) => {
  if (value.trim() !== resolvedFor.value) resolved.value = null
})

// The resolved issue, awaited. Idempotent for text already read, so the
// paste, the blur that follows it and a capture racing both spend one round
// trip between them rather than three.
//
// Read against the text it is handed, never against the model: a write to
// `text` only reaches the parent on the next tick, so a paste asking the
// model what it just pasted gets back the value from before it.
async function read(raw) {
  const value = (raw || '').trim()
  if (!value) return null
  if (resolved.value && resolvedFor.value === value) return resolved.value
  const found = await preview(value)
  if (!found) return null
  resolved.value = found
  resolvedFor.value = value
  emit('resolved', found)
  return found
}

function resolve() {
  return read(text.value)
}

// The pasted text is taken from the clipboard rather than read back out of the
// input: the read has to start on this event, and the value the browser would
// insert is not there yet.
async function onPaste(event) {
  const pasted = (event.clipboardData?.getData('text') || '').trim()
  if (!pasted) return
  event.preventDefault()
  text.value = pasted
  return read(pasted)
}

function reset() {
  text.value = ''
  resolved.value = null
  resolvedFor.value = ''
  error.value = ''
}

defineExpose({ resolve, ref: () => (resolved.value ? resolvedFor.value : ''),
               reset })

onMounted(loadProviders)
</script>

<template>
  <div>
    <label class="field-label" for="task-link-ref">
      Issue <span class="font-normal text-slate-400">(optional)</span>
    </label>
    <div class="tlf-row">
      <Input
        id="task-link-ref"
        v-model="text"
        class="tlf-input"
        type="text"
        :placeholder="placeholder"
        :disabled="!configured.length"
        data-testid="task-composer-link-input"
        @paste="onPaste"
        @change="resolve()"
        @keydown.enter.prevent.stop="resolve()"
      />
      <Button
        type="button"
        variant="secondary"
        :disabled="!configured.length || !text.trim() || busyId !== 0"
        data-testid="task-composer-link-submit"
        @click="resolve()"
      >
        <Icon name="plus" :size="13" />
        <span>{{ busyId !== 0 ? 'Reading…' : 'Fetch' }}</span>
      </Button>
    </div>

    <p v-if="resolved" class="tlf-resolved" data-testid="task-composer-link-resolved">
      <span class="tlf-key">{{ resolved.key }}</span>
      <span class="tlf-title">{{ resolved.issue.title }}</span>
      <Button
        type="button" variant="ghost" size="sm" class="tlf-clear"
        aria-label="Drop the linked issue"
        data-testid="task-composer-link-clear" @click="reset"
      >
        <Icon name="x" :size="12" />
      </Button>
    </p>

    <p v-if="error" class="tlf-error" role="alert"
       data-testid="task-composer-link-error">
      {{ error }}
    </p>
  </div>
</template>

<style scoped>
.tlf-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.375rem;
}
.tlf-input {
    flex: 1 1 16rem;
    min-width: 0;
}
.tlf-note,
.tlf-error,
.tlf-resolved {
    margin: 0.25rem 0 0;
    font-size: 0.75rem;
    overflow-wrap: anywhere;
}
.tlf-note { color: var(--color-fg-faint); }
.tlf-error { color: var(--color-danger-fg, var(--color-fg-muted)); }
.tlf-resolved {
    display: flex;
    align-items: center;
    gap: 0.375rem;
    color: var(--color-fg-muted);
}
.tlf-key {
    font-family: var(--font-mono);
    color: var(--color-fg);
}
.tlf-title {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.tlf-clear { padding: 0 0.25rem; }
</style>
