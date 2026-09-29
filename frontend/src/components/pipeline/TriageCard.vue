<script setup>
// One defect on the Features triage rail. The focused card carries the blame
// picker, the verbs and whichever verb is collecting a field.
import { computed, nextTick, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import FeaturePicker from './FeaturePicker.vue'
import DefectImages from '../defects/DefectImages.vue'
import DefectMergePicker from '../defects/DefectMergePicker.vue'
import DefectRefusal from '../defects/DefectRefusal.vue'
import { VERBS, VERB_ORDER } from '../../composables/useDefectTriage'
import { defectRef, monthDay, reopenedLabel } from '../../utils/pipelineFormat'
import { utcTitle } from '../../utils/time.js'

const props = defineProps({
  defect: { type: Object, required: true },
  focused: { type: Boolean, default: false },
  pending: { type: String, default: '' },
  refusal: { type: Object, default: null },
  busy: { type: Boolean, default: false },
})
const blamed = defineModel('blamed', { type: String, default: '' })
const emit = defineEmits(['focus', 'verb', 'submit', 'cancel', 'remedy'])

const value = ref('')
const field = ref(null)
const card = ref(null)
const problem = computed(() => (props.pending ? VERBS[props.pending].invalid(value.value) : ''))

// A picked feature hands focus back to the card, so A works at once.
function picked() {
  card.value?.focus()
}

watch(() => props.pending, async (verb) => {
  value.value = ''
  if (!verb) return
  await nextTick()
  field.value?.focus()
})

function onPointer() {
  emit('focus')
  // Keys are live while the rail holds focus; a click must put it there.
  if (!card.value?.contains(document.activeElement)) card.value?.focus()
}

defineExpose({ focus: () => card.value?.focus() })
</script>

<template>
  <li
    ref="card"
    class="tc"
    :class="{ 'is-focused': focused }"
    tabindex="-1"
    :data-testid="`triage-card-${defect.id}`"
    :aria-current="focused ? 'true' : undefined"
    @click="onPointer"
  >
    <div class="tc-head">
      <router-link
        class="tc-id focus-visible:outline-2 focus-visible:outline-blue-500"
        :to="`/defects/${defect.id}`"
        :data-testid="`triage-permalink-${defect.id}`"
        @click.stop
      >{{ defectRef(defect.id) }}</router-link>
      <span v-if="reopenedLabel(defect.round)" class="tc-round">{{ reopenedLabel(defect.round) }}</span>
      <span class="tc-spacer" />
      <span class="tc-when" :title="utcTitle(defect.opened_at)">{{ monthDay(defect.opened_at) }}</span>
    </div>
    <p class="tc-symptom">{{ defect.symptom }}</p>
    <p v-if="defect.suspect_paths?.length" class="tc-paths" title="Where it was seen, not who caused it">
      {{ defect.suspect_paths.join(' · ') }}
    </p>
    <DefectImages v-if="defect.images?.length" :defect="defect" />

    <template v-if="focused">
      <FeaturePicker v-model="blamed" :disabled="busy" testid="triage-blame" @picked="picked" />
      <div class="tc-verbs" role="group" aria-label="Triage verbs">
        <Button
          v-for="chord in VERB_ORDER"
          :key="chord"
          size="sm"
          :variant="chord === 'A' ? 'secondary' : 'ghost'"
          class="tc-verb"
          :class="{ 'tc-verb--accept': chord === 'A', 'tc-verb--wide': chord === 'A' || chord === 'Shift+A',
                    'is-ready': chord === 'A' && blamed }"
          :disabled="busy"
          :aria-keyshortcuts="chord"
          :data-testid="`triage-verb-${VERBS[chord].key}`"
          @click.stop="emit('verb', chord)"
        >
          <kbd>{{ VERBS[chord].kbd || chord }}</kbd>{{ VERBS[chord].label }}
        </Button>
      </div>
      <div v-if="pending && VERBS[pending].picker" :data-testid="`triage-prompt-${VERBS[pending].key}`">
        <DefectMergePicker
          :defect="defect" :busy="busy"
          @confirm="id => emit('submit', pending, String(id))"
          @cancel="emit('cancel')"
        />
      </div>
      <form
        v-else-if="pending"
        class="tc-prompt"
        :data-testid="`triage-prompt-${VERBS[pending].key}`"
        @submit.prevent="emit('submit', pending, value)"
        @click.stop
      >
        <label class="sr-only" :for="`tc-${defect.id}`">{{ VERBS[pending].prompt }}</label>
        <Input
          :id="`tc-${defect.id}`"
          ref="field"
          v-model="value"
          class="tc-input"
          :placeholder="VERBS[pending].prompt"
          :disabled="busy"
          :error="!!problem && !!value"
          @keydown.esc.stop.prevent="emit('cancel')"
        />
        <Button
          type="submit"
          size="sm"
          :variant="pending === 'X' ? 'danger' : 'primary'"
          :disabled="busy || !!problem"
          :data-testid="`triage-submit-${VERBS[pending].key}`"
        >{{ pending === 'X' ? 'Confirm decline' : VERBS[pending].label }}</Button>
      </form>
      <DefectRefusal v-if="refusal" :refusal="refusal" :busy="busy"
                     :testid="`triage-refusal-${defect.id}`" @remedy="r => emit('remedy', r)" />
    </template>
  </li>
</template>

<style scoped>
.tc {
    display: flex; flex-direction: column; gap: 0.5rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface); padding: 0.75rem 0.85rem; cursor: pointer;
    outline: none;
}
.tc.is-focused {
    border-color: var(--color-info);
    box-shadow: 0 0 0 3px var(--color-info-soft);
    cursor: default;
}
.tc-head { display: flex; align-items: center; gap: 0.5rem; font-size: 11.5px; color: var(--color-fg-faint); }
.tc-id { font-family: var(--font-mono, monospace); font-weight: 700; color: var(--color-fg); text-decoration: none; }
.tc-id:hover { text-decoration: underline; }
.tc-spacer { flex: 1; }
.tc-symptom { font-size: 13px; line-height: 1.5; color: var(--color-fg); overflow-wrap: anywhere; }
.tc-paths { font-family: var(--font-mono, monospace); font-size: 11px; color: var(--color-fg-muted); overflow-wrap: anywhere; }
.tc-verbs { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 0.35rem; }
.tc-verbs .tc-verb { grid-column: span 2; }
.tc-verbs .tc-verb--wide { grid-column: span 3; }
.tc-verbs .tc-verb { gap: 0.3rem; padding: 0 0.3rem; background: var(--color-surface-2); }
.tc-verbs .tc-verb--accept { background: var(--color-info-soft); color: var(--color-info); }
.tc-verbs .tc-verb--accept.is-ready { background: var(--color-info); color: var(--color-primary-fg); }
.tc-verb kbd { font-family: var(--font-mono, monospace); font-size: 10px; opacity: 0.7; }
.tc-prompt { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; }
.tc-prompt .tc-input { flex: 1 1 10rem; min-width: 0; }
</style>
