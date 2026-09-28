<script setup>
// One defect at its own address.
//
// Every figure the Pipeline shows about a defect used to be reachable only
// through a list — a predicate plus a page position, neither of which survives
// being pasted into a message. `/defects/12` is what a row can be pointed at
// with, and it resolves through the id the ledger already has rather than
// through a re-run of the query the reader happened to be in.
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import { usePipelineHeaderPublisher } from '../composables/usePipelineHeader'
import { rememberedQuery } from '../composables/usePipelineTabState'
import { useDetailBack } from '../composables/useDetailBack'
import { useFlash } from '../composables/useFlash'
import { usePipelineLive } from '../composables/usePipelineEvents'
import DefectRow from '../components/defects/DefectRow.vue'

const route = useRoute()
const router = useRouter()
const publish = usePipelineHeaderPublisher()
const { flash } = useFlash()

const defect = ref(null)
const loading = ref(true)
const loadError = ref('')

const canWrite = computed(() => api.getStoredUser()?.role !== 'viewer')
// Back to the feature this defect is blamed on, or to the triage rail it is
// routed from when it is blamed on none — carrying the query the Features tab
// was last left under.
// With no Pipeline route to return to: the feature this defect is blamed on,
// or the triage rail it is routed from when it is blamed on none.
const back = useDetailBack(() => (defect.value?.feature_id
  ? { to: { path: `/features/${defect.value.feature_id}` }, label: `feature #${defect.value.feature_id}` }
  : { to: { path: '/features', query: rememberedQuery('/features') }, label: 'triage' }))

// An event re-read can still be in flight when the route moves to another
// defect, so every answer is checked against the id it was asked for.
async function load() {
  const want = route.params.id
  loading.value = true
  loadError.value = ''
  try {
    const body = await api.get(`/defects/${want}`)
    if (want !== route.params.id) return
    defect.value = body.defect
    publish({ subtitle: `Defect #${body.defect.id}, on its own address.` })
  } catch (e) {
    if (want === route.params.id) loadError.value = e?.message || String(e)
  } finally {
    if (want === route.params.id) loading.value = false
  }
}
usePipelineLive(load)
watch(() => route.params.id, () => { defect.value = null; load() },
      { immediate: true })

// A write here re-reads this one row rather than the list it came from. A
// merge is the one write that deletes the row this address names, so it moves
// to the keeper instead of re-reading an id that no longer exists.
function onChanged(message, info) {
  if (message) {
    flash(message, 'success', info?.keeper
      ? { link: { to: `/defects/${info.keeper}`, label: `Open #${info.keeper}` } } : {})
  }
  if (info?.keeper && String(info.keeper) !== String(route.params.id)) {
    router.replace(`/defects/${info.keeper}`)
    return
  }
  load()
}
</script>

<template>
  <div class="space-y-4">
    <p class="back">
      <router-link v-slot="{ href }" :to="back.to.value" custom>
        <a :href="href" class="focus-visible:outline-2 focus-visible:outline-blue-500" data-testid="defect-back" @click="back.go">← {{ back.label.value }}</a>
      </router-link>
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
