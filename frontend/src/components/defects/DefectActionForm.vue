<script setup>
// One text field a defect verb collects, with its submit and its hint. The
// problem is said before the post, not after it: a round trip to learn what
// the field already knew costs the operator their place.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import Textarea from '../ui/Textarea.vue'

const props = defineProps({
  form: { type: String, required: true },
  spec: { type: Object, required: true },
  defectId: { type: Number, required: true },
  busy: { type: Boolean, default: false },
})
const value = defineModel({ type: String, default: '' })
const emit = defineEmits(['submit'])

const problem = computed(() => props.spec.invalid(value.value))
</script>

<template>
  <form class="daf" @submit.prevent="!problem && emit('submit')">
    <label class="sr-only" :for="`f-${defectId}`">{{ spec.placeholder }}</label>
    <Textarea
      v-if="spec.multiline"
      :id="`f-${defectId}`"
      v-model="value"
      class="daf-grow"
      rows="3"
      :placeholder="spec.placeholder"
      :disabled="busy"
      :error="!!problem"
      :data-testid="`defect-${form}-field-${defectId}`"
    />
    <Input
      v-else
      :id="`f-${defectId}`"
      v-model="value"
      class="daf-grow"
      :placeholder="spec.placeholder"
      :disabled="busy"
      :error="!!problem && !!value"
      :data-testid="`defect-${form}-field-${defectId}`"
    />
    <!-- Carries the defect id so the submit and the disclosure that
         revealed it do not share one accessible name. -->
    <Button type="submit" size="sm" :disabled="busy || !!problem"
            :data-testid="`defect-${form}-submit-${defectId}`">
      {{ spec.label }} #{{ defectId }}
    </Button>
    <p class="daf-hint">{{ spec.hint }}</p>
    <p v-if="problem && value" class="daf-problem" role="alert"
       :data-testid="`defect-${form}-problem-${defectId}`">{{ problem }}</p>
  </form>
</template>

<style scoped>
.daf {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem;
    max-width: 44rem;
}
.daf .daf-grow { flex: 1 1 16rem; }
.daf-hint { flex-basis: 100%; font-size: 11px; color: var(--color-fg-faint); }
.daf-problem {
    border-radius: var(--radius-lg); background: var(--color-red-50);
    padding: 0.35rem 0.6rem; font-size: 11.5px; color: var(--color-red-700);
    max-width: 44rem;
}
</style>
