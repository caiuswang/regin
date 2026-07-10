<script setup>
// Wiki recall panel: how often each topic's curated wiki has been consulted.
// `read` is distinct-session opens (the battle-tested signal, sorted-desc and
// shown with a magnitude bar); `exposure` is index_fetch surfacing the path (a
// weaker secondary signal, kept faint). Read auto-refreshes at SessionEnd; the
// Sync button recomputes it from the trace on demand.
import { computed, onMounted, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Badge from '../Badge.vue'

const TOP_N = 8

const rows = ref([])
const repo = ref('')
const loading = ref(false)
const expanded = ref(false)

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

const totalReads = computed(() => rows.value.reduce((n, r) => n + (r.read || 0), 0))
const maxRead = computed(() => rows.value.reduce((n, r) => Math.max(n, r.read || 0), 0))
// Rows arrive read-desc from the API, so the first is the most-consulted.
const topWiki = computed(() => rows.value[0] || null)
const missingCount = computed(() => rows.value.filter(r => !r.wiki_present).length)
const visibleRows = computed(() =>
  expanded.value ? rows.value : rows.value.slice(0, TOP_N))
const hiddenCount = computed(() => Math.max(0, rows.value.length - TOP_N))

const summaryTiles = computed(() => [
  { key: 'consulted', label: 'Consulted', value: rows.value.length },
  { key: 'reads', label: 'Total reads', value: totalReads.value },
  {
    key: 'top', label: 'Most consulted', value: topWiki.value?.read ?? 0,
    note: topWiki.value?.topic_id, noteTitle: topWiki.value?.label,
  },
  { key: 'missing', label: 'Missing wiki', value: missingCount.value, warn: missingCount.value > 0 },
])

function barWidth(read) {
  return maxRead.value ? Math.round((read / maxRead.value) * 100) : 0
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
  <div class="space-y-3 text-sm">
    <div class="flex items-center gap-2">
      <h2 class="text-sm font-semibold text-fg">Wiki recall</h2>
      <Button
        variant="secondary"
        size="sm"
        class="ml-auto"
        :disabled="loading"
        @click="load(true)"
      >{{ loading ? 'Syncing…' : 'Sync reads' }}</Button>
    </div>

    <p v-if="loading && !rows.length" class="rounded-lg border border-border bg-surface p-4 text-[11px] text-fg-faint">
      Loading…
    </p>
    <p v-else-if="!rows.length" class="rounded-lg border border-border bg-surface p-4 text-[11px] text-fg-faint">
      No wiki has been consulted yet. Reads are reconstructed from Read spans in
      the session trace.
    </p>

    <template v-else>
      <div class="grid grid-cols-2 gap-2 sm:grid-cols-4">
        <div
          v-for="t in summaryTiles"
          :key="t.key"
          class="rounded-lg border border-border bg-surface px-3 py-2"
        >
          <div class="text-[10px] font-semibold uppercase tracking-wider text-fg-faint">{{ t.label }}</div>
          <div class="mt-0.5 flex items-baseline gap-1.5">
            <span
              class="text-lg font-semibold tabular-nums"
              :class="t.warn ? 'text-warning-strong' : 'text-fg'"
            >{{ t.value }}</span>
            <span
              v-if="t.note"
              class="truncate font-mono text-[11px] text-fg-subtle"
              :title="t.noteTitle"
            >{{ t.note }}</span>
          </div>
        </div>
      </div>

      <div class="rounded-lg border border-border bg-surface px-4 py-1.5">
        <table class="w-full text-left">
          <thead>
            <tr class="text-[10px] font-semibold uppercase tracking-wider text-fg-faint">
              <th class="py-2 pr-3 font-semibold">Topic</th>
              <th class="py-2 px-3 text-right font-semibold">Read</th>
              <th class="py-2 px-3 text-right font-semibold max-sm:hidden">Exp.</th>
              <th class="py-2 pl-3 text-right font-semibold max-sm:hidden">Last read</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="r in visibleRows"
              :key="r.topic_id"
              class="border-t border-border-subtle"
            >
              <td class="max-w-0 w-full py-2.5 pr-3">
                <div class="flex min-w-0 items-center gap-1.5">
                  <router-link
                    v-if="r.wiki_present && repo"
                    :to="wikiLink(r.topic_id)"
                    class="text-link truncate"
                    :title="r.label"
                  >{{ r.label }}</router-link>
                  <span v-else class="truncate text-fg" :title="r.label">{{ r.label }}</span>
                  <Icon
                    v-if="r.wiki_present && repo"
                    name="arrow-up-right"
                    :size="12"
                    class="shrink-0 text-fg-faint"
                  />
                  <Badge
                    v-if="!r.wiki_present"
                    color="yellow"
                    label="missing"
                    class="shrink-0"
                    title="Counter outlived its wiki file — prune or refresh"
                  />
                </div>
              </td>
              <td class="py-2.5 px-3">
                <div class="flex items-center justify-end gap-2.5">
                  <span class="h-1.5 w-16 max-sm:w-10 shrink-0 overflow-hidden rounded-full bg-surface-3" aria-hidden="true">
                    <span class="block h-full rounded-full bg-primary" :style="{ width: barWidth(r.read) + '%' }"></span>
                  </span>
                  <span class="w-5 text-right font-mono tabular-nums text-fg">{{ r.read }}</span>
                </div>
              </td>
              <td class="py-2.5 px-3 text-right font-mono tabular-nums text-fg-faint max-sm:hidden">{{ r.exposure }}</td>
              <td class="py-2.5 pl-3 text-right font-mono tabular-nums whitespace-nowrap text-fg-faint max-sm:hidden">{{ shortDate(r.last_read) }}</td>
            </tr>
          </tbody>
        </table>
        <Button
          v-if="hiddenCount"
          variant="ghost"
          size="sm"
          class="mt-1 w-full justify-center border-t border-border-subtle text-xs"
          @click="expanded = !expanded"
        >{{ expanded ? 'Show fewer' : `Show all ${rows.length} (${hiddenCount} more)` }}</Button>
      </div>
    </template>
  </div>
</template>
