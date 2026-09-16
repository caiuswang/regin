<script setup>
// What this session built, on the one screen where the reader can see what it
// actually did.
//
// It reads the MERGED lookup, so it cannot disagree with the feature page: on
// the live database 22 of 23 attributed traces reach their feature only
// through the task lane, and a line built on the declarations-only reader
// would call every one of them unattributed while the ledger counted them.
//
// Declaring from here is the primary gesture — the trace is in hand and only
// a key has to be named, which is the ergonomic direction and the moment a
// human actually knows the answer. The page is admin-gated and every admin
// passes the editor check, so the control needs no role conditional.
//
// The word "task" already means the session's TodoWrite list on this screen,
// so the tag is never rendered bare. It is not renamed either — a tag whose
// wording forks by surface is worse than a local collision — so the label
// does the disambiguating: `feature · CAI-242 · via task #44`.
import { ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import DeclareFeatureDialog from '../features/DeclareFeatureDialog.vue'

const props = defineProps({
  traceId: { type: String, required: true },
})

const rows = ref([])
const loaded = ref(false)
const declaring = ref(false)

async function load() {
  if (!props.traceId) return
  try {
    const body = await api.get(
      `/sessions/${encodeURIComponent(props.traceId)}/features`)
    rows.value = body.features || []
  } catch {
    // A read that cannot answer says nothing rather than claiming the session
    // is unattributed — those are different facts.
    rows.value = []
  } finally {
    loaded.value = true
  }
}
watch(() => props.traceId, load, { immediate: true })

async function undeclare(row) {
  await api.del(`/features/${row.feature_id}`
                + `/attribute/${encodeURIComponent(props.traceId)}`)
  await load()
}

function onDeclared() {
  declaring.value = false
  load()
}
</script>

<template>
  <div v-if="loaded" class="attr" data-testid="session-attribution">
    <span class="attr-label">feature</span>

    <span v-if="!rows.length" class="attr-none" data-testid="attr-none">
      none declared
    </span>

    <span v-for="row in rows" :key="row.feature_id" class="attr-item">
      <router-link class="attr-key" :to="`/features/${row.feature_id}`"
      >{{ row.title || `#${row.feature_id}` }}</router-link>
      <!-- The tag is the reason this session is counted, so the task-lane one
           names the task it came from — a provenance nobody can open is a
           provenance nobody can audit. -->
      <router-link
        v-if="row.provenance === 'run' && row.task_id"
        class="attr-via attr-via-link"
        :to="`/tasks/${row.task_id}`"
      >via task #{{ row.task_id }} ↗</router-link>
      <span v-else-if="row.provenance !== 'declared'" class="attr-via">{{ row.provenance }}</span>
      <span v-if="row.is_primary" class="attr-via">primary</span>

      <!-- Absent, never disabled, on a task-lane row: there is no row to
           delete. The link beside it goes where the assertion actually is. -->
      <Button
        v-if="row.provenance !== 'run'"
        class="attr-undo"
        variant="ghost"
        size="sm"
        :aria-label="`Un-declare ${row.title} from this session`"
        :title="`Un-declare ${row.title}`"
        @click="undeclare(row)"
      ><Icon name="x" :size="11" /></Button>
    </span>

    <Button
      class="attr-declare"
      variant="ghost"
      size="sm"
      @click="declaring = true"
    >
      <Icon name="plus" :size="11" />
      {{ rows.length ? 'Declare another' : 'Declare' }}
    </Button>

    <DeclareFeatureDialog
      v-if="declaring"
      :trace-id="traceId"
      :declared="rows.map(r => r.feature_id)"
      @close="declaring = false"
      @declared="onDeclared"
    />
  </div>
</template>

<style scoped>
.attr {
    display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;
    font-size: 11.5px;
}
.attr-label {
    font-size: 10px; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--color-fg-faint);
}
.attr-none {
    font-style: italic; color: var(--color-fg-subtle);
    border-bottom: 1px dashed var(--color-border-strong); padding-bottom: 1px;
}
.attr-item { display: inline-flex; align-items: center; gap: 0.3rem; }
.attr-key {
    font-family: var(--font-mono, monospace); font-weight: 600;
    color: var(--color-primary); text-decoration: none;
}
.attr-key:hover { text-decoration: underline; }
.attr-key:focus-visible { outline: 2px solid var(--color-blue-500); }
.attr-via {
    border-radius: 999px; background: var(--color-slate-100);
    color: var(--color-slate-600); padding: 0.05rem 0.35rem; font-size: 10px;
    white-space: nowrap; text-decoration: none;
}
.attr-via-link { color: var(--color-primary); }
.attr-via-link:hover { text-decoration: underline; }
.attr-via-link:focus-visible { outline: 2px solid var(--color-blue-500); }
.attr-undo {
    opacity: 0.5; transition: opacity 150ms;
}
.attr-undo:hover {
    opacity: 1; background: var(--color-danger-soft); color: var(--color-danger);
}
.attr-undo:focus-visible { opacity: 1; outline: 2px solid var(--color-blue-500); }
.attr-declare { margin-left: 0.15rem; }
</style>
