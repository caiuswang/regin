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
import { useDetailBack } from '../composables/useDetailBack'
import { usePipelineLive } from '../composables/usePipelineEvents'
import {
  DEFECTS_ANCHOR, IMAGES_ANCHOR, LEDGER_ANCHORS, SESSIONS_ANCHOR, SLICES_ANCHOR,
  SPEC_ANCHOR, TASKS_ANCHOR, reveal,
} from '../utils/featureLinks'
import { reopenMessage, shipMessage } from '../utils/featureConfirm'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import DeclareSessionDialog from '../components/features/DeclareSessionDialog.vue'
import FeatureDefects from '../components/features/FeatureDefects.vue'
import FeatureEvidence from '../components/features/FeatureEvidence.vue'
import FeatureFigures from '../components/features/FeatureFigures.vue'
import FeatureProjectPicker from '../components/features/FeatureProjectPicker.vue'
import FeatureImages from '../components/features/FeatureImages.vue'
import FeatureSlices from '../components/features/FeatureSlices.vue'
import FeatureSpec from '../components/features/FeatureSpec.vue'
import FeatureSectionNav from '../components/features/FeatureSectionNav.vue'
import FeatureStageLine from '../components/features/FeatureStageLine.vue'
import FeatureTasks from '../components/features/FeatureTasks.vue'

const route = useRoute()
const featureId = computed(() => Number(route.params.id))
// The list you opened this row from, not the default one: the back link
// carries the query the tab was last left under, so a filter, a search or a
// scope survives the round trip. An arrival by deep link has none, and the
// bare path is the right answer there.
const back = useDetailBack(() => ({ to: { path: '/features', query: rememberedQuery('/features') }, label: 'Features' }))

const ledger = ref(null)
const loading = ref(true)
const loadError = ref('')
const refreshing = ref(false)
const refreshFailed = ref('')
const tasksCard = ref(null)
const taskCount = ref(null)
const declaring = ref(false)
const writeFailed = ref('')
const undeclareFailed = ref('')
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
// `verbs` is the server's R2 table for this stage; the page only draws it.
const offers = (verb) => canWrite.value && (ledger.value?.verbs || []).includes(verb)

// An event re-read can still be in flight when the route moves to another
// feature, so every answer is checked against the id it was asked for.
async function load({ quiet = false } = {}) {
  const want = featureId.value
  if (!quiet) loading.value = true
  loadError.value = ''
  try {
    const body = await api.get(`/features/${want}`)
    if (want !== featureId.value) return
    ledger.value = body
    // Here and not on every `ledger` reassignment: a ship, a refresh or a
    // project edit returns the same attachments, and rebinding on each would
    // re-fetch every blob and blow away thumbnails the operator just added.
    // A quiet (event) re-read is the same case.
    if (!quiet) bindImages(ledger.value)
  } catch (e) {
    if (want === featureId.value) loadError.value = e?.message || String(e)
  } finally {
    if (want === featureId.value) loading.value = false
  }
}
watch(featureId, () => { taskCount.value = null; load() }, { immediate: true })
usePipelineLive(() => load({ quiet: true }))

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
  const anchor = route.hash.slice(1)
  if (!LEDGER_ANCHORS.includes(anchor) || revealedHash === route.hash) return
  revealedHash = route.hash
  nextTick(() => reveal(anchor))
}
watch(ledger, revealHash)
watch(() => route.hash, (hash) => {
  revealedHash = ''
  if (hash) revealHash()
})

