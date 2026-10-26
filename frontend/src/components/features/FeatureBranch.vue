<script setup>
// Which branches this feature's code lives on, as git answers right now —
// the Slices card's footer, since the slices are what lands on them. One line
// at rest; the integration's state, its editor and the run branches behind it.
//
// The integration branch is the one an operator set on the feature, else the
// `f{id}/integration` convention when that ref exists; the run branches are
// the worktree branches the feature's runs were armed with. Nothing here is
// cached: every figure is the server's live git read, and a repo it cannot
// read is shown as that read's error inside this card rather than failing the
// page. Those read failures are a status of this card, not an alert: the
// page's alerts are refused writes, and a stale repo path must not outshout
// them.
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'

const props = defineProps({
  featureId: { type: Number, required: true },
  // The row's own `features.branch`, which the editor opens on.
  branch: { type: String, default: null },
  canWrite: { type: Boolean, default: false },
})
const emit = defineEmits(['saved'])

const view = ref(null)
const loadError = ref('')
const editing = ref(false)
const draft = ref('')
const saving = ref(false)
const saveError = ref('')
const open = ref(false)

// Only the newest read may write: two saves in a row re-read twice, and the
// view is reused when the route moves to another feature.
let latest = 0
async function load() {
  const token = ++latest
  loadError.value = ''
  try {
    const body = await api.get(`/features/${props.featureId}/branches`)
    if (token === latest) view.value = body
  } catch (e) {
    if (token === latest) loadError.value = e?.message || String(e)
  }
}
watch(() => props.featureId, () => {
  view.value = null
  open.value = false
  cancelEdit()
  load()
}, { immediate: true })

const integration = computed(() => view.value?.integration || null)
const runs = computed(() => view.value?.runs || [])

function yesNo(value, yes, no) {
  if (value == null) return '—'
  return value ? yes : no
}

function startEdit() {
  draft.value = props.branch || ''
  saveError.value = ''
  editing.value = true
}

function cancelEdit() {
  editing.value = false
  saveError.value = ''
}

