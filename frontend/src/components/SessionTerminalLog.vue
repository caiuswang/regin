<script setup>
import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { terminalSpanLabel, terminalSpanDetail } from '../utils/traceFormatters.js'

const props = defineProps({
  spans: { type: Array, default: () => [] },
  turns: { type: Array, default: null },
  selectedSpan: { type: Object, default: null },
})

// eslint-disable-next-line no-unused-vars
const emit = defineEmits(['select-span', 'fetch-content', 'load-subtree'])

// ── Filter state ──────────────────────────────────────────────

const searchQuery = ref('')
const activeFilter = ref('all') // all | prompt | tool | skill | edit | rule | other
const searchInputRef = ref(null)
const followTail = ref(false)

// ── Span helpers ──────────────────────────────────────────────

const spanById = computed(() => {
  const map = new Map()
  for (const s of props.spans) map.set(s.span_id, s)
  return map
})

// Parent subagent.start spans get their children indented + caret-prefixed
// in the flat log. Build a quick `parent_id -> parent_span` lookup so the
// row renderer can ask "is my parent a subagent.start?" in O(1).
function parentIsSubagent(span) {
  if (!span?.parent_id) return false
  const parent = spanById.value.get(span.parent_id)
  return parent?.name === 'subagent.start'
}

// Categorize each span into one of the visible buckets (excluding 'all').
// Tracked once for chip counts and reused by the filter predicate.
// Server-side tools (advisor today, any future ones with
// attributes.server_side) bucket separately from local tools so users
// can isolate model-to-model calls — they look like a tool span but
// cost orders of magnitude more and carry a textual reply.
// File-mutating tools. They are `tool.*` spans but bucket under `edit`
// (not the generic `tool` pill) so the edit filter mirrors the Sessions
// list's Edits column. Must be checked before the tool-prefix rule.
const EDIT_TOOL_NAMES = new Set([
  'tool.Edit', 'tool.Write', 'tool.MultiEdit', 'tool.NotebookEdit', 'tool.apply_patch',
])

function categoryOf(span) {
  const n = span.name || ''
  if (n === 'prompt') return 'prompt'
  if (n === 'assistant_response') return 'assistant'
  if (n === 'assistant.thinking') return 'thinking'
  if (span.attributes?.server_side || n === 'tool.advisor') return 'advisor'
  if (EDIT_TOOL_NAMES.has(n)) return 'edit'
  if (n.startsWith('tool.') || n.startsWith('pre_tool.')) return 'tool'
  if (n === 'skill.read' || n === 'skill.invoke' || n === 'skill.launch') return 'skill'
  if (n === 'rule.check') return 'rule'
  return 'other'
}

// Chip palette mirrors the dot color used per row. Soft tints for the
// chip background, saturated dots — matches the sketch.
const FILTERS = [
  { id: 'all',       label: 'All',       dotClass: 'bg-slate-400' },
  { id: 'prompt',    label: 'prompt',    dotClass: 'bg-purple-500' },
  { id: 'assistant', label: 'assistant', dotClass: 'bg-emerald-500' },
  { id: 'thinking',  label: 'thinking',  dotClass: 'bg-amber-400' },
  { id: 'tool',      label: 'tool',      dotClass: 'bg-blue-500' },
  { id: 'advisor',   label: 'advisor',   dotClass: 'bg-violet-500' },
  { id: 'skill',     label: 'skill',     dotClass: 'bg-green-500' },
  { id: 'edit',      label: 'edit',      dotClass: 'bg-orange-500' },
  { id: 'rule',      label: 'rule',      dotClass: 'bg-red-500' },
  { id: 'other',     label: 'other',     dotClass: 'bg-slate-400' },
]

// Pre-compute spans in chronological order. The terminal view is a flat
// log so we ignore parent_id for ordering — caret prefixes carry the
// subagent relationship visually.
const orderedSpans = computed(() => {
  const arr = [...props.spans]
  arr.sort((a, b) => {
    const at = a.start_time ? new Date(a.start_time).getTime() : 0
    const bt = b.start_time ? new Date(b.start_time).getTime() : 0
    return at - bt
  })
  return arr
})

