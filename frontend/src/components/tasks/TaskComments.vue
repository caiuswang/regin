<script setup>
// The conversation about one task.
//
// Threads are flat and resolution is its own axis: marking a conversation
// resolved says nothing about whether the work shipped, which is the task's
// own status. Two axes, two controls, and neither moves the other.
//
// A pasted or dropped image is held in the browser until Post and rides the
// same request as the text, so the server writes the comment and its images
// in one transaction and a discarded draft never reaches it at all. The draft
// numbers its images from 1, the same scope a posted comment resolves in.
import { computed, onMounted, ref, watch } from 'vue'
import api from '../../api'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import MarkdownEditor from '../editor/MarkdownEditor.vue'
import TaskCommentBody from './TaskCommentBody.vue'
import TaskImageStrip from './TaskImageStrip.vue'
import { useAttachedImages } from '../../composables/useAttachedImages.js'
import { imageFilesFrom, toBase64 } from '../../utils/imageFiles'
import { localStamp, utcTitle } from '../../utils/time.js'

const props = defineProps({
  taskId: { type: Number, required: true },
  canWrite: { type: Boolean, default: false },
  project: { type: Number, default: null },
  feature: { type: Number, default: null },
})

const threads = ref([])
const draft = ref('')
const busy = ref(false)
const error = ref('')
const failed = ref('')
const editor = ref(null)
const {
  items: images, error: imagesError,
  add: addImages, remove: removeImage, reset: resetImages,
} = useAttachedImages('tasks')
const hasDraft = computed(() => draft.value.trim() !== '' || images.value.length > 0)

async function load() {
  error.value = ''
  const body = await api.get(`/tasks/${props.taskId}/comments`)
  if (body?.ok === false) {
    error.value = body.msg || 'could not read comments'
    return
  }
  threads.value = body.threads || []
}

async function takeImages(source, event) {
  editor.value?.insertImages(await addImages(source), event)
}

const attach = (source) => addImages(source)

async function onPaste(e) {
  if (!imageFilesFrom(e.clipboardData).length) return
  e.preventDefault()
  await takeImages(e.clipboardData, e)
}

async function onDrop(e) {
  if (!imageFilesFrom(e.dataTransfer).length) return
  e.preventDefault()
  await takeImages(e.dataTransfer, e)
}

// The server refuses a body citing an image it was not sent, so a held image
// leaves the draft together with its citation.
function unhold(item) {
  const cite = new RegExp(`\\n?!\\[[^\\]]*\\]\\(image:${item.idx}\\)\\n?`, 'g')
  draft.value = draft.value.replace(cite, '\n')
  removeImage(item)
}

function discard() {
  draft.value = ''
  failed.value = ''
  resetImages()
}

async function post(threadId = null) {
  if (busy.value || !hasDraft.value) return
  busy.value = true
  failed.value = ''
  try {
    const staged = await Promise.all(images.value.map(async (img) => (
      { idx: img.idx, data_b64: await toBase64(img.file) })))
    const body = await api.post(`/tasks/${props.taskId}/comments`, {
      body: draft.value.trim(),
      ...(staged.length ? { images: staged } : {}),
      ...(threadId ? { thread_id: threadId } : {}),
    })
    if (body?.ok === false) {
      failed.value = body.msg || 'the comment was refused'
      return
    }
    discard()
    await load()
  } catch (e) {
    failed.value = e?.message || 'the comment was not sent'
  } finally {
    busy.value = false
  }
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

watch(() => props.taskId, () => { discard(); load() })
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
          <TaskCommentBody class="tc-body" :comment="comment" :project="project" />
          <span class="tc-when" :title="utcTitle(comment.created_at)">{{ localStamp(comment.created_at) }}</span>
        </div>

        <Button
          v-if="canWrite && hasDraft"
          variant="ghost" size="sm"
          :data-testid="`task-thread-reply-${thread.id}`"
          @click="post(thread.id)"
        >Reply here</Button>
      </li>
    </ul>

    <form
      v-if="canWrite" class="tc-form"
      @submit.prevent="post()" @paste="onPaste" @dragover.prevent @drop="onDrop"
    >
      <MarkdownEditor
        ref="editor"
        v-model="draft" class="tc-input"
        :project="project" :feature="feature"
        :images="images" :attach="attach"
        :expandable="false" :outline="false" mode-scope="compose"
        preview-testid="task-comment-draft-rich"
        :rows="2"
        placeholder="Add a comment"
        aria-label="Add a comment"
        data-testid="task-comment-draft"
        @submit="post()"
        @attach-error="failed = $event"
      />
      <TaskImageStrip
        v-if="images.length || imagesError"
        :items="images" :error="imagesError"
        @pick="takeImages" @remove="unhold"
      />
      <p v-if="failed" class="tc-error" role="alert" data-testid="task-comment-error">{{ failed }}</p>
      <div class="tc-form-actions">
        <Button
          v-if="hasDraft"
          variant="ghost" size="sm" :disabled="busy"
          data-testid="task-comment-discard"
          @click="discard"
        >Discard</Button>
        <Button
          type="submit" variant="secondary" size="sm"
          :disabled="busy || !hasDraft"
          data-testid="task-comment-submit"
        >{{ busy ? 'Posting…' : 'Comment' }}</Button>
      </div>
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
.tc-body { flex: 1 1 12rem; min-width: 0; overflow-wrap: anywhere; }
.tc-when {
    font-size: 11px; color: var(--color-fg-faint);
    font-variant-numeric: tabular-nums;
}
.tc-empty, .tc-error {
    margin: 0; font-size: 0.8125rem; color: var(--color-fg-subtle);
}
.tc-error { color: var(--color-danger-fg, var(--color-fg-muted)); }
.tc-form { display: flex; flex-direction: column; gap: 0.4rem; margin-top: 0.6rem; }
.tc-input { min-width: 0; font-size: 0.8125rem; }
.tc-form-actions { display: flex; justify-content: flex-end; gap: 0.4rem; }
</style>
