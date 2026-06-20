<script setup>
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import Breadcrumb from '../components/Breadcrumb.vue'
import MarkdownContent from '../components/MarkdownContent.vue'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import PatternContentEditor from '../components/PatternContentEditor.vue'
import PatternDescriptionEditor from '../components/PatternDescriptionEditor.vue'
import PatternTagsEditor from '../components/PatternTagsEditor.vue'
import { useFlash } from '../composables/useFlash'
import { useConfirm } from '../composables/useConfirm'
import { useProviderPaths } from '../composables/useProviderPaths'

const route = useRoute()
const router = useRouter()
const { flash } = useFlash()
const { confirm } = useConfirm()
const data = ref(null)
const loading = ref(true)
const notFound = ref(false)
const deployments = ref([])
const repos = ref([])
const repoQuery = ref('')
const repoPickerOpen = ref(false)
const selectedRepos = ref([])
const pushing = ref(false)

const stateBadge = {
  in_sync: { color: 'green', label: 'deployed', scope: 'global' },
  drifted: { color: 'yellow', label: 'out of sync', scope: 'global' },
  deployed_only: { color: 'blue', label: 'orphan (source missing)' },
  source_only: { color: 'purple', label: 'not deployed' },
  project_only: { color: 'green', label: 'deployed', scope: 'project' },
}

const { globalDir, projectSubpath, providerName, providerBadge } = useProviderPaths(data)
const isPatternSkill = computed(() => data.value?.entry?.type === 'pattern')
const deployedPath = computed(() => data.value?.deployed || `${globalDir.value}/${data.value?.skill_id || ''}`)

