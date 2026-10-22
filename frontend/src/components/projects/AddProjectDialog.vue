<script setup>
// The one way to add a project. A path is probed before anything is written,
// and the probe picks the mode: a git working tree registers as a repo, a
// folder of them as a workspace (`probeVerdict` holds the branch table).
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Dialog from '../ui/Dialog.vue'
import Input from '../ui/Input.vue'
import PlGlyph from '../pipeline/PlGlyph.vue'
import RadioGroup from '../ui/RadioGroup.vue'
import { useProjects } from '../../composables/useProjects'
import { MODE_REPO, MODE_WORKSPACE, probeVerdict } from '../../composables/useAddProject'

const open = defineModel('open', { type: Boolean, default: false })
const emit = defineEmits(['added'])

const { items: projects, refresh: reloadProjects } = useProjects()

const path = ref('')
const name = ref('')
const probe = ref(null)
const mode = ref('')
const existingName = ref('')
const busy = ref(false)
const failure = ref('')
const pathInput = ref(null)

const MODE_LABELS = { [MODE_REPO]: 'Repository', [MODE_WORKSPACE]: 'Workspace' }

const verdict = computed(() => (probe.value ? probeVerdict(probe.value) : null))
const blocked = computed(() => {
  const v = verdict.value
  if (!v) return ''
  if (v.existing) return `Already workspace ${existingName.value || `#${probe.value.workspace_id}`}`
  return v.error
})
const modeOptions = computed(() => (verdict.value?.modes || []).map(m => ({ value: m, label: MODE_LABELS[m] })))
const childRepos = computed(() => probe.value?.child_repos || [])
const primaryLabel = computed(() => {
  if (busy.value) return probe.value ? 'Adding…' : 'Checking…'
  if (!probe.value) return 'Check path'
  return mode.value === MODE_WORKSPACE ? 'Create workspace' : 'Add repository'
})
const primaryDisabled = computed(() => busy.value || !path.value.trim() || !!blocked.value)

watch(open, (isOpen) => {
  if (!isOpen) return
  path.value = ''
  name.value = ''
  reset()
})
watch(path, reset)

function reset() {
  probe.value = null
  mode.value = ''
  existingName.value = ''
  failure.value = ''
}

async function runProbe() {
  const asked = path.value.trim()
  busy.value = true
  failure.value = ''
  try {
    const found = await api.get(`/repos/probe?path=${encodeURIComponent(asked)}`)
    if (asked !== path.value.trim()) return
    probe.value = found
    mode.value = probeVerdict(found).modes[0] || ''
    if (found.workspace_id != null) existingName.value = await workspaceName(found.workspace_id)
  } catch (e) {
    failure.value = e?.message || String(e)
  } finally {
    busy.value = false
  }
}

async function workspaceName(id) {
  try {
    const { workspaces } = await api.get('/workspaces')
    return workspaces.find(w => w.id === id)?.name || ''
  } catch {
    return ''
  }
}

async function addRepo() {
  const res = await api.post('/repos', { path: probe.value.path })
  if (!res.ok) return res
  await reloadProjects()
  const project = projects.value.find(p => p.path === res.repo.path) || null
  return { ...res, added: { kind: MODE_REPO, project, cwd: res.repo.path } }
}

async function addWorkspace() {
  const res = await api.post('/workspaces', { path: probe.value.path, name: name.value.trim() || undefined })
  if (!res.ok) return res
  await reloadProjects()
  return { ...res, added: { kind: MODE_WORKSPACE, workspace: res.workspace, cwd: res.workspace.root_path } }
}

async function submit() {
  if (primaryDisabled.value) return
  if (!probe.value) return runProbe()
  busy.value = true
  failure.value = ''
  let res
  try {
    res = await (mode.value === MODE_WORKSPACE ? addWorkspace() : addRepo())
  } catch (e) {
    res = { ok: false, msg: e?.message || String(e) }
  } finally {
    busy.value = false
  }
  if (!res.ok) {
    failure.value = res.msg || 'Add failed'
    return
  }
  open.value = false
  emit('added', res.added)
}
</script>

<template>
  <Dialog
    v-model:open="open"
    title="Add project"
    description="Enter the absolute path of a git working tree, or of a folder holding several."
    data-testid="add-project-dialog"
    data-pl-part="dialog"
    kicker="New project · repo or workspace"
    @open-auto-focus="pathInput?.focus()"
  >
    <template #mark><PlGlyph kind="project" :scale="2" /></template>
    <form class="ap-form" @submit.prevent="submit">
      <label class="ap-lbl" for="add-project-path" data-pl-part="field-label">Path</label>
      <Input
        id="add-project-path"
        ref="pathInput"
        v-model="path"
        class="font-mono"
        placeholder="/Users/you/code/my-service"
        aria-label="Project path"
        data-testid="add-project-path"
        data-pl-part="input"
        :disabled="busy"
        :error="!!blocked"
      />
      <p v-if="blocked" class="ap-error" role="alert" data-pl-part="dialog-error" data-testid="add-project-blocked">{{ blocked }}</p>
      <template v-else-if="probe">
        <RadioGroup
          v-if="modeOptions.length > 1"
          v-model="mode"
          :options="modeOptions"
          name="add-project-mode"
          inline
          data-testid="add-project-mode"
        />
        <p v-else class="ap-note" data-pl-part="field-hint" data-testid="add-project-kind">{{ modeOptions[0]?.label }}</p>
        <template v-if="mode === MODE_WORKSPACE">
          <label class="ap-lbl" for="add-project-name" data-pl-part="field-label">Workspace name <span class="ap-optional">(optional)</span></label>
          <Input
            id="add-project-name"
            v-model="name"
            :placeholder="probe.path.split(/[\\/]/).filter(Boolean).pop() || ''"
            aria-label="Workspace name"
            data-testid="add-project-name"
            data-pl-part="input"
          />
          <p class="ap-note" data-pl-part="field-label">Repositories it will hold:</p>
          <ul class="ap-children" data-pl-part="dialog-note" data-testid="add-project-children">
            <li v-if="probe.is_git"><code>{{ probe.path }}</code></li>
            <li v-for="c in childRepos" :key="c.path">
              <code>{{ c.name }}</code>
              <span v-if="c.registered" class="ap-optional">already registered</span>
            </li>
          </ul>
        </template>
      </template>
      <p v-if="failure" class="ap-error" role="alert" data-pl-part="dialog-error" data-testid="add-project-error">{{ failure }}</p>
    </form>
    <template #footer>
      <Button variant="secondary" data-pl-part="key" :disabled="busy" @click="open = false">Cancel</Button>
      <Button
        variant="primary"
        :disabled="primaryDisabled"
        data-testid="add-project-submit"
        data-pl-part="key--primary"
        @click="submit"
      >{{ primaryLabel }}</Button>
    </template>
  </Dialog>
</template>

<style scoped>
.ap-form { display: flex; flex-direction: column; gap: 0.5rem; }
.ap-lbl { font-size: 0.75rem; font-weight: 500; color: var(--color-fg-muted); }
.ap-optional { font-weight: 400; color: var(--color-fg-faint); margin-left: 0.35rem; }
.ap-note { font-size: 0.8125rem; color: var(--color-fg-muted); }
.ap-error { font-size: 0.8125rem; color: var(--color-danger-strong); }
.ap-children { display: flex; flex-direction: column; gap: 0.2rem; padding-left: 0.25rem; }
</style>
