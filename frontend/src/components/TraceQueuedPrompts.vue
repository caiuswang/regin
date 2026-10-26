<script setup>
// Queued / steering prompts on the trace session page — the same server rows
// the /live card shows (`_queued_prompts`), with the same write surface.
//
// Controls follow the ROW, never the page: an id is what the edit/remove
// routes name, and only regin's own SDK queue hands one out. A
// transcript-derived row has no id (that queue lives inside Claude Code,
// which offers no write path) and a bridge steer's `b<row>` id can only
// dismiss its chip — the keystrokes were already typed into the pane, so an
// edit there would be a control that silently changes nothing.
//
// The draft lives here rather than in a per-row child so a poll that briefly
// serves an empty queue can't unmount it and discard what the operator typed;
// the parent owns the round trip, because a refused edit has to keep the row
// and say why.
import { ref, computed, watch, nextTick } from 'vue'
import Button from './ui/Button.vue'
import Textarea from './ui/Textarea.vue'

const props = defineProps({
  items: { type: Array, default: () => [] },
  // Ids with a request in flight — the row goes inert rather than letting a
  // double-tap fire a second edit or remove against the same entry.
  busyIds: { type: Object, default: () => new Set() },
  notice: { type: String, default: '' },
  // The session's regin-bridge mod is listening, so its running turn can be
  // aborted and Claude Code goes straight on to this queue.
  canSendNow: { type: Boolean, default: false },
  sending: { type: Boolean, default: false },
  // A composer is mounted on this page to take a dropped row's text back.
  canRestore: { type: Boolean, default: false },
})
const emit = defineEmits(['edit', 'remove', 'send-now', 'restore'])

// A dropped row is not waiting for anything: the session abandoned it after
// acking the send, so it is the operator's last copy of that text and reads
// as a loss, never as one more prompt in line.
const queuedItems = computed(() => props.items.filter(q => q.source !== 'dropped'))
const droppedItems = computed(() => props.items.filter(q => q.source === 'dropped'))
const dropReason = q => (q.detail || '').replace(/^dropped:\s*/, '') || 'dropped'

const editingId = ref('')
const draft = ref('')
// A `ref` inside v-for collects into an ARRAY, so the editor's element comes
// through a function ref instead — keyed by the row's id, because Vue patches
// the list in index order: opening a row ABOVE the open one mounts the new
// editor first and only then unmounts the old, whose `null` would otherwise
// land last and wipe the ref we are about to focus.
const inputEl = ref(null)
function setInput(id, el) {
  if (id !== editingId.value) return
  inputEl.value = el?.$el || el
}

// A row the agent has consumed takes its editor with it. Left open, the draft
// could be saved onto whatever row inherits the id — sdk ids are a per-runner
// counter (`q1`, `q2`, …) that restarts when a runner is relaunched on the
// same trace, so a stale editor is not merely cosmetic. The queue is
// server-authoritative: a row's absence means it is gone, not blipping.
watch(() => props.items, (rows) => {
  if (editingId.value && !rows.some(q => q.id === editingId.value)) close()
})

function open(item) {
  // The RAW content: the row displays it verbatim, so there is nothing to
  // strip, and seeding from anything else would rewrite the operator's text.
  draft.value = item.content || ''
  editingId.value = item.id
  nextTick(() => inputEl.value?.focus?.())
}

function close() {
  editingId.value = ''
  draft.value = ''
}

function save(item) {
  if (!draft.value.trim()) return
  emit('edit', { id: item.id, text: draft.value.trim(), done: close })
}
</script>

