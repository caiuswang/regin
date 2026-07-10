<script setup>
import { ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import Breadcrumb from '../components/Breadcrumb.vue'

const route = useRoute()
const data = ref(null)
const loading = ref(true)

onMounted(async () => {
  data.value = await api.get(`/repos/${route.params.name}`)
  loading.value = false
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
        <thead><tr><th>Branch</th><th>Tracked</th></tr></thead>
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
    <Card :no-padding="true" class="mb-6 max-w-4xl">
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

    <h2 class="section-heading">Approved Wiki ({{ data.wiki.length }})</h2>
    <Card :no-padding="true" class="mb-6 max-w-4xl">
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
