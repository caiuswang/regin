<script setup>
// One feature's ledger. The stage leads, the figures follow, coverage sits
// between the figures and the evidence, and the evidence is last because it is
// what the figures are summing.
//
// Everything on this page is local. The one control that reaches the tracker
// is the stage refresh, and it is a separate, explicit event.
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import api from '../api'
import Button from '../components/ui/Button.vue'
import Icon from '../components/ui/Icon.vue'
import DeclareSessionDialog from '../components/features/DeclareSessionDialog.vue'
import FeatureEvidence from '../components/features/FeatureEvidence.vue'
import FeatureFigures from '../components/features/FeatureFigures.vue'
import FeatureStageLine from '../components/features/FeatureStageLine.vue'

const route = useRoute()
const featureKey = computed(() => String(route.params.key || ''))

const ledger = ref(null)
const loading = ref(true)
const loadError = ref('')
const refreshing = ref(false)
const refreshFailed = ref('')
const declaring = ref(false)

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
    ledger.value = await api.get(
      `/features/${encodeURIComponent(featureKey.value)}`)
  } catch (e) {
    loadError.value = e?.message || String(e)
  } finally {
    loading.value = false
  }
}
watch(featureKey, load, { immediate: true })

async function refreshStage() {
  refreshing.value = true
  refreshFailed.value = ''
  const body = await api.post(
    `/features/${encodeURIComponent(featureKey.value)}/stage/refresh`)
  refreshing.value = false
  if (body?.ok === false) {
    // Never empty: an empty reason renders no note at all, and a failed
    // refresh that renders nothing is indistinguishable from one that worked.
    // Deliberately not "unreachable": a refusal and a missing issue are
    // different facts, and the server's own sentence distinguishes them.
    refreshFailed.value = body.msg || 'the tracker could not answer'
    return
  }
  ledger.value = { ...ledger.value, stage: body.stage,
                   stage_synced_at: body.stage_synced_at }
}

async function undeclare(row) {
  const body = await api.del(
    `/features/${encodeURIComponent(featureKey.value)}`
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
  <div class="mx-auto max-w-5xl space-y-4 p-4">
    <router-link to="/features" class="back">
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
        :feature-key="ledger.key"
        :stage="ledger.stage"
        :synced-at="ledger.stage_synced_at"
        :refreshing="refreshing"
        :failed="refreshFailed"
        :live="ledger.stage_live"
        :can-refresh="canWrite && !ledger.stage_live"
        @refresh="refreshStage"
      />

      <p v-if="ledger.ambiguous" class="ambiguous" role="status">
        <Icon name="alert-triangle" :size="12" />
        {{ ledger.ambiguous }}
      </p>

      <FeatureFigures :ledger="ledger" />

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
      :feature-key="ledger.key"
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
.ambiguous {
    display: flex; align-items: flex-start; gap: 0.4rem;
    border-radius: 0.5rem; background: var(--color-amber-50);
    color: var(--color-amber-800); padding: 0.4rem 0.6rem; font-size: 11.5px;
}
</style>
