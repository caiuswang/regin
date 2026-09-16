<script setup>
// Declaring a feature from the population page — and adopting one that already
// exists in a tracker.
//
// The spec field is offered here and not deferred to the ledger page because
// the thing worth writing down is what the feature is for, and it is never
// closer to hand than at the moment somebody decides to have one. It stays
// optional: a feature with a title and no spec is a real row, and refusing it
// would push the operator back to the command line the page exists to replace.
//
// Adopting is a MODE, not a second field. The two shapes have two writers of
// the title — a native feature's is typed here, an adopted one's comes from the
// fetch — so one input meaning either a title to write or a reference to
// resolve is exactly the ambiguity the modes remove. `POST /api/features` has
// carried `ref` since the endpoint existed; until now nothing in the browser
// could reach it, so an issue could only be adopted from the CLI.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import Textarea from '../ui/Textarea.vue'

const open = defineModel('open', { type: Boolean, default: false })
const emit = defineEmits(['declared'])

const mode = ref('declare')
const title = ref('')
const body = ref('')
const reference = ref('')
const provider = ref('')
const saving = ref(false)
const failed = ref('')

const adopting = computed(() => mode.value === 'adopt')
const description = computed(() => (adopting.value
  ? 'The issue keeps its own title and its own key. regin fetches it once, and'
    + ' from then on the rounds, the cost and the defects are counted here.'
  : 'What is being delivered. The spec is thin prose — no file paths, which at'
    + ' this point would specify an implementation that does not exist yet.'))

watch(open, (isOpen) => {
  if (!isOpen) return
  mode.value = 'declare'
  title.value = ''
  body.value = ''
  reference.value = ''
  provider.value = ''
  failed.value = ''
})

// The empty title and the unresolvable reference are both refused by the
// server, and that refusal is the one this renders: a second rule in the page
// would be a second writer of what a feature needs, and the two drift.
async function declare() {
  saving.value = true
  failed.value = ''
  const payload = adopting.value
    ? { ref: reference.value.trim(), provider: provider.value.trim() }
    : { title: title.value, body: body.value }
  const ledger = await api.post('/features', payload)
  saving.value = false
  if (ledger?.ok === false) {
    failed.value = ledger.msg || 'that feature was refused'
    return
  }
  open.value = false
  emit('declared', ledger)
}
</script>

<template>
  <Dialog
    v-model:open="open"
    :title="adopting ? 'Adopt a tracker issue' : 'Declare a feature'"
    :description="description"
  >
    <div class="nf-modes" role="group" aria-label="How this feature comes into being">
      <Button
        :variant="adopting ? 'ghost' : 'secondary'"
        size="sm"
        data-testid="mode-declare"
        :aria-pressed="!adopting"
        @click="mode = 'declare'"
      >Declare new</Button>
      <Button
        :variant="adopting ? 'secondary' : 'ghost'"
        size="sm"
        data-testid="mode-adopt"
        :aria-pressed="adopting"
        @click="mode = 'adopt'"
      >Adopt a tracker issue</Button>
    </div>

    <form class="nf-form" @submit.prevent="declare">
      <template v-if="adopting">
        <label class="nf-label" for="nf-ref">Issue reference</label>
        <Input
          id="nf-ref"
          v-model="reference"
          data-testid="adopt-ref"
          placeholder="CAI-274, or a full issue URL"
        />

        <label class="nf-label" for="nf-provider">Provider</label>
        <Input
          id="nf-provider"
          v-model="provider"
          data-testid="adopt-provider"
          placeholder="leave blank to let the reference decide"
        />
      </template>

      <template v-else>
        <label class="nf-label" for="nf-title">Title</label>
        <Input
          id="nf-title"
          v-model="title"
          data-testid="declare-title"
          placeholder="what is being delivered"
        />

        <label class="nf-label" for="nf-body">Spec</label>
        <Textarea
          id="nf-body"
          v-model="body"
          data-testid="declare-body"
          :rows="8"
          placeholder="Intent, non-goals, and the acceptance items — as test names"
        />
      </template>

      <p v-if="failed" class="nf-fail" role="alert">{{ failed }}</p>
    </form>

    <template #footer>
      <Button variant="ghost" @click="open = false">Cancel</Button>
      <Button
        variant="primary"
        data-testid="declare-submit"
        :disabled="saving"
        @click="declare"
      >{{ adopting ? 'Adopt' : 'Declare' }}</Button>
    </template>
  </Dialog>
</template>

<style scoped>
.nf-modes { display: flex; gap: 0.35rem; margin-bottom: 0.4rem; }
.nf-form { display: flex; flex-direction: column; gap: 0.35rem; }
.nf-label {
    font-size: 11.5px; font-weight: 600; color: var(--color-fg-subtle);
    margin-top: 0.4rem;
}
.nf-fail {
    margin-top: 0.5rem; font-size: 12px; color: var(--color-red-700);
}
</style>