const counts = computed(() => {
  const c = { all: orderedSpans.value.length, prompt: 0, assistant: 0, thinking: 0, tool: 0, advisor: 0, skill: 0, edit: 0, rule: 0, other: 0 }
  for (const s of orderedSpans.value) c[categoryOf(s)]++
  return c
})

function matchesSearch(span, q) {
  if (!q) return true
  const a = span.attributes || {}
  const hay = [
    span.name,
    a.file_path,
    a.tool_name,
    a.command_preview,
    a.pattern,
    a.skill_id,
    a.rule_id,
    a.plan_filename,
    a.text,
    a.questions && a.questions.map(q => q.question).join(' '),
    a.answers && Object.values(a.answers).join(' '),
  ].filter(Boolean).join(' ').toLowerCase()
  return hay.includes(q.toLowerCase())
}

const ROW_CAP = 400 // soft cap on rendered rows; footer notes the rest

const filteredSpans = computed(() => {
  const q = searchQuery.value.trim()
  const cat = activeFilter.value
  return orderedSpans.value.filter(s => {
    if (cat !== 'all' && categoryOf(s) !== cat) return false
    if (!matchesSearch(s, q)) return false
    return true
  })
})

const visibleSpans = computed(() => filteredSpans.value.slice(0, ROW_CAP))
const hiddenCount = computed(() => Math.max(0, filteredSpans.value.length - visibleSpans.value.length))

// ── Display helpers ───────────────────────────────────────────

function fmtTime(iso) {
  if (!iso) return '--:--:--'
  const d = new Date(iso)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  const ms = String(d.getMilliseconds()).padStart(3, '0')
  return `${hh}:${mm}:${ss}.${ms}`
}