async function load() {
  try {
    const result = await api.get(`/skills/${route.params.id}`)
    if (result.redirect) {
      // Pattern skills are managed inline from the skill page. Load the
      // pattern detail alongside the skill metadata.
      const slug = result.redirect.replace(/^\/patterns\//, '')
      const pattern = await api.get(`/patterns/${slug}`)
      data.value = {
        skill_id: route.params.id,
        entry: { type: 'pattern', procedure_id: slug },
        state: pattern.skill_state,
        source_rel: pattern.doc?.file_path || '',
        deployed: '',
        body_md: pattern.body_md,
        description: pattern.description,
        tags: pattern.tags,
        all_tags: pattern.all_tags,
        doc: pattern.doc,
        provider: pattern.provider,
        concealed_texts: pattern.concealed_texts || [],
        _pattern: pattern,
      }
    } else {
      data.value = result
    }
    loading.value = false
    await loadDeployments()
  } catch {
    notFound.value = true
    loading.value = false
  }
}

async function loadDeployments() {
  if (!data.value) return
  const result = await api.get(`/skills/${data.value.skill_id}/deployments`)
  deployments.value = result?.deployments || []
  if (!repos.value.length) {
    repos.value = (await api.get('/repos')).repos || []
  }
}

const availableRepos = computed(() => {
  const deployedIds = new Set(
    deployments.value.filter(d => d.scope === 'project').map(d => d.project_id),
  )
  const selectedIds = new Set(selectedRepos.value.map(r => r.id))
  return repos.value.filter(r => !deployedIds.has(r.id) && !selectedIds.has(r.id))
})

const filteredRepos = computed(() => {
  const q = repoQuery.value.trim().toLowerCase()
  if (!q) return availableRepos.value.slice(0, 12)
  return availableRepos.value
    .filter(r => r.name.toLowerCase().includes(q))
    .slice(0, 12)
})

const projectDeployments = computed(() =>
  deployments.value.filter(d => d.scope === 'project'),
)
const globalDeployment = computed(() =>
  deployments.value.find(d => d.scope === 'global'),
)

function pickRepo(r) {
  if (!selectedRepos.value.find(x => x.id === r.id)) {
    selectedRepos.value.push(r)
  }
  repoQuery.value = ''
}

function removeSelected(r) {
  selectedRepos.value = selectedRepos.value.filter(x => x.id !== r.id)
}

function clearAllSelected() {
  selectedRepos.value = []
  repoQuery.value = ''
}

onMounted(load)

async function pull() {
  const result = await api.post(`/skills/${data.value.skill_id}/pull`)
  if (!result.ok) { flash(result.msg || 'Failed to pull', 'error'); return }
  flash(result.msg)
  await load()
}
async function push(opts = {}) {
  const result = await api.post(`/skills/${data.value.skill_id}/push`, opts)
  if (!result.ok) {
    if (result.confirm_force) {
      const ok = await confirm('Force push', result.msg, false)
      if (ok) { await push({ force: true }); return }
    } else {
      flash(result.msg || 'Failed to push', 'error')
    }
    return
  }
  flash(result.msg)
  await load()
}
async function forcePush() {
  const ok = await confirm('Force push', 'Overwrite deployed skill with source version?', false)
  if (!ok) return
  const result = await api.post(`/skills/${data.value.skill_id}/push`, { force: true })
  if (!result.ok) { flash(result.msg || 'Failed to push', 'error'); return }
  flash(result.msg)
  await load()
}
async function regenerate() {
  const result = await api.post(`/skills/${data.value.skill_id}/regenerate`)
  if (!result.ok) { flash(result.msg || 'Failed to regenerate', 'error'); return }
  flash(result.msg)
  await load()
}
async function undeploy() {
  const ok = await confirm('Undeploy', `Remove ${globalDir}/${data.value.skill_id}/? Source stays. Re-push later.`, true)
  if (!ok) return
  const result = await api.post(`/skills/${data.value.skill_id}/undeploy`)
  if (!result.ok) { flash(result.msg || 'Failed to undeploy', 'error'); return }
  flash(result.msg)
  await load()
}
async function deleteSkill() {
  if (!isPatternSkill.value) {
    flash('Only pattern skills can be deleted', 'error')
    return
  }
  const slug = data.value.entry.procedure_id
  const ok = await confirm(
    'Delete skill',
    `Permanently delete "${data.value.doc?.title || slug}"? This removes the source directory, any deployed skill copies, and deployment records.`,
    true,
  )
  if (!ok) return
  const result = await api.post(`/patterns/${slug}/delete`)
  if (!result.ok) { flash(result.msg || 'Failed to delete', 'error'); return }
  flash(result.msg || `Deleted ${slug}`)
  router.push('/skills')
}
async function pushToProject(opts = {}) {
  if (!selectedRepos.value.length) {
    flash('Select at least one project', 'error')
    return
  }
  pushing.value = true
  const targets = [...selectedRepos.value]
  const results = await Promise.all(
    targets.map(r =>
      api.post(`/skills/${data.value.skill_id}/push-to-project`, {
        project_id: r.id,
        ...opts,
      }).then(res => ({ repo: r, res })),
    ),
  )
  pushing.value = false

  const succeeded = results.filter(({ res }) => res.ok)
  const failed = results.filter(({ res }) => !res.ok && !res.confirm_force)
  const needsForce = results.filter(({ res }) => res.confirm_force)

  if (needsForce.length) {
    const names = needsForce.map(({ repo }) => repo.name).join(', ')
    const ok = await confirm('Force push to projects', `Drift detected on: ${names}. Force overwrite?`, false)
    if (ok) {
      selectedRepos.value = needsForce.map(({ repo }) => repo)
      await pushToProject({ force: true })
      return
    }
  }

  if (succeeded.length) {
    flash(`Pushed to ${succeeded.length} project${succeeded.length > 1 ? 's' : ''}: ${succeeded.map(({ repo }) => repo.name).join(', ')}`)
  }
  if (failed.length) {
    flash(`Failed: ${failed.map(({ repo, res }) => `${repo.name} (${res.msg})`).join('; ')}`, 'error')
  }
  clearAllSelected()
  await loadDeployments()
}

async function removeProjectDeployment(projectId, projectName) {
  const ok = await confirm(
    'Remove project deployment',
    `Delete ${data.value.skill_id} from ${projectName} (${projectSubpath}/)? Source stays.`,
    true,
  )
  if (!ok) return
  const result = await api.del(`/skills/${data.value.skill_id}/project-deployment/${projectId}`)
  if (!result.ok) { flash(result.msg || 'Failed to remove', 'error'); return }
  flash(result.msg)
  await loadDeployments()
}
</script>

<template>
  <div v-if="loading" class="empty-state">Loading skill…</div>
  <div v-else-if="notFound">
    <Breadcrumb :items="[
      { label: 'Skills', to: '/skills' },
      { label: route.params.id },
    ]" />
    <div class="empty-state">
      <p class="text-gray-500 text-sm">
        <code class="cell-code">{{ route.params.id }}</code> is not managed by regin — it may be a bundled {{ providerName }} skill or installed externally.
      </p>
      <router-link to="/skills" class="text-link text-sm mt-3 inline-flex items-center gap-1"><Icon name="chevron-left" :size="14" />Back to Skills</router-link>
    </div>
  </div>
  <div v-else>
    <Breadcrumb :items="[
      { label: 'Skills', to: '/skills' },
      { label: data.skill_id },
    ]" />

    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Skill</div>
        <h1 class="page-title">
          <code class="cell-code text-base">{{ data.skill_id }}</code>
          <Badge color="gray" :label="data.entry.type" />
          <Badge :color="stateBadge[data.state]?.color || 'gray'" :label="stateBadge[data.state]?.label || data.state" />
          <Badge v-if="stateBadge[data.state]?.scope" color="gray" :label="stateBadge[data.state].scope" />
        </h1>
      </div>
    </header>

    <Card>
      <h2 class="card-header">Paths</h2>
      <dl class="meta-list">
        <dt>Source</dt>
        <dd><code class="cell-code">{{ data.source_rel }}</code></dd>
        <dt>Deployed</dt>
        <dd><code class="cell-code">{{ deployedPath }}</code></dd>
        <template v-if="data.entry.type === 'pattern'">
          <dt>Guide</dt>
          <dd>
            <router-link :to="`/patterns/${data.entry.procedure_id}`"
              class="text-link focus-visible:outline-2 focus-visible:outline-blue-500">
              <code class="cell-code">{{ data.entry.procedure_id }}</code>
            </router-link>
          </dd>
        </template>
      </dl>
    </Card>

    <Card>
      <h2 class="card-header">{{ providerName }}</h2>
      <div class="btn-row">
        <template v-if="data.entry.type !== 'auto'">
          <Button variant="secondary" @click="pull"><span class="inline-flex items-center gap-1">Pull (deployed <Icon name="arrow-up-right" :size="13" /> source)</span></Button>
          <Button variant="secondary" @click="push"><span class="inline-flex items-center gap-1">Push (source <Icon name="arrow-up-right" :size="13" /> deployed)</span></Button>
          <Button v-if="data.state === 'drifted'" variant="primary" @click="forcePush">Force push</Button>
        </template>
        <Button v-else variant="primary" @click="regenerate">Regenerate</Button>
        <Button v-if="['in_sync','drifted','deployed_only'].includes(data.state)" variant="danger" @click="undeploy">Undeploy</Button>
        <Button v-if="isPatternSkill" variant="danger" @click="deleteSkill">Delete skill</Button>
      </div>
    </Card>

    <Card v-if="data.entry.type === 'pattern'">
      <h2 class="card-header flex items-center gap-2">
        <span>Deployments</span>
        <Badge color="blue" :label="`1 global${globalDeployment ? '' : ' (not recorded)'}`" />
        <Badge color="green" :label="`${projectDeployments.length} project${projectDeployments.length !== 1 ? 's' : ''}`" />
      </h2>
      <p class="text-xs text-gray-500 mb-4">
        <strong>Global</strong> = <code>{{ globalDir }}/</code> (visible everywhere).
        <strong>Project</strong> = <code>&lt;repo&gt;/{{ projectSubpath }}/</code> (visible only in that repo).
      </p>

      <!-- Global row -->
      <div v-if="globalDeployment"
        class="flex items-center gap-3 px-3 py-2 bg-blue-50/50 border border-blue-100 rounded-md text-sm mb-3">
        <Badge color="blue" label="global" />
        <Badge color="gray" :label="providerBadge(globalDeployment.provider)" />
        <code class="text-xs text-gray-600 flex-1 truncate">{{ globalDeployment.deployed_path }}</code>
        <span class="text-xs text-gray-400 tabular-nums">{{ globalDeployment.deployed_at }}</span>
      </div>

      <!-- Project rows -->
      <ul v-if="projectDeployments.length" class="space-y-2 mb-4">
        <li v-for="d in projectDeployments" :key="`${d.id}:${d.provider || 'active'}`"
          class="group flex items-center gap-3 px-3 py-2 bg-green-50/40 border border-green-100 hover:bg-green-50 rounded-md text-sm transition-colors">
          <Badge color="green" label="project" />
          <Badge color="gray" :label="providerBadge(d.provider)" />
          <span class="font-medium text-green-800">{{ d.project_name }}</span>
          <code class="text-xs text-gray-500 flex-1 truncate" :title="d.deployed_path">{{ d.deployed_path }}</code>
          <span class="text-xs text-gray-400 tabular-nums">{{ d.deployed_at }}</span>
          <Button
            variant="ghost"
            size="icon"
            class="text-gray-400 hover:text-red-600 opacity-60 group-hover:opacity-100 transition-opacity"
            :title="`Remove from ${d.project_name}`"
            :aria-label="`Remove ${data.skill_id} from ${d.project_name}`"
            @click="removeProjectDeployment(d.project_id, d.project_name)">
            <Icon name="x" :size="16" />
          </Button>
        </li>
      </ul>

      <!-- Push to project picker (multi-select) -->
      <div class="relative">
        <div class="flex items-start gap-2">
          <div class="relative flex-1 min-w-0">
            <div class="w-full border border-gray-300 rounded-md bg-white focus-within:ring-2 focus-within:ring-blue-500 focus-within:border-blue-500 px-2 py-1.5 flex items-center flex-wrap gap-1.5 min-h-[42px]">
              <span v-for="r in selectedRepos" :key="r.id"
                class="inline-flex items-center gap-1 bg-blue-100 text-blue-800 text-xs font-medium px-2 py-0.5 rounded">
                <code>{{ r.name }}</code>
                <button type="button" @click="removeSelected(r)"
                  class="text-blue-600 hover:text-blue-900 focus-visible:outline-2 focus-visible:outline-blue-500"
                  :aria-label="`Remove ${r.name}`"
                  :title="`Remove ${r.name}`">
                  <Icon name="x" :size="12" />
                </button>
              </span>
              <input
                type="text"
                v-model="repoQuery"
                @focus="repoPickerOpen = true"
                @input="repoPickerOpen = true"
                @keydown.esc="repoPickerOpen = false"
                @keydown.enter.prevent="filteredRepos[0] && pickRepo(filteredRepos[0])"
                @keydown.backspace="!repoQuery && selectedRepos.length && removeSelected(selectedRepos[selectedRepos.length - 1])"
                :placeholder="selectedRepos.length ? '' : 'Type to filter repos…'"
                class="flex-1 min-w-[140px] text-sm border-0 outline-none focus-visible:outline-none focus:ring-0 bg-transparent p-0"
                aria-label="Filter project repos">
            </div>
          </div>
          <Button variant="primary" class="whitespace-nowrap"
            :disabled="!selectedRepos.length || pushing" @click="pushToProject()">
            {{ pushing ? 'Pushing…' : selectedRepos.length > 1 ? `Push to ${selectedRepos.length} Projects` : 'Push to Project' }}
          </Button>
        </div>

        <aside v-if="repoPickerOpen && filteredRepos.length"
          @mousedown.prevent
          class="absolute left-0 top-full mt-1 bg-white border border-gray-200 rounded-md shadow-lg z-10 max-h-64 overflow-auto"
          role="listbox"
          aria-label="Repo suggestions"
          style="width: calc(100% - 200px)">
          <button v-for="r in filteredRepos" :key="r.id"
            type="button"
            @click="pickRepo(r)"
            class="w-full text-left px-3 py-2 text-sm hover:bg-blue-50 flex items-baseline gap-2 border-b border-gray-50 last:border-0 focus-visible:outline-2 focus-visible:outline-blue-500">
            <code class="text-blue-700 font-medium">{{ r.name }}</code>
            <span class="text-xs text-gray-400 truncate">{{ r.path }}</span>
          </button>
        </aside>
        <div v-else-if="repoPickerOpen && repoQuery && !filteredRepos.length"
          class="absolute left-0 top-full mt-1 bg-white border border-gray-200 rounded-md shadow-lg z-10 px-3 py-2 text-sm text-gray-400"
          style="width: calc(100% - 200px)">
          No repos match "{{ repoQuery }}"
        </div>
      </div>
      <aside v-if="repoPickerOpen" class="fixed inset-0 z-0"
        aria-hidden="true"
        @click="repoPickerOpen = false"
        @keydown.esc="repoPickerOpen = false"></aside>
    </Card>

    <Card v-if="data.files?.length">
      <h2 class="card-header">Files ({{ data.files.length }})</h2>
      <ul class="list-none text-sm space-y-1">
        <li v-for="f in data.files" :key="f"><code class="text-xs">{{ f }}</code></li>
      </ul>
    </Card>

    <Card v-if="isPatternSkill && data.doc">
      <h2 class="card-header">Description</h2>
      <PatternDescriptionEditor
        :slug="data.entry.procedure_id"
        :description="data.description"
        @saved="load" />
    </Card>

    <Card v-if="isPatternSkill">
      <h2 class="card-header">Tags</h2>
      <PatternTagsEditor
        :slug="data.entry.procedure_id"
        :tags="data.tags"
        :all-tags="data.all_tags"
        @saved="load" />
    </Card>

    <Card v-if="data.body_md">
      <h2 class="card-header">SKILL.md preview</h2>
      <template v-if="isPatternSkill">
        <PatternContentEditor
          :slug="data.entry.procedure_id"
          :body-md="data.body_md"
          :concealed-texts="data.concealed_texts"
          @saved="load" />
      </template>
      <MarkdownContent v-else :markdown="data.body_md" />
    </Card>
  </div>
</template>
