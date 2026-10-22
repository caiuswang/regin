<script setup>
import { ref, onMounted } from 'vue'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import Button from '../components/ui/Button.vue'
import AddProjectDialog from '../components/projects/AddProjectDialog.vue'
import { useFlash } from '../composables/useFlash'
import { useConfirm } from '../composables/useConfirm'

const { flash } = useFlash()
const { confirm } = useConfirm()

const repos = ref([])
const loading = ref(true)

const showAdd = ref(false)

async function refresh() {
  const data = await api.get('/repos')
  repos.value = data.repos || []
}

onMounted(async () => {
  await refresh()
  loading.value = false
})

async function onAdded(added) {
  flash(added.kind === 'workspace' ? `Workspace ${added.workspace.name} created` : `Added ${added.project?.name || added.cwd}`)
  await refresh()
}

async function removeRepo(repo) {
  const ok = await confirm(
    'Remove repo',
    `Drop "${repo.name}" from the registry?\n\nThe source tree on disk is untouched.`,
    true,
  )
  if (!ok) return
  const result = await api.del(`/repos/${encodeURIComponent(repo.name)}`)
  if (!result.ok) {
    flash(result.msg || 'Remove failed', 'error')
    return
  }
  flash(result.msg || 'Removed')
  await refresh()
}
</script>

<template>
  <div v-if="loading" class="empty-state">Loading repos…</div>
  <div v-else>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Library</div>
        <h1 class="page-title">Repos</h1>
        <p class="page-subtitle">
          {{ repos.length }} registered {{ repos.length === 1 ? 'repository' : 'repositories' }}.
          Add a repo by its absolute filesystem path; remove drops the registry entry without touching the source tree.
        </p>
      </div>
      <div class="page-actions">
        <Button
          variant="primary"
          data-testid="repos-add"
          @click="showAdd = true"
        >
          Add project
        </Button>
      </div>
    </header>

    <Card :no-padding="true">
      <div class="overflow-x-auto">
      <table class="tbl">
        <thead>
          <tr>
            <th>Repo</th>
            <th>Path</th>
            <th>Branch</th>
            <th class="text-right">Patterns</th>
            <th class="sticky right-0 bg-slate-50"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!repos.length">
            <td colspan="5" class="text-center text-slate-400 text-sm py-8">
              No repos registered. Click <span class="font-medium">Add project</span> to register one by path.
            </td>
          </tr>
          <tr v-for="r in repos" :key="r.name">
            <td>
              <router-link
                :to="`/repos/${r.name}`"
                class="table-link block py-2.5 -my-2.5 focus-visible:outline-2 focus-visible:outline-blue-500"
              >
                {{ r.name }}
              </router-link>
            </td>
            <td><code class="cell-code whitespace-nowrap">{{ r.path }}</code></td>
            <td class="font-mono text-xs">{{ r.branch_name || '-' }}</td>
            <td class="text-right font-mono text-xs">{{ r.pattern_count }}</td>
            <td class="text-right sticky right-0 bg-white">
              <Button
                variant="danger"
                size="sm"
                @click="removeRepo(r)"
              >
                Remove
              </Button>
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </Card>

    <AddProjectDialog v-model:open="showAdd" @added="onAdded" />
  </div>
</template>

<style scoped>
</style>
