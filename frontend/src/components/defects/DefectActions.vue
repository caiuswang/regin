<script setup>
// The writes the ledger owns, one defect at a time.
//
// There is no bulk control anywhere on this page. Attribution is a human
// asserting which feature CAUSED a bug, and a checkbox column is how eight
// rows get one guessed parent — the exact corruption the NULL `feature_id`
// state exists to prevent.
//
// Neither the feature nor the missing check is ever pre-filled: both are the
// assertion this control exists to collect, and a default is an inference
// wearing a human's signature.
//
// What the row offers is the R5 matrix (`utils/defectVerbs.js`): one primary
// verb for its state, the rest behind ⋯. Every refusal is the server's own
// sentence, and one that names a way out carries it as a button.
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../../api'
import { useConfirm } from '../../composables/useConfirm'
import { refusalOf, runRemedy } from '../../composables/useDefectRemedy'
import { defectState, GROUP, MENU, PRIMARY } from '../../utils/defectVerbs'
import { toBase64 } from '../../utils/imageFiles'
import Button from '../ui/Button.vue'
import DropdownMenu from '../ui/DropdownMenu.vue'
import FeaturePicker from '../pipeline/FeaturePicker.vue'
import DefectActionForm from './DefectActionForm.vue'
import DefectMergePicker from './DefectMergePicker.vue'
import DefectRefusal from './DefectRefusal.vue'
import { FORMS } from './defectForms'

const props = defineProps({
  defect: { type: Object, required: true },
  canWrite: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])

const router = useRouter()
const { confirm } = useConfirm()

const form = ref('')
const value = ref('')
const busy = ref(false)
const refusal = ref(null)
// Raised by a close on this page, never by arriving at an already-closed
// defect: the offer is the answer to "you just named a check — who writes
// it", and a row closed weeks ago is being read, not decided.
const offered = ref(false)
// The task this page just filed. Held here because a flash fades, and the
// row's "fix item" fact reads as a pointer that was always there.
const created = ref(null)
const filePick = ref(null)

// A task no feature owns is an orphan, so the store refuses the create.
const ATTRIBUTE_FIRST = 'Attribute it to a feature to create its fix task.'

const state = computed(() => defectState(props.defect))

const VERB = {
  attribute: d => ({ label: d.feature_id ? 'Re-attribute' : 'Attribute', run: () => reveal('attribute'),
                     testid: `defect-attribute-reveal-${d.id}` }),
  close: () => ({ label: 'Close', run: () => reveal('close') }),
  reopen: () => ({ label: 'Reopen', run: reopen }),
  restore: () => ({ label: 'Restore', run: () => act('restore', 'restored to the queue') }),
  unsnooze: () => ({ label: 'Unsnooze', run: () => act('unsnooze', 'back in the triage queue') }),
  snooze: () => ({ label: 'Snooze', run: () => act('snooze', 'snoozed until something touches it') }),
  decline: () => ({ label: 'Decline…', run: () => reveal('decline') }),
  fix_create: d => ((d.fix_task_id || offered.value) ? null
    : { label: 'Create fix task', run: createFixTask, testid: `defect-fix-create-row-${d.id}` }),
  fix_link: d => ({ label: d.fix_task_id ? 'Re-point fix task' : 'Link existing task',
                    run: () => reveal('fix-item'), testid: `defect-fix-item-reveal-${d.id}` }),
  fix_clear: d => (d.fix_task_id ? { label: 'Clear fix task', run: clearFixTask } : null),
  merge: () => ({ label: 'Merge into…', run: () => reveal('merge'), testid: 'defect-merge-reveal' }),
  absorb: d => ({ label: 'Absorb duplicates…', run: () => reveal('absorb'),
                  testid: `defect-absorb-reveal-${d.id}` }),
  edit: d => ({ label: 'Edit symptom', run: () => reveal('edit'), testid: `defect-edit-reveal-${d.id}` }),
  paths: () => ({ label: 'Edit suspect paths', run: () => reveal('paths') }),
  attach: () => ({ label: 'Attach image…', run: () => filePick.value?.click() }),
}

function verbItem(verb) {
  const spec = VERB[verb](props.defect)
  if (!spec) return null
  return { label: spec.label, onSelect: spec.run, disabled: busy.value,
           testid: spec.testid || `defect-verb-${verb}-${props.defect.id}` }
}

