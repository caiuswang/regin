import { ref } from 'vue'
import { useRouter } from 'vue-router'
import api from '../api'
import { useFlash } from './useFlash'

// Extracted from PatternsView. Owns the single-file / drag-drop skill import
// flow and the shared conflict-resolution dialog state. The conflict refs are
// shared: doImport writes them on a 409, and conflictOverwrite/Rename call back
// into doImport — so this composable is the single owner of that state and the
// SFC wires the inline dialog to these same refs.
export function useSkillImport() {
  const { flash } = useFlash()
  const router = useRouter()

  const importDragging = ref(false)
  const importUploading = ref(false)
  const importInput = ref(null)

  const conflictVisible = ref(false)
  const conflictMsg = ref('')
  const conflictSlug = ref('')
  const conflictSelection = ref(null)
  const conflictRenaming = ref(false)
  const conflictNewSlug = ref('')

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

  // A 409 came back with a conflict — open the inline dialog primed for this
  // selection. Split out of doImport so doImport stays under the CC threshold.
  function enterConflictMode(payload, selection) {
    conflictSelection.value = selection
    conflictSlug.value = payload.slug || ''
    conflictMsg.value = payload.msg || 'A pattern with this name already exists.'
    conflictNewSlug.value = ''
    conflictRenaming.value = false
    conflictVisible.value = true
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
          enterConflictMode(payload, selection)
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

  return {
    importDragging,
    importUploading,
    importInput,
    conflictVisible,
    conflictMsg,
    conflictSlug,
    conflictSelection,
    conflictRenaming,
    conflictNewSlug,
    doImport,
    conflictOverwrite,
    conflictRename,
    conflictCancel,
    onImportPick,
    onImportDrop,
  }
}
