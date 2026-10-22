<script setup>
// Arm every idea slice in one request. Each row opens with what its slice-table
// row proposes (`GET /tasks/<id>/arm-proposal`, derived now, never stored) and
// stays editable; the server arms row by row and names each refusal, so the
// sheet keeps the refused rows open with their reasons and drops the rest.
// Arming starts nothing by itself: a slice whose dependencies are already done
// is released by its edge (`fire_if_ready`, as for a single arm), and the
// operator starts the chain with Run on the first slice.
import { computed, onMounted, reactive, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import { DEFAULT_PROVIDER, useTaskArm } from '../../composables/useTaskArm.js'
import { useProjects } from '../../composables/useProjects.js'
import { ADD_PROJECT_VALUE, withAddProject } from '../../composables/useAddProject.js'
import AddProjectDialog from '../projects/AddProjectDialog.vue'

const props = defineProps({
  featureId: { type: Number, required: true },
  // SlicesReport rows whose task is an idea.
  rows: { type: Array, required: true },
  projectId: { type: Number, default: null },
})
const emit = defineEmits(['close', 'armed'])

const { loading, error, cwdOptions, providerOptions, loadOptions } = useTaskArm()
const { ensure: ensureProjects, byId } = useProjects()

const drafts = ref([])
const shared = reactive({ cwd: '', provider: DEFAULT_PROVIDER })
const reasons = ref({})
const saving = ref(false)
const failed = ref('')
let armedAny = false

function draftFor(row, proposal) {
  return reactive({
    task_id: row.task_id, n: row.n, title: row.title,
    branch: proposal?.branch || '',
    base: proposal?.base || row.base || '',
    skill: proposal?.skill || '',
  })
}

onMounted(async () => {
  const proposals = await Promise.all(props.rows.map(r =>
    api.get(`/tasks/${r.task_id}/arm-proposal`).catch(() => null)))
  drafts.value = props.rows.map((r, i) => draftFor(r, proposals[i]))
  await Promise.all([loadOptions(), ensureProjects()])
  shared.cwd = byId(props.projectId)?.path || ''
})

// A Select whose value is not among its options renders blank.
const cwdChoices = computed(() => {
  const listed = cwdOptions.value.some(o => o.value === shared.cwd)
  const extra = listed || !shared.cwd ? [] : [{ value: shared.cwd, label: shared.cwd }]
  return withAddProject([...cwdOptions.value, ...extra])
})
const addingProject = ref(false)
const cwdModel = computed({
  get: () => shared.cwd,
  set: (v) => { if (v === ADD_PROJECT_VALUE) addingProject.value = true; else shared.cwd = v },
})
async function projectAdded(added) {
  await loadOptions()
  shared.cwd = added.cwd
}

function item(d) {
  const body = {
    task_id: d.task_id, provider: shared.provider, cwd: shared.cwd,
  }
  if (d.branch.trim()) body.worktree = { branch: d.branch.trim(), base: d.base.trim() }
  if (d.skill.trim()) body.skill = { name: d.skill.trim() }
  return body
}

async function submit() {
  saving.value = true
  failed.value = ''
  const res = await api.post(`/features/${props.featureId}/arm-slices`,
    { items: drafts.value.map(item) })
  saving.value = false
  if (res?.ok === false) {
    failed.value = res.msg || 'the arm was refused'
    return
  }
  armedAny = armedAny || res.armed.length > 0
  reasons.value = Object.fromEntries(res.refused.map(r => [r.task_id, r.reason]))
  drafts.value = drafts.value.filter(d => d.task_id in reasons.value)
  if (!drafts.value.length) emit('armed')
}

function close() {
  emit(armedAny ? 'armed' : 'close')
}
</script>

<template>
  <Dialog
    :open="true"
    :title="`Arm ${rows.length} slices`"
    description="Each row opens with its slice-table proposal. A slice starts only once its dependencies are done."
    wide
    @update:open="v => !v && close()"
  >
    <div v-if="loading" class="sa-note">reading launch options…</div>
    <div v-else-if="error" class="sa-warn" role="alert">{{ error }}</div>
    <div v-else class="sa-body" data-testid="slice-arm-form">
      <label class="sa-lbl" for="slice-arm-cwd">working directory (every slice)</label>
      <Select
        id="slice-arm-cwd" v-model="cwdModel" block :options="cwdChoices"
        aria-label="Working directory" data-testid="slice-arm-cwd"
      />
      <template v-if="providerOptions.length">
        <label class="sa-lbl" for="slice-arm-provider">agent (every slice)</label>
        <Select
          id="slice-arm-provider" v-model="shared.provider" block :options="providerOptions"
          aria-label="Agent provider" data-testid="slice-arm-provider"
        />
      </template>
      <fieldset
        v-for="d in drafts" :key="d.task_id" class="sa-row"
        :data-testid="`slice-arm-row-${d.n}`"
      >
        <legend class="sa-legend">{{ d.n }} — {{ d.title }}</legend>
        <div class="sa-grid">
          <Input v-model="d.branch" :aria-label="`${d.n} branch`" placeholder="branch" :data-testid="`slice-arm-branch-${d.n}`" />
          <Input v-model="d.base" :aria-label="`${d.n} base`" placeholder="base" :data-testid="`slice-arm-base-${d.n}`" />
          <Input v-model="d.skill" :aria-label="`${d.n} skill`" placeholder="skill" :data-testid="`slice-arm-skill-${d.n}`" />
        </div>
        <p
          v-if="reasons[d.task_id]" class="sa-warn" role="alert"
          :data-testid="`slice-arm-refusal-${d.n}`"
        >{{ reasons[d.task_id] }}</p>
      </fieldset>
      <p v-if="failed" class="sa-warn" role="alert" data-testid="slice-arm-error">{{ failed }}</p>
    </div>

    <template #footer>
      <Button variant="ghost" @click="close">Cancel</Button>
      <Button
        variant="primary" :disabled="saving || loading || !!error || !drafts.length"
        data-testid="slice-arm-submit" @click="submit"
      >{{ saving ? 'Arming…' : `Arm ${drafts.length}` }}</Button>
    </template>
  </Dialog>
  <AddProjectDialog v-model:open="addingProject" @added="projectAdded" />
</template>

<style scoped>
.sa-body { display: flex; flex-direction: column; gap: 0.6rem; }
.sa-lbl { font-size: 0.8125rem; color: var(--color-fg-muted); }
.sa-note { font-size: 0.8125rem; color: var(--color-fg-muted); }
.sa-row {
    border: 1px solid var(--color-border); border-radius: var(--radius-md);
    padding: 0.5rem 0.6rem;
}
.sa-legend { font-size: 0.8125rem; font-weight: 600; padding: 0 0.25rem; color: var(--color-fg); }
.sa-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr)); gap: 0.4rem; }
.sa-warn {
    margin: 0.35rem 0 0; font-size: 0.8125rem; color: var(--color-amber-800);
    background: var(--color-amber-50); border-radius: 0.4rem; padding: 0.3rem 0.5rem;
}
</style>
