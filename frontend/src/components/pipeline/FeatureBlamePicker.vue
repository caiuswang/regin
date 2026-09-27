<script setup>
// "Blame a feature…" — every live feature, most recently active first. It
// starts empty on every card and nothing here guesses a default. The list is
// read the first time the control is reached, not when a card takes focus.
import { computed, ref } from 'vue'
import api from '../../api'
import Select from '../ui/Select.vue'

defineProps({
  placeholder: { type: String, default: 'Blame a feature…' },
  disabled: { type: Boolean, default: false },
})
const model = defineModel({ type: String, default: '' })

const features = ref([])
const error = ref('')

let asked = false
async function ensure() {
  if (asked) return
  asked = true
  try {
    const body = await api.get('/features?size=200&sort=activity:desc')
    features.value = body.items || []
  } catch (e) {
    error.value = e?.message || String(e)
  }
}

const options = computed(() => features.value.map(f => ({
  value: String(f.id), label: `#${f.id} ${f.title}`,
})))
</script>

<template>
  <div class="bp">
    <!-- Select forwards attrs to its trigger, so these land on the button. -->
    <Select
      @pointerdown="ensure"
      @focus="ensure"
      v-model="model"
      :options="options"
      :placeholder="error ? 'Features could not be read' : placeholder"
      :disabled="disabled || !!error"
      block
      aria-label="Blame a feature"
      data-testid="triage-blame"
    />
  </div>
</template>

<style scoped>
.bp { min-width: 0; }
</style>
