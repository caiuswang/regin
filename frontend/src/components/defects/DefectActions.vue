<script setup>
// The writes the ledger owns, one defect at a time.
//
// There is no bulk control anywhere on this page. Attribution is a human
// asserting which feature CAUSED a bug, and a checkbox column is how eight
// rows get one guessed parent — the exact corruption the NULL `feature_id`
// state exists to prevent.
//
// Neither field is ever pre-filled. The feature is not proposed from the
// suspect paths, the session or anything else, and the missing check is not
// proposed at all: both are the assertion this control exists to collect, and
// a default is an inference wearing a human's signature.
//
// Every refusal rendered here is the server's own sentence. The store's
// message names its remedy (`merge`, `reopen`), so paraphrasing it would
// leave the operator holding a dead end.
import { computed, ref } from 'vue'
import api from '../../api'
import { useConfirm } from '../../composables/useConfirm'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import DropdownMenu from '../ui/DropdownMenu.vue'

const props = defineProps({
  defect: { type: Object, required: true },
  canWrite: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])

const { confirm } = useConfirm()

const form = ref('')
const value = ref('')
const busy = ref(false)
const refusal = ref('')
// Raised by a close on this page, never by arriving at an already-closed
// defect: the offer is the answer to "you just named a check — who writes
// it", and a row closed weeks ago is being read, not decided.
const offered = ref(false)
// The task this page just filed. Held here rather than read off the row
// because a flash fades, and the row's "fix item" fact reads as a pointer that
// was always there — neither says "this is the task you just created".
const created = ref(null)

// A task no feature owns is an orphan, so the store refuses the create; the
// disabled button says so before the round trip does.
const ATTRIBUTE_FIRST = 'Attribute it to a feature to create its fix task.'

const FORMS = {
  attribute: {
    label: 'Attribute', placeholder: 'feature id, e.g. 81',
    field: 'feature_id', numeric: true,
    // `Number('eight')` is `NaN`, and a NaN posted as a foreign key is a
    // request the server can only answer with a 400 the operator reads as
    // the ledger refusing their assertion rather than as their own typo.
    invalid: (raw) => (/^\d+$/.test(raw.trim()) && Number(raw) > 0
      ? '' : 'A feature id is a positive whole number.'),
    hint: 'The feature that CAUSED this, which is not necessarily the one the reporting session was working on. Naming a different one re-points the blame at that feature\u2019s current last event, so the escape verdict follows the blame.',
    done: 'attributed',
  },
  close: {
    label: 'Close', placeholder: 'the test that would have caught this',
    field: 'missing_check',
    // The store refuses a blank check with a 400. Refusing it here as well is
    // not a second rulebook — it is the same rule stated where the operator
    // can still act on it, before the round trip that loses their place.
    invalid: (raw) => (raw.trim()
      ? '' : 'Name the check that would have caught this. It is the only field here that changes future behaviour.'),
    hint: 'Name a test, then write it. This is the only field here that changes future behaviour.',
    done: 'closed',
  },
  merge: {
    label: 'Merge', placeholder: 'the defect to KEEP, e.g. 17',
    field: 'into', numeric: true,
    invalid: (raw) => (/^\d+$/.test(raw.trim()) && Number(raw) > 0
      ? '' : 'A defect id is a positive whole number.'),
    hint: 'This defect folds into that one and is deleted; the keeper takes the union of both suspect paths. The remedy `attribute` names when it refuses a symptom already reported against the feature.',
    done: 'folded into the keeper',
  },
  'fix-item': {
    label: 'Link task', placeholder: 'task id, e.g. 142',
    field: 'task_id', numeric: true,
    invalid: (raw) => (/^\d+$/.test(raw.trim()) && Number(raw) > 0
      ? '' : 'A task id is a positive whole number.'),
    hint: 'An item that already exists. Last writer wins — re-pointing is how a wrong one is corrected, and nothing is counted from this pointer.',
    done: 'pointed at its fix task',
  },
}

// Offered on an attributed defect too, now that `attribute()` re-takes the
// blame snapshot when the key actually changes. While it did not, a second
// attribution left the FIRST feature's `state_category` standing and silently
// mis-stated which state the defect escaped against (defect #137).
// Create is hidden while the post-close offer stands, so the same verb is not
// asked for twice on one row. Merge is the remedy `attribute`'s own refusal
// names.
const menuItems = computed(() => {
  const d = props.defect
  const items = [{
    label: d.feature_id ? 'Re-attribute' : 'Attribute',
    onSelect: () => reveal('attribute'),
    testid: `defect-attribute-reveal-${d.id}`,
  }]
  if (!d.fix_task_id && !offered.value) {
    items.push({
      label: 'Create fix task',
      onSelect: createFixTask,
      disabled: busy.value || !d.feature_id,
      title: d.feature_id ? '' : ATTRIBUTE_FIRST,
      testid: `defect-fix-create-row-${d.id}`,
    })
  }
  items.push(
    {
      label: d.fix_task_id ? 'Re-point fix task' : 'Link existing task',
      onSelect: () => reveal('fix-item'),
      testid: `defect-fix-item-reveal-${d.id}`,
    },
    { separator: true },
    { label: 'Merge', onSelect: () => reveal('merge'), testid: 'defect-merge-reveal' },
  )
  return items
})

// Live, so the submit is disabled as soon as the field cannot be sent —
// rather than after a post that was never going to be accepted.
const problem = computed(
  () => (form.value ? FORMS[form.value].invalid(value.value) : ''))

function reveal(which) {
  form.value = form.value === which ? '' : which
  value.value = ''
  refusal.value = ''
}

