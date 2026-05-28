<script setup>
/**
 * DiffPanel — the keystone of Phase C.
 *
 * Replaces the asymmetric Accept / Replace / Merge buttons with one
 * Apply workflow:
 *   1. Show the user a *previewable* diff of what would happen.
 *   2. Let them toggle resolution checkboxes for the three filter
 *      flags (prune_orphan_edges, drop_dead_refs, dedupe_aliases).
 *   3. Apply when the post-resolution diff has no introduced_errors.
 *
 * Always recompute the diff server-side — the panel sends
 * (strategy, target, options) to /diff, never the diff itself. That
 * keeps the prospective_graph fresh even if other accepts landed
 * between the user opening the panel and clicking Apply.
 */
import { computed, onMounted, ref, watch } from 'vue'
import api from '../../api'

const props = defineProps({
  repoName: { type: String, required: true },
  proposalId: { type: String, required: true },
  topic: { type: Object, required: true },         // proposed topic dict
  approvedTopicIds: { type: Array, default: () => [] },
})

const emit = defineEmits(['applied', 'cancelled'])

// ── State ──────────────────────────────────────────────────────────

const strategy = ref('create')
const targetTopicId = ref('')
const options = ref({
  prune_orphan_edges: true,
  drop_dead_refs: false,
  dedupe_aliases: false,
})

const diff = ref(null)
const droppedItems = ref(null)
const rawIntroducedErrors = ref([])
const loadingDiff = ref(false)
const applying = ref(false)
const error = ref('')

// Default the strategy on first /diff response.
const validStrategies = computed(() => {
  if (!diff.value) return ['create', 'replace', 'merge']
  const byTopic = diff.value.valid_strategies_by_topic || {}
  return byTopic[diff.value.proposed_topic_id] || []
})

const canApply = computed(() => {
  if (!diff.value || applying.value || loadingDiff.value) return false
  if (strategy.value === 'merge' && !targetTopicId.value) return false
  return diff.value.is_applyable
})

const mergeTargets = computed(() =>
  (props.approvedTopicIds || []).filter(id => id !== props.topic.id)
)
const mergeDisabled = computed(() => mergeTargets.value.length === 0)

function isStrategyDisabled(s) {
  if (s === 'merge' && mergeDisabled.value) return true
  return !validStrategies.value.includes(s) && !!diff.value
}

const introducedErrors = computed(() => diff.value?.introduced_errors || [])
const graphWarnings = computed(() => diff.value?.graph_warnings || [])
const topicDeltas = computed(() => diff.value?.topic_deltas || [])

// ── Diff fetch ─────────────────────────────────────────────────────

async function fetchDiff() {
  loadingDiff.value = true
  error.value = ''
  try {
    const url = `/repos/${props.repoName}/topics/proposals/${props.proposalId}/topics/${props.topic.id}/diff`
    const body = {
      strategy: strategy.value,
      target_topic_id: strategy.value === 'merge' ? targetTopicId.value || null : null,
      options: options.value,
    }
    const res = await api.post(url, body)
    if (res?.ok) {
      diff.value = res.diff
      droppedItems.value = res.dropped_items
      rawIntroducedErrors.value = res.raw_introduced_errors || []
      // If the strategy we just sent isn't actually valid for this
      // topic (e.g. parent passed stale approvedTopicIds and we
      // auto-picked 'replace' for a topic that's no longer in the
      // approved graph), switch to the first backend-validated
      // option. The watcher will re-fire fetchDiff with the corrected
      // strategy.
      const validForTopic = (diff.value.valid_strategies_by_topic || {})[diff.value.proposed_topic_id] || []
      if (validForTopic.length && !validForTopic.includes(strategy.value)) {
        strategy.value = validForTopic[0]
      }
    } else {
      error.value = res?.error || 'Failed to compute diff'
    }
  } catch (err) {
    error.value = err?.message || String(err)
  } finally {
    loadingDiff.value = false
  }
}

// Watch all inputs that affect the diff — debounce-free is fine for
// human-driven changes (radio + checkbox), no typing involved.
watch(
  [strategy, targetTopicId, () => ({ ...options.value })],
  () => { fetchDiff() },
  { deep: false },
)

onMounted(() => {
  // Pick the most natural starting strategy: if the topic id collides
  // with an approved one, default to replace; otherwise create.
  if (props.approvedTopicIds.includes(props.topic.id)) {
    strategy.value = 'replace'
  }
  fetchDiff()
})