// Every count is the one its section draws, off the payload that section
// draws from: the Tasks card's own list, the Images card's own items.
const sections = computed(() => {
  const l = ledger.value
  if (!l) return []
  const t = taskCount.value
  return [
    { key: 'spec', anchor: SPEC_ANCHOR, label: 'Spec',
      count: (l.body || '').trim() ? null : 'empty' },
    { key: 'slices', anchor: SLICES_ANCHOR, label: 'Slices', count: String(l.slice_tasks ?? 0) },
    { key: 'images', anchor: IMAGES_ANCHOR, label: 'Images', count: String(imageItems.value.length) },
    { key: 'tasks', anchor: TASKS_ANCHOR, label: 'Tasks', count: t ? `${t.done}/${t.total}` : null },
    { key: 'defects', anchor: DEFECTS_ANCHOR, label: 'Defects',
      count: `${l.defects.open}/${l.defects.total}` },
    { key: 'sessions', anchor: SESSIONS_ANCHOR, label: 'Sessions', count: String(l.sessions.rows.length) },
  ]
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

async function ship() {
  const agreed = await confirm(
    'Ship', shipMessage(ledger.value?.title, ledger.value?.ship_check))
  if (agreed) await write('ship', '/ship')
}

async function reopen() {
  const agreed = await confirm(
    'Reopen', reopenMessage(ledger.value?.title, ledger.value?.stage?.stage))
  if (agreed) await write('reopen', '/reopen')
}

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

// Confirmed: withdrawing a declaration also drops the primary flag, which
// moves this session's money to whichever feature holds it next. A refusal
// renders in the Sessions card beside the row's control; routed through
// `loadError` it replaced the whole page, ledger included.
async function undeclare(row) {
  const agreed = await confirm(
    'Unattribute',
    `Withdraw ${row.short_trace || row.trace_id} from this feature?\n\n`
    + 'Its cost stops counting here, and a primary declaration is cleared.',
    true)
  if (!agreed) return
  undeclareFailed.value = ''
  try {
    const body = await api.del(
      `/features/${featureId.value}`
      + `/attribute/${encodeURIComponent(row.trace_id)}`)
    if (body?.ok === false) {
      undeclareFailed.value = body.msg || 'the un-declare was refused'
      return
    }
  } catch (e) {
    undeclareFailed.value = `the un-declare could not be sent — ${e?.message || e}`
    return
  }
  await load()
}

// A tail append answers with the ledger it wrote; a transcribe or an arm
// changes only the task-side figures, which a re-read picks up.
async function onSlicesChanged(fresh) {
  if (fresh) {
    ledger.value = fresh
    tasksCard.value?.load()
    return
  }
  await onDefectsChanged()
}

// Not `load()`: that rebinds the image list, re-fetching every blob and
// dropping thumbnails the operator just added.
async function onDefectsChanged() {
  tasksCard.value?.load()
  try {
    ledger.value = await api.get(`/features/${featureId.value}`)
  } catch (e) {
    writeFailed.value = `the figures could not be re-read — ${e?.message || e}`
  }
}

function onDeclared() {
  declaring.value = false
  load()
}
</script>

<template>
  <div class="ledger space-y-4 p-4">
    <router-link v-slot="{ href }" :to="back.to.value" custom>
      <a :href="href" class="back focus-visible:outline-2 focus-visible:outline-blue-500" data-testid="detail-back" @click="back.go">
        <Icon name="chevron-left" :size="14" />{{ back.label.value }}
      </a>
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
        :can-ship="offers('ship')"
        :can-abandon="offers('abandon')"
        :can-reopen="offers('reopen')"
        :can-rename="canWrite && ledger.stage_live"
        :next-step="ledger.next_step"
        :busy="busyAction"
        @refresh="refreshStage"
        @ship="ship"
        @abandon="abandon"
        @reopen="reopen"
        @renamed="ledger = $event"
      >
        <template #project>
          <FeatureProjectPicker
            :feature-id="ledger.id"
            :project-id="ledger.project_id"
            :project-name="ledger.project_name"
            :can-write="canWrite"
            @saved="ledger = $event"
          />
        </template>
      </FeatureStageLine>

      <FeatureSectionNav :entries="sections" end-id="feature-end" />

      <!-- Beside the control that failed, never a toast: a refused ship has to
           be readable next to the stage it did not change. -->
      <p
        v-if="writeFailed"
        class="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700"
        role="alert"
      >{{ writeFailed }}</p>

      <FeatureFigures :ledger="ledger" />

      <FeatureSpec
        :feature-id="ledger.id"
        :body="ledger.body"
        :images="imageItems"
        :can-write="canWrite"
        :attach="addImages"
        :tracked="!ledger.stage_live"
        :tracker="ledger.origin"
        :external-url="ledger.external_url"
        @saved="ledger = $event"
      />

      <FeatureSlices
        :feature-id="ledger.id"
        :body="ledger.body || ''"
        :can-write="canWrite"
        :tracked="!ledger.stage_live"
        :project-id="ledger.project_id"
        @changed="onSlicesChanged"
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
        ref="tasksCard"
        :feature-id="ledger.id" :can-write="canWrite"
        :has-slices="ledger.slice_tasks > 0"
        :project-id="ledger.project_id"
        @counted="taskCount = $event"
      />

      <FeatureDefects :feature="ledger" :can-write="canWrite"
                      @changed="onDefectsChanged" />

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
        <p
          v-if="undeclareFailed"
          class="mb-2 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700"
          role="alert"
          data-testid="undeclare-refusal"
        >{{ undeclareFailed }}</p>
        <FeatureEvidence
          :rows="ledger.sessions.rows"
          :gaps="ledger.sessions.gaps"
          :can-open-trace="canOpenTrace"
          :can-undeclare="canWrite"
          @undeclare="undeclare"
        />
      </section>
      <div id="feature-end" aria-hidden="true"></div>
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
/* A jump lands the section under the sticky index, not behind it. */
.ledger :deep(:is(#feature-spec, #slices, #feature-images, #feature-tasks,
                  #feature-defects, #feature-sessions)) {
    scroll-margin-top: 3.25rem;
}
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
