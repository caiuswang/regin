<script setup>
// The defects blamed on a feature — read, and reported.
//
// Reports are always feature-scoped: it posts `feature_id`, and a task under
// no feature has nothing to blame. The LIST is scoped by who is reading: the
// feature page reads `?feature_id=`, the task page passes `fixTaskId` and reads
// `?fix_item=` — a feature's defects are not every step's, and listing them on
// each step made every task look like the one that fixes them.
//
// Mounting it here is what lets a feature with NO task report a bug at all: a
// shape the model allows and can ship, and which had no browser control for it
// while this card lived only on the task page.
//
// The list is the point. Reporting used to be write-only: the outcome line was
// a local ref, so a reload left the operator with a defect they had been told
// was saved and could not find anywhere. A report that cannot be read back on
// the page that filed it is indistinguishable from one that was dropped.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Icon from '../ui/Icon.vue'
import Textarea from '../ui/Textarea.vue'
import DefectRow from '../defects/DefectRow.vue'
import TaskImageStrip from '../tasks/TaskImageStrip.vue'
import { imageFilesFrom } from '../../utils/imageFiles'
import { DEFECTS_ANCHOR, defectsLink } from '../../utils/featureLinks'
import { useAttachedImages } from '../../composables/useAttachedImages.js'

const props = defineProps({
  feature: { type: Object, default: null },
  canWrite: { type: Boolean, default: false },
  fixTaskId: { type: Number, default: null },
})
// Any write here moves the page's figures (open/total, escapes, stage) and a
// row's write can file a task, so the page re-reads both on this.
const emit = defineEmits(['changed'])

const open = ref(false)
const symptom = ref('')
// Bound to no defect: the screenshot is pasted while the report is still being
// typed, so it is held in the browser and flushed against the new id. Nothing
// is staged server-side for a report that is abandoned mid-sentence, and there
// is no orphan to sweep.
const {
  items: shots, busy: shotsBusy, error: shotsError,
  add: addShots, remove: removeShot, flush: flushShots, reset: resetShots,
} = useAttachedImages('defects')
const busy = ref(false)
const failed = ref('')
const outcome = ref('')
const outcomeId = ref(null)
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
const canSubmit = computed(() => symptom.value.trim().length > 0 && !busy.value)
const truncated = computed(() => total.value > items.value.length)