watch(mergeDisabled, (disabled) => {
  if (disabled && strategy.value === 'merge') {
    strategy.value = props.approvedTopicIds.includes(props.topic.id) ? 'replace' : 'create'
  }
})

// ── Apply ──────────────────────────────────────────────────────────

async function apply() {
  applying.value = true
  error.value = ''
  try {
    const url = `/repos/${props.repoName}/topics/proposals/${props.proposalId}/topics/${props.topic.id}/apply`
    const body = {
      strategy: strategy.value,
      target_topic_id: strategy.value === 'merge' ? targetTopicId.value || null : null,
      options: options.value,
    }
    const res = await api.post(url, body)
    if (res?.ok) {
      emit('applied', { snapshotId: res.snapshot_id, alreadyApplied: !!res.already_applied })
      return
    }
    // 400 path: server returned the resolved diff so the UI can render
    // unresolved errors inline. Replace our diff state with the fresh
    // server-computed one — that's the source of truth.
    if (res?.diff) {
      diff.value = res.diff
      droppedItems.value = res.dropped_items
    }
    error.value = res?.error === 'unresolvable_errors'
      ? 'Resolve the remaining errors before applying.'
      : (res?.error || 'Apply failed')
  } catch (err) {
    error.value = err?.message || String(err)
  } finally {
    applying.value = false
  }
}

// ── Display helpers ───────────────────────────────────────────────

function severityColor(severity) {
  if (severity === 'error') return 'red'
  if (severity === 'warning') return 'amber'
  return 'slate'
}

const STRATEGY_META = {
  create: { label: 'Create', tone: 'emerald', desc: 'Add as a new topic' },
  replace: { label: 'Replace', tone: 'blue', desc: 'Overwrite the existing topic' },
  merge: { label: 'Merge', tone: 'violet', desc: 'Fold into another topic' },
}

const KIND_BADGE = {
  create: 'bg-emerald-50 text-emerald-700 ring-1 ring-inset ring-emerald-200',
  replace: 'bg-blue-50 text-blue-700 ring-1 ring-inset ring-blue-200',
  merge: 'bg-violet-50 text-violet-700 ring-1 ring-inset ring-violet-200',
  update: 'bg-blue-50 text-blue-700 ring-1 ring-inset ring-blue-200',
}

function kindBadgeClass(kind) {
  return KIND_BADGE[kind] || 'bg-slate-100 text-slate-700 ring-1 ring-inset ring-slate-200'
}

function deltaIsEmpty(d) {
  return !d.alias_adds.length && !d.alias_removes.length
    && !d.ref_adds.length && !d.ref_removes.length
    && !d.edge_adds.length && !d.edge_removes.length
    && !d.scalar_changes.length
}
</script>