async function submit() {
  const spec = FORMS[form.value]
  if (!spec) return
  if (problem.value) {
    refusal.value = problem.value
    return
  }
  busy.value = true
  refusal.value = ''
  const body = await api.post(
    `/defects/${props.defect.id}/${form.value}`,
    // An id goes as a number: the route reads `feature_id` straight off the
    // body, and a string there would reach the foreign key as one.
    // Trimmed: the store stores what it is sent, and a check named
    // "  test_x  " is a different string from the one the author typed.
    { [spec.field]: spec.numeric ? Number(value.value.trim()) : value.value.trim() })
  busy.value = false
  if (body?.ok === false) {
    refusal.value = body.msg || 'the ledger refused, without saying why'
    return
  }
  const which = form.value
  form.value = ''
  if (which === 'merge') {
    // The row the operator was looking at no longer exists — the store
    // deletes the duplicate — and the response carries the keeper. Naming the
    // survivor is the only honest sentence here.
    emit('changed', `#${props.defect.id} folded into #${body.defect.id}.`)
    return
  }
  // Only a close raises the offer, and only when nothing is pointed at yet:
  // the pointer is last-writer-wins, but silently proposing a SECOND task for
  // a defect that already has one is how the board grows duplicates.
  offered.value = which === 'close' && !props.defect.fix_task_id
  emit('changed', `#${props.defect.id} ${spec.done}.`)
}

// The other half of the close: the check just named is a test somebody has to
// write, and this is the only moment anyone is looking at the defect knowing
// that. The task carries the symptom verbatim as its title and the named
// check in its body — regin authors it, so no field here is pre-filled for a
// human to rubber-stamp.
async function createFixTask() {
  busy.value = true
  refusal.value = ''
  const body = await api.post(`/defects/${props.defect.id}/fix-item`,
                              { create: true })
  busy.value = false
  if (body?.ok === false) {
    refusal.value = body.msg || 'the ledger refused, without saying why'
    return
  }
  offered.value = false
  created.value = body.defect.fix_task_id
  emit('changed',
       `#${props.defect.id} will be fixed by task #${body.defect.fix_task_id}.`)
}

// Reopening is not undo. It records that we claimed this fixed and were
// wrong, and the round it bumps is a permanent figure on the feature that
// caused it — so it is asked for once, out loud, before it is written.
async function reopen() {
  const ok = await confirm(
    'Reopen',
    `Reopen defect #${props.defect.id}? This records that the fix did not hold `
    + `and takes it to round ${props.defect.round + 1} — a figure its feature `
    + 'keeps for good.',
    true)
  if (!ok) return
  busy.value = true
  refusal.value = ''
  const body = await api.post(`/defects/${props.defect.id}/reopen`)
  busy.value = false
  if (body?.ok === false) {
    refusal.value = body.msg || 'the ledger refused, without saying why'
    return
  }
  emit('changed', `#${props.defect.id} reopened at round ${body.defect.round}.`)
}
</script>

<template>
  <div v-if="canWrite" class="acts">
    <div class="row">
      <!-- One primary verb, un-nested: the status decides it. Everything
           else a row can do sits behind the menu, so a closed defect with
           nothing left to decide no longer reads as a wall of buttons. -->
      <Button
        v-if="defect.status === 'open'"
        variant="secondary" size="sm"
        :aria-expanded="form === 'close'"
        @click="reveal('close')"
      >Close</Button>
      <Button
        v-else
        variant="secondary" size="sm" :disabled="busy"
        @click="reopen"
      >Reopen</Button>
      <DropdownMenu :items="menuItems" align="start">
        <template #trigger>
          <Button variant="ghost" size="sm" class="ledger-reveal"
                  :aria-label="`More actions for defect #${defect.id}`"
                  :data-testid="`defect-more-${defect.id}`">⋯</Button>
        </template>
      </DropdownMenu>
      <span v-if="!defect.feature_id && !defect.fix_task_id" class="note"
            :data-testid="`defect-fix-create-blocked-${defect.id}`">{{ ATTRIBUTE_FIRST }}</span>
    </div>

    <form v-if="form" class="form" @submit.prevent="submit">
      <label class="sr-only" :for="`f-${defect.id}`">
        {{ FORMS[form].placeholder }}
      </label>
      <Input
        :id="`f-${defect.id}`"
        v-model="value"
        class="grow"
        :placeholder="FORMS[form].placeholder"
        :disabled="busy"
        :error="!!problem && !!value"
      />
      <!-- Carries the defect id so the submit and the disclosure that
           revealed it do not share one accessible name. -->
      <Button type="submit" size="sm" :disabled="busy || !!problem"
              :data-testid="`defect-${form}-submit-${defect.id}`">
        {{ FORMS[form].label }} #{{ defect.id }}
      </Button>
      <p class="hint">{{ FORMS[form].hint }}</p>
      <!-- Said before the post, not after it: the server would answer a blank
           check or a NaN id with a 400, and a round trip to learn what the
           field already knew costs the operator their place in the list. -->
      <p v-if="problem && value" class="refusal" role="alert"
         :data-testid="`defect-${form}-problem-${defect.id}`">{{ problem }}</p>
    </form>

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

    <p v-if="refusal" class="refusal" role="alert">{{ refusal }}</p>
  </div>
</template>

<style scoped>
.acts { display: flex; flex-direction: column; gap: 0.4rem; }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; }
.note { font-size: 11.5px; color: var(--color-fg-faint); max-width: 44rem; }
.form {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem;
    max-width: 44rem;
}
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
.refusal {
    border-radius: var(--radius-lg); background: var(--color-red-50);
    padding: 0.35rem 0.6rem; font-size: 11.5px; color: var(--color-red-700);
    max-width: 44rem;
}
</style>
