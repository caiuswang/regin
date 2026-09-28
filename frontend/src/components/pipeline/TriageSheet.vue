<script setup>
// Triage as a bottom sheet (design 2d): one report at a time, ‹ › to page, and
// the four verbs as big buttons. Accept here also authors the fix task.
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import FeatureBlamePicker from './FeatureBlamePicker.vue'
import { useDefectTriage, VERBS } from '../../composables/useDefectTriage'
import { invalidatePipelineCounts } from '../../composables/usePipelineCounts'
import { defectRef, monthDay, reopenedLabel } from '../../utils/pipelineFormat'

const props = defineProps({
  scope: { type: String, default: '' },
})
const open = defineModel('open', { type: Boolean, default: false })
const emit = defineEmits(['changed'])

const {
  items, total, loading, busy, error, refusal, cursor, pending, blamed, focused,
  load, move, begin, submit, cancel, acceptAndFix,
} = useDefectTriage(() => props.scope)

const value = ref('')
const field = ref(null)
const panel = ref(null)
const problem = computed(() => (pending.value ? VERBS[pending.value].invalid(value.value) : ''))

function onEscape(event) {
  if (event.key === 'Escape' && !pending.value) close()
}

watch(open, async (isOpen) => {
  if (!isOpen) {
    window.removeEventListener('keydown', onEscape)
    return
  }
  window.addEventListener('keydown', onEscape)
  cursor.value = 0
  cancel()
  await load()
  await nextTick()
  panel.value?.focus()
})
watch(pending, async (verb) => {
  value.value = ''
  if (!verb) return
  await nextTick()
  field.value?.focus()
})

function after(message) {
  if (!message) return
  invalidatePipelineCounts()
  emit('changed', message)
  if (!items.value.length) open.value = false
}

async function accept() { after(await acceptAndFix()) }
async function verb(chord) { after(await begin(chord)) }
async function confirm() { after(await submit(pending.value, value.value)) }