const primary = computed(() => verbItem(PRIMARY[state.value]))

const menuItems = computed(() => {
  const items = []
  let group = null
  for (const verb of MENU[state.value]) {
    const item = verbItem(verb)
    if (!item) continue
    if (group !== null && GROUP[verb] !== group) items.push({ separator: true })
    group = GROUP[verb]
    items.push(item)
  }
  return items
})

// Edit and paths start from what is there — they correct it — while every
// other field starts empty.
function reveal(which) {
  form.value = form.value === which ? '' : which
  value.value = FORMS[form.value]?.initial?.(props.defect) ?? ''
  refusal.value = null
}

function refuse(body) {
  refusal.value = refusalOf(body)
}

async function call(send) {
  busy.value = true
  refusal.value = null
  try {
    const body = await send()
    if (body?.ok === false) {
      refuse(body)
      return null
    }
    return body
  } catch (e) {
    refuse(e?.message || String(e))
    return null
  } finally {
    busy.value = false
  }
}

async function act(verb, done) {
  const body = await call(() => api.post(`/defects/${props.defect.id}/${verb}`))
  if (body) emit('changed', `#${props.defect.id} ${done}.`)
}

async function submit(raw = value.value) {
  const which = form.value
  const spec = FORMS[which]
  if (!spec || spec.invalid(raw)) return
  const body = await call(() => spec.send(props.defect.id, raw))
  if (!body) return
  form.value = ''
  if (which === 'merge') {
    // The row this page named no longer exists — the store deletes the
    // duplicate — and the response carries the keeper.
    emit('changed', `#${props.defect.id} folded into #${body.defect.id}.`,
         { keeper: body.defect.id })
    return
  }
  // Only a close raises the offer, and only when nothing is pointed at yet:
  // silently proposing a SECOND task is how the board grows duplicates.
  offered.value = which === 'close' && !props.defect.fix_task_id
  emit('changed', `#${props.defect.id} ${spec.done}.`)
}

// The check just named is a test somebody has to write, and this is the only
// moment anyone is looking at the defect knowing that.
async function createFixTask() {
  const body = await call(() => api.post(`/defects/${props.defect.id}/fix-item`, { create: true }))
  if (!body) return
  offered.value = false
  created.value = body.defect.fix_task_id
  emit('changed', `#${props.defect.id} will be fixed by task #${body.defect.fix_task_id}.`)
}

async function clearFixTask() {
  const ok = await confirm('Clear fix task',
    `Unpoint defect #${props.defect.id} from task #${props.defect.fix_task_id}? The task itself is kept.`)
  if (!ok) return
  const body = await call(() => api.del(`/defects/${props.defect.id}/fix-item`))
  if (body) emit('changed', `#${props.defect.id} no longer points at a fix task.`)
}

// Reopening is not undo: the round it bumps is a permanent figure on the
// feature that caused it, so it is asked for once, out loud.
async function reopen() {
  const ok = await confirm(
    'Reopen',
    `Reopen defect #${props.defect.id}? This records that the fix did not hold `
    + `and takes it to round ${props.defect.round + 1} — a figure its feature `
    + 'keeps for good.',
    true)
  if (!ok) return
  const body = await call(() => api.post(`/defects/${props.defect.id}/reopen`))
  if (body) emit('changed', `#${props.defect.id} reopened at round ${body.defect.round}.`)
}

async function attach(event) {
  const files = [...(event.target.files || [])]
  event.target.value = ''
  if (!files.length) return
  for (const file of files) {
    const data_b64 = await toBase64(file)
    const body = await call(() => api.post(`/defects/${props.defect.id}/images`, { data_b64 }))
    if (!body) return
  }
  emit('changed', `#${props.defect.id}: ${files.length} image${files.length === 1 ? '' : 's'} attached.`)
}

async function onRemedy(remedy) {
  busy.value = true
  const result = await runRemedy(remedy, { confirm }).finally(() => { busy.value = false })
  if (result.reveal === 'attribute') {
    if (String(result.defectId) === String(props.defect.id)) reveal('attribute')
    else router.push(`/defects/${result.defectId}`)
    return
  }
  if (!result.ok) {
    if (result.refusal) refusal.value = result.refusal
    return
  }
  form.value = ''
  refusal.value = null
  emit('changed', result.message, result.keeper ? { keeper: result.keeper } : undefined)
}
</script>

