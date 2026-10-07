<script setup>
// The defects blamed on a feature — read here, reported through a dialog.
//
// The card draws no form of its own: `report` asks the page for the one defect
// dialog (the shell's, or the task page's local copy), so a report filed from
// this button carries the same fields — refs, suspect paths, images — as one
// filed from anywhere else, and its outcome is told the same way. The LIST is
// scoped by who is reading: the
// feature page reads `?feature_id=`, the task page passes `fixTaskId` and reads
// `?fix_item=` — a feature's defects are not every step's, and listing them on
// each step made every task look like the one that fixes them.
//
// The list is the point. A report that cannot be read back on the page that
// filed it is indistinguishable from one that was dropped.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Icon from '../ui/Icon.vue'
import DefectRow from '../defects/DefectRow.vue'
import { DEFECTS_ANCHOR, defectListLink, defectsLink } from '../../utils/featureLinks'

const props = defineProps({
  feature: { type: Object, default: null },
  canWrite: { type: Boolean, default: false },
  fixTaskId: { type: Number, default: null },
})
// Any write here moves the page's figures (open/total, escapes, stage) and a
// row's write can file a task, so the page re-reads both on this.
const emit = defineEmits(['changed', 'report'])

const outcome = ref('')
const items = ref([])
const total = ref(0)
const loading = ref(true)
const loadError = ref('')

// The list is bounded server-side now, so this panel can no longer claim to
// be "every defect blamed on this feature" without checking. It asks for the
// whole bound and says so when the ledger holds more — a truncated list that
// looks complete is worse than one that names its own edge.
const MAX_ROWS = 200

const isFeature = computed(() => Boolean(props.feature))
const byTask = computed(() => props.fixTaskId != null)
const scopeParam = computed(() => byTask.value
  ? `fix_item=${props.fixTaskId}` : `feature_id=${props.feature.id}`)
const truncated = computed(() => total.value > items.value.length)

// Re-read rather than patch: re-attributing a defect moves it off this
// feature, and splicing the row back would leave it listed under "every defect
// blamed on this feature" when it no longer is.
async function load() {
  if (!isFeature.value) {
    loading.value = false
    return
  }
  // A push re-read can still be in flight when the page moves to another
  // feature or task, so an answer for a scope this card has left is dropped.
  const want = scopeParam.value
  loading.value = true
  loadError.value = ''
  try {
    const body = await api.get(`/defects?${want}&size=${MAX_ROWS}`)
    if (want !== scopeParam.value) return
    items.value = body.items || []
    total.value = body.pagination?.total ?? items.value.length
  } catch (e) {
    if (want === scopeParam.value) loadError.value = e?.message || String(e)
  } finally {
    if (want === scopeParam.value) loading.value = false
  }
}
// A different feature's rows are not this one's, so they go before the read
// rather than lingering under the new title until it lands.
// Two sources, not one getter returning an array: a fresh array every run
// would fire on each same-id re-fetch of the feature and unmount the rows,
// losing the notice a row's write just rendered.
watch([() => props.feature?.id, () => props.fixTaskId],
      () => { items.value = []; load() }, { immediate: true })
defineExpose({ load })

async function onRowChanged(message) {
  if (message) outcome.value = message
  await load()
  emit('changed')
}
</script>

<template>
  <Card v-if="isFeature" :id="DEFECTS_ANCHOR" data-testid="feature-defects">
    <div class="td-head">
      <h2 class="card-header">{{ byTask ? 'Defects this task fixes' : 'Defects' }}</h2>
      <span class="td-spacer" />
      <Button
        v-if="canWrite"
        variant="ghost" size="sm"
        data-testid="feature-report-defect"
        :aria-label="`Report a defect against ${feature.title}`"
        @click="emit('report')"
      >
        <Icon name="alert-triangle" :size="12" />
        Report defect
      </Button>
    </div>

    <p v-if="outcome" class="td-note" role="status" data-testid="feature-defect-result">
      {{ outcome }}
    </p>

    <!-- Only before the first read: a re-read after a row's write keeps the
         rows mounted, so the task a row just filed stays named on it. -->
    <p v-if="loading && !items.length" class="td-note">Loading…</p>
    <p v-else-if="loadError" class="td-fail" role="alert">{{ loadError }}</p>
    <p v-else-if="!items.length" class="td-note" data-testid="feature-defects-empty">
      {{ byTask ? 'This task is not the fix for any defect.'
        : 'No defects are blamed on this feature.' }}
    </p>
    <ul v-else class="ledger-list" data-testid="feature-defects-list">
      <DefectRow
        v-for="defect in items"
        :key="defect.id"
        :defect="defect"
        :can-write="canWrite"
        hide-feature
        flat
        @changed="onRowChanged"
      />
    </ul>
    <p v-if="truncated" class="td-note" data-testid="feature-defects-truncated">
      Showing {{ items.length }} of {{ total }} —
      <router-link class="td-key" :to="defectListLink(feature.id)"
                   data-testid="feature-defects-full-list">open the full list</router-link>
      to see the rest.
    </p>
    <p v-if="byTask" class="td-note">
      <router-link
        class="td-key" :to="defectsLink(feature.id)"
        data-testid="feature-defects-all-link"
      >Every defect blamed on {{ feature.title }} →</router-link>
    </p>
  </Card>
</template>

<style scoped>
.td-head {
    display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem;
    margin-bottom: 0.6rem;
}
.td-head .card-header { margin-bottom: 0; }
.td-key {
    font-family: var(--font-mono, monospace); font-size: 11.5px;
    color: var(--color-primary); text-decoration: none;
}
.td-key:hover { text-decoration: underline; }
.td-spacer { flex: 1 1 auto; }
.td-note {
    margin: 0.4rem 0 0; font-size: 11.5px; color: var(--color-fg-muted);
}
.td-fail {
    margin: 0.4rem 0 0; font-size: 11.5px; color: var(--color-amber-800);
    background: var(--color-amber-50); border-radius: 0.4rem;
    padding: 0.25rem 0.45rem;
}
</style>
