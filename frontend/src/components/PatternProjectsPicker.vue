<script setup>
import { computed, ref } from 'vue'
import api from '../api'
import { useFlash } from '../composables/useFlash'
import { useConfirm } from '../composables/useConfirm'
import ChannelRow from './ChannelRow.vue'

// Extracted from PatternDetailView's deployment rail (PR 2.4f). Owns:
//   * the repo typeahead picker (selectedRepos / repoQuery / repoPickerOpen)
//   * the pushing flag
//   * pickRepo / removeSelected / clearAllSelected
//   * pushToProject (incl. the force-push confirm flow)
//   * removeProjectDeployment
//   * the projectDeployments + filteredRepos + availableRepos computeds
//
// Parent provides the catalog (`repos`) and the loaded deployments list.
// After every action that mutates server state, the child emits `saved`
// so the parent can reload deployments + repos.
const props = defineProps({
  skillId: { type: String, required: true },
  repos: { type: Array, default: () => [] },
  deployments: { type: Array, default: () => [] },
})
const emit = defineEmits(['saved'])

const { flash } = useFlash()
const { confirm } = useConfirm()

const selectedRepos = ref([])
const repoQuery = ref('')
const repoPickerOpen = ref(false)
const pushing = ref(false)
const backfilling = ref(null)

const projectDeployments = computed(() =>
  props.deployments.filter((d) => d.scope === 'project'),
)

const availableRepos = computed(() => {
  // Hide repos that are already deployed AND repos in the pending
  // selection chip-list — matches the parent's prior behaviour.
  const deployedIds = new Set(projectDeployments.value.map((d) => d.project_id))
  const selectedIds = new Set(selectedRepos.value.map((r) => r.id))
  return props.repos.filter(
    (r) => !deployedIds.has(r.id) && !selectedIds.has(r.id),
  )
})

const filteredRepos = computed(() => {
  const q = repoQuery.value.trim().toLowerCase()
  if (!q) return availableRepos.value.slice(0, 12)
  return availableRepos.value
    .filter((r) => r.name.toLowerCase().includes(q))
    .slice(0, 12)
})

function pickRepo(r) {
  if (!selectedRepos.value.find((x) => x.id === r.id)) {
    selectedRepos.value.push(r)
  }
  repoQuery.value = ''
}

function removeSelected(r) {
  selectedRepos.value = selectedRepos.value.filter((x) => x.id !== r.id)
}

function clearAllSelected() {
  selectedRepos.value = []
  repoQuery.value = ''
}

async function pushToProject(opts = {}) {
  if (!selectedRepos.value.length) {
    flash('Select at least one project', 'error')
    return
  }
  pushing.value = true
  const targets = [...selectedRepos.value]
  const results = await Promise.all(
    targets.map((r) =>
      api.post(`/skills/${props.skillId}/push-to-project`, {
        project_id: r.id,
        ...opts,
      }).then((res) => ({ repo: r, res })),
    ),
  )
  pushing.value = false

  const succeeded = results.filter(({ res }) => res.ok)
  const failed = results.filter(({ res }) => !res.ok && !res.confirm_force)
  const needsForce = results.filter(({ res }) => res.confirm_force)

  if (needsForce.length) {
    const names = needsForce.map(({ repo }) => repo.name).join(', ')
    const ok = await confirm(
      'Force push to projects',
      `Drift detected on: ${names}. Force overwrite?`,
      false,
    )
    if (ok) {
      selectedRepos.value = needsForce.map(({ repo }) => repo)
      await pushToProject({ force: true })
      return
    }
  }

  if (succeeded.length) {
    flash(
      `Pushed to ${succeeded.length} project${succeeded.length > 1 ? 's' : ''}: ` +
        succeeded.map(({ repo }) => repo.name).join(', '),
    )
  }
  if (failed.length) {
    flash(
      `Failed: ${failed
        .map(({ repo, res }) => `${repo.name} (${res.msg})`)
        .join('; ')}`,
      'error',
    )
  }
  clearAllSelected()
  emit('saved')
}

async function backfillDeployment(projectId, projectName) {
  backfilling.value = projectId
  const result = await api.post(
    `/skills/${props.skillId}/backfill-deployment`,
    { project_id: projectId },
  )
  backfilling.value = null
  if (!result.ok) {
    flash(result.msg || `Failed to record ${projectName}`, 'error')
    return
  }
  flash(result.msg)
  emit('saved')
}

async function removeProjectDeployment(projectId, projectName) {
  const ok = await confirm(
    'Remove project deployment',
    `Delete ${props.skillId} from ${projectName} (.claude/skills/)? Source stays.`,
    true,
  )
  if (!ok) return
  const result = await api.del(
    `/skills/${props.skillId}/project-deployment/${projectId}`,
  )
  if (!result.ok) {
    flash(result.msg || 'Failed to remove', 'error')
    return
  }
  flash(result.msg)
  emit('saved')
}
</script>

