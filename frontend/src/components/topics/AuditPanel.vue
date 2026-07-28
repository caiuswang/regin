<script setup>
/**
 * AuditPanel — graph-wide validation issues from /audit + bulk-fix.
 *
 * Each code group shows a header with the count + a checkbox when the
 * code is auto-fixable (dead refs, orphan edge targets). Everything else
 * renders a disabled checkbox and a tag saying why bulk-fix can't take
 * it: "manual" for a code needing a human decision (duplicate_alias),
 * "not checked out" for a ref that is not a defect at all, "unverified"
 * for one the branch-tip check could not run on.
 *
 * The "Fix selected" button posts to `/audit/fix` and refreshes.
 */
import { computed, onMounted, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'

const props = defineProps({
  repoName: { type: String, required: true },
})

const issues = ref([])
const byCode = ref({})
const autoFixableCodes = ref([])
const errorCount = ref(0)
const warningCount = ref(0)
const loading = ref(false)
const fixing = ref(false)
const error = ref('')
const lastFixSummary = ref(null)  // { fixed_counts, snapshot_ids, skipped_codes }

const selectedCodes = ref(new Set())
// A single boundary regression can emit dozens of issues per code; showing them
// all turns the panel into an unreadable wall, so each group opens truncated.
const PREVIEW_LIMIT = 10
const expandedCodes = ref(new Set())

function visibleIssues(code) {
  const list = byCode.value[code] || []
  return expandedCodes.value.has(code) ? list : list.slice(0, PREVIEW_LIMIT)
}

function hiddenCount(code) {
  return Math.max(0, (byCode.value[code] || []).length - PREVIEW_LIMIT)
}

function toggleExpanded(code) {
  const next = new Set(expandedCodes.value)
  if (next.has(code)) next.delete(code)
  else next.add(code)
  expandedCodes.value = next
}

async function refresh() {
  loading.value = true
  error.value = ''
  try {
    const res = await api.get(`/repos/${props.repoName}/topics/audit`)
    if (res?.ok) {
      issues.value = res.issues || []
      byCode.value = res.by_code || {}
      autoFixableCodes.value = res.auto_fixable_codes || []
      errorCount.value = res.error_count || 0
      warningCount.value = res.warning_count || 0
      // Discard selections for codes that no longer have issues.
      const presentCodes = new Set(Object.keys(byCode.value))
      selectedCodes.value = new Set(
        [...selectedCodes.value].filter((c) => presentCodes.has(c))
      )
    } else {
      error.value = res?.error || 'Failed to load audit'
    }
  } catch (err) {
    error.value = err?.message || String(err)
  } finally {
    loading.value = false
  }
}

const sortedCodes = computed(() => Object.keys(byCode.value).sort())

const autoFixableSet = computed(() => new Set(autoFixableCodes.value))
const selectableCount = computed(() => sortedCodes.value.filter(isAutoFixable).length)
const selectedCount = computed(() => selectedCodes.value.size)

function isAutoFixable(code) {
  return autoFixableSet.value.has(code)
}

// Absent from this checkout but alive on another branch tip. Not a defect and
// not something to "resolve" — it clears once that branch merges and the file
// lands in the working tree, and the one thing you must not do is strip the
// anchor. A stale branch carrying the path does not by itself keep the finding
// alive: it also needs the file to be absent here. What can outlive the merge
// is a path deleted afterwards — a stale tip still carries it, so the backend
// scans refs/heads+refs/remotes with no merged filter and reports it here
// rather than as the dead ref it now is.
const BRANCH_OWNED_CODE = 'graph.ref_on_other_branch'

// Absent here and git could not say whether any branch carries it — no repo,
// unborn HEAD, git off PATH, or a ref spelled as something git refuses as a
// pathspec. Same non-deletable treatment as the branch-owned case, but it must
// read differently: telling the user the file "lives on another branch" when
// the question was never answered is why the strip button appeared to do
// nothing in a checkout with no branches to scan.
const UNPROVABLE_CODE = 'graph.ref_unverifiable'

function codeHint(code) {
  if (isAutoFixable(code)) return 'Auto-fixable — select to bulk fix'
  if (code === BRANCH_OWNED_CODE) {
    return 'Nothing to fix — the file lives on a branch that is not checked out; clears when it merges'
  }
  if (code === UNPROVABLE_CODE) {
    return 'Nothing to fix — the branch-tip check could not run here, so this ref cannot be proven dead'
  }
  return 'Manual resolution only — fix via DiffPanel or the originating proposal'
}

function codeTag(code) {
  if (code === BRANCH_OWNED_CODE) return 'not checked out'
  if (code === UNPROVABLE_CODE) return 'unverified'
  return 'manual'
}

function severityForCode(code) {
  const list = byCode.value[code] || []
  if (list.some((i) => i.severity === 'error')) return 'error'
  if (list.some((i) => i.severity === 'warning')) return 'warning'
  return 'info'
}

function severityClasses(severity) {
  if (severity === 'error') return 'border-red-200 bg-red-50 text-red-900'
  if (severity === 'warning') return 'border-amber-200 bg-amber-50 text-amber-900'
  return 'border-slate-200 bg-slate-50 text-slate-700'
}

function toggleCode(code) {
  if (!isAutoFixable(code)) return
  const next = new Set(selectedCodes.value)
  if (next.has(code)) next.delete(code)
  else next.add(code)
  selectedCodes.value = next
}

function selectAllFixable() {
  selectedCodes.value = new Set(
    sortedCodes.value.filter(isAutoFixable)
  )
}

function clearSelection() {
  selectedCodes.value = new Set()
}

async function fixSelected() {
  if (!selectedCount.value || fixing.value) return
  fixing.value = true
  error.value = ''
  lastFixSummary.value = null
  try {
    const res = await api.post(
      `/repos/${props.repoName}/topics/audit/fix`,
      { issue_codes: [...selectedCodes.value] },
    )
    if (res?.ok) {
      lastFixSummary.value = res
      await refresh()
    } else {
      error.value = res?.error || 'Bulk fix failed'
    }
  } catch (err) {
    error.value = err?.message || String(err)
  } finally {
    fixing.value = false
  }
}

onMounted(refresh)
</script>

<template>
  <section class="space-y-3" data-testid="audit-panel">
    <header class="flex items-center justify-between gap-3">
      <div>
        <h3 class="text-base font-semibold text-slate-900">Graph audit</h3>
        <p class="text-xs text-slate-500">
          Validation issues against the live approved graph.
          <span v-if="!loading && (errorCount || warningCount)">
            {{ errorCount }} error<span v-if="errorCount !== 1">s</span>, {{ warningCount }} warning<span v-if="warningCount !== 1">s</span>.
          </span>
          <span v-else-if="!loading">Graph is clean.</span>
        </p>
      </div>
      <div class="btn-row flex gap-2">
        <Button
          variant="secondary"
          :disabled="loading || fixing"
          @click="refresh"
        >
          {{ loading ? 'Refreshing…' : 'Refresh' }}
        </Button>
        <Button
          v-if="selectableCount > 0"
          variant="primary"
          :disabled="!selectedCount || fixing || loading"
          data-testid="audit-fix-selected"
          @click="fixSelected"
        >
          {{ fixing ? 'Fixing…' : `Fix selected (${selectedCount})` }}
        </Button>
      </div>
    </header>

    <p v-if="error" class="text-sm text-red-700">{{ error }}</p>

    <div v-if="lastFixSummary" class="text-xs text-green-800 bg-green-50 border border-green-200 rounded px-3 py-2">
      Fixed
      <span v-if="(lastFixSummary.fixed_counts?.['graph.dead_ref'] || 0) > 0">
        {{ lastFixSummary.fixed_counts['graph.dead_ref'] }} dead ref<span v-if="lastFixSummary.fixed_counts['graph.dead_ref'] !== 1">s</span>
      </span>
      <span v-if="(lastFixSummary.fixed_counts?.['graph.orphan_edge_target'] || 0) > 0">
        {{ ' ' }}{{ lastFixSummary.fixed_counts['graph.orphan_edge_target'] }} orphan edge<span v-if="lastFixSummary.fixed_counts['graph.orphan_edge_target'] !== 1">s</span>
      </span>
      across {{ lastFixSummary.snapshot_ids?.length || 0 }} new snapshot<span v-if="(lastFixSummary.snapshot_ids?.length || 0) !== 1">s</span>.
      <span v-if="lastFixSummary.skipped_codes?.length">
        Skipped (manual): {{ lastFixSummary.skipped_codes.join(', ') }}
      </span>
    </div>

    <p v-if="!loading && !issues.length && !error" class="text-sm text-slate-500 italic">
      No issues found — the approved graph is consistent.
    </p>

    <div v-if="selectableCount > 0" class="flex items-center gap-3 text-xs">
      <Button
        variant="link"
        size="sm"
        @click="selectAllFixable"
      >
        Select all auto-fixable
      </Button>
      <Button
        v-if="selectedCount > 0"
        variant="link"
        size="sm"
        @click="clearSelection"
      >
        Clear ({{ selectedCount }})
      </Button>
    </div>

    <div
      v-for="code in sortedCodes"
      :key="code"
      :class="['border rounded p-3 space-y-1', severityClasses(severityForCode(code))]"
      data-testid="audit-group"
    >
      <div class="flex items-center justify-between">
        <Checkbox
          :disabled="!isAutoFixable(code)"
          :model-value="selectedCodes.has(code)"
          :title="codeHint(code)"
          @update:model-value="toggleCode(code)"
        >
          <code class="text-xs font-semibold">{{ code }}</code>
          <span v-if="!isAutoFixable(code)" class="text-[10px] uppercase tracking-wide text-slate-500 ml-1">{{ codeTag(code) }}</span>
        </Checkbox>
        <span class="text-xs">{{ byCode[code].length }} issue<span v-if="byCode[code].length !== 1">s</span></span>
      </div>
      <ul class="text-xs space-y-0.5">
        <li v-for="(issue, i) in visibleIssues(code)" :key="i" class="break-words">
          <span>{{ issue.message }}</span>
          <span v-if="issue.topic_ids?.length" class="text-slate-600">
            — topics: {{ issue.topic_ids.join(', ') }}
          </span>
          <span v-if="issue.paths?.length" class="text-slate-600">
            — paths: {{ issue.paths.join(', ') }}
          </span>
        </li>
      </ul>
      <Button
        v-if="hiddenCount(code) > 0 || expandedCodes.has(code)"
        variant="link"
        size="sm"
        :data-testid="`audit-toggle-${code}`"
        @click="toggleExpanded(code)"
      >
        {{ expandedCodes.has(code) ? 'Show fewer' : `Show all ${byCode[code].length}` }}
      </Button>
    </div>
  </section>
</template>