// Re-read rather than patch: re-attributing a defect moves it off this
// feature, and splicing the row back would leave it listed under "every defect
// blamed on this feature" when it no longer is.
async function load() {
  if (!isFeature.value) {
    loading.value = false
    return
  }
  loading.value = true
  loadError.value = ''
  try {
    const body = await api.get(
      `/defects?${scopeParam.value}&size=${MAX_ROWS}`)
    items.value = body.items || []
    total.value = body.pagination?.total ?? items.value.length
  } catch (e) {
    loadError.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}
// A different feature's rows are not this one's, so they go before the read
// rather than lingering under the new title until it lands.
// Two sources, not one getter returning an array: a fresh array every run
// would fire on each same-id re-fetch of the feature and unmount the rows,
// losing the notice a row's write just rendered.
watch([() => props.feature?.id, () => props.fixTaskId],
      () => { items.value = []; load() }, { immediate: true })
// The held blobs cannot survive a close: they belong to a report that was
// never filed, and a thumbnail still there on the next open would be evidence
// for a different bug.
watch(open, (isOpen) => { if (!isOpen) resetShots() })

// A screenshot is what the sentence is usually describing. Paste and drop are
// bound on the form rather than on the strip: a reporter pastes into the field
// they are typing in, and the event bubbles to here.
async function onPaste(e) {
  if (!imageFilesFrom(e.clipboardData).length) return
  e.preventDefault()
  await addShots(e.clipboardData)
}

async function onDrop(e) {
  if (!imageFilesFrom(e.dataTransfer).length) return
  e.preventDefault()
  await addShots(e.dataTransfer)
}

// `opened` and `deduped` are two different facts and say so: the second means
// the ledger already held this complaint, and reading it as a fresh report is
// how a defect goes unreported. Both re-read the list, because both changed it.
async function submit() {
  if (!canSubmit.value) return
  busy.value = true
  failed.value = ''
  const body = await api.post('/defects', {
    symptom: symptom.value.trim(), feature_id: props.feature.id,
  })
  busy.value = false
  if (body?.ok === false || !body?.defect) {
    failed.value = body?.msg || 'the report was refused'
    return
  }
  // The screenshots land BEFORE the list reloads, so the row renders already
  // carrying them. A dedup gets them too: the second reporter's picture is
  // evidence for the same bug, and dropping it would lose the only thing the
  // duplicate added.
  await flushShots(body.defect.id)
  outcomeId.value = body.defect.id
  outcome.value = body.outcome === 'deduped'
    ? `#${body.defect.id} already tracked this symptom — deduped, not reopened.`
    : `#${body.defect.id} opened against ${props.feature.title}.`
  symptom.value = ''
  open.value = false
  await load()
  emit('changed')
}

async function onRowChanged(message) {
  if (message) {
    outcome.value = message
    outcomeId.value = null
  }
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
        v-if="canWrite && !open"
        variant="ghost" size="sm"
        data-testid="feature-report-defect"
        :aria-label="`Report a defect against ${feature.title}`"
        @click="open = true"
      >
        <Icon name="alert-triangle" :size="12" />
        Report defect
      </Button>
    </div>

    <form
      v-if="open" class="td-form"
      @submit.prevent="submit" @paste="onPaste" @dragover.prevent @drop="onDrop"
    >
      <label class="td-label" :for="`rd-symptom-${feature.id}`">
        What went wrong — blamed on {{ feature.title }}
      </label>
      <Textarea
        :id="`rd-symptom-${feature.id}`"
        v-model="symptom" :rows="2" :disabled="busy"
        placeholder="the symptom, in your words"
        data-testid="feature-defect-symptom"
      />
      <p class="td-project" data-testid="feature-defect-project">
        Project: {{ feature.project_name || 'No project' }}
        <span class="td-optional">(the feature's — a defect has none of its own)</span>
      </p>
      <span class="td-label">Screenshots <span class="td-optional">(paste or drop)</span></span>
      <TaskImageStrip
        :items="shots"
        :busy="shotsBusy"
        :error="shotsError"
        @pick="addShots"
        @remove="removeShot"
      />
      <div class="td-actions">
        <Button
          type="submit" variant="secondary" size="sm" :disabled="!canSubmit"
          data-testid="feature-defect-submit"
        >{{ busy ? 'Reporting…' : 'Report' }}</Button>
        <Button
          type="button" variant="ghost" size="sm" :disabled="busy"
          @click="open = false"
        >Cancel</Button>
      </div>
    </form>

    <p v-if="outcome" class="td-note" role="status" data-testid="feature-defect-result">
      {{ outcome }}
      <!-- On the task page a new report fixes nothing yet, so it is not in
           this list: the link is how the page that filed it reads it back. -->
      <router-link
        v-if="byTask && outcomeId != null" class="td-key"
        :to="`/defects/${outcomeId}`" data-testid="feature-defect-result-link"
      >Open #{{ outcomeId }}</router-link>
    </p>
    <p v-if="failed" class="td-fail" role="status" data-testid="feature-defect-error">
      {{ failed }}
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
      Showing {{ items.length }} of {{ total }} — open the full list to see the rest.
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
.td-form { display: flex; flex-direction: column; gap: 0.35rem; }
.td-label { font-size: 11.5px; color: var(--color-fg-muted); }
.td-optional { color: var(--color-fg-faint); }
.td-project { margin: 0; font-size: 11.5px; color: var(--color-fg-muted); }
.td-actions { display: flex; gap: 0.4rem; }
.td-note {
    margin: 0.4rem 0 0; font-size: 11.5px; color: var(--color-fg-muted);
}
.td-fail {
    margin: 0.4rem 0 0; font-size: 11.5px; color: var(--color-amber-800);
    background: var(--color-amber-50); border-radius: 0.4rem;
    padding: 0.25rem 0.45rem;
}
</style>