<template>
  <ChannelRow
    name="Projects"
    :status="{ color: 'blue', label: String(projectDeployments.length) }">
    <ul v-if="projectDeployments.length" class="pdv-project-list">
      <li v-for="d in projectDeployments" :key="d.id" class="pdv-project-item">
        <span class="pdv-project-name">{{ d.project_name }}</span>
        <span class="pdv-project-path" :title="d.deployed_path">{{ d.deployed_path }}</span>
        <span
          v-if="!d.tracked"
          class="pdv-project-untracked"
          title="Found on disk but not in regin's deployment records">untracked</span>
        <button
          v-if="!d.tracked"
          type="button"
          class="pdv-project-backfill focus-visible:outline-2 focus-visible:outline-blue-500"
          :disabled="backfilling === d.project_id"
          :title="`Record this deployment to ${d.project_name}`"
          @click="backfillDeployment(d.project_id, d.project_name)">
          {{ backfilling === d.project_id ? 'Recording…' : 'Backfill' }}
        </button>
        <button
          v-else
          type="button"
          class="pdv-project-remove focus-visible:outline-2 focus-visible:outline-blue-500"
          :title="`Remove from ${d.project_name}`"
          :aria-label="`Remove from ${d.project_name}`"
          @click="removeProjectDeployment(d.project_id, d.project_name)">×</button>
      </li>
    </ul>
    <p v-else class="pdv-empty-hint">Not deployed to any project yet.</p>

    <div class="pdv-picker">
      <div class="pdv-picker-input">
        <span v-for="r in selectedRepos" :key="r.id" class="pdv-chip">
          <code>{{ r.name }}</code>
          <button
            type="button"
            @click="removeSelected(r)"
            class="pdv-chip-x focus-visible:outline-2 focus-visible:outline-blue-500"
            :aria-label="`Remove ${r.name}`">×</button>
        </span>
        <input
          type="text"
          v-model="repoQuery"
          @focus="repoPickerOpen = true"
          @input="repoPickerOpen = true"
          @keydown.esc="repoPickerOpen = false"
          @keydown.enter.prevent="filteredRepos[0] && pickRepo(filteredRepos[0])"
          @keydown.backspace="!repoQuery && selectedRepos.length && removeSelected(selectedRepos[selectedRepos.length - 1])"
          :placeholder="selectedRepos.length ? '' : 'Type to add a project…'"
          class="pdv-picker-search"
          aria-label="Filter project repos">
      </div>
      <button
        type="button"
        class="btn btn-primary text-xs focus-visible:outline-2 focus-visible:outline-blue-500"
        :disabled="!selectedRepos.length || pushing"
        @click="pushToProject()">
        {{ pushing
          ? 'Pushing…'
          : selectedRepos.length > 1
            ? `Push to ${selectedRepos.length}`
            : 'Push' }}
      </button>

      <aside
        v-if="repoPickerOpen && filteredRepos.length"
        @mousedown.prevent
        class="pdv-picker-menu"
        role="listbox"
        aria-label="Repo suggestions">
        <button
          v-for="r in filteredRepos"
          :key="r.id"
          type="button"
          @click="pickRepo(r)"
          class="pdv-picker-option focus-visible:outline-2 focus-visible:outline-blue-500">
          <code>{{ r.name }}</code>
          <span class="pdv-picker-option-path">{{ r.path }}</span>
        </button>
      </aside>
      <div
        v-else-if="repoPickerOpen && repoQuery && !filteredRepos.length"
        class="pdv-picker-menu pdv-picker-empty">
        No repos match "{{ repoQuery }}"
      </div>
    </div>
    <aside
      v-if="repoPickerOpen"
      class="fixed inset-0 z-0"
      aria-hidden="true"
      @click="repoPickerOpen = false"
      @keydown.esc="repoPickerOpen = false"></aside>
  </ChannelRow>
</template>

<style scoped>
/* These rules style slot content owned by this component, so they must
   live here: Vue's scoped CSS keys off the component that *defines* the
   markup, and the parent (PatternDetailView) can't reach it. */

/* Project list within the Projects channel row */
.pdv-project-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}
.pdv-project-item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0.5rem;
  background: #fff;
  border: 1px solid #f1f5f9;
  border-radius: 0.375rem;
  font-size: 0.8125rem;
}
.pdv-project-name {
  color: #047857;
  font-weight: 500;
  white-space: nowrap;
}
.pdv-project-path {
  flex: 1;
  min-width: 0;
  font-size: 0.6875rem;
  color: #94a3b8;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pdv-project-remove {
  background: transparent;
  border: 0;
  color: #94a3b8;
  cursor: pointer;
  padding: 0 0.25rem;
  font-size: 1rem;
  line-height: 1;
}
.pdv-project-remove:hover { color: #b91c1c; }

.pdv-project-untracked {
  flex-shrink: 0;
  font-size: 0.625rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: #b45309;
  background: #fef3c7;
  border-radius: 0.25rem;
  padding: 0.0625rem 0.3125rem;
}
.pdv-project-backfill {
  flex-shrink: 0;
  font-size: 0.6875rem;
  font-weight: 500;
  color: #1d4ed8;
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  border-radius: 0.25rem;
  padding: 0.125rem 0.4375rem;
  cursor: pointer;
}
.pdv-project-backfill:hover:not(:disabled) { background: #dbeafe; }
.pdv-project-backfill:disabled { opacity: 0.6; cursor: default; }
</style>
