<script setup>
// Wiki recall panel: how often each topic's curated wiki has been consulted.
// `read` is distinct-session opens (the battle-tested signal), `exposure` is
// index_fetch surfacing the path. Read auto-refreshes at SessionEnd; the
// Sync button recomputes it from the trace on demand.
import { onMounted, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

const rows = ref([])
const repo = ref('')
const loading = ref(false)

async function load(sync = false) {
  loading.value = true
  try {
    const data = await api.get(`/memory/wiki-recalls${sync ? '?sync=1' : ''}`)
    rows.value = data.rows || []
    repo.value = data.repo || ''
  } finally {
    loading.value = false
  }
}

function wikiLink(topicId) {
  return `/repos/${repo.value}/topics?tab=wiki&topic=${encodeURIComponent(topicId)}`
}

function shortDate(iso) {
  return iso ? iso.slice(0, 10) : '—'
}

onMounted(() => load(false))
</script>

<template>
  <div class="rounded-lg border border-border bg-surface p-4 text-sm">
    <div class="flex items-center gap-2 mb-3">
      <h2 class="text-sm font-semibold text-fg">Wiki recall</h2>
      <span class="text-xs font-mono text-fg-faint">{{ rows.length }} consulted</span>
      <Button
        variant="secondary"
        size="sm"
        class="ml-auto"
        :disabled="loading"
        @click="load(true)"
      >{{ loading ? 'Syncing…' : 'Sync reads' }}</Button>
    </div>

    <p v-if="!rows.length && !loading" class="text-[11px] text-fg-faint">
      No wiki has been consulted yet. Reads are reconstructed from Read spans in
      the session trace.
    </p>

    <table v-else class="w-full text-left">
      <thead>
        <tr class="text-[10px] font-semibold uppercase tracking-wider text-fg-faint">
          <th class="py-1 pr-2 font-semibold">Topic</th>
          <th class="py-1 px-2 text-right font-semibold">Read</th>
          <th class="py-1 px-2 text-right font-semibold">Exposure</th>
          <th class="py-1 pl-2 text-right font-semibold">Last read</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="r in rows"
          :key="r.topic_id"
          class="border-t border-border/60"
        >
          <td class="py-1 pr-2">
            <router-link
              v-if="r.wiki_present && repo"
              :to="wikiLink(r.topic_id)"
              class="text-link inline-flex items-center gap-0.5"
              title="Open this topic's wiki page"
            >{{ r.label }}<Icon name="arrow-up-right" :size="12" class="opacity-60" /></router-link>
            <span v-else class="text-fg">{{ r.label }}</span>
            <span
              v-if="!r.wiki_present"
              class="ml-1 text-[10px] text-amber-700"
              title="Counter outlived its wiki file — prune or refresh"
            >⚠ missing</span>
          </td>
          <td class="py-1 px-2 text-right font-mono tabular-nums text-fg">{{ r.read }}</td>
          <td class="py-1 px-2 text-right font-mono tabular-nums text-fg-subtle">{{ r.exposure }}</td>
          <td class="py-1 pl-2 text-right font-mono tabular-nums text-fg-faint">{{ shortDate(r.last_read) }}</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