<template>
  <div v-if="canWrite" class="acts">
    <div class="row">
      <Button
        v-if="primary"
        variant="secondary" size="sm" :disabled="busy"
        :data-testid="`defect-primary-${defect.id}`"
        @click="primary.onSelect"
      >{{ primary.label }}</Button>
      <DropdownMenu :items="menuItems" align="start">
        <template #trigger>
          <Button variant="ghost" size="sm" class="ledger-reveal"
                  :aria-label="`More actions for defect #${defect.id}`"
                  :data-testid="`defect-more-${defect.id}`">⋯</Button>
        </template>
      </DropdownMenu>
      <span v-if="!defect.feature_id && !defect.fix_task_id" class="note"
            :data-testid="`defect-fix-create-blocked-${defect.id}`">{{ ATTRIBUTE_FIRST }}</span>
      <input
        ref="filePick" type="file" accept="image/*" multiple class="sr-only"
        tabindex="-1" aria-hidden="true"
        :data-testid="`defect-attach-input-${defect.id}`"
        @change="attach"
      >
    </div>

    <DefectMergePicker
      v-if="form === 'merge'"
      :defect="defect" :busy="busy"
      @confirm="id => submit(String(id))"
      @cancel="form = ''"
    />
    <form v-else-if="form === 'attribute'" class="form" @submit.prevent="submit()">
      <FeaturePicker
        v-model="value" class="grow" :disabled="busy"
        label="The feature that caused this"
        :testid="`defect-attribute-picker-${defect.id}`"
      />
      <Button type="submit" size="sm" :disabled="busy || !value"
              :data-testid="`defect-attribute-submit-${defect.id}`">
        {{ defect.feature_id ? 'Re-attribute' : 'Attribute' }} #{{ defect.id }}
      </Button>
      <p class="hint">{{ FORMS.attribute.hint }}</p>
    </form>
    <DefectActionForm
      v-else-if="form"
      v-model="value"
      :form="form" :spec="FORMS[form]" :defect-id="defect.id" :busy="busy"
      @submit="submit()"
    />

    <div v-if="offered" class="offer" role="status"
         :data-testid="`defect-fix-offer-${defect.id}`">
      <span>Closed. <b>{{ defect.missing_check }}</b> still has to be written — file the task?</span>
      <Button
        variant="secondary" size="sm" :disabled="busy || !defect.feature_id"
        :title="defect.feature_id ? '' : ATTRIBUTE_FIRST"
        :data-testid="`defect-fix-create-${defect.id}`"
        @click="createFixTask"
      >Create fix task</Button>
      <Button variant="ghost" size="sm" :disabled="busy"
              @click="offered = false">Not now</Button>
    </div>

    <p v-if="created && created === defect.fix_task_id" class="created" role="status"
       :data-testid="`defect-fix-created-${defect.id}`">
      Created
      <router-link :to="`/tasks/${created}`"><b>task #{{ created }}</b></router-link>
      to fix #{{ defect.id }} — listed under
      <router-link :to="`/features/${defect.feature_id}`">{{ defect.feature_title || `feature #${defect.feature_id}` }}</router-link>'s tasks.
    </p>

    <DefectRefusal v-if="refusal" :refusal="refusal" :busy="busy"
                   :testid="`defect-refusal-${defect.id}`" @remedy="onRemedy" />
  </div>
</template>

<style scoped>
.acts { display: flex; flex-direction: column; gap: 0.4rem; }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; }
.note { font-size: 11.5px; color: var(--color-fg-faint); max-width: 44rem; }
.form {
    display: flex; flex-wrap: wrap; align-items: flex-start; gap: 0.4rem;
    max-width: 44rem;
}
.form .grow { flex: 1 1 16rem; }
.hint { flex-basis: 100%; font-size: 11px; color: var(--color-fg-faint); }
.offer {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem;
    border-radius: var(--radius-lg); background: var(--color-surface-2);
    padding: 0.35rem 0.6rem; font-size: 11.5px; color: var(--color-fg-muted);
    max-width: 44rem;
}
.offer b { color: var(--color-fg); font-family: var(--font-mono, monospace); }
.created {
    border-radius: var(--radius-lg); background: var(--color-surface-2);
    padding: 0.35rem 0.6rem; font-size: 11.5px; color: var(--color-fg-muted);
    max-width: 44rem;
}
.created a { color: var(--color-primary); }
</style>
