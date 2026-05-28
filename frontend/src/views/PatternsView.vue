<script setup>
import { ref, onMounted, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../api'
import Card from '../components/Card.vue'
import Badge from '../components/Badge.vue'
import { useFlash } from '../composables/useFlash'
import { useFeatures } from '../composables/useFeatures'
import { useConfirm } from '../composables/useConfirm'

const { flash } = useFlash()
const { features } = useFeatures()
const { confirm } = useConfirm()
const route = useRoute()
const router = useRouter()
const data = ref(null)
const loading = ref(true)
const showCreate = ref(false)
const newTitle = ref('')
const newSlug = ref('')
const newDescription = ref('')
const newTags = ref([])
const creating = ref(false)
const showCategoryFilter = ref(false)
const showTagFilter = ref(false)

const manageMode = ref(false)
const unusedTags = ref([])
const renamingTag = ref(null)
const renameValue = ref('')

// EXPERIMENTAL — SkillRouter dense routing
const denseOn = ref(false)
const denseQuery = ref('')
const denseResults = ref([])
const denseScoreKind = ref(null)
const denseLoading = ref(false)
const denseError = ref(null)
const coverage = ref(null)
const reindexing = ref(false)

async function runDenseSearch() {
  const q = denseQuery.value.trim()
  if (!q) { denseResults.value = []; denseError.value = null; return }
  denseLoading.value = true
  denseError.value = null
  try {
    const params = new URLSearchParams({ q, top_k: '20' })
    const payload = await api.get('/patterns/route?' + params.toString())
    denseResults.value = payload.docs || []
    denseScoreKind.value = payload.score_kind
    if (payload.hint) denseError.value = payload.hint
  } catch (e) {
    denseError.value = e?.message || 'dense search failed (router deps may be missing — see `regin doctor`)'
    denseResults.value = []
  } finally {
    denseLoading.value = false
  }
}

async function loadCoverage() {
  try {
    coverage.value = await api.get('/patterns/embedding-coverage')
  } catch {
    coverage.value = null
  }
}

async function reindex() {
  reindexing.value = true
  try {
    const res = await api.post('/patterns/reindex', {})
    if (res?.ok) flash('Re-embedding in background…')
    // Poll once after a short delay so the chip reflects new state.
    setTimeout(loadCoverage, 5000)
  } catch (e) {
    flash(e?.message || 'reindex failed', 'error')
  } finally {
    reindexing.value = false
  }
}

const coverageHasDrift = computed(() => {
  const c = coverage.value
  if (!c) return false
  return (c.unembedded || 0) > 0 || (c.stale || 0) > 0
})

const denseActive = computed(() => features.experimental_dense_search && denseOn.value)
const visibleDocs = computed(() => denseActive.value ? denseResults.value : (data.value?.docs || []))

async function load() {
  loading.value = true
  const params = new URLSearchParams()
  if (route.query.tag) params.set('tag', route.query.tag)
  if (route.query.category) params.set('category', route.query.category)
  const qs = params.toString()
  data.value = await api.get('/patterns' + (qs ? '?' + qs : ''))
  loading.value = false
}

onMounted(load)
watch(() => route.query, load)
watch(() => denseOn.value && features.experimental_dense_search, (on) => {
  if (on) loadCoverage()
})

function autoSlug() {
  if (!newSlug.value) {
    newSlug.value = newTitle.value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
  }
}

const importDragging = ref(false)
const importUploading = ref(false)
const importInput = ref(null)

const conflictVisible = ref(false)
const conflictMsg = ref('')
const conflictSlug = ref('')
const conflictSelection = ref(null)
const conflictRenaming = ref(false)
const conflictNewSlug = ref('')

const activeFilterCount = computed(() => {
  if (!data.value) return 0
  return (data.value.tag_filter ? 1 : 0) + (data.value.cat_filter ? 1 : 0)
})

const groupedTags = computed(() => {
  const groups = {}
  for (const t of (data.value?.tags || [])) {
    const cat = t.category || 'uncategorized'
    if (!groups[cat]) groups[cat] = []
    groups[cat].push(t)
  }
  for (const list of Object.values(groups)) {
    list.sort((a, b) => (b.doc_count - a.doc_count) || a.name.localeCompare(b.name))
  }
  return Object.entries(groups).sort((a, b) => a[0].localeCompare(b[0]))
})

function toggleTagFilter() {
  showTagFilter.value = !showTagFilter.value
  if (!showTagFilter.value) exitManageMode()
}

async function enterManageMode() {
  manageMode.value = true
  try {
    const all = await api.get('/tags')
    unusedTags.value = (all || []).filter((t) => (t.doc_count || 0) === 0)
  } catch (e) {
    flash(e?.message || 'Failed to load tags', 'error')
  }
}

function exitManageMode() {
  manageMode.value = false
  unusedTags.value = []
  renamingTag.value = null
  renameValue.value = ''
}

async function deleteTag(name) {
  const ok = await confirm('Delete tag', `Delete tag "${name}"?`, true)
  if (!ok) return
  const result = await api.post(`/tags/${encodeURIComponent(name)}/delete`)
  if (!result.ok) { flash(result.msg || 'Failed to delete tag', 'error'); return }
  if (data.value) {
    data.value.tags = (data.value.tags || []).filter((t) => t.name !== name)
  }
  unusedTags.value = unusedTags.value.filter((t) => t.name !== name)
  if (route.query.tag === name) router.replace({ query: {} })
  flash(result.msg || `Deleted tag ${name}`)
}

function startRename(name) {
  renamingTag.value = name
  renameValue.value = name
}

function cancelRename() {
  renamingTag.value = null
  renameValue.value = ''
}

async function commitRename() {
  const oldName = renamingTag.value
  const newName = renameValue.value.trim().toLowerCase()
  if (!oldName) return
  if (!newName || newName === oldName) { cancelRename(); return }
  const result = await api.post(`/tags/${encodeURIComponent(oldName)}/rename`, { name: newName })
  if (!result.ok || !result.new_name) {
    flash(result.msg || 'Rename failed', 'error')
    return
  }
  if (data.value) {
    const t = (data.value.tags || []).find((tag) => tag.name === oldName)
    if (t) t.name = result.new_name
  }
  const ut = unusedTags.value.find((tag) => tag.name === oldName)
  if (ut) ut.name = result.new_name
  if (route.query.tag === oldName) {
    router.replace({ query: { ...route.query, tag: result.new_name } })
  }
  cancelRename()
  flash(result.msg || `Renamed to ${result.new_name}`)
}

// A selection is either { kind: 'single', file } (a .zip/.md upload) or
// { kind: 'folder', entries: [{ file, path }] } (a whole skill folder,
// each path relative to the picked folder).
function hasSkillMd(entries) {
  return entries.some((e) => e.path.split('/').pop() === 'SKILL.md')
}

function buildImportBody(selection) {
  const fd = new FormData()
  if (selection.kind === 'folder') {
    for (const e of selection.entries) fd.append('file', e.file)
    fd.append('paths', JSON.stringify(selection.entries.map((e) => e.path)))
  } else {
    fd.append('file', selection.file)
  }
  return fd
}

async function doImport(selection, opts = {}) {
  if (!selection) return
  if (selection.kind === 'single' && !/\.(zip|md)$/i.test(selection.file.name)) {
    flash('Import accepts a skill folder, a .zip bundle, or SKILL.md', 'error')
    return
  }
  if (selection.kind === 'folder' && !hasSkillMd(selection.entries)) {
    flash('No SKILL.md found in the selected folder', 'error')
    return
  }
  importUploading.value = true
  try {
    const fd = buildImportBody(selection)
    if (opts.slug) fd.append('slug', opts.slug)
    const headers = {}
    const token = api.getToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
    const url = opts.force ? '/api/patterns/import?force=true' : '/api/patterns/import'
    const res = await fetch(url, {
      method: 'POST', headers, body: fd,
    })
    if (res.status === 401) {
      api.clearAuth()
      window.location.href = '/login'
      return
    }
    const payload = await res.json().catch(() => ({ ok: false, msg: `HTTP ${res.status}` }))
    if (!payload.ok) {
      if (res.status === 409 && payload.conflict) {
        conflictSelection.value = selection
        conflictSlug.value = payload.slug || ''
        conflictMsg.value = payload.msg || 'A pattern with this name already exists.'
        conflictNewSlug.value = ''
        conflictRenaming.value = false
        conflictVisible.value = true
        return
      }
      flash(payload.msg || payload.error || 'Import failed', 'error')
      return
    }
    flash(payload.msg)
    router.push(`/patterns/${payload.slug}`)
  } finally {
    importUploading.value = false
  }
}

async function conflictOverwrite() {
  conflictVisible.value = false
  await doImport(conflictSelection.value, { force: true })
}

async function conflictRename() {
  if (!conflictNewSlug.value.trim()) {
    flash('Please enter a new name', 'error')
    return
  }
  conflictVisible.value = false
  await doImport(conflictSelection.value, { slug: conflictNewSlug.value.trim() })
}

function conflictCancel() {
  conflictVisible.value = false
  conflictSelection.value = null
}

// Folder picker (webkitdirectory): files carry webkitRelativePath.
function onImportPick(ev) {
  const files = Array.from(ev.target.files || [])
  if (files.length) {
    const entries = files.map((f) => ({ file: f, path: f.webkitRelativePath || f.name }))
    doImport({ kind: 'folder', entries })
  }
  ev.target.value = ''
}

// Recursively read a dropped FileSystemEntry into {file, path} pairs.
async function traverseEntry(entry, prefix, out) {
  if (entry.isFile) {
    const file = await new Promise((res, rej) => entry.file(res, rej))
    out.push({ file, path: prefix + entry.name })
    return
  }
  const reader = entry.createReader()
  // readEntries yields in batches; keep reading until it returns empty.
  for (;;) {
    const batch = await new Promise((res, rej) => reader.readEntries(res, rej))
    if (!batch.length) break
    for (const child of batch) await traverseEntry(child, prefix + entry.name + '/', out)
  }
}

async function onImportDrop(ev) {
  importDragging.value = false
  if (importUploading.value) return  // ignore drops while a previous import is in flight
  const dt = ev.dataTransfer
  if (!dt) return
  const entries = (dt.items ? Array.from(dt.items) : [])
    .map((it) => (it.webkitGetAsEntry ? it.webkitGetAsEntry() : null))
    .filter(Boolean)

  // A single dropped .zip/.md file → single-file import (preserves old path).
  if (entries.length === 1 && entries[0].isFile) {
    const f = dt.files?.[0]
    if (f && /\.(zip|md)$/i.test(f.name)) {
      doImport({ kind: 'single', file: f })
      return
    }
  }

  // Otherwise traverse everything (folders + loose files) into a folder upload.
  const out = []
  for (const entry of entries) await traverseEntry(entry, '', out)
  if (out.length) {
    doImport({ kind: 'folder', entries: out })
  } else if (dt.files?.[0]) {
    doImport({ kind: 'single', file: dt.files[0] })
  }
}

// Batch-import-from-folder modal state
const folderVisible = ref(false)
const folderPath = ref('~/.claude/skills')
const folderOnConflict = ref('skip')
const folderScanning = ref(false)
const folderImporting = ref(false)
const folderCandidates = ref([])     // [{name, derived_slug, conflict, error}]
const folderResolvedPath = ref('')   // absolute path the server resolved
const folderResults = ref([])        // [{name, status, slug, ...}] after import
const folderResultPath = ref('')
const folderCounts = ref({})

function openFolderImport() {
  folderVisible.value = true
  folderCandidates.value = []
  folderResults.value = []
  folderCounts.value = {}
  folderResolvedPath.value = ''
  folderResultPath.value = ''
}

function closeFolderImport() {
  if (folderScanning.value || folderImporting.value) return
  folderVisible.value = false
}

async function scanFolder() {
  const path = folderPath.value.trim()
  if (!path) { flash('Enter a folder path', 'error'); return }
  folderScanning.value = true
  folderResults.value = []
  try {
    const payload = await api.post('/patterns/import-dir/scan', { path })
    if (!payload.ok) {
      flash(payload.msg || 'Scan failed', 'error')
      folderCandidates.value = []
      folderResolvedPath.value = ''
      return
    }
    folderCandidates.value = payload.candidates || []
    folderResolvedPath.value = payload.path || ''
    if (!folderCandidates.value.length) {
      flash(`No <name>/SKILL.md found under ${payload.path}`, 'warn')
    }
  } finally {
    folderScanning.value = false
  }
}

async function runFolderImport() {
  if (!folderCandidates.value.length) { flash('Scan first', 'error'); return }
  folderImporting.value = true
  try {
    const payload = await api.post('/patterns/import-dir', {
      path: folderResolvedPath.value || folderPath.value.trim(),
      on_conflict: folderOnConflict.value,
    })
    if (!payload.ok) { flash(payload.msg || 'Import failed', 'error'); return }
    folderResults.value = payload.results || []
    folderCounts.value = payload.counts || {}
    folderResultPath.value = payload.path || ''
    const c = payload.counts || {}
    const imported = (c.imported || 0) + (c.overwritten || 0) + (c.renamed || 0)
    const gritTotal = (payload.results || []).reduce((n, r) => n + ((r.grit_rules || []).length), 0)
    const gritNote = gritTotal ? ` (+${gritTotal} grit rule(s))` : ''
    flash(`Imported ${imported} skill(s) from ${payload.path}${gritNote}`, imported ? 'success' : 'warn')
    await load()
  } finally {
    folderImporting.value = false
  }
}

const folderImportableCount = computed(() => {
  if (!folderCandidates.value.length) return 0
  const importable = folderCandidates.value.filter((c) => !c.error)
  if (folderOnConflict.value === 'skip') {
    return importable.filter((c) => !c.conflict).length
  }
  return importable.length
})

const folderResultGlyph = {
  imported: '+',
  overwritten: '↻',
  renamed: '~',
  skipped: '·',
  failed: '!',
  planned: '·',
}

async function createPattern() {
  if (!newTitle.value.trim()) { flash('Title is required', 'error'); return }
  creating.value = true
  const result = await api.post('/patterns/create', {
    title: newTitle.value.trim(),
    slug: newSlug.value.trim(),
    description: newDescription.value.trim(),
    tags: newTags.value,
  })
  creating.value = false
  if (result.ok) {
    flash(result.msg)
    router.push(`/patterns/${result.slug}`)
  } else {
    flash(result.msg || 'Failed to create', 'error')
  }
}

function setFilter(key, value) {
  const q = {}
  if (value) q[key] = value
  router.push({ query: q })
}

function clearFilters() {
  router.push({ query: {} })
}

// Dense search includes wiki rows (source_kind='wiki') from
// `regin route`. Their slug is `wiki/<repo>/<topic-id>` and they have
// no pattern detail page — clicking should jump to the topics
// workspace scoped to that topic, where the wiki preview already
// lives. Pattern rows route to /patterns/<slug> as before.
function linkFor(d) {
  if (d.source_kind === 'wiki' && d.repo_name) {
    const topicId = (d.slug || '').split('/').slice(2).join('/') || ''
    return {
      path: `/repos/${d.repo_name}/topics`,
      query: { tab: 'wiki', topic: topicId },
    }
  }
  return `/patterns/${d.slug}`
}

const skillBadge = {
  in_sync: { color: 'green', label: 'deployed', scope: 'global' },
  drifted: { color: 'yellow', label: 'out of sync', scope: 'global' },
  source_only: { color: 'purple', label: 'not deployed' },
  project_only: { color: 'green', label: 'deployed', scope: 'project' },
  deployed_only: { color: 'blue', label: 'orphan (source missing)' },
}
</script>

<template>
  <div v-if="loading" class="empty-state">Loading patterns…</div>
  <div v-else>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Library</div>
        <h1 class="page-title">
          Patterns
          <Badge v-if="data.tag_filter" color="blue" :label="`tag: ${data.tag_filter}`" />
          <Badge v-if="data.cat_filter" color="purple" :label="data.cat_filter" />
        </h1>
        <p class="page-subtitle">Curated procedure guides synced from sibling source repos. {{ data.docs.length }} pattern{{ data.docs.length === 1 ? '' : 's' }} shown.</p>
      </div>
      <div class="page-actions">
        <button
          type="button"
          class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500"
          aria-label="Batch import"
          @click="openFolderImport"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M12 11v6"/><path d="M9 14h6"/></svg>
          Batch import
        </button>
        <button
          type="button"
          class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500"
          @click="showCreate = true"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5v14M5 12h14"/></svg>
          New pattern
        </button>
        <input ref="importInput" type="file" webkitdirectory multiple aria-label="Import skill folder" class="hidden" @change="onImportPick">
      </div>
    </header>

    <!-- Filter row -->
    <div class="filter-row">
      <button
        type="button"
        class="filter-toggle focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-open': showCategoryFilter }"
        @click="showCategoryFilter = !showCategoryFilter"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/></svg>
        Category
        <span v-if="data.cat_filter" class="filter-toggle-count">1</span>
      </button>
      <button
        type="button"
        class="filter-toggle focus-visible:outline-2 focus-visible:outline-blue-500"
        :class="{ 'is-open': showTagFilter }"
        @click="toggleTagFilter"
      >
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.6 12.6 12 21 3.4 12.6a5.5 5.5 0 0 1 7.8-7.8L12 5.8l.8-.8a5.5 5.5 0 0 1 7.8 7.8Z"/></svg>
        Tags
        <span class="filter-toggle-count">{{ data.tags.length }}</span>
      </button>
      <button
        v-if="activeFilterCount"
        type="button"
        class="filter-clear focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="clearFilters"
      >
        Clear filters
      </button>
      <span class="toolbar-count">{{ visibleDocs.length }} {{ visibleDocs.length === 1 ? 'pattern' : 'patterns' }}</span>

      <template v-if="features.experimental_dense_search">
        <label class="dense-toggle">
          <input type="checkbox" v-model="denseOn" aria-label="Toggle dense search" />
          Dense search
          <Badge color="amber" label="EXPERIMENTAL" />
        </label>
        <span
          v-if="denseOn && coverage"
          class="coverage-chip"
          :class="{ 'has-drift': coverageHasDrift }"
          :title="`Embedded ${coverage.embedded}/${coverage.total}` +
                  (coverage.unembedded ? ` · ${coverage.unembedded} unembedded` : '') +
                  (coverage.stale ? ` · ${coverage.stale} stale` : '')"
        >
          {{ coverage.embedded }}/{{ coverage.total }} embedded
          <template v-if="coverageHasDrift">
            · <span class="coverage-drift">{{ (coverage.unembedded || 0) + (coverage.stale || 0) }} stale</span>
            <button
              type="button"
              class="coverage-reembed focus-visible:outline-2 focus-visible:outline-blue-500"
              :disabled="reindexing"
              @click="reindex"
            >{{ reindexing ? 'Starting…' : 'Re-embed' }}</button>
          </template>
        </span>
        <input
          v-if="denseOn"
          v-model="denseQuery"
          type="search"
          aria-label="Dense search query"
          placeholder="describe what the pattern should do…"
          class="input dense-input focus-visible:outline-2 focus-visible:outline-blue-500"
          @keyup.enter="runDenseSearch"
        />
        <button
          v-if="denseOn"
          type="button"
          class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500"
          :disabled="denseLoading || !denseQuery.trim()"
          @click="runDenseSearch"
        >
          {{ denseLoading ? 'Routing…' : 'Route' }}
        </button>
      </template>
    </div>
    <div v-if="denseActive && denseError" class="text-amber-700 text-sm mb-2">{{ denseError }}</div>

    <div v-if="showCategoryFilter" class="filter-bar mb-3">
      <span class="filter-chip" :class="{ active: !data.cat_filter }" @click="setFilter('category', null)">All</span>
      <span v-for="c in data.categories" :key="c" class="filter-chip" :class="{ active: data.cat_filter === c }" @click="setFilter('category', c)">{{ c }}</span>
    </div>
    <div v-if="showTagFilter" class="tag-panel mb-3" :class="{ 'is-manage': manageMode }">
      <div class="tag-panel-header">
        <span v-if="manageMode" class="tag-panel-title">Editing tags · double-click name to rename</span>
        <span v-else class="tag-panel-title">Filter patterns by tag</span>
        <button
          v-if="!manageMode"
          type="button"
          class="tag-panel-action focus-visible:outline-2 focus-visible:outline-blue-500"
          @click="enterManageMode"
        >Manage tags →</button>
        <button
          v-else
          type="button"
          class="tag-panel-action is-done focus-visible:outline-2 focus-visible:outline-blue-500"
          @click="exitManageMode"
        >Done</button>
      </div>

      <div v-if="!manageMode" class="filter-bar tag-panel-row">
        <span class="filter-chip" :class="{ active: !data.tag_filter }" @click="setFilter('tag', null)">All</span>
      </div>

      <template v-for="[cat, catTags] in groupedTags" :key="cat">
        <div class="tag-group-header">{{ cat }}</div>
        <div class="filter-bar tag-panel-row">
          <template v-for="t in catTags" :key="t.name">
            <span
              v-if="!manageMode"
              class="filter-chip"
              :class="{ active: data.tag_filter === t.name }"
              @click="setFilter('tag', t.name)"
            >
              {{ t.name }} <span class="filter-chip-count">{{ t.doc_count }}</span>
            </span>
            <span v-else-if="renamingTag === t.name" class="filter-chip is-renaming">
              <input
                v-model="renameValue"
                type="text"
                class="rename-input"
                :aria-label="`Rename ${t.name}`"
                autofocus
                @keyup.enter="commitRename"
                @keyup.esc="cancelRename"
                @blur="commitRename"
              />
            </span>
            <span
              v-else
              class="filter-chip is-editable"
              :title="`Double-click to rename ${t.name}`"
              @dblclick="startRename(t.name)"
            >
              {{ t.name }} <span class="filter-chip-count">{{ t.doc_count }}</span>
              <button
                type="button"
                class="chip-delete focus-visible:outline-2 focus-visible:outline-blue-500"
                :aria-label="`Delete tag ${t.name}`"
                @click.stop="deleteTag(t.name)"
              >&times;</button>
            </span>
          </template>
        </div>
      </template>

      <details v-if="manageMode && unusedTags.length" class="unused-block">
        <summary class="unused-summary focus-visible:outline-2 focus-visible:outline-blue-500">
          {{ unusedTags.length }} unused tag{{ unusedTags.length === 1 ? '' : 's' }}
        </summary>
        <div class="filter-bar tag-panel-row mt-2">
          <button
            v-for="t in unusedTags"
            :key="t.name"
            type="button"
            class="filter-chip is-unused focus-visible:outline-2 focus-visible:outline-blue-500"
            :aria-label="`Delete unused tag ${t.name}`"
            @click="deleteTag(t.name)"
          >
            {{ t.name }} &times;
          </button>
        </div>
      </details>
    </div>

    <!-- Drop zone wrapper -->
    <aside
      class="drop-zone"
      :class="{ 'is-dragging': importDragging }"
      aria-label="Pattern import drop zone"
      @dragenter.prevent="importDragging = true"
      @dragover.prevent="importDragging = true"
      @dragleave.prevent="importDragging = false"
      @drop.prevent="onImportDrop"
    >
      <div v-if="!showCreate" class="drop-target">
        <svg class="drop-target-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12"/></svg>
        <div class="drop-target-copy">
          <p class="drop-target-title">{{ importUploading ? 'Importing…' : 'Drop a skill folder here to import' }}</p>
          <p class="drop-target-hint">
            A skill <strong>folder</strong> (with its <code>scripts/</code>, <code>references/</code>, …), a <code>.zip</code> bundle, or a single <code>SKILL.md</code>. Then Push from the pattern page to deploy.
          </p>
        </div>
        <button
          type="button"
          class="drop-browse focus-visible:outline-2 focus-visible:outline-blue-500"
          :disabled="importUploading"
          aria-label="Browse for a skill folder to import"
          @click="importInput?.click()"
        >
          or browse instead
        </button>
      </div>

      <Card v-if="showCreate">
        <h2 class="card-header">Create new pattern</h2>
        <div class="grid grid-cols-2 gap-4 max-w-2xl">
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">Title</label>
            <input v-model="newTitle" type="text" aria-label="Pattern title" @blur="autoSlug" placeholder="e.g. Distributed Lock with RLockUtil"
              class="input focus-visible:outline-2 focus-visible:outline-blue-500">
          </div>
          <div>
            <label class="block text-sm font-medium text-slate-700 mb-1">Slug</label>
            <input v-model="newSlug" type="text" aria-label="Pattern slug" placeholder="auto-generated from title"
              class="input font-mono focus-visible:outline-2 focus-visible:outline-blue-500">
          </div>
          <div class="col-span-2">
            <label class="block text-sm font-medium text-slate-700 mb-1">Description</label>
            <textarea v-model="newDescription" rows="2" aria-label="Pattern description" placeholder="What this pattern covers…"
              class="input focus-visible:outline-2 focus-visible:outline-blue-500"></textarea>
          </div>
          <div class="col-span-2" v-if="data?.tags?.length">
            <label class="block text-sm font-medium text-slate-700 mb-1">Tags</label>
            <div class="flex flex-wrap gap-1.5">
              <label v-for="t in data.tags" :key="t.name" class="tag-pick"
                :class="{ 'is-active': newTags.includes(t.name) }">
                <input type="checkbox" :value="t.name" v-model="newTags" :aria-label="t.name" class="hidden">
                {{ t.name }}
              </label>
            </div>
          </div>
        </div>
        <div class="mt-4 flex gap-2">
          <button type="button" @click="createPattern" :disabled="creating"
                  class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500">
            {{ creating ? 'Creating…' : 'Create' }}
          </button>
          <button type="button" @click="showCreate = false; newTitle = ''; newSlug = ''; newDescription = ''; newTags = []"
                  class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500">
            Cancel
          </button>
        </div>
      </Card>
    </aside>

    <!-- Batch import folder modal -->
    <Teleport to="body">
      <aside
        v-if="folderVisible"
        class="modal-overlay"
        role="dialog"
        aria-modal="true"
        aria-labelledby="folder-import-title"
        @click.self="closeFolderImport"
        @keydown.esc="closeFolderImport"
      >
        <div class="modal-card modal-card-wide">
          <div class="modal-body">
            <h2 id="folder-import-title" class="modal-title">Batch import skills from folder</h2>
            <p class="modal-text">
              Walks each <code>&lt;folder&gt;/&lt;name&gt;/SKILL.md</code> and imports it as a pattern.
              Path is resolved on the regin server (this machine).
            </p>

            <div class="flex gap-2 mt-3">
              <input
                v-model="folderPath"
                type="text"
                aria-label="Folder path to scan"
                placeholder="~/.claude/skills"
                class="input font-mono flex-1 focus-visible:outline-2 focus-visible:outline-blue-500"
                @keydown.enter.prevent="scanFolder"
              />
              <button type="button" class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500"
                      :disabled="folderScanning || folderImporting"
                      @click="scanFolder">
                {{ folderScanning ? 'Scanning…' : 'Scan' }}
              </button>
            </div>
            <p v-if="folderResolvedPath" class="text-[11px] text-slate-500 mt-1 font-mono">
              resolved → {{ folderResolvedPath }}
            </p>

            <div v-if="folderCandidates.length" class="mt-4 max-h-72 overflow-y-auto border border-slate-200 rounded">
              <table class="tbl tbl-compact">
                <thead>
                  <tr><th>Folder</th><th>Derived slug</th><th>Status</th></tr>
                </thead>
                <tbody>
                  <tr v-for="c in folderCandidates" :key="c.name">
                    <td class="font-mono text-[12px]">{{ c.name }}</td>
                    <td class="font-mono text-[12px]">{{ c.derived_slug || '—' }}</td>
                    <td>
                      <Badge v-if="c.error" color="red" :label="c.error" />
                      <Badge v-else-if="c.conflict" color="yellow" label="already exists" />
                      <Badge v-else color="green" label="new" />
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            <div v-if="folderCandidates.length" class="mt-4">
              <label class="block text-sm font-medium text-slate-700 mb-1">On conflict:</label>
              <div class="flex gap-3 text-sm">
                <label class="inline-flex items-center gap-1.5">
                  <input type="radio" v-model="folderOnConflict" value="skip" aria-label="On conflict: skip" /> Skip
                </label>
                <label class="inline-flex items-center gap-1.5">
                  <input type="radio" v-model="folderOnConflict" value="overwrite" aria-label="On conflict: overwrite" /> Overwrite
                </label>
                <label class="inline-flex items-center gap-1.5">
                  <input type="radio" v-model="folderOnConflict" value="rename" aria-label="On conflict: rename" /> Rename (-2, -3, …)
                </label>
              </div>
            </div>

            <div v-if="folderResults.length" class="mt-4 max-h-72 overflow-y-auto border border-slate-200 rounded">
              <table class="tbl tbl-compact">
                <thead>
                  <tr><th></th><th>Folder</th><th>Slug</th><th>Status</th><th>Note</th></tr>
                </thead>
                <tbody>
                  <tr v-for="r in folderResults" :key="r.name">
                    <td class="font-mono text-[14px] text-slate-500 text-center">
                      {{ folderResultGlyph[r.status] || '?' }}
                    </td>
                    <td class="font-mono text-[12px]">{{ r.name }}</td>
                    <td class="font-mono text-[12px]">{{ r.slug || '—' }}</td>
                    <td>
                      <Badge
                        :color="r.status === 'imported' ? 'green'
                              : r.status === 'overwritten' ? 'blue'
                              : r.status === 'renamed' ? 'purple'
                              : r.status === 'skipped' ? 'gray'
                              : r.status === 'failed' ? 'red' : 'gray'"
                        :label="r.status"
                      />
                    </td>
                    <td class="text-[12px] text-slate-600">
                      {{ r.error || (r.file_count != null ? `${r.file_count} file(s)` : '') }}
                      <span v-if="r.grit_rules && r.grit_rules.length" class="text-emerald-600">
                        +{{ r.grit_rules.length }} grit rule(s)
                      </span>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500"
                    :disabled="folderScanning || folderImporting"
                    @click="closeFolderImport">
              {{ folderResults.length ? 'Close' : 'Cancel' }}
            </button>
            <button v-if="folderCandidates.length && !folderResults.length"
                    type="button"
                    class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500"
                    :disabled="folderImporting || folderImportableCount === 0"
                    @click="runFolderImport">
              {{ folderImporting ? 'Importing…'
                 : `Import ${folderImportableCount} skill${folderImportableCount === 1 ? '' : 's'}` }}
            </button>
          </div>
        </div>
      </aside>
    </Teleport>

    <!-- Conflict dialog -->
    <Teleport to="body">
      <aside
        v-if="conflictVisible"
        class="modal-overlay"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pattern-conflict-title"
        @click.self="conflictCancel"
        @keydown.esc="conflictCancel"
      >
        <div class="modal-card">
          <div class="modal-body">
            <h2 id="pattern-conflict-title" class="modal-title">Pattern already exists</h2>
            <p class="modal-text">{{ conflictMsg }}</p>
            <div v-if="conflictRenaming" class="mt-3">
              <label class="block text-sm font-medium text-slate-700 mb-1">New slug</label>
              <input v-model="conflictNewSlug" type="text" aria-label="New slug" placeholder="e.g. debug-hooks-v2"
                class="input font-mono focus-visible:outline-2 focus-visible:outline-blue-500"
                @keydown.enter.prevent="conflictRename">
            </div>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500" @click="conflictCancel">Cancel</button>
            <button v-if="!conflictRenaming" type="button" class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500" @click="conflictRenaming = true">Rename</button>
            <button v-if="conflictRenaming" type="button" class="btn btn-primary focus-visible:outline-2 focus-visible:outline-blue-500" @click="conflictRename">Import as new name</button>
            <button type="button" class="btn btn-danger focus-visible:outline-2 focus-visible:outline-blue-500" @click="conflictOverwrite">Overwrite</button>
          </div>
        </div>
      </aside>
    </Teleport>

    <!-- Pattern list -->
    <div v-if="!visibleDocs.length" class="card empty-state">
      <p class="mb-2 text-slate-600 text-sm">
        <template v-if="denseActive">No dense results — enter a query and press Enter.</template>
        <template v-else>No patterns match the current filters.</template>
      </p>
      <button v-if="activeFilterCount && !denseActive" type="button" class="btn btn-secondary focus-visible:outline-2 focus-visible:outline-blue-500" @click="clearFilters">Clear filters</button>
    </div>
    <Card v-else :no-padding="true">
      <table class="tbl">
        <thead>
          <tr>
            <th v-if="denseActive">Score</th>
            <th>Title</th>
            <th>Category</th>
            <th>Tags</th>
            <th>Skill state</th>
            <th>Skill scope</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="d in visibleDocs" :key="d.slug">
            <td v-if="denseActive" class="font-mono text-[12px] text-slate-600 whitespace-nowrap">
              {{ d.score?.toFixed(3) }}
            </td>
            <td>
              <router-link :to="linkFor(d)"
                class="table-link focus-visible:outline-2 focus-visible:outline-blue-500">
                {{ d.title }}
              </router-link>
              <div v-if="d.header" class="text-[11px] text-slate-500 mt-0.5 font-mono">
                {{ d.header }}
              </div>
            </td>
            <td><Badge color="purple" :label="d.category" /></td>
            <td>
              <span class="inline-flex flex-wrap gap-1">
                <Badge v-for="tag in (d.tag_names || '').split(', ').filter(Boolean)" :key="tag" color="gray" :label="tag" />
              </span>
            </td>
            <td class="whitespace-nowrap">
              <template v-if="data.skill_states[d.slug]">
                <Badge :color="skillBadge[data.skill_states[d.slug]]?.color || 'gray'"
                       :label="skillBadge[data.skill_states[d.slug]]?.label || data.skill_states[d.slug]" />
              </template>
            </td>
            <td class="whitespace-nowrap">
              <Badge v-if="skillBadge[data.skill_states[d.slug]]?.scope"
                     color="gray" :label="skillBadge[data.skill_states[d.slug]].scope" />
              <span v-else class="text-slate-400 text-[12px]">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </Card>
  </div>
</template>

<style scoped>
.filter-row {
    display: flex;
    align-items: center;
    gap: 0.5rem;
    margin-bottom: 0.75rem;
    flex-wrap: wrap;
}

.filter-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    padding: 0.4375rem 0.75rem;
    font-size: 0.8125rem;
    font-weight: 500;
    color: #475569;
    background: #fff;
    border: 1px solid #E2E8F0;
    border-radius: 0.625rem;
    cursor: pointer;
    transition: background-color 150ms, color 150ms, border-color 150ms;
}

