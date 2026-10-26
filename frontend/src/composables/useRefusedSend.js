import { ref } from 'vue'
import api from '../api'

// A send to a task run that has already reported finished is refused with
// `reason: 'run_sealed'` and the task it belongs to. The text has nowhere left
// to go in this session, so the composer offers to post it as a comment on
// that task instead: an open thread is what the task's next run reads back
// as its <followup>.
export function useRefusedSend() {
  const refusal = ref(null)
  const posting = ref(false)
  const postError = ref('')
  const posted = ref(null)

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

  return { refusal, posting, postError, posted, take, clear, postAsComment }
}
