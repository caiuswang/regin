<script setup>
// One defect in the triage queue, and whichever verb is collecting a field
// for it.
//
// Deliberately not `DefectRow`: that card offers every write the ledger owns,
// and a queue whose common path is four keys must not also present six
// buttons. What it shows is what the routing decision needs — the symptom in
// full, the suspect paths, how many sessions have reported it — and nothing
// that proposes an answer.
import { computed, nextTick, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import { VERBS } from '../../composables/useDefectTriage'
import { shortDate } from '../../utils/ledgerFormat'

const props = defineProps({
  defect: { type: Object, required: true },
  focused: { type: Boolean, default: false },
  pending: { type: String, default: '' },
  refusal: { type: String, default: '' },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['submit', 'cancel', 'focus'])

const value = ref('')
const field = ref(null)

// Live, and owned here because this is where the text is: the submit is
// disabled the moment the field cannot be sent, rather than after a post that
// was never going to be accepted. The same rule runs again at the writer.
const problem = computed(
  () => (props.pending ? VERBS[props.pending].invalid(value.value) : ''))

// The field is opened by a key, so it has to take the caret without a click —
// the whole flow is "read, then one chord", and a prompt the operator has to
// reach for the mouse to answer is the flow broken in the middle.
watch(() => props.pending, async (verb) => {
  value.value = ''
  if (!verb) return
  await nextTick()
  field.value?.$el?.querySelector('input')?.focus()
})
</script>

<template>
  <li
    class="q-card"
    :class="{ 'is-focused': focused }"
    :data-testid="`triage-card-${defect.id}`"
    :aria-current="focused ? 'true' : undefined"
    @click="emit('focus')"
  >
    <div class="head">
      <router-link class="id" :to="`/defects/${defect.id}`"
                   :data-testid="`triage-permalink-${defect.id}`"
      >#{{ defect.id }}</router-link>
      <span class="unblamed">unattributed</span>
      <span class="spacer" />
      <span class="when" :title="defect.opened_at">{{ shortDate(defect.opened_at) }}</span>
    </div>

    <p class="symptom">{{ defect.symptom }}</p>

    <p v-if="defect.suspect_paths.length" class="paths">
      {{ defect.suspect_paths.join(' · ') }}
      <!-- Named as what it is. These are the files the reporter was in, not a
           proposal about which feature owns them: regin never turns a path
           into a parent, and saying so where the guess would be made is the
           cheapest place to say it. -->
      <span class="paths-note">— where it was seen, not who caused it</span>
    </p>

    <form
      v-if="focused && pending"
      class="prompt"
      :data-testid="`triage-prompt-${VERBS[pending].key}`"
      @submit.prevent="emit('submit', pending, value)"
    >
      <label class="sr-only" :for="`tq-${defect.id}`">
        {{ VERBS[pending].prompt }}
      </label>
      <Input
        :id="`tq-${defect.id}`"
        ref="field"
        v-model="value"
        class="grow"
        :placeholder="VERBS[pending].prompt"
        :disabled="busy"
        :error="!!problem && !!value"
        @keydown.esc.stop.prevent="emit('cancel')"
      />
      <Button
        type="submit"
        size="sm"
        :disabled="busy || !!problem"
        :data-testid="`triage-submit-${VERBS[pending].key}`"
      >{{ VERBS[pending].label }} #{{ defect.id }}</Button>
      <p v-if="problem && value" class="refusal" role="alert">{{ problem }}</p>
    </form>

    <p v-if="focused && refusal" class="refusal" role="alert"
       :data-testid="`triage-refusal-${defect.id}`">{{ refusal }}</p>
  </li>
</template>

<style scoped>
.q-card {
    display: flex; flex-direction: column; gap: 0.45rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface-1); padding: 0.7rem 0.9rem;
    border-left: 3px solid transparent;
}
/* The cursor, and the only thing on the card that moves. A queue walked by
   key needs the eye to find the row without hunting. */
.q-card.is-focused {
    border-left-color: var(--color-primary);
    background: var(--color-surface-2);
}
.head {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem;
    font-size: 11.5px; color: var(--color-fg-faint);
}
.id {
    font-family: var(--font-mono, monospace); font-weight: 600;
    color: var(--color-fg); text-decoration: none;
}
.id:hover { text-decoration: underline; }
.unblamed { color: var(--color-amber-800); }
.spacer { flex: 1 1 auto; }
.when { font-variant-numeric: tabular-nums; }
.symptom {
    max-width: 60rem; font-size: 13px; line-height: 1.5;
    color: var(--color-fg); white-space: pre-wrap; overflow-wrap: anywhere;
}
.paths {
    font-size: 11px; color: var(--color-fg-muted);
    font-family: var(--font-mono, monospace); overflow-wrap: anywhere;
}
.paths-note {
    font-family: var(--font-sans, system-ui); color: var(--color-fg-faint);
}
.prompt {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem;
    max-width: 44rem;
}
.refusal {
    flex-basis: 100%;
    border-radius: var(--radius-lg); background: var(--color-red-50);
    padding: 0.35rem 0.6rem; font-size: 11.5px; color: var(--color-red-700);
    max-width: 44rem;
}
</style>