.filter-toggle:hover { background: #F8FAFC; color: #0F172A; }
.filter-toggle.is-open { background: #EFF6FF; color: #1E40AF; border-color: #BFDBFE; }

.filter-toggle-count {
    font-size: 0.625rem;
    padding: 0.0625rem 0.375rem;
    border-radius: 9999px;
    background: #F1F5F9;
    color: #64748B;
    font-weight: 600;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.filter-toggle.is-open .filter-toggle-count { background: #DBEAFE; color: #1E40AF; }

.filter-clear {
    font-size: 0.75rem;
    color: #64748B;
    background: transparent;
    border: 0;
    cursor: pointer;
    padding: 0.25rem 0.5rem;
    border-radius: 0.375rem;
}

.filter-clear:hover { color: #1E40AF; text-decoration: underline; }

.filter-chip-count {
    font-size: 0.625rem;
    color: inherit;
    opacity: 0.7;
    margin-left: 0.125rem;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.drop-zone {
    margin-bottom: 1rem;
}

.drop-target {
    display: flex;
    align-items: center;
    gap: 0.875rem;
    border: 1.5px dashed #CBD5E1;
    border-radius: 0.75rem;
    background: #F8FAFC;
    padding: 0.875rem 1.25rem;
    transition: background-color 150ms, border-color 150ms;
}

.drop-zone.is-dragging .drop-target {
    background: #EFF6FF;
    border-color: #3B82F6;
}

.drop-target-icon {
    flex: none;
    color: #94A3B8;
    transition: color 150ms;
}

.drop-zone.is-dragging .drop-target-icon {
    color: #3B82F6;
}

.drop-target-copy {
    flex: 1 1 auto;
    min-width: 0;
}

.drop-target-title {
    font-size: 0.8125rem;
    font-weight: 600;
    color: #334155;
}

.drop-target-hint {
    font-size: 0.75rem;
    color: #94A3B8;
    line-height: 1.6;
    margin-top: 0.125rem;
}

.drop-target-hint code {
    font-size: 0.6875rem;
    background: #F1F5F9;
    color: #475569;
    padding: 0.0625rem 0.3125rem;
    border-radius: 0.25rem;
}

.drop-browse {
    flex: none;
    font-size: 0.75rem;
    font-weight: 500;
    color: #2563EB;
    background: none;
    border: none;
    cursor: pointer;
    padding: 0.25rem 0.375rem;
    border-radius: 0.375rem;
    transition: background-color 150ms, color 150ms;
}

.drop-browse:hover:not(:disabled) {
    color: #1D4ED8;
    background: #EFF6FF;
}

.drop-browse:disabled {
    color: #94A3B8;
    cursor: default;
}

.input {
    width: 100%;
    border: 1px solid #E2E8F0;
    background: #fff;
    border-radius: 0.5rem;
    padding: 0.5rem 0.75rem;
    font-size: 0.8125rem;
    color: #0F172A;
    outline: none;
    transition: border-color 150ms, box-shadow 150ms;
}

.input:focus {
    border-color: #3B82F6;
    box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.15);
}

/* Tag pick chips (create form) */
.tag-pick {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    font-size: 0.75rem;
    background: #F1F5F9;
    color: #475569;
    border-radius: 0.375rem;
    padding: 0.25rem 0.625rem;
    cursor: pointer;
    transition: background-color 150ms, color 150ms;
}

.tag-pick:hover { background: #E2E8F0; }

.tag-pick.is-active { background: #DBEAFE; color: #1E40AF; font-weight: 500; }

/* Modal */
.modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(15, 23, 42, 0.4);
    backdrop-filter: blur(4px);
    z-index: 50;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 1rem;
}

.modal-card {
    background: #fff;
    border-radius: 1rem;
    max-width: 28rem;
    width: 100%;
    box-shadow: 0 24px 64px rgba(15, 23, 42, 0.25);
    overflow: hidden;
}

.modal-card-wide {
    max-width: 48rem;
}

.tbl-compact th,
.tbl-compact td {
    padding: 0.4rem 0.6rem;
    font-size: 0.8125rem;
}

.modal-body { padding: 1.25rem 1.25rem 1rem; }

.modal-title {
    font-size: 1rem;
    font-weight: 600;
    color: #0F172A;
    margin-bottom: 0.375rem;
}

.modal-text { font-size: 0.875rem; color: #64748B; }

.modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 0.5rem;
    padding: 0.75rem 1.25rem 1rem;
    border-top: 1px solid #F1F5F9;
}

.dense-toggle {
    display: inline-flex;
    align-items: center;
    gap: 0.375rem;
    margin-left: auto;
    font-size: 0.8125rem;
    color: #475569;
    cursor: pointer;
    user-select: none;
}

.dense-input {
    flex: 1 1 18rem;
    min-width: 12rem;
    max-width: 28rem;
}

.coverage-chip {
    display: inline-flex;
    align-items: center;
    gap: 0.3rem;
    font-size: 0.6875rem;
    color: #475569;
    background: #F1F5F9;
    border-radius: 9999px;
    padding: 0.125rem 0.5rem;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
}

.coverage-chip.has-drift {
    background: #FEF3C7;
    color: #92400E;
}

.coverage-drift { font-weight: 600; }

.coverage-reembed {
    font-size: 0.6875rem;
    color: #1E40AF;
    background: transparent;
    border: 0;
    cursor: pointer;
    padding: 0 0.25rem;
    text-decoration: underline;
}

.coverage-reembed:disabled { color: #94A3B8; cursor: default; text-decoration: none; }

/* Tag filter panel */
.tag-panel {
    border: 1px solid #E2E8F0;
    border-radius: 0.75rem;
    padding: 0.625rem 0.875rem 0.75rem;
    background: #fff;
    transition: border-color 150ms, background-color 150ms;
}
.tag-panel.is-manage {
    border-color: #F59E0B;
    box-shadow: 0 0 0 1px #FDE68A inset;
}
.tag-panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.5rem;
    margin-bottom: 0.25rem;
}
.tag-panel-title {
    font-size: 0.6875rem;
    font-weight: 500;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: #64748B;
}
.tag-panel.is-manage .tag-panel-title { color: #92400E; }
.tag-panel-action {
    font-size: 0.75rem;
    color: #1E40AF;
    background: transparent;
    border: 0;
    cursor: pointer;
    padding: 0.125rem 0.375rem;
    border-radius: 0.375rem;
}
.tag-panel-action:hover { text-decoration: underline; }
.tag-panel-action.is-done { color: #047857; font-weight: 500; }

.tag-panel-row {
    padding: 0.25rem 0;
    margin: 0;
}

.tag-group-header {
    font-size: 0.75rem;
    font-weight: 600;
    color: #1E40AF;
    margin: 0.5rem 0 0.125rem;
}
.tag-panel.is-manage .tag-group-header { color: #92400E; }

.filter-chip.is-editable {
    display: inline-flex;
    align-items: center;
    gap: 0.25rem;
    background: #fff;
    border: 1px solid #FDE68A;
    padding: 0.25rem 0.375rem 0.25rem 0.625rem;
    cursor: default;
}
.filter-chip.is-editable:hover { background: #FEF3C7; color: #475569; }

.chip-delete {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 1.125rem;
    height: 1.125rem;
    border-radius: 9999px;
    border: 0;
    background: transparent;
    color: #B91C1C;
    font-size: 0.875rem;
    line-height: 1;
    cursor: pointer;
    padding: 0;
}
.chip-delete:hover { background: #FECACA; }

.filter-chip.is-renaming {
    padding: 0.125rem 0.375rem;
    background: #fff;
    border: 1px solid #3B82F6;
    cursor: text;
}
.rename-input {
    width: 10rem;
    border: 0;
    outline: none;
    font: inherit;
    font-size: 0.8125rem;
    color: #0F172A;
    background: transparent;
    padding: 0;
}

.filter-chip.is-unused {
    background: #FECACA;
    color: #B91C1C;
    border: 0;
    font-weight: 500;
}
.filter-chip.is-unused:hover { background: #FCA5A5; color: #7F1D1D; }

.unused-block { margin-top: 0.75rem; }
.unused-summary {
    font-size: 0.75rem;
    color: #64748B;
    cursor: pointer;
    list-style: none;
    padding: 0.25rem 0;
    border-radius: 0.25rem;
}
.unused-summary:hover { color: #0F172A; }
.unused-summary::marker, .unused-summary::-webkit-details-marker { display: none; }
</style>
