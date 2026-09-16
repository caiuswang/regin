// The text half of re-editing a saved idea.
//
// Separate from `useTasks` because that composable owns the FEED — the filter,
// the cursor, the capture draft — and an edit sheet needs none of it. It owns
// one row's draft and the PATCH that settles it, and the refusal it renders is
// the server's own sentence: `parse_update` names the offending field, and
// re-wording it here would hide which one.
import { ref } from 'vue'
import api from '../api'
import { TITLE_MAX } from './useTasks.js'

function draftFrom(task) {
  return {
    title: task.title || '',
    body: task.body || '',
    tags: (task.tags || []).join(', '),
    priority: task.priority || 'none',
    // '' is the backlog, and a Select round-trips strings — the save converts
    // it back to the null the server reads as "unfile this".
    project_id: task.project_id == null ? '' : String(task.project_id),
    // Same round-trip for the grain: '' is top-level, and clearing it is the
    // detach — an act, not an omission.
    parent_id: task.parent_id == null ? '' : String(task.parent_id),
  }
}

function parseTags(raw) {
  return String(raw || '')
    .split(',')
    .map(t => t.trim())
    .filter(Boolean)
    .slice(0, 10)
}

export function useTaskEdit(task) {
  const draft = ref(draftFrom(task))
  const saving = ref(false)
  const editError = ref('')

  function localError() {
    const title = draft.value.title.trim()
    if (!title) return 'A title is required.'
    if (title.length > TITLE_MAX) {
      return `Title must be ${TITLE_MAX} characters or fewer — this one is ${title.length}.`
    }
    return ''
  }

  /**
   * PATCH the whole sheet. Every field is sent, including an emptied one:
   * clearing the details — or the project — is an edit, and omitting the key
   * would read as "leave it".
   */
  async function save() {
    editError.value = localError()
    if (editError.value) return false
    saving.value = true
    const result = await api.patch(`/tasks/${task.id}`, {
      title: draft.value.title.trim(),
      body: draft.value.body.trim(),
      tags: parseTags(draft.value.tags),
      priority: draft.value.priority,
      project_id: draft.value.project_id
        ? Number(draft.value.project_id)
        : null,
      parent_id: draft.value.parent_id
        ? Number(draft.value.parent_id)
        : null,
    })
    saving.value = false
    if (result.ok === false) {
      editError.value = result.msg || 'The server refused this edit.'
      return false
    }
    return true
  }

  return { draft, saving, editError, save }
}