<template>
  <div v-if="droppedItems.length"
       class="mb-3 rounded-lg border border-red-200 bg-red-50 px-3 py-2"
       data-testid="trace-dropped">
    <div class="mb-1 text-[11px] font-semibold uppercase tracking-wider text-red-700">
      ✗ {{ droppedItems.length }} dropped — never reached the agent
    </div>
    <div v-if="notice && !queuedItems.length"
         class="mb-1 text-[11px] text-red-600"
         data-testid="trace-queued-notice">✗ {{ notice }}</div>
    <div v-for="q in droppedItems" :key="q.id"
         class="flex items-start gap-2 py-0.5"
         data-testid="trace-dropped-item">
      <div class="min-w-0 flex-1">
        <div class="truncate text-sm text-red-900" :title="q.content">{{ q.content }}</div>
        <div class="text-[11px] text-red-700" data-testid="trace-dropped-reason">{{ dropReason(q) }}</div>
      </div>
      <Button
        v-if="canRestore"
        variant="link"
        size="sm"
        class="shrink-0 text-[11px] text-red-700"
        :disabled="busyIds.has(q.id)"
        data-testid="trace-dropped-restore"
        @click="emit('restore', { id: q.id, text: q.content })"
      >Restore to composer</Button>
      <Button
        variant="link"
        size="sm"
        class="shrink-0 text-[11px] text-slate-500 hover:text-red-600"
        aria-label="Dismiss this dropped prompt"
        :disabled="busyIds.has(q.id)"
        data-testid="trace-dropped-dismiss"
        @click="emit('remove', { id: q.id })"
      >✕</Button>
    </div>
  </div>
  <div v-if="queuedItems.length"
       class="mb-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2"
       data-testid="trace-queued">
    <div class="mb-1 flex items-center gap-3">
      <div class="text-[11px] font-semibold uppercase tracking-wider text-amber-700">
        ⏳ {{ queuedItems.length }} queued
      </div>
      <Button
        v-if="canSendNow"
        variant="link"
        size="sm"
        class="text-[11px] text-amber-700"
        title="Interrupt the running turn so the queued prompts run now"
        :disabled="sending"
        data-testid="trace-queued-send-now"
        @click="emit('send-now')"
      >send now</Button>
    </div>
    <div v-if="notice"
         class="mb-1 text-[11px] text-red-600"
         data-testid="trace-queued-notice">✗ {{ notice }}</div>
    <div v-for="(q, qi) in queuedItems" :key="q.id || `${qi}-${(q.content || '').slice(0, 24)}`"
         class="flex items-start gap-2 py-0.5"
         data-testid="trace-queued-item">
      <template v-if="editingId && editingId === q.id">
        <div class="min-w-0 flex-1">
          <Textarea
            :ref="el => setInput(q.id, el)"
            v-model="draft"
            :rows="2"
            aria-label="Edit this queued prompt"
            class="w-full text-sm"
            :disabled="busyIds.has(q.id)"
            data-testid="trace-queued-input"
            @keydown.enter.exact.prevent="save(q)"
            @keydown.esc.prevent="close"
          />
          <div class="mt-1 flex gap-3">
            <Button
              variant="link"
              size="sm"
              class="text-[11px] text-amber-700"
              :disabled="!draft.trim() || busyIds.has(q.id)"
              data-testid="trace-queued-save"
              @click="save(q)"
            >save</Button>
            <Button
              variant="link"
              size="sm"
              class="text-[11px] text-slate-500"
              :disabled="busyIds.has(q.id)"
              data-testid="trace-queued-cancel-edit"
              @click="close"
            >cancel</Button>
          </div>
        </div>
      </template>

      <template v-else>
        <div class="min-w-0 flex-1 truncate text-sm text-amber-900" :title="q.content">
          {{ q.content }}
        </div>
        <Button
          v-if="q.id && q.source !== 'bridge'"
          variant="link"
          size="sm"
          class="shrink-0 text-[11px] text-amber-700"
          aria-label="Edit this queued prompt"
          :disabled="busyIds.has(q.id)"
          data-testid="trace-queued-edit"
          @click="open(q)"
        >edit</Button>
        <Button
          v-if="q.id"
          variant="link"
          size="sm"
          class="shrink-0 text-[11px] text-slate-500 hover:text-red-600"
          aria-label="Remove this queued prompt"
          :disabled="busyIds.has(q.id)"
          data-testid="trace-queued-remove"
          @click="emit('remove', { id: q.id })"
        >✕</Button>
      </template>
    </div>
  </div>
</template>