<template>
  <div class="diffpanel" data-testid="diff-panel">
    <!-- Header -->
    <header class="diffpanel__header">
      <div class="min-w-0">
        <div class="text-[0.6875rem] font-semibold uppercase tracking-[0.08em] text-slate-400">
          Apply proposal
        </div>
        <h4 class="mt-0.5 text-base font-semibold text-slate-900 truncate">
          <code class="diffpanel__topic-id">{{ topic.id }}</code>
        </h4>
      </div>
      <button
        type="button"
        class="diffpanel__close focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
        aria-label="Cancel"
        :disabled="applying"
        @click="$emit('cancelled')"
      >
        <svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4" aria-hidden="true">
          <path d="M6.28 5.22a.75.75 0 0 0-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 1 0 1.06 1.06L10 11.06l3.72 3.72a.75.75 0 1 0 1.06-1.06L11.06 10l3.72-3.72a.75.75 0 0 0-1.06-1.06L10 8.94 6.28 5.22Z"/>
        </svg>
      </button>
    </header>

    <div class="diffpanel__body">
      <!-- Strategy: segmented control -->
      <section class="diffpanel__section">
        <div class="diffpanel__section-label">Strategy</div>
        <div class="diffpanel__segmented" role="radiogroup" aria-label="Apply strategy">
          <button
            v-for="s in ['create', 'replace', 'merge']"
            :key="s"
            type="button"
            role="radio"
            :aria-checked="strategy === s"
            :disabled="isStrategyDisabled(s)"
            class="diffpanel__segment focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
            :class="{ 'diffpanel__segment--active': strategy === s }"
            :title="s === 'merge' && mergeDisabled ? 'No other approved topics available to merge into' : ''"
            @click="strategy = s"
          >
            <span class="diffpanel__segment-label">{{ STRATEGY_META[s].label }}</span>
            <span class="diffpanel__segment-desc">{{ STRATEGY_META[s].desc }}</span>
          </button>
        </div>

        <div v-if="strategy === 'merge'" class="diffpanel__merge-target">
          <template v-if="mergeTargets.length">
            <label class="diffpanel__merge-label">Merge into</label>
            <select
              v-model="targetTopicId"
              class="topics-input text-xs focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
              data-testid="diff-merge-target"
            >
              <option value="" disabled>Pick a topic…</option>
              <option v-for="id in mergeTargets" :key="id" :value="id">{{ id }}</option>
            </select>
          </template>
          <div v-else class="diffpanel__merge-empty">
            <svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4 flex-shrink-0" aria-hidden="true">
              <path fill-rule="evenodd" d="M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Zm-7-4a1 1 0 1 1-2 0 1 1 0 0 1 2 0ZM9 9a.75.75 0 0 0 0 1.5h.253a.25.25 0 0 1 .244.304l-.459 2.066A1.75 1.75 0 0 0 10.747 15H11a.75.75 0 0 0 0-1.5h-.253a.25.25 0 0 1-.244-.304l.459-2.066A1.75 1.75 0 0 0 9.253 9H9Z" clip-rule="evenodd"/>
            </svg>
            <span>No other approved topics yet — choose <strong>Create</strong> or <strong>Replace</strong> instead.</span>
          </div>
        </div>
      </section>

      <!-- Resolution options -->
      <section class="diffpanel__section">
        <div class="diffpanel__section-label">Resolution</div>
        <ul class="diffpanel__options">
          <li>
            <label class="diffpanel__option">
              <input type="checkbox" v-model="options.prune_orphan_edges" class="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2">
              <span>
                <span class="diffpanel__option-title">Drop edges to non-existent topics</span>
                <span class="diffpanel__option-hint">Default — matches legacy behavior</span>
              </span>
            </label>
          </li>
          <li>
            <label class="diffpanel__option">
              <input type="checkbox" v-model="options.drop_dead_refs" class="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2">
              <span>
                <span class="diffpanel__option-title">Drop refs to deleted files</span>
              </span>
            </label>
          </li>
          <li>
            <label class="diffpanel__option">
              <input type="checkbox" v-model="options.dedupe_aliases" class="focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2">
              <span>
                <span class="diffpanel__option-title">Drop aliases that clash with sibling topics</span>
              </span>
            </label>
          </li>
        </ul>
      </section>

      <!-- Loading -->
      <div v-if="loadingDiff" class="diffpanel__hint">
        <span class="diffpanel__spinner" aria-hidden="true"></span>
        Recomputing diff…
      </div>

      <!-- Introduced errors (block apply) -->
      <section
        v-if="introducedErrors.length"
        class="diffpanel__callout diffpanel__callout--error"
        data-testid="diff-introduced-errors"
      >
        <div class="diffpanel__callout-head">
          <svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4" aria-hidden="true">
            <path fill-rule="evenodd" d="M10 18a8 8 0 1 0 0-16 8 8 0 0 0 0 16Zm-.75-12a.75.75 0 0 1 1.5 0v4.5a.75.75 0 0 1-1.5 0V6Zm.75 8.25a.9.9 0 1 0 0-1.8.9.9 0 0 0 0 1.8Z" clip-rule="evenodd"/>
          </svg>
          <span>Issues this apply would introduce ({{ introducedErrors.length }})</span>
        </div>
        <ul class="diffpanel__issue-list">
          <li v-for="(issue, i) in introducedErrors" :key="i">
            <code class="diffpanel__code">{{ issue.code }}</code>
            <span>{{ issue.message }}</span>
          </li>
        </ul>
      </section>

      <!-- Topic deltas -->
      <section v-if="topicDeltas.length" class="diffpanel__section">
        <div class="diffpanel__section-label">Changes</div>
        <div class="space-y-2">
          <article v-for="delta in topicDeltas" :key="delta.topic_id" class="diffpanel__delta">
            <header class="diffpanel__delta-head">
              <span class="diffpanel__kind-badge" :class="kindBadgeClass(delta.kind)">{{ delta.kind }}</span>
              <code class="diffpanel__topic-id diffpanel__topic-id--sm">{{ delta.topic_id }}</code>
            </header>

            <p v-if="deltaIsEmpty(delta)" class="diffpanel__empty">No changes — topic state already matches.</p>

            <div v-else class="diffpanel__delta-body">
              <!-- Scalar field changes -->
              <dl v-if="delta.scalar_changes.length" class="diffpanel__fields">
                <div v-for="(s, i) in delta.scalar_changes" :key="'sc-' + i" class="diffpanel__field">
                  <dt>{{ s.field }}</dt>
                  <dd>
                    <span v-if="s.before" class="diffpanel__field-before">{{ s.before }}</span>
                    <span v-else class="diffpanel__field-empty">—</span>
                    <svg viewBox="0 0 20 20" fill="currentColor" class="w-3 h-3 text-slate-400" aria-hidden="true"><path fill-rule="evenodd" d="M3 10a.75.75 0 0 1 .75-.75h10.94L11.97 6.53a.75.75 0 1 1 1.06-1.06l4 4a.75.75 0 0 1 0 1.06l-4 4a.75.75 0 1 1-1.06-1.06l2.72-2.72H3.75A.75.75 0 0 1 3 10Z" clip-rule="evenodd"/></svg>
                    <span class="diffpanel__field-after">{{ s.after }}</span>
                  </dd>
                </div>
              </dl>

              <!-- Aliases -->
              <div v-if="delta.alias_adds.length || delta.alias_removes.length" class="diffpanel__group">
                <div class="diffpanel__group-label">Aliases</div>
                <ul>
                  <li v-for="(a, i) in delta.alias_adds" :key="'aa-' + i" class="diffpanel__add">
                    <span class="diffpanel__sign diffpanel__sign--add">+</span>
                    <span>{{ a }}</span>
                  </li>
                  <li v-for="(a, i) in delta.alias_removes" :key="'ar-' + i" class="diffpanel__remove">
                    <span class="diffpanel__sign diffpanel__sign--remove">−</span>
                    <span>{{ a }}</span>
                  </li>
                </ul>
              </div>

              <!-- Refs -->
              <div v-if="delta.ref_adds.length || delta.ref_removes.length" class="diffpanel__group">
                <div class="diffpanel__group-label">References</div>
                <ul>
                  <li v-for="(r, i) in delta.ref_adds" :key="'rfa-' + i" class="diffpanel__add">
                    <span class="diffpanel__sign diffpanel__sign--add">+</span>
                    <code class="diffpanel__path">{{ r.path }}</code>
                    <span class="diffpanel__role">{{ r.role }}</span>
                  </li>
                  <li v-for="(r, i) in delta.ref_removes" :key="'rfr-' + i" class="diffpanel__remove">
                    <span class="diffpanel__sign diffpanel__sign--remove">−</span>
                    <code class="diffpanel__path">{{ r.path }}</code>
                    <span class="diffpanel__role">{{ r.role }}</span>
                  </li>
                </ul>
              </div>

              <!-- Edges -->
              <div v-if="delta.edge_adds.length || delta.edge_removes.length" class="diffpanel__group">
                <div class="diffpanel__group-label">Edges</div>
                <ul>
                  <li v-for="(e, i) in delta.edge_adds" :key="'ea-' + i" class="diffpanel__add">
                    <span class="diffpanel__sign diffpanel__sign--add">+</span>
                    <span>→ <code class="diffpanel__path">{{ e.target }}</code></span>
                    <span class="diffpanel__role">{{ e.type }}</span>
                  </li>
                  <li v-for="(e, i) in delta.edge_removes" :key="'er-' + i" class="diffpanel__remove">
                    <span class="diffpanel__sign diffpanel__sign--remove">−</span>
                    <span>→ <code class="diffpanel__path">{{ e.target }}</code></span>
                    <span class="diffpanel__role">{{ e.type }}</span>
                  </li>
                </ul>
              </div>
            </div>
          </article>
        </div>
      </section>

      <!-- Dropped items (silently filtered) -->
      <section
        v-if="droppedItems && (droppedItems.orphan_edges.length || droppedItems.dead_refs.length || droppedItems.duplicate_aliases.length)"
        class="diffpanel__callout diffpanel__callout--muted"
        data-testid="diff-dropped-items"
      >
        <div class="diffpanel__callout-head">Silently filtered</div>
        <ul class="diffpanel__issue-list diffpanel__issue-list--muted">
          <li v-for="(item, i) in droppedItems.orphan_edges" :key="'oe-' + i">
            orphan edge: <code class="diffpanel__code">{{ item.topic_id }}</code> → <code class="diffpanel__code">{{ item.target }}</code> <span class="diffpanel__role">{{ item.type }}</span>
          </li>
          <li v-for="(item, i) in droppedItems.dead_refs" :key="'dr-' + i">
            dead ref: <code class="diffpanel__code">{{ item.topic_id }}</code> → <code class="diffpanel__code">{{ item.path }}</code> <span class="diffpanel__role">{{ item.role }}</span>
          </li>
          <li v-for="(item, i) in droppedItems.duplicate_aliases" :key="'da-' + i">
            duplicate alias: <code class="diffpanel__code">{{ item.topic_id }}</code> → "{{ item.alias }}"
          </li>
        </ul>
      </section>

      <!-- Pre-existing rot (advisory) -->
      <section v-if="graphWarnings.length" class="diffpanel__callout diffpanel__callout--warn">
        <div class="diffpanel__callout-head">
          <svg viewBox="0 0 20 20" fill="currentColor" class="w-4 h-4" aria-hidden="true">
            <path fill-rule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495ZM10 6a.75.75 0 0 1 .75.75v3.5a.75.75 0 1 1-1.5 0v-3.5A.75.75 0 0 1 10 6Zm0 9a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z" clip-rule="evenodd"/>
          </svg>
          <span>Pre-existing graph rot ({{ graphWarnings.length }})</span>
        </div>
        <p class="text-[0.75rem] text-amber-700 mb-1.5">
          These issues already exist in the approved graph; they don't block this apply.
        </p>
        <ul class="diffpanel__issue-list">
          <li v-for="(w, i) in graphWarnings" :key="i">
            <code class="diffpanel__code">{{ w.code }}</code>
            <span>{{ w.message }}</span>
          </li>
        </ul>
      </section>
    </div>

    <!-- Footer / actions -->
    <footer class="diffpanel__footer">
      <div v-if="error" class="diffpanel__error" data-testid="diff-error">{{ error }}</div>
      <div class="diffpanel__footer-actions">
        <button
          type="button"
          class="btn btn-secondary text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          :disabled="applying"
          @click="$emit('cancelled')"
        >
          Cancel
        </button>
        <button
          type="button"
          class="btn btn-primary text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
          :disabled="!canApply"
          data-testid="diff-apply"
          @click="apply"
        >
          {{ applying ? 'Applying…' : 'Apply' }}
        </button>
      </div>
    </footer>
  </div>
