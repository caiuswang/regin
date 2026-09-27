<script setup>
// One defect at its own address.
//
// Every figure the Pipeline shows about a defect used to be reachable only
// through a list — a predicate plus a page position, neither of which survives
// being pasted into a message. `/defects/12` is what a row can be pointed at
// with, and it resolves through the id the ledger already has rather than
// through a re-run of the query the reader happened to be in.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import { usePipelineHeaderPublisher } from '../composables/usePipelineHeader'
import { rememberedQuery } from '../composables/usePipelineTabState'
import { useFlash } from '../composables/useFlash'
import DefectRow from '../components/defects/DefectRow.vue'

const route = useRoute()
const publish = usePipelineHeaderPublisher()
const { flash } = useFlash()

const defect = ref(null)
const loading = ref(true)
const loadError = ref('')

const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')
// Back to the feature this defect is blamed on, or to the triage rail it is
// routed from when it is blamed on none — carrying the query the Features tab
// was last left under.
const backTo = computed(() => (defect.value?.feature_id
  ? { path: `/features/${defect.value.feature_id}` }
  : { path: '/features', query: rememberedQuery('/features') }))
const backLabel = computed(() => (defect.value?.feature_id
  ? `← feature #${defect.value.feature_id}` : '← triage'))

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    const body = await api.get(`/defects/${route.params.id}`)
    defect.value = body.defect
    publish({ subtitle: `Defect #${body.defect.id}, on its own address.` })
  } catch (e) {
    loadError.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}
watch(() => route.params.id, () => { defect.value = null; load() },
      { immediate: true })

// A write here re-reads this one row rather than the list it came from: the
// address is the id, so nothing about the page's identity can change under it.
function onChanged(message) {
  if (message) flash(message)
  load()
}
</script>

<template>
  <div class="space-y-4">
    <p class="back">
      <router-link :to="backTo" data-testid="defect-back">{{ backLabel }}</router-link>
    </p>

    <!-- Only before the first row lands: a re-read after a write keeps the row
         mounted, so what the row just told the operator is not wiped. -->
    <p v-if="loading && !defect" class="text-sm text-slate-500">Loading…</p>
    <p
      v-else-if="loadError"
      class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700"
      role="alert"
      data-testid="defect-detail-error"
    >{{ loadError }}</p>

    <ul v-else-if="defect" class="list" data-testid="defect-detail">
      <DefectRow :key="defect.id" :defect="defect" :can-write="canWrite"
                 @changed="onChanged" />
    </ul>
  </div>
</template>

<style scoped>
.back { font-size: 11.5px; }
.list { display: flex; flex-direction: column; gap: 0.6rem; }
</style>
