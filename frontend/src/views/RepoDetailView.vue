<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import Button from '../components/ui/Button.vue'
import Breadcrumb from '../components/Breadcrumb.vue'

const route = useRoute()
const data = ref(null)
const loading = ref(true)
const bundles = ref([])
const busyBundle = ref('')

async function loadBundles() {
  try {
    const resp = await api.get(`/repos/${route.params.name}/bundles`)
    bundles.value = resp.bundles || []
  } catch {
    bundles.value = []
  }
}

// Trust is what lets a repo-shipped bundle actually run; until then regin
// only lists its rules. Re-fetch after toggling so a stale fingerprint
// (checker edited since approval) can't linger in the table.
async function setTrust(bundle, trusted) {
  busyBundle.value = bundle.bundle_id
  const url = `/repos/${route.params.name}/bundles/${bundle.bundle_id}/trust`
  try {
    if (trusted) await api.post(url, {})
    else await api.del(url)
    await loadBundles()
  } finally {
    busyBundle.value = ''
  }
}

function trustColor(bundle) {
  if (bundle.trusted) return 'green'
  return bundle.code_changed ? 'red' : 'gray'
}

function trustLabel(bundle) {
  if (bundle.trusted) return 'trusted'
  return bundle.code_changed ? 'code changed' : 'not trusted'
}

onMounted(async () => {
  data.value = await api.get(`/repos/${route.params.name}`)
  loading.value = false
  await loadBundles()
})
</script>

<template>
  <div v-if="loading" class="empty-state">Loading repo…</div>
  <div v-else>
    <Breadcrumb :items="[
      { label: 'Repos', to: '/repos' },
      { label: data.repo.name, to: null },
    ]" />

    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Repo</div>
        <h1 class="page-title">{{ data.repo.name }}</h1>
        <p class="page-subtitle"><code class="cell-code">{{ data.repo.path }}</code></p>
      </div>
      <div class="page-actions">
        <router-link :to="`/repos/${data.repo.name}/topics`"
          class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500">
          Topics
        </router-link>
      </div>
    </header>

    <h2 class="section-heading">Branches</h2>
    <Card :no-padding="true" class="mb-6">
      <div class="overflow-x-auto">
      <table class="tbl">
        <thead><tr><th>Branch</th><th style="width: 12rem">Tracked</th></tr></thead>
        <tbody>
          <tr v-for="b in data.branches" :key="b.id">
            <td class="font-medium">{{ b.name }}</td>
            <td><Badge :color="b.is_tracked ? 'green' : 'gray'" :label="b.is_tracked ? 'yes' : 'no'" /></td>
          </tr>
        </tbody>
      </table>
      </div>
    </Card>

    <h2 class="section-heading">Patterns ({{ data.patterns.length }})</h2>
    <Card :no-padding="true" class="mb-6">
      <div class="overflow-x-auto">
      <table class="tbl">
        <thead><tr><th>Title</th><th style="width: 12rem">Category</th></tr></thead>
        <tbody>
          <tr v-for="p in data.patterns" :key="p.slug">
            <td>
              <router-link :to="`/patterns/${p.slug}`"
                class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">
                {{ p.title }}
              </router-link>
            </td>
            <td><Badge color="purple" :label="p.category" /></td>
          </tr>
        </tbody>
      </table>
      </div>
    </Card>

    <h2 class="section-heading">Rule bundles ({{ bundles.length }})</h2>
    <Card :no-padding="true" class="mb-6">
      <div class="overflow-x-auto">
      <table class="tbl">
        <thead>
          <tr>
            <th>Bundle</th>
            <th style="width: 10rem">Languages</th>
            <th style="width: 9rem">Code</th>
            <th style="width: 8rem">Status</th>
            <th style="width: 8rem"></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in bundles" :key="b.bundle_id">
            <td>
              <div class="font-medium">{{ b.engine_id }}</div>
              <div class="cell-sub">{{ b.root }}</div>
            </td>
            <td>{{ b.languages.join(', ') }}</td>
            <td><code class="cell-code">{{ b.fingerprint }}</code></td>
            <td><Badge :color="trustColor(b)" :label="trustLabel(b)" /></td>
            <td>
              <Button size="sm" :variant="b.trusted ? 'secondary' : 'primary'"
                :disabled="busyBundle === b.bundle_id"
                @click="setTrust(b, !b.trusted)">
                {{ b.trusted ? 'Untrust' : 'Trust' }}
              </Button>
            </td>
          </tr>
          <tr v-if="!bundles.length">
            <td colspan="5" class="empty-state">
              This repo ships no rule bundles. Add one at
              <code class="cell-code">.regin/rules/&lt;id&gt;/regin-bundle.yaml</code>
              to keep its conventions with the code.
            </td>
          </tr>
        </tbody>
      </table>
      </div>
    </Card>

    <h2 class="section-heading">Approved Wiki ({{ data.wiki.length }})</h2>
    <Card :no-padding="true" class="mb-6">
      <div class="overflow-x-auto">
      <table class="tbl">
        <thead><tr><th>Topic</th><th style="width: 12rem">Category</th></tr></thead>
        <tbody>
          <tr v-for="w in data.wiki" :key="w.slug">
            <td>
              <router-link :to="`/repos/${data.repo.name}/topics?topic=${w.topic_id}`"
                class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">
                {{ w.title }}
              </router-link>
            </td>
            <td><Badge color="blue" :label="w.category" /></td>
          </tr>
          <tr v-if="!data.wiki.length && data.approved_topic_count > 0">
            <td colspan="2" class="empty-state">
              {{ data.approved_topic_count }} approved topic{{ data.approved_topic_count === 1 ? '' : 's' }} exist on disk but aren't indexed yet.
              Open <router-link :to="`/repos/${data.repo.name}/topics`" class="table-link">Topics</router-link> and press <strong>Re-index Wikis</strong>
              (requires the embedding stack: <code>pip install sentence-transformers torch</code>).
            </td>
          </tr>
          <tr v-else-if="!data.wiki.length">
            <td colspan="2" class="empty-state">No approved wiki pages yet.</td>
          </tr>
        </tbody>
      </table>
      </div>
    </Card>

  </div>
</template>
