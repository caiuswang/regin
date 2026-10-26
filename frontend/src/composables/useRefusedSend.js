import { ref } from 'vue'
import api from '../api'

// A send to a task run that has already reported finished is refused with
// `reason: 'run_sealed'` and the task it belongs to. Two ways on, because they
// answer different wishes: resume the same conversation with the draft as its
// next prompt (the resume lifts the seal server-side), or post the draft as a
// task comment, which the task's NEXT run reads back as its <followup> — a
// fresh conversation, not this one.
export function useRefusedSend() {
  const refusal = ref(null)
  const posting = ref(false)
  const postError = ref('')
  const posted = ref(null)
  const resuming = ref(false)

  function take(res) {
    posted.value = null
    postError.value = ''
    refusal.value = res?.reason === 'run_sealed' && res.task_id ? res : null
    return !!refusal.value
  }

  function clear() {
    refusal.value = null
    posted.value = null
    postError.value = ''
    posting.value = false
    resuming.value = false
  }

  // Resolves true once the continuation is launched with `text` as its first
  // prompt. Refused while the sealed session is still closing — the server
  // will not run two processes on one conversation — and that refusal is
  // shown as is.
  async function resumeWith(text) {
    const r = refusal.value
    if (!r?.resume?.session_id || resuming.value || posting.value || !text) return false
    resuming.value = true
    postError.value = ''
    let res = null
    try {
      res = await api.post('/agent-runs', {
        resume: r.resume.session_id,
        cwd: r.resume.cwd || undefined,
        model: r.resume.model || undefined,
        prompt: text,
      })
    } catch (e) {
      res = { launched: false, detail: e?.message }
    }
    resuming.value = false
    if (refusal.value !== r) return false
    if (!res?.launched) {
      postError.value = res?.detail || res?.error || res?.msg || 'the session was not resumed'
      return false
    }
    refusal.value = null
    return true
  }

  // Resolves true once the comment exists; the caller owns the draft and
  // clears it only then.
  async function postAsComment(text) {
    const r = refusal.value
    if (!r || posting.value || !text) return false
    posting.value = true
    postError.value = ''
    let res = null
    try {
      res = await api.post(`/tasks/${r.task_id}/comments`, { body: text })
    } catch (e) {
      res = { ok: false, msg: e?.message }
    }
    posting.value = false
    // Cleared mid-flight (the composer moved to another session): the draft
    // in front of the operator is no longer the one that was posted.
    if (refusal.value !== r) return false
    if (!res?.thread) {
      postError.value = res?.msg || 'the comment was not posted'
      return false
    }
    posted.value = { taskId: r.task_id, threadId: res.thread.id,
                     href: `/tasks/${r.task_id}#task-thread-${res.thread.id}` }
    refusal.value = null
    return true
  }

  return { refusal, posting, resuming, postError, posted, take, clear, postAsComment, resumeWith }
}