function fmtDuration(ms) {
  if (!ms || ms <= 0) return ''
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)}s`
  const minutes = Math.floor(ms / 60000)
  const seconds = Math.floor((ms / 1000) % 60)
  return `${minutes}m${String(seconds).padStart(2, '0')}s`
}

// Semantic dot color per span name. Same palette family as
// SessionTraceView.barColor — keeps the cross-view recognition cheap.
function dotColor(name) {
  const map = {
    'prompt': 'bg-purple-500',
    'assistant_response': 'bg-emerald-500',
    'assistant.thinking': 'bg-amber-400',
    'skill.read': 'bg-green-500',
    'skill.invoke': 'bg-green-600',
    'skill.launch': 'bg-green-500',
    'rule.check': 'bg-red-500',
    'session.start': 'bg-slate-500',
    'session.end': 'bg-slate-400',
    'environment.git_status': 'bg-cyan-600',
    'compact.pre': 'bg-amber-500',
    'compact.post': 'bg-amber-600',
    'subagent.start': 'bg-pink-500',
    'subagent.stop': 'bg-pink-300',
    'conversation': 'bg-slate-600',
  }
  if (map[name]) return map[name]
  if (name === 'tool.advisor') return 'bg-violet-500'
  if (EDIT_TOOL_NAMES.has(name)) return 'bg-orange-500'
  if (name.startsWith('tool.')) return 'bg-blue-500'
  if (name.startsWith('pre_tool.')) return 'bg-indigo-400'
  return 'bg-slate-300'
}


// Two-column "SPAN · DETAIL" split. `terminalSpanLabel` returns the
// canonical name (left), `terminalSpanDetail` the per-event context
// (right) — both live in traceFormatters.js. `spanDetailLines` wraps the
// detail string into per-line rows (multi-line AskUserQuestion previews).
function spanDetailLines(span) {
  const text = terminalSpanDetail(span)
  if (!text) return []
  return String(text).split('\n')
}

function isSelected(span) {
  return props.selectedSpan && props.selectedSpan.span_id === span.span_id
}

function onRowClick(span) {
  emit('select-span', span)
  // Lazy-fetch full attributes on demand so click → details sidebar
  // shows the full payload even if it came in via shallow.
  const a = span.attributes || {}
  if (Object.keys(a).length === 0) emit('fetch-content', span.span_id)
}

function setFilter(id) {
  activeFilter.value = id
}

// ── Follow tail ───────────────────────────────────────────────
// When toggled on, auto-scroll to the latest row each time the
// visible span list grows. The actual scroll viewport is the
// AppLayout's .content-scroll (overflow-y: auto), so scrollIntoView
// on the last row delegates to that ancestor automatically.

function scrollToBottom(smooth = true) {
  nextTick(() => {
    const list = visibleSpans.value
    if (!list.length) return
    const last = list[list.length - 1]
    const row = document.querySelector(`[data-span-id="${last.span_id}"]`)
    if (row && typeof row.scrollIntoView === 'function') {
      row.scrollIntoView({ block: 'end', behavior: smooth ? 'smooth' : 'auto' })
    }
  })
}

function toggleFollowTail() {
  followTail.value = !followTail.value
  if (followTail.value) scrollToBottom(true)
}

watch(() => visibleSpans.value.length, () => {
  if (followTail.value) scrollToBottom(true)
})

// ── Keyboard nav ──────────────────────────────────────────────
// vim-ish navigation; only fires while focus is outside text inputs.
//   j  next row     k  prev row
//   /  focus search  G  jump to last + bottom
//   Esc blurs the search input

function isTypingTarget(t) {
  if (!t) return false
  return t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable
}

function moveSelection(delta) {
  const list = visibleSpans.value
  if (!list.length) return
  const cur = props.selectedSpan
    ? list.findIndex(s => s.span_id === props.selectedSpan.span_id)
    : -1
  const next = Math.max(0, Math.min(list.length - 1, (cur < 0 ? 0 : cur + delta)))
  const target = list[next]
  if (!target) return
  emit('select-span', target)
  if (Object.keys(target.attributes || {}).length === 0) emit('fetch-content', target.span_id)
  nextTick(() => {
    const row = document.querySelector(`[data-span-id="${target.span_id}"]`)
    if (row && typeof row.scrollIntoView === 'function') {
      row.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
    }
  })
}

function jumpToEnd() {
  const list = visibleSpans.value
  if (!list.length) return
  const target = list[list.length - 1]
  emit('select-span', target)
  if (Object.keys(target.attributes || {}).length === 0) emit('fetch-content', target.span_id)
  scrollToBottom(true)
}

function handleKey(e) {
  if (isTypingTarget(e.target)) {
    if (e.key === 'Escape') e.target.blur()
    return
  }
  if (e.metaKey || e.ctrlKey || e.altKey) return
  switch (e.key) {
    case 'j':
      e.preventDefault(); moveSelection(1); break
    case 'k':
      e.preventDefault(); moveSelection(-1); break
    case '/':
      e.preventDefault(); searchInputRef.value?.focus(); break
    case 'G':
      if (e.shiftKey) { e.preventDefault(); jumpToEnd() }
      break
  }
}

onMounted(() => document.addEventListener('keydown', handleKey))
onUnmounted(() => document.removeEventListener('keydown', handleKey))
</script>

<template>
  <div class="text-sm">
    <!-- Filter bar -->
    <div class="sticky top-0 z-10 bg-white border-b border-slate-200 px-4 py-2.5 flex items-center gap-3 flex-wrap">
      <span class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold shrink-0">Filter</span>
      <div class="relative flex-1 min-w-[14rem] max-w-md">
        <span class="absolute left-2 top-1/2 -translate-y-1/2 text-slate-400 text-xs pointer-events-none">⌕</span>
        <input
          ref="searchInputRef"
          v-model="searchQuery"
          type="text"
          aria-label="Search spans"
          placeholder="search spans, files, attrs…  ( / to focus )"
          class="w-full pl-7 pr-2 py-1 text-xs border border-slate-200 rounded bg-slate-50 focus:bg-white focus:border-blue-400 focus:outline-none placeholder:text-slate-400"
        />
      </div>
      <div class="flex items-center gap-1 flex-wrap">
        <button
          v-for="f in FILTERS"
          :key="f.id"
          type="button"
          class="inline-flex items-center gap-1.5 px-2 py-0.5 text-[11px] rounded border transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
          :class="activeFilter === f.id
            ? 'bg-blue-50 border-blue-400 text-blue-700 font-medium'
            : 'bg-white border-slate-200 text-slate-600 hover:bg-slate-50'"
          @click="setFilter(f.id)"
        >
          <span class="inline-block w-2 h-2 rounded-full" :class="f.dotClass"></span>
          <span>{{ f.label }}</span>
          <span class="text-slate-400 tabular-nums">{{ counts[f.id] }}</span>
        </button>
      </div>
      <button
        type="button"
        class="ml-auto inline-flex items-center gap-1 text-[11px] rounded px-1.5 py-0.5 transition-colors focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="followTail
          ? 'text-blue-700 bg-blue-50 border border-blue-200'
          : 'text-slate-400 hover:text-slate-600 hover:bg-slate-50 border border-transparent'"
        :title="followTail ? 'Auto-scroll to newest row (on)' : 'Auto-scroll to newest row (off)'"
        :aria-pressed="followTail"
        @click="toggleFollowTail"
      >▼ Follow tail <span class="opacity-60">⌥+T</span></button>
    </div>

    <!-- Log table -->
    <div class="overflow-x-auto">
      <table class="w-full text-xs border-collapse">
        <thead>
          <tr class="text-[10px] uppercase tracking-wider text-slate-400 font-semibold border-b border-slate-200">
            <th class="text-left font-semibold px-4 py-1.5 w-[7.5rem]">Time</th>
            <th class="text-left font-semibold px-2 py-1.5 w-[13rem]">Span</th>
            <th class="text-left font-semibold px-2 py-1.5">Detail</th>
            <th class="text-right font-semibold px-4 py-1.5 w-[6rem]">Duration</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="span in visibleSpans"
            :key="span.span_id"
            :data-span-id="span.span_id"
            class="border-b border-slate-100 cursor-pointer transition-colors hover:bg-slate-50"
            :class="isSelected(span) ? 'bg-blue-50' : ''"
            @click="onRowClick(span)"
          >
            <td class="relative px-4 py-2 align-middle text-[11px] font-mono text-slate-500 whitespace-nowrap">
              <span
                v-if="isSelected(span)"
                class="absolute left-0 top-0 bottom-0 w-[3px] bg-blue-500"
                aria-hidden="true"
              ></span>
              {{ fmtTime(span.start_time) }}
            </td>
            <td class="px-2 py-2 align-middle">
              <div class="flex items-center gap-2" :style="parentIsSubagent(span) ? 'padding-left: 1.5rem' : ''">
                <span class="inline-block w-2 h-2 rounded-full shrink-0" :class="dotColor(span.name)"></span>
                <span v-if="parentIsSubagent(span)" class="text-slate-300 font-mono text-[11px] -ml-3 mr-1">↳</span>
                <span class="text-slate-700 font-medium text-[12px] truncate">{{ terminalSpanLabel(span) }}</span>
              </div>
            </td>
            <td class="px-2 py-2 align-middle text-slate-600 text-[12px] max-w-0">
              <div
                v-for="(line, lineNo) in spanDetailLines(span)"
                :key="lineNo"
                class="truncate"
              >{{ line }}</div>
            </td>
            <td class="px-4 py-2 align-middle text-right text-[11px] font-mono"
                :class="isSelected(span) ? 'text-blue-700 font-semibold' : 'text-slate-500'">
              {{ fmtDuration(span.duration_ms) }}
            </td>
          </tr>
        </tbody>
      </table>

      <!-- Empty / footer states -->
      <div v-if="!orderedSpans.length" class="text-slate-400 text-center py-8 text-xs">
        No spans recorded for this session.
      </div>
      <div v-else-if="!filteredSpans.length" class="text-slate-400 text-center py-8 text-xs">
        No spans match the current filter.
      </div>
      <div v-else-if="hiddenCount > 0" class="text-slate-400 text-center py-3 text-[11px] border-b border-slate-100">
        + {{ hiddenCount }} more spans · scroll to load
      </div>

      <!-- Keyboard hints: only meaningful when there's something to navigate. -->
      <div
        v-if="visibleSpans.length"
        class="flex items-center justify-end gap-3 px-4 py-2 text-[10px] text-slate-400 font-mono"
      >
        <span><kbd class="px-1 rounded border border-slate-200 bg-slate-50">j</kbd>/<kbd class="px-1 rounded border border-slate-200 bg-slate-50">k</kbd> navigate</span>
        <span><kbd class="px-1 rounded border border-slate-200 bg-slate-50">/</kbd> search</span>
        <span><kbd class="px-1 rounded border border-slate-200 bg-slate-50">G</kbd> end</span>
      </div>
    </div>
  </div>
</template>
