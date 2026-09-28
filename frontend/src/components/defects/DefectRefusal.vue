<script setup>
// A defect refusal, in the server's own words, beside the control that caused
// it — and, when the sentence names a way out, that way out as a button.
import Button from '../ui/Button.vue'
import { REMEDY_LABEL } from '../../composables/useDefectRemedy'

defineProps({
  refusal: { type: Object, required: true },
  busy: { type: Boolean, default: false },
  testid: { type: String, default: 'defect-refusal' },
})
const emit = defineEmits(['remedy'])
</script>

<template>
  <div class="dr" role="alert" :data-testid="testid">
    <span class="dr-msg">{{ refusal.msg }}</span>
    <Button
      v-if="refusal.remedy"
      variant="secondary" size="sm" :disabled="busy"
      :data-testid="`${testid}-remedy`"
      @click.stop="emit('remedy', refusal.remedy)"
    >{{ REMEDY_LABEL[refusal.remedy.verb](refusal.remedy.args) }}</Button>
  </div>
</template>

<style scoped>
.dr {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem;
    border-radius: var(--radius-lg); background: var(--color-danger-soft);
    padding: 0.35rem 0.6rem; font-size: 11.5px; color: var(--color-danger);
    max-width: 44rem;
}
.dr-msg { flex: 1 1 16rem; min-width: 0; overflow-wrap: anywhere; }
</style>
