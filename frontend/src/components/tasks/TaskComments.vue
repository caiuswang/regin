<script setup>
// The conversation about one task.
//
// Threads are flat and resolution is its own axis: marking a conversation
// resolved says nothing about whether the work shipped, which is the task's
// own status. Two axes, two controls, and neither moves the other.
import { onMounted, ref, watch } from 'vue'
import api from '../../api'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import Textarea from '../ui/Textarea.vue'

const props = defineProps({
  taskId: { type: Number, required: true },
  canWrite: { type: Boolean, default: false },
})

const threads = ref([])
const draft = ref('')
const busy = ref(false)
const error = ref('')

async function load() {
  error.value = ''
  const body = await api.get(`/tasks/${props.taskId}/comments`)
  if (body?.ok === false) {
    error.value = body.msg || 'could not read comments'
    return
  }
  threads.value = body.threads || []
}

async function post(threadId = null) {
  const text = draft.value.trim()
  if (!text) return
  busy.value = true
  const body = await api.post(`/tasks/${props.taskId}/comments`,
    { body: text, ...(threadId ? { thread_id: threadId } : {}) })
  busy.value = false
  if (body?.ok === false) {
    error.value = body.msg || 'the comment was refused'
    return
  }
  draft.value = ''
  await load()
}

async function setResolution(thread, state) {
  const body = await api.patch(
    `/tasks/${props.taskId}/comments/${thread.id}`, { resolution_state: state })
  if (body?.ok === false) {
    error.value = body.msg || 'could not change the thread'
    return
  }
  await load()
}

watch(() => props.taskId, load)
onMounted(load)
</script>

<template>
  <Card data-testid="task-comments">
    <h2 class="card-header">Comments</h2>

    <p v-if="error" class="tc-error" role="alert">{{ error }}</p>

    <p v-if="!threads.length" class="tc-empty" data-testid="task-comments-empty">
      No one has commented on this task.
    </p>

    <ul v-else class="tc-threads">
      <li
        v-for="thread in threads"
        :key="thread.id"
        class="tc-thread"
        :data-testid="`task-thread-${thread.id}`"
      >
        <header class="tc-thread-head">
          <Badge
            :color="thread.resolution_state === 'resolved' ? 'green' : 'gray'"
            :label="thread.resolution_state"
          />
          <Button
            v-if="canWrite"
            variant="ghost" size="sm"
            :data-testid="`task-thread-resolve-${thread.id}`"
            @click="setResolution(thread,
                                  thread.resolution_state === 'resolved'
                                    ? 'open' : 'resolved')"
          >{{ thread.resolution_state === 'resolved' ? 'Reopen' : 'Resolve' }}</Button>
        </header>

        <div
          v-for="comment in thread.comments"
          :key="comment.id"
          class="tc-comment"
        >
          <span class="tc-author">{{ comment.author_kind }}</span>
          <span class="tc-body">{{ comment.body }}</span>
          <span class="tc-when">{{ comment.created_at }}</span>
        </div>

        <Button
          v-if="canWrite && draft.trim()"
          variant="ghost" size="sm"
          :data-testid="`task-thread-reply-${thread.id}`"
          @click="post(thread.id)"
        >Reply here</Button>
      </li>
    </ul>

    <form v-if="canWrite" class="tc-form" @submit.prevent="post()">
      <Textarea
        v-model="draft"
        class="tc-input"
        :rows="2"
        placeholder="Add a comment"
        aria-label="Add a comment"
        data-testid="task-comment-draft"
      />
      <Button
        type="submit" variant="secondary" size="sm"
        :disabled="busy || !draft.trim()"
        data-testid="task-comment-submit"
      >{{ busy ? 'Posting…' : 'Comment' }}</Button>
    </form>
  </Card>
</template>

<style scoped>
.tc-threads { list-style: none; margin: 0; padding: 0;
              display: flex; flex-direction: column; gap: 0.6rem; }
.tc-thread {
    border: 1px solid var(--color-border); border-radius: var(--radius-lg);
    padding: 0.5rem 0.6rem; background: var(--color-surface-2);
}
.tc-thread-head {
    display: flex; align-items: center; gap: 0.4rem; margin-bottom: 0.3rem;
}
.tc-comment {
    display: flex; gap: 0.5rem; align-items: baseline; flex-wrap: wrap;
    font-size: 0.8125rem; padding: 0.15rem 0;
}
.tc-author { font-weight: 600; color: var(--color-fg-muted); font-size: 11.5px; }
.tc-body { flex: 1; min-width: 0; overflow-wrap: anywhere; }
.tc-when {
    font-size: 11px; color: var(--color-fg-faint);
    font-variant-numeric: tabular-nums;
}
.tc-empty, .tc-error {
    margin: 0; font-size: 0.8125rem; color: var(--color-fg-subtle);
}
.tc-error { color: var(--color-danger-fg, var(--color-fg-muted)); }
.tc-form { display: flex; gap: 0.4rem; margin-top: 0.6rem; align-items: flex-start; }
.tc-input { flex: 1; min-width: 0; font-size: 0.8125rem; }
</style>