function close() {
  cancel()
  open.value = false
}
onBeforeUnmount(() => window.removeEventListener('keydown', onEscape))
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="ts-backdrop">
      <Button variant="ghost" class="ts-scrim" aria-label="Close triage"
              data-testid="triage-sheet-backdrop" @click="close"
      />
      <div
        ref="panel"
        class="ts"
        role="dialog"
        aria-modal="true"
        aria-labelledby="ts-title"
        tabindex="-1"
        data-testid="triage-sheet"
      >
        <span class="ts-grip" aria-hidden="true" />
        <header class="ts-head">
          <h2 id="ts-title" class="ts-title">Triage</h2>
          <span v-if="items.length" class="ts-pos" data-testid="triage-sheet-position">
            {{ cursor + 1 }} of {{ total }}
          </span>
          <span class="ts-spacer" />
          <Button variant="secondary" size="icon" class="ts-page" aria-label="Previous report"
                  :disabled="cursor === 0" @click="move(-1)"
          ><Icon name="chevron-left" :size="16" /></Button>
          <Button variant="secondary" size="icon" class="ts-page" aria-label="Next report"
                  :disabled="cursor >= items.length - 1" @click="move(1)"
          ><Icon name="chevron-right" :size="16" /></Button>
        </header>

        <p v-if="loading" class="ts-muted">Loading…</p>
        <p v-else-if="error" class="ts-refusal" role="alert">{{ error }}</p>
        <p v-else-if="!focused" class="ts-muted">Nothing waiting on a routing decision.</p>
        <div v-else class="ts-body">
          <div class="ts-meta">
            <router-link class="ts-id focus-visible:outline-2 focus-visible:outline-blue-500"
                         :to="`/defects/${focused.id}`" @click="close"
            >{{ defectRef(focused.id) }}</router-link>
            <template v-if="reopenedLabel(focused.round)">
              <span>{{ reopenedLabel(focused.round) }}</span>
              <span>·</span>
            </template>
            <span>{{ monthDay(focused.opened_at) }}</span>
          </div>
          <p class="ts-symptom">{{ focused.symptom }}</p>
          <pre v-if="focused.suspect_paths?.length" class="ts-paths">{{ focused.suspect_paths.join('\n') }}</pre>
          <div class="ts-field">
            <span class="ts-label">Blame a feature</span>
            <FeatureBlamePicker :key="focused.id" v-model="blamed" placeholder="Choose…" :disabled="busy" />
          </div>
          <form v-if="pending" class="ts-prompt" @submit.prevent="confirm">
            <label class="sr-only" for="ts-field">{{ VERBS[pending].prompt }}</label>
            <Input id="ts-field" ref="field" v-model="value" :placeholder="VERBS[pending].prompt"
                   :disabled="busy" class="ts-input"
            />
            <Button type="submit" :variant="pending === 'X' ? 'danger' : 'primary'" class="ts-big"
                    :disabled="busy || !!problem" :data-testid="`triage-sheet-confirm-${VERBS[pending].key}`"
            >{{ pending === 'X' ? 'Confirm decline' : 'Mark duplicate' }}</Button>
          </form>
          <p v-if="refusal" class="ts-refusal" role="alert" data-testid="triage-sheet-refusal">{{ refusal }}</p>
        </div>

        <footer v-if="focused" class="ts-actions">
          <Button variant="primary" class="ts-big ts-accept" :disabled="busy"
                  data-testid="triage-sheet-accept" @click="accept"
          >Accept &amp; create fix task</Button>
          <Button variant="secondary" class="ts-big" :disabled="busy"
                  data-testid="triage-sheet-duplicate" @click="verb('D')"
          >Duplicate</Button>
          <Button variant="secondary" class="ts-big" :disabled="busy"
                  data-testid="triage-sheet-snooze" @click="verb('S')"
          >Snooze</Button>
          <Button variant="danger" class="ts-big" :disabled="busy"
                  data-testid="triage-sheet-decline" @click="verb('X')"
          >Decline</Button>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.ts-backdrop {
    position: fixed; inset: 0; z-index: var(--z-modal, 50);
    background: rgb(15 23 42 / 0.45);
    display: flex; align-items: flex-end; justify-content: center;
}
.ts-backdrop .ts-scrim {
    position: absolute; inset: 0; width: 100%; height: 100%;
    border-radius: 0; background: transparent;
}
.ts {
    position: relative;
    width: 100%; max-width: 32rem; max-height: 88vh; overflow-y: auto;
    display: flex; flex-direction: column; gap: 0.85rem;
    background: var(--color-surface); color: var(--color-fg);
    border-radius: 1.25rem 1.25rem 0 0;
    padding: 0.5rem 1rem calc(1rem + env(safe-area-inset-bottom));
    outline: none;
}
.ts-grip { align-self: center; width: 2.5rem; height: 4px; border-radius: 999px; background: var(--color-border-strong); }
.ts-head { display: flex; align-items: center; gap: 0.5rem; }
.ts-title { font-size: 16px; font-weight: 700; }
.ts-pos { font-size: 13px; color: var(--color-fg-muted); }
.ts-spacer { flex: 1; }
.ts-head .ts-page { width: 44px; height: 44px; }
.ts-body { display: flex; flex-direction: column; gap: 0.7rem; }
.ts-meta { display: flex; gap: 0.45rem; font-size: 12.5px; color: var(--color-fg-faint); }
.ts-id { font-family: var(--font-mono, monospace); font-weight: 700; color: var(--color-fg); text-decoration: none; }
.ts-symptom { font-size: 15px; line-height: 1.5; overflow-wrap: anywhere; }
.ts-paths {
    margin: 0; padding: 0.6rem 0.75rem; border-radius: var(--radius-lg);
    background: var(--color-surface-2); font-size: 12px; color: var(--color-fg-muted);
    white-space: pre-wrap; overflow-wrap: anywhere;
}
.ts-field { display: flex; flex-direction: column; gap: 0.35rem; }
.ts-label { font-size: 12.5px; font-weight: 600; color: var(--color-fg-muted); }
.ts-prompt { display: flex; flex-direction: column; gap: 0.5rem; }
.ts-prompt .ts-input { min-height: 44px; }
.ts-refusal {
    border-radius: var(--radius-lg); background: var(--color-danger-soft);
    padding: 0.4rem 0.65rem; font-size: 12px; color: var(--color-danger);
}
.ts-muted { font-size: 13px; color: var(--color-fg-muted); }
.ts-actions {
    display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 0.5rem;
    border-top: 1px solid var(--color-border); padding-top: 0.75rem;
}
.ts-actions .ts-big, .ts-prompt .ts-big { height: auto; min-height: 44px; font-size: 14px; }
.ts-actions .ts-accept { grid-column: 1 / -1; min-height: 52px; font-size: 15px; }
</style>