</template>

<style scoped>
/* ── Shell ──────────────────────────────────────────────────────── */
.diffpanel {
  background: #fff;
  border: 1px solid #E2E8F0;
  border-radius: 0.75rem;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04), 0 8px 24px -12px rgba(15, 23, 42, 0.08);
  overflow: hidden;
  font-size: 0.8125rem;
  color: #0F172A;
}

.diffpanel code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.75rem;
}

/* ── Header ─────────────────────────────────────────────────────── */
.diffpanel__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 0.75rem;
  padding: 0.875rem 1rem;
  border-bottom: 1px solid #F1F5F9;
  background: linear-gradient(180deg, #FAFBFC 0%, #fff 100%);
}

.diffpanel__topic-id {
  display: inline-block;
  padding: 0.125rem 0.5rem;
  background: #F1F5F9;
  border-radius: 0.375rem;
  color: #1E293B;
  font-weight: 500;
  font-size: 0.8125rem;
}
.diffpanel__topic-id--sm {
  padding: 0.0625rem 0.375rem;
  font-size: 0.75rem;
}

.diffpanel__close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.75rem;
  height: 1.75rem;
  border-radius: 0.5rem;
  color: #64748B;
  background: transparent;
  border: 1px solid transparent;
  cursor: pointer;
  flex-shrink: 0;
  transition: background-color 150ms, color 150ms, border-color 150ms;
}
.diffpanel__close:hover { background: #F1F5F9; color: #0F172A; }
.diffpanel__close:disabled { opacity: 0.5; cursor: not-allowed; }
.diffpanel__close:focus-visible {
  outline: none;
  border-color: #3B82F6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.25);
}

/* ── Body ───────────────────────────────────────────────────────── */
.diffpanel__body {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 1.125rem;
}

.diffpanel__section { display: flex; flex-direction: column; gap: 0.5rem; }
.diffpanel__section-label {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.08em;
  color: #64748B;
}

/* ── Segmented strategy control ─────────────────────────────────── */
.diffpanel__segmented {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0.375rem;
  padding: 0.25rem;
  background: #F1F5F9;
  border-radius: 0.625rem;
}

.diffpanel__segment {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0.125rem;
  padding: 0.5rem 0.75rem;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 0.5rem;
  cursor: pointer;
  text-align: left;
  color: #475569;
  transition: background-color 150ms, color 150ms, box-shadow 150ms, border-color 150ms;
}
.diffpanel__segment:hover:not(:disabled):not(.diffpanel__segment--active) {
  background: rgba(255, 255, 255, 0.6);
  color: #0F172A;
}
.diffpanel__segment--active {
  background: #fff;
  color: #0F172A;
  box-shadow: 0 1px 2px rgba(15, 23, 42, 0.08), 0 1px 0 rgba(255, 255, 255, 0.5) inset;
  border-color: #E2E8F0;
}
.diffpanel__segment:disabled { opacity: 0.4; cursor: not-allowed; }
.diffpanel__segment:focus-visible {
  outline: none;
  border-color: #3B82F6;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.25);
}

.diffpanel__segment-label { font-size: 0.8125rem; font-weight: 600; line-height: 1.1; }
.diffpanel__segment-desc { font-size: 0.6875rem; color: #94A3B8; line-height: 1.2; }
.diffpanel__segment--active .diffpanel__segment-desc { color: #64748B; }

/* ── Resolution options ─────────────────────────────────────────── */
.diffpanel__options { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 0.25rem; }
.diffpanel__option {
  display: flex;
  align-items: flex-start;
  gap: 0.625rem;
  padding: 0.5rem 0.625rem;
  border-radius: 0.5rem;
  cursor: pointer;
  transition: background-color 150ms;
}
.diffpanel__option:hover { background: #F8FAFC; }
.diffpanel__option input[type="checkbox"] {
  margin-top: 0.125rem;
  accent-color: #2563EB;
  width: 0.875rem;
  height: 0.875rem;
  flex-shrink: 0;
}
.diffpanel__option input[type="checkbox"]:focus-visible {
  outline: 2px solid #3B82F6;
  outline-offset: 2px;
}
.diffpanel__option > span:last-child { display: flex; flex-direction: column; gap: 0.125rem; }
.diffpanel__option-title { font-size: 0.8125rem; color: #0F172A; line-height: 1.3; }
.diffpanel__option-hint { font-size: 0.6875rem; color: #94A3B8; }

/* ── Hint / spinner ─────────────────────────────────────────────── */
.diffpanel__hint {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
  color: #64748B;
}
.diffpanel__spinner {
  width: 0.75rem;
  height: 0.75rem;
  border: 2px solid #CBD5E1;
  border-top-color: #2563EB;
  border-radius: 999px;
  animation: diffpanel-spin 700ms linear infinite;
}
@keyframes diffpanel-spin { to { transform: rotate(360deg); } }

/* ── Callouts ───────────────────────────────────────────────────── */
.diffpanel__callout {
  border-radius: 0.625rem;
  padding: 0.625rem 0.75rem;
  border: 1px solid;
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}
.diffpanel__callout--error { background: #FEF2F2; border-color: #FECACA; color: #991B1B; }
.diffpanel__callout--warn  { background: #FFFBEB; border-color: #FDE68A; color: #92400E; }
.diffpanel__callout--muted { background: #F8FAFC; border-color: #E2E8F0; color: #475569; }

.diffpanel__callout-head {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  font-weight: 600;
}

.diffpanel__issue-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.25rem; font-size: 0.75rem; }
.diffpanel__issue-list li { display: flex; align-items: flex-start; gap: 0.375rem; flex-wrap: wrap; }
.diffpanel__issue-list--muted li { color: #475569; }

.diffpanel__code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.6875rem;
  padding: 0.0625rem 0.3125rem;
  background: rgba(15, 23, 42, 0.06);
  border-radius: 0.25rem;
  color: inherit;
}

/* ── Delta cards ────────────────────────────────────────────────── */
.diffpanel__delta {
  border: 1px solid #E2E8F0;
  border-radius: 0.625rem;
  background: #fff;
  overflow: hidden;
}
.diffpanel__delta-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  background: #F8FAFC;
  border-bottom: 1px solid #E2E8F0;
}
.diffpanel__kind-badge {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: lowercase;
  padding: 0.125rem 0.5rem;
  border-radius: 999px;
}
.diffpanel__delta-body {
  padding: 0.625rem 0.75rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.diffpanel__empty {
  padding: 0.75rem;
  font-size: 0.75rem;
  color: #94A3B8;
  font-style: italic;
}

/* ── Scalar field diff ──────────────────────────────────────────── */
.diffpanel__fields { display: flex; flex-direction: column; gap: 0.375rem; margin: 0; }
.diffpanel__field {
  display: grid;
  grid-template-columns: 5.5rem 1fr;
  align-items: baseline;
  gap: 0.625rem;
  font-size: 0.75rem;
}
.diffpanel__field dt {
  font-weight: 500;
  color: #64748B;
  text-transform: capitalize;
}
.diffpanel__field dd {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 0.375rem;
  flex-wrap: wrap;
  color: #0F172A;
}
.diffpanel__field-before {
  text-decoration: line-through;
  color: #94A3B8;
  background: #FEF2F2;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.25rem;
}
.diffpanel__field-empty { color: #CBD5E1; }
.diffpanel__field-after {
  background: #ECFDF5;
  color: #065F46;
  padding: 0.125rem 0.375rem;
  border-radius: 0.25rem;
  max-width: 60ch;
  line-height: 1.45;
  word-break: break-word;
}
.diffpanel__field dd { max-width: 100%; }

/* ── Merge target empty / picker ────────────────────────────────── */
.diffpanel__merge-target {
  margin-top: 0.625rem;
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
}
.diffpanel__merge-label {
  font-size: 0.75rem;
  font-weight: 500;
  color: #475569;
}
.diffpanel__merge-target .topics-input {
  flex: 1;
  max-width: 18rem;
}
.diffpanel__merge-empty {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.75rem;
  font-size: 0.75rem;
  color: #92400E;
  background: #FFFBEB;
  border: 1px solid #FDE68A;
  border-radius: 0.5rem;
  width: 100%;
}

/* ── Add / remove groups ────────────────────────────────────────── */
.diffpanel__group { display: flex; flex-direction: column; gap: 0.25rem; }
.diffpanel__group-label {
  font-size: 0.6875rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: #94A3B8;
}
.diffpanel__group ul { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.125rem; }
.diffpanel__group li {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.75rem;
  padding: 0.1875rem 0;
}
.diffpanel__sign {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1rem;
  height: 1rem;
  border-radius: 0.25rem;
  font-weight: 700;
  font-size: 0.75rem;
  line-height: 1;
  flex-shrink: 0;
}
.diffpanel__sign--add    { background: #DCFCE7; color: #15803D; }
.diffpanel__sign--remove { background: #FEE2E2; color: #B91C1C; }
.diffpanel__add    { color: #166534; }
.diffpanel__remove { color: #991B1B; }

.diffpanel__path {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 0.6875rem;
  color: inherit;
  background: transparent;
}
.diffpanel__role {
  font-size: 0.6875rem;
  color: #94A3B8;
  padding: 0.0625rem 0.3125rem;
  background: #F1F5F9;
  border-radius: 0.25rem;
}

/* ── Footer ─────────────────────────────────────────────────────── */
.diffpanel__footer {
  border-top: 1px solid #F1F5F9;
  padding: 0.75rem 1rem;
  background: #FAFBFC;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.diffpanel__error {
  font-size: 0.75rem;
  color: #B91C1C;
  background: #FEF2F2;
  border: 1px solid #FECACA;
  padding: 0.375rem 0.625rem;
  border-radius: 0.5rem;
}
.diffpanel__footer-actions {
  display: flex;
  justify-content: flex-end;
  gap: 0.5rem;
}
.diffpanel__footer-actions .btn:focus-visible {
  outline: none;
  box-shadow: 0 0 0 2px rgba(59, 130, 246, 0.4);
}
</style>
