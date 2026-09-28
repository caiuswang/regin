<script setup>
// The ledger's title, editable in place. A tracked feature's title is written
// by every Refresh from the issue, so an edit here would be silently undone —
// the same reason its spec is read-only (D4).
import { nextTick, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'

const props = defineProps({
  featureId: { type: Number, required: true },
  title: { type: String, default: '' },
  editable: { type: Boolean, default: false },
})
const emit = defineEmits(['saved'])

const editing = ref(false)
const draft = ref('')
const failed = ref('')
const busy = ref(false)
const input = ref(null)

async function start() {
  draft.value = props.title
  failed.value = ''
  editing.value = true
  await nextTick()
  input.value?.focus()
}

async function save() {
  const name = draft.value.trim()
  if (busy.value) return
  if (!name || name === props.title) {
    editing.value = false
    return
  }
  busy.value = true
  const res = await api.patch(`/features/${props.featureId}`, { title: name })
  busy.value = false
  if (res?.ok === false) {
    failed.value = res.msg || 'the rename was refused'
    return
  }
  editing.value = false
  emit('saved', res)
}
</script>

<template>
  <span class="ftl">
    <Input
      v-if="editing"
      ref="input"
      v-model="draft"
      class="ftl-input"
      aria-label="Feature title"
      data-testid="feature-title-input"
      maxlength="200"
      :disabled="busy"
      @keydown.enter.prevent="save"
      @keydown.esc.prevent="editing = false"
      @blur="save"
    />
    <Button
      v-else-if="editable"
      variant="ghost" size="sm"
      class="ftl-key ftl-editable"
      title="Rename"
      data-testid="feature-title"
      @click="start"
    >{{ title }}</Button>
    <span v-else class="ftl-key" data-testid="feature-title">{{ title }}</span>
    <span v-if="failed" class="ftl-fail" role="alert">{{ failed }}</span>
  </span>
</template>

<style scoped>
.ftl { display: inline-flex; align-items: center; gap: 0.4rem; min-width: 0; }
.ftl-key {
    font-family: var(--font-mono, monospace); font-weight: 700;
    font-size: 0.95rem; color: var(--color-fg);
}
.ftl-editable { cursor: text; padding: 0 0.25rem; height: auto; }
.ftl-editable:focus-visible { outline: 2px solid var(--color-blue-500); }
.ftl-input {
    font-family: var(--font-mono, monospace); font-weight: 700;
    font-size: 0.95rem; min-width: 16rem;
}
.ftl-input:focus-visible { outline: 2px solid var(--color-blue-500); }
.ftl-fail { font-size: 11.5px; color: var(--color-red-700, #b91c1c); }
</style>