// An empty field clears the set branch, which hands the integration back to
// the convention — the server treats '' and null alike. A refusal RETURNS
// `{ ok: false }`; an unreachable server throws.
async function save() {
  if (saving.value) return
  saving.value = true
  saveError.value = ''
  try {
    const res = await api.patch(`/features/${props.featureId}`,
      { branch: draft.value.trim() || null })
    if (res?.ok === false) {
      saveError.value = res.msg || 'the branch could not be saved'
      return
    }
    editing.value = false
    emit('saved', res)
    load()
  } catch (e) {
    saveError.value = `the branch could not be sent — ${e?.message || e}`
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="fb-foot" data-testid="feature-branch" data-pl-part="card-foot">
    <Button
      variant="ghost" size="sm" class="fb-summary"
      :aria-expanded="open" :aria-controls="`fb-detail-${featureId}`"
      data-testid="feature-branch-toggle"
      @click="open = !open"
    >
      <Icon :name="open ? 'chevron-down' : 'chevron-right'" :size="11" />
      <span class="fb-label">Integration branch</span>
      <code v-if="integration?.name" class="fb-name ledger-ellipsis" :title="integration.name"
            data-testid="feature-branch-name">{{ integration.name }}</code>
      <span v-else-if="integration" class="fb-meta" data-testid="feature-branch-name">none</span>
      <span v-if="view" class="fb-meta" data-testid="feature-branch-run-count">· {{ runs.length }} run branch{{ runs.length === 1 ? '' : 'es' }}</span>
    </Button>

    <p v-if="loadError" class="fb-fail" role="status">{{ loadError }}</p>
    <p v-if="view?.error" class="fb-fail" role="status" data-testid="feature-branch-error">
      git could not be read: {{ view.error }}
    </p>

    <div v-if="open" :id="`fb-detail-${featureId}`" class="fb-detail" data-testid="feature-branch-detail">
      <div class="fb-row" data-testid="feature-branch-integration">
        <span class="fb-label">integration</span>
        <template v-if="editing">
          <Input
            v-model="draft"
            class="fb-input"
            :placeholder="`f${featureId}/integration`"
            aria-label="Integration branch"
            data-testid="feature-branch-input"
            @keydown.enter="save"
          />
          <Button variant="primary" size="sm" :disabled="saving" data-testid="feature-branch-save" @click="save">
            {{ saving ? 'Saving…' : 'Save' }}
          </Button>
          <Button variant="ghost" size="sm" :disabled="saving" data-testid="feature-branch-cancel" @click="cancelEdit">
            Cancel
          </Button>
        </template>
        <template v-else-if="integration">
          <code v-if="integration.name" class="fb-name">{{ integration.name }}</code>
          <span v-else class="fb-meta">none</span>
          <span v-if="integration.source" class="fb-meta" data-testid="feature-branch-source">{{ integration.source }}</span>
          <span v-if="integration.name" class="fb-meta">{{ integration.exists ? 'exists' : 'missing' }}</span>
          <span v-if="integration.name" class="fb-meta">
            {{ yesNo(integration.merged_into_default, 'merged', 'not merged') }}
          </span>
          <Button v-if="canWrite" variant="ghost" size="sm" data-testid="feature-branch-edit" @click="startEdit">
            Edit
          </Button>
        </template>
      </div>
      <p v-if="saveError" class="fb-fail" role="alert" data-testid="feature-branch-save-error">{{ saveError }}</p>

      <p v-if="view && !runs.length" class="fb-meta" data-testid="feature-branch-runs-empty">
        No run of this feature was armed on a worktree branch.
      </p>
      <ul v-else-if="runs.length" class="ledger-list" data-testid="feature-branch-runs">
        <li
          v-for="r in runs" :key="r.branch" class="ledger-row fb-row"
          :data-testid="`feature-branch-run-${r.last_run_id}`"
        >
          <code class="fb-name">{{ r.branch }}</code>
          <span v-if="r.base" class="fb-meta">from {{ r.base }}</span>
          <router-link class="fb-link" :to="`/tasks/${r.task_id}`">
            #{{ r.task_id }} · run {{ r.last_run_id }}
          </router-link>
          <span class="fb-meta fb-path">{{ r.worktree_path || 'no worktree' }}</span>
          <span class="fb-meta">{{ r.exists ? 'exists' : 'missing' }}</span>
          <span class="fb-meta">{{ yesNo(r.merged_into_base, 'merged', 'not merged') }}</span>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.fb-foot {
    margin: 0.6rem -1.25rem -1.25rem; padding: 0.35rem 1.25rem;
    border-top: 1px solid var(--color-border-subtle);
}
.fb-summary {
    justify-content: flex-start; gap: 0.4rem; max-width: 100%; min-width: 0;
    margin: 0 -0.5rem; font-weight: 400; white-space: nowrap;
}
.fb-summary .fb-name { flex: 0 1 auto; }
.fb-detail { display: flex; flex-direction: column; gap: 0.35rem; padding: 0.35rem 0 0.25rem; }
.fb-row { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; min-width: 0; }
.fb-label { font-size: 11.5px; color: var(--color-fg-subtle); flex: none; }
.fb-name {
    font-family: var(--font-mono, monospace); font-size: 0.8125rem;
    color: var(--color-fg); overflow-wrap: anywhere; min-width: 0;
}
.fb-input { flex: 1 1 12rem; min-width: 0; }
.fb-meta { font-size: 11px; color: var(--color-fg-subtle); }
.fb-path { overflow-wrap: anywhere; min-width: 0; }
.fb-link { font-size: 11.5px; color: var(--color-info); text-decoration: none; flex: none; }
.fb-link:hover { text-decoration: underline; }
.fb-link:focus-visible { outline: 2px solid var(--color-blue-500); }
.fb-fail {
    margin: 0.4rem 0; font-size: 11.5px; color: var(--color-amber-800);
    background: var(--color-amber-50); border-radius: 0.4rem;
    padding: 0.35rem 0.5rem; overflow-wrap: anywhere;
}
</style>
