<script setup>
// One feature's ledger, and the pipeline's single workbench for it.
//
// The stage leads, the figures follow, then the three edges the data layer
// always had and the browser never drew: the SPEC it was declared with, the
// TASKS that are its steps, and the DEFECTS blamed on it. The evidence is last
// because it is what the figures above are summing.
//
// Order is the argument. Spec before tasks before defects is intent, then the
// work, then what the work got wrong — and it is the order the page is read in
// when the question is "how is this going". Running a task is NOT here: the
// rows link to the task page, because a second copy of the run controls would
// be a second writer of one lifecycle.
//
// Everything on this page is local, including the stage. The one control that
// reaches a tracker is the refresh on a remotely-tracked feature, and it is a
// separate, explicit event — a native feature has no such control, because its
// stage is derived here.
import { computed, nextTick, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import { useAttachedImages } from '../composables/useAttachedImages.js'
import { useBusyAction } from '../composables/useBusyAction'
import { useConfirm } from '../composables/useConfirm'
import { rememberedQuery } from '../composables/usePipelineTabState'
import { SESSIONS_ANCHOR, revealSessions } from '../utils/featureLinks'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import DeclareSessionDialog from '../components/features/DeclareSessionDialog.vue'
import FeatureDefects from '../components/features/FeatureDefects.vue'
import FeatureEvidence from '../components/features/FeatureEvidence.vue'
import FeatureFigures from '../components/features/FeatureFigures.vue'
import FeatureProject from '../components/features/FeatureProject.vue'
import FeatureImages from '../components/features/FeatureImages.vue'
import FeatureSpec from '../components/features/FeatureSpec.vue'
import FeatureStageLine from '../components/features/FeatureStageLine.vue'
import FeatureTasks from '../components/features/FeatureTasks.vue'

const route = useRoute()
const featureId = computed(() => Number(route.params.id))
// The list you opened this row from, not the default one: the back link
// carries the query the tab was last left under, so a filter, a search or a
// scope survives the round trip. An arrival by deep link has none, and the
// bare path is the right answer there.
const backTo = computed(() => ({ path: '/features', query: rememberedQuery('/features') }))

const ledger = ref(null)
const loading = ref(true)
const loadError = ref('')
const refreshing = ref(false)
const refreshFailed = ref('')
const declaring = ref(false)
const writeFailed = ref('')
const { busyAction, startBusy, stopBusy } = useBusyAction()
// One list for the two cards that show it. The spec editor writes
// `![](image:N)` references into prose the Images card holds the bytes for, so
// a second composable instance would let the reference and the thumbnail
// disagree about what is attached.
const {
  items: imageItems, busy: imagesBusy, error: imagesError,
  bind: bindImages, add: addImages, remove: removeImage,
} = useAttachedImages('features')
const { confirm } = useConfirm()

const user = api.getStoredUser()
// The trace route is admin-only, and the declare endpoints are editor-level.
// Both are read off the stored role in the component — never off a field in
// the payload, which would make the payload vary by role.
const canOpenTrace = computed(() => user?.role === 'admin')
const canWrite = computed(() => user?.role !== 'viewer')

async function load() {
  loading.value = true
  loadError.value = ''
  try {
    ledger.value = await api.get(`/features/${featureId.value}`)
    // Here and not on every `ledger` reassignment: a ship, a refresh or a
    // project edit returns the same attachments, and rebinding on each would
    // re-fetch every blob and blow away thumbnails the operator just added.
    bindImages(ledger.value)
  } catch (e) {
    loadError.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}
watch(featureId, load, { immediate: true })

// An arrival at `/features/12#feature-sessions` — the index's sessions count —
// has to land ON the evidence list, and the list does not exist until the
// ledger has drawn. The router declares no `scrollBehavior` and the page
// scrolls inside `.content-scroll`, so the hash is a linkable address and
// nothing more until something reveals it.
// Once per arrival, not once per render: `ledger` is reassigned by every
// refresh, ship and declare on this page, and an unguarded reveal would yank
// the reader back down to the evidence list after each of them.
let revealedHash = ''
function revealHash() {
  if (route.hash !== `#${SESSIONS_ANCHOR}` || revealedHash === route.hash) return
  revealedHash = route.hash
  nextTick(revealSessions)
}
watch(ledger, revealHash)
watch(() => route.hash, (hash) => {
  revealedHash = ''
  if (hash) revealHash()
})

async function refreshStage() {
  refreshing.value = true
  refreshFailed.value = ''
  const body = await api.post(`/features/${featureId.value}/refresh`)
  refreshing.value = false
  if (body?.ok === false) {
    // Never empty: an empty reason renders no note at all, and a failed
    // refresh that renders nothing is indistinguishable from one that worked.
    // Deliberately not "unreachable": a refusal and a missing issue are
    // different facts, and the server's own sentence distinguishes them.
    refreshFailed.value = body.msg || 'the tracker could not answer'
    return
  }
  ledger.value = body
}

// `api.post` reports a refusal by RETURNING `{ ok: false, msg }`, never by
// throwing. Assigning that envelope straight into `ledger` replaced the whole
// page with an error object — every figure below read `undefined` and the
// stage line threw — so a 404 on the ship destroyed the page it was fired
// from. The check its two siblings already carried, and one busy flag so the
// append-only log cannot take a second event from a double click.
async function write(action, path) {
  writeFailed.value = ''
  startBusy(action)
  try {
    // A REFUSAL returns `{ ok: false }`; an unreachable server THROWS. Both
    // have to clear the flag, or the control that fired the write stays
    // disabled with nothing on screen to say why — a dead row is worse than
    // the refusal it was hiding.
    const body = await api.post(`/features/${featureId.value}${path}`)
    if (body?.ok === false) {
      writeFailed.value = body.msg || `the ${action} was refused`
      return
    }
    ledger.value = body
  } catch (e) {
    writeFailed.value = `the ${action} could not be sent — ${e?.message || e}`
  } finally {
    stopBusy()
  }
}

function ship() { return write('ship', '/ship') }

// Confirmed, because it is the one act on this page that ends a feature's
// life: `abandoned` is a stage nothing walks back, and the endpoint has been
// reachable from the CLI alone since it was written.
async function abandon() {
  const agreed = await confirm(
    'Abandon',
    `Take "${ledger.value?.title}" down?\n\nIt becomes stage `
    + '"abandoned" — not a walked-back completion, and not a delete. Its '
    + 'events, rounds and defects all stay exactly as they are.',
    true)
  if (!agreed) return
  await write('abandon', '/abandon')
}

async function undeclare(row) {
  const body = await api.del(
    `/features/${featureId.value}`
    + `/attribute/${encodeURIComponent(row.trace_id)}`)
  if (body?.ok === false) {
    loadError.value = body.msg
    return
  }
  await load()
}

function onDeclared() {
  declaring.value = false
  load()
}
</script>

<template>
  <div class="space-y-4 p-4">
    <router-link :to="backTo" class="back">
      <Icon name="chevron-left" :size="14" />Features
    </router-link>

    <p v-if="loading" class="text-sm text-slate-500">Loading…</p>
    <p
      v-else-if="loadError"
      class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700"
      role="alert"
    >{{ loadError }}</p>

    <template v-else-if="ledger">
      <FeatureStageLine
        :feature-id="ledger.id"
        :title="ledger.title"
        :external-ref="ledger.external_ref"
        :external-url="ledger.external_url"
        :stage="ledger.stage"
        :synced-at="ledger.synced_at"
        :refreshing="refreshing"
        :failed="refreshFailed || ledger.sync_error"
        :live="ledger.stage_live"
        :can-refresh="canWrite && !ledger.stage_live"
        :can-ship="canWrite && ledger.stage.stage !== 'shipped'"
        :can-abandon="canWrite && ledger.stage.stage !== 'abandoned'"
        :busy="busyAction"
        @refresh="refreshStage"
        @ship="ship"
        @abandon="abandon"
      />

      <!-- Beside the control that failed, never a toast: a refused ship has to
           be readable next to the stage it did not change. -->
      <p
        v-if="writeFailed"
        class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700"
        role="alert"
      >{{ writeFailed }}</p>

      <FeatureProject
        :feature-id="ledger.id"
        :project-id="ledger.project_id"
        :project-name="ledger.project_name"
        :can-write="canWrite"
        @saved="ledger = $event"
      />

      <FeatureFigures :ledger="ledger" />

      <FeatureSpec
        :feature-id="ledger.id"
        :body="ledger.body"
        :images="imageItems"
        :can-write="canWrite"
        :attach="addImages"
        @saved="ledger = $event"
      />

      <FeatureImages
        :items="imageItems"
        :busy="imagesBusy"
        :error="imagesError"
        :can-write="canWrite"
        @pick="addImages"
        @remove="removeImage"
      />

      <FeatureTasks
        :feature-id="ledger.id" :can-write="canWrite"
        :project-id="ledger.project_id"
      />

      <FeatureDefects :feature="ledger" :can-write="canWrite" />

      <section class="card">
        <header class="mb-2 flex items-center justify-between gap-3">
          <div>
            <h2 class="h2">Sessions</h2>
            <p class="h2-sub">the evidence the figures above are summing</p>
          </div>
          <Button
            v-if="canWrite"
            variant="secondary"
            size="sm"
            @click="declaring = true"
          >
            <Icon name="plus" :size="12" /> Declare a session
          </Button>
        </header>
        <!-- The same gaps the sessions figure counts, deliberately. They are
             not a duplicate: the figure states the count, and once a gap
             outnumbers the rows it qualifies the list has to show it at the
             size of the thing it is missing. The two must reconcile — a
             qualifier that disagrees with its own evidence is the defect this
             page exists to prevent. -->
        <FeatureEvidence
          :rows="ledger.sessions.rows"
          :gaps="ledger.sessions.gaps"
          :can-open-trace="canOpenTrace"
          :can-undeclare="canWrite"
          @undeclare="undeclare"
        />
      </section>
    </template>

    <DeclareSessionDialog
      v-if="declaring && ledger"
      :feature-id="ledger.id"
      :declared="ledger.sessions.rows"
      @close="declaring = false"
      @declared="onDeclared"
    />
  </div>
</template>

<style scoped>
.back {
    display: inline-flex; align-items: center; gap: 0.25rem;
    font-size: 0.875rem; font-weight: 600; color: var(--color-fg-subtle);
    text-decoration: none;
}
.back:hover { color: var(--color-fg); }
.back:focus-visible { outline: 2px solid var(--color-blue-500); }
.card {
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface); padding: 1rem 1.1rem;
}
.h2 { font-size: 0.9rem; font-weight: 600; color: var(--color-fg); }
.h2-sub { font-size: 11.5px; color: var(--color-fg-subtle); }
</style>
