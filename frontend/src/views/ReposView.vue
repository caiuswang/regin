<script setup>
import { ref, onMounted, nextTick } from 'vue'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import Button from '../components/ui/Button.vue'
import { useFlash } from '../composables/useFlash'
import { useConfirm } from '../composables/useConfirm'

const { flash } = useFlash()
const { confirm } = useConfirm()

const repos = ref([])
const loading = ref(true)

const showAdd = ref(false)
const adding = ref(false)
const newPath = ref('')
const pathInput = ref(null)

async function refresh() {
  const data = await api.get('/repos')
  repos.value = data.repos || []
}

onMounted(async () => {
  await refresh()
  loading.value = false
})

async function openAdd() {
  newPath.value = ''
  showAdd.value = true
  await nextTick()
  pathInput.value?.focus()
}

function closeAdd() {
  if (adding.value) return
  showAdd.value = false
}

async function submitAdd() {
  const path = newPath.value.trim()
  if (!path) return
  adding.value = true
  const result = await api.post('/repos', { path })
  adding.value = false
  if (!result.ok) {
    flash(result.msg || 'Add failed', 'error')
    return
  }
  flash(result.msg || 'Added')
  showAdd.value = false
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
          @click="openAdd"
        >
          Add repo
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
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!repos.length">
            <td colspan="5" class="text-center text-slate-400 text-sm py-8">
              No repos registered. Click <span class="font-medium">Add repo</span> to register one by path.
            </td>
          </tr>
          <tr v-for="r in repos" :key="r.name">
            <td>
              <router-link
                :to="`/repos/${r.name}`"
                class="table-link focus-visible:outline-2 focus-visible:outline-blue-500"
              >
                {{ r.name }}
              </router-link>
            </td>
            <td><code class="cell-code">{{ r.path }}</code></td>
            <td class="font-mono text-xs">{{ r.branch_name || '-' }}</td>
            <td class="text-right font-mono text-xs">{{ r.pattern_count }}</td>
            <td class="text-right">
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

    <Teleport to="body">
      <div
        v-if="showAdd"
        class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center"
        @click.self="closeAdd"
        @keydown.esc="closeAdd"
      >
        <div class="bg-white rounded-lg shadow-xl max-w-md w-full mx-4 overflow-hidden">
          <div class="px-5 pt-5 pb-3">
            <h2 class="text-base font-semibold text-gray-900 mb-1">Add repository</h2>
            <p class="text-sm text-gray-500">
              Enter the absolute path of a local git working tree. The repo is registered
              immediately; run a sync afterward to import its patterns.
            </p>
          </div>
          <form class="px-5 pb-2" @submit.prevent="submitAdd">
            <label class="block text-xs font-medium text-gray-600 mb-1" for="repo-path">Path</label>
            <input
              id="repo-path"
              ref="pathInput"
              v-model="newPath"
              type="text"
              aria-label="Repository path"
              placeholder="/Users/you/code/my-service"
              class="w-full text-sm border border-gray-300 rounded-md px-2.5 py-1.5 font-mono focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
              :disabled="adding"
            />
          </form>
          <div class="flex justify-end gap-2 px-5 pb-4 pt-2">
            <Button
              variant="secondary"
              :disabled="adding"
              @click="closeAdd"
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              :disabled="adding || !newPath.trim()"
              @click="submitAdd"
            >
              {{ adding ? 'Adding…' : 'Add repo' }}
            </Button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>

<style scoped>
</style>
