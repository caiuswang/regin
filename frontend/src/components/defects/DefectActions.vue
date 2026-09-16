<script setup>
// The three writes the ledger owns, one defect at a time.
//
// There is no bulk control anywhere on this page. Attribution is a human
// asserting which feature CAUSED a bug, and a checkbox column is how eight
// rows get one guessed parent — the exact corruption the NULL `feature_key`
// state exists to prevent.
//
// Neither field is ever pre-filled. The feature key is not proposed from the
// suspect paths, the session or anything else, and the missing check is not
// proposed at all: both are the assertion this control exists to collect, and
// a default is an inference wearing a human's signature.
//
// Every refusal rendered here is the server's own sentence. The store's
// message names its remedy (`merge`, `reopen`), so paraphrasing it would
// leave the operator holding a dead end.
import { ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'

const props = defineProps({
  defect: { type: Object, required: true },
  canWrite: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])

const form = ref('')
const value = ref('')
const busy = ref(false)
const refusal = ref('')

const FORMS = {
  attribute: {
    label: 'Attribute', placeholder: 'feature key, e.g. regin:81',
    field: 'feature_key',
    hint: 'The feature that CAUSED this, which is not necessarily the one the reporting session was working on. Naming a different one re-reads that feature\u2019s state, so the escape verdict follows the blame.',
  },
  close: {
    label: 'Close', placeholder: 'the test that would have caught this',
    field: 'missing_check',
    hint: 'Name a test, then write it. This is the only field here that changes future behaviour.',
  },
}

function reveal(which) {
  form.value = form.value === which ? '' : which
  value.value = ''
  refusal.value = ''
}

async function submit() {
  const spec = FORMS[form.value]
  if (!spec) return
  busy.value = true
  refusal.value = ''
  const body = await api.post(
    `/defects/${props.defect.id}/${form.value}`, { [spec.field]: value.value })
  busy.value = false
  if (body?.ok === false) {
    refusal.value = body.msg || 'the ledger refused, without saying why'
    return
  }
  form.value = ''
  emit('changed', body.defect)
}

async function reopen() {
  busy.value = true
  refusal.value = ''
  const body = await api.post(`/defects/${props.defect.id}/reopen`)
  busy.value = false
  if (body?.ok === false) {
    refusal.value = body.msg || 'the ledger refused, without saying why'
    return
  }
  emit('changed', body.defect)
}
</script>

<template>
  <div v-if="canWrite" class="acts">
    <div class="row">
      <!-- Offered on an attributed defect too, now that `attribute()`
           re-takes the blame snapshot when the key actually changes. While it
           did not, a second attribution left the FIRST feature's
           `state_category` standing and silently mis-stated which state the
           defect escaped against (defect #137). -->
      <Button
        variant="secondary" size="sm"
        :aria-expanded="form === 'attribute'"
        @click="reveal('attribute')"
      >{{ defect.feature_key ? 'Re-attribute' : 'Attribute' }}</Button>
      <Button
        v-if="defect.status === 'open'"
        variant="secondary" size="sm"
        :aria-expanded="form === 'close'"
        @click="reveal('close')"
      >Close</Button>
      <Button
        v-if="defect.status === 'closed'"
        variant="secondary" size="sm" :disabled="busy"
        @click="reopen"
      >Reopen</Button>
      <span v-if="defect.feature_key" class="note">
        blamed on <b>{{ defect.feature_key }}</b>
      </span>
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
      />
      <!-- Carries the defect id so the submit and the disclosure that
           revealed it do not share one accessible name. -->
      <Button type="submit" size="sm" :disabled="busy">
        {{ FORMS[form].label }} #{{ defect.id }}
      </Button>
      <p class="hint">{{ FORMS[form].hint }}</p>
    </form>

    <p v-if="refusal" class="refusal" role="alert">{{ refusal }}</p>
  </div>
</template>

<style scoped>
.acts { display: flex; flex-direction: column; gap: 0.4rem; }
.row { display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem; }
.note { font-size: 11.5px; color: var(--color-fg-faint); max-width: 44rem; }
.note b { color: var(--color-fg-muted); font-family: var(--font-mono, monospace); }
.note code { font-size: 11px; color: var(--color-fg-muted); }
.form {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.4rem;
    max-width: 44rem;
}
.hint { flex-basis: 100%; font-size: 11px; color: var(--color-fg-faint); }
.refusal {
    border-radius: var(--radius-lg); background: var(--color-red-50);
    padding: 0.35rem 0.6rem; font-size: 11.5px; color: var(--color-red-700);
    max-width: 44rem;
}
</style>
