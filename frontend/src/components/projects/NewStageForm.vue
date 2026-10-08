<script setup>
// Appends a custom stage. The key is the step's identity
// (`feature:<id>:<key>`) and never changes, so it is derived from the label
// until the operator types one of their own.
import { computed, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import { STAGE_ROLES } from '../../composables/useProjectStages'

const props = defineProps({
  busy: { type: Boolean, default: false },
  error: { type: String, default: '' },
})
const emit = defineEmits(['add'])

const KEY_RE = /^[a-z][a-z0-9-]{0,31}$/
const label = ref('')
const key = ref('')
const role = ref('spec')
const keyTouched = ref(false)

const slug = text => text.toLowerCase().replace(/[^a-z0-9]+/g, '-')
  .replace(/^[^a-z]+/, '').replace(/-+$/, '').slice(0, 32)
watch(label, (text) => { if (!keyTouched.value) key.value = slug(text) })

const keyOk = computed(() => KEY_RE.test(key.value))
const ready = computed(() => !!label.value.trim() && keyOk.value && !props.busy)

function submit() {
  if (!ready.value) return
  emit('add', { key: key.value, label: label.value.trim(), role: role.value })
}

function reset() {
  label.value = ''
  key.value = ''
  role.value = 'spec'
  keyTouched.value = false
}
defineExpose({ reset })
</script>

<template>
  <form class="nsf" data-testid="stage-new" @submit.prevent="submit">
    <label class="nsf-field">
      <span class="nsf-label">Label</span>
      <Input v-model="label" placeholder="Design review" :disabled="busy" data-testid="stage-new-label" />
    </label>
    <label class="nsf-field">
      <span class="nsf-label">Key</span>
      <Input
        v-model="key" placeholder="design-review" :disabled="busy" :error="!!key && !keyOk"
        data-testid="stage-new-key" @input="keyTouched = true"
      />
    </label>
    <label class="nsf-field">
      <span class="nsf-label">Role</span>
      <Select v-model="role" :options="STAGE_ROLES" :disabled="busy" aria-label="Role of the new stage" data-testid="stage-new-role" />
    </label>
    <Button type="submit" variant="primary" size="sm" :disabled="!ready" data-testid="stage-new-submit">
      Add stage
    </Button>
    <p v-if="key && !keyOk" class="nsf-hint">A key is lowercase letters, digits and dashes, starting with a letter.</p>
    <p v-if="error" class="nsf-error" role="alert" data-testid="stage-new-error">{{ error }}</p>
  </form>
</template>

<style scoped>
.nsf { display: flex; flex-wrap: wrap; align-items: flex-end; gap: 0.6rem; }
.nsf-field { display: flex; flex-direction: column; gap: 0.2rem; min-width: 9rem; }
.nsf-label { font-size: 11px; font-weight: 600; color: var(--color-fg-subtle); }
.nsf-hint, .nsf-error { flex-basis: 100%; font-size: 11.5px; }
.nsf-hint { color: var(--color-fg-muted); }
.nsf-error { color: var(--color-danger); }
</style>
