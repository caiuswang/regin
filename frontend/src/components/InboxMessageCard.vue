<script setup>
import { computed, ref, onMounted, nextTick } from 'vue'
import MarkdownContent from './MarkdownContent.vue'
import Button from './ui/Button.vue'
import { useCopy } from '../composables/useCopy.js'

const props = defineProps({
  message: { type: Object, required: true },
})
const emit = defineEmits(['open', 'dismiss', 'read'])

// Some messages (e.g. a progress dump with an embedded code block) are far
// taller than a triage card should be, which wrecks the inbox grid. Clamp
// the body and fade it out when it overflows; the session link below always
// reaches the full text.
const bodyEl = ref(null)
const overflowing = ref(false)
onMounted(async () => {
  await nextTick()
  const el = bodyEl.value
  if (el) overflowing.value = el.scrollHeight - el.clientHeight > 4
})

const { copyText } = useCopy()

// `send_to_user` links are "file paths / URLs". Only some of those are
// navigable from the web inbox: external URLs open in a new tab, and an
// in-app absolute path (e.g. /trace/sessions/x) routes within the SPA.
// A repo-relative file path (lib/foo.py, ARCHITECTURE.md) has no server
// route, so rendering it as an <a href> produced a dead link — classify
// it as `file` and offer copy-to-clipboard instead of a broken anchor.
function linkKind(href) {
  if (/^(https?:|mailto:)/i.test(href || '')) return 'external'
  if ((href || '').startsWith('/')) return 'route'
  return 'file'
}
const classifiedLinks = computed(() =>
  (props.message.links || []).map(lnk => ({ ...lnk, kind: linkKind(lnk.href) })))

// type → pill palette. Ordered by severity in the store; here we only
// need the visual mapping.
const TYPE_STYLES = {
  progress: { label: 'Progress', cls: 'bg-slate-100 text-slate-600' },
  note: { label: 'Note', cls: 'bg-slate-100 text-slate-600' },
  lesson: { label: 'Lesson', cls: 'bg-violet-100 text-violet-700' },
  result: { label: 'Result', cls: 'bg-emerald-100 text-emerald-700' },
  summary: { label: 'Summary', cls: 'bg-indigo-100 text-indigo-700' },
  warning: { label: 'Warning', cls: 'bg-amber-100 text-amber-800' },
  blocker: { label: 'Blocker', cls: 'bg-red-100 text-red-700' },
}

const typeStyle = computed(
  () => TYPE_STYLES[props.message.msg_type] || TYPE_STYLES.progress)
const isUnread = computed(() => !props.message.read_at)
// Only persisted rows (numeric id) can be marked read; legacy span-derived
// messages carry id == null and expose no action.
const canMarkRead = computed(
  () => isUnread.value && typeof props.message.id === 'number')
const isAttention = computed(
  () => ['warning', 'blocker'].includes(props.message.msg_type))
// Trace ids that group system-event cards but are NOT navigable sessions
// (content-drift lives under "wiki-debt") — routing to /trace/sessions/<id>
// would show a blank pane. Keep in sync with lib/agent_messages/events.py
// NON_SESSION_TRACE_IDS. Such cards carry their own action links instead.
const NON_SESSION_TRACES = new Set(['wiki-debt'])
const sessionHref = computed(() => {
  const traceId = props.message.trace_id
  if (!traceId || NON_SESSION_TRACES.has(traceId)) return null
  const base = `/trace/sessions/${traceId}`
  return props.message.span_id ? `${base}?span=${props.message.span_id}` : base
})
const timeLabel = computed(() => {
  if (!props.message.created_at) return ''
  return new Date(props.message.created_at).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
})
</script>

<template>
  <div
    class="rounded-lg border bg-white px-4 py-3 shadow-sm transition-colors"
    :class="[
      isUnread ? 'border-blue-300' : 'border-slate-200',
      isAttention ? 'ring-1 ring-amber-200' : '',
    ]"
  >
    <div class="flex items-center gap-2 mb-1.5">
      <span
        v-if="isUnread"
        class="h-2 w-2 rounded-full bg-blue-500 shrink-0"
        aria-label="Unread"
      ></span>
      <span
        class="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded"
        :class="typeStyle.cls"
      >{{ typeStyle.label }}</span>
      <span v-if="message.title" class="text-sm font-semibold text-slate-800 truncate">
        {{ message.title }}
      </span>
      <span class="ml-auto text-[11px] font-mono text-slate-400 shrink-0">{{ timeLabel }}</span>
    </div>

    <div
      ref="bodyEl"
      class="relative text-sm text-slate-800 leading-relaxed max-h-72 overflow-hidden"
    >
      <MarkdownContent :markdown="message.body" />
      <div
        v-if="overflowing"
        class="pointer-events-none absolute inset-x-0 bottom-0 h-12 bg-gradient-to-t from-white to-transparent"
      ></div>
    </div>
    <router-link
      v-if="overflowing && sessionHref"
      :to="sessionHref"
      class="mt-1 inline-block text-[11px] font-medium text-blue-600 hover:text-blue-800 no-underline focus-visible:outline-2 focus-visible:outline-blue-500"
      @click="emit('open', message)"
    >Read full message →</router-link>

    <ul v-if="classifiedLinks.length" class="mt-2 flex flex-wrap gap-1.5">
      <li v-for="(lnk, i) in classifiedLinks" :key="i">
        <a
          v-if="lnk.kind === 'external'"
          :href="lnk.href"
          target="_blank"
          rel="noopener"
          class="inline-flex items-center gap-1 text-[11px] font-mono text-blue-600 hover:text-blue-800 bg-blue-50 px-1.5 py-0.5 rounded no-underline focus-visible:outline-2 focus-visible:outline-blue-500"
        >{{ lnk.label }}</a>
        <router-link
          v-else-if="lnk.kind === 'route'"
          :to="lnk.href"
          class="inline-flex items-center gap-1 text-[11px] font-mono text-blue-600 hover:text-blue-800 bg-blue-50 px-1.5 py-0.5 rounded no-underline focus-visible:outline-2 focus-visible:outline-blue-500"
        >{{ lnk.label }}</router-link>
        <button
          v-else
          type="button"
          :title="`Copy path: ${lnk.href}`"
          class="inline-flex items-center gap-1 text-[11px] font-mono text-slate-600 hover:text-slate-800 bg-slate-100 hover:bg-slate-200 px-1.5 py-0.5 rounded focus-visible:outline-2 focus-visible:outline-blue-500"
          @click="copyText(lnk.href)"
        >
          <svg class="w-3 h-3 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
          {{ lnk.label }}
        </button>
      </li>
    </ul>

    <div class="mt-2.5 flex items-center gap-3 text-[11px] text-slate-500">
      <router-link
        v-if="sessionHref"
        :to="sessionHref"
        class="inline-flex items-center gap-1 text-slate-500 hover:text-blue-600 no-underline truncate max-w-[60%] rounded focus-visible:outline-2 focus-visible:outline-blue-500"
        @click="emit('open', message)"
      >
        <svg class="w-3 h-3 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="22 12 18 12 15 21 9 3 6 12 2 12"/></svg>
        <span class="truncate">{{ message.session_title || message.trace_id }}</span>
      </router-link>
      <span v-if="message.agent_type" class="font-mono text-slate-400 shrink-0">{{ message.agent_type }}</span>
      <Button
        v-if="canMarkRead"
        variant="ghost"
        size="sm"
        class="ml-auto text-slate-400 hover:text-blue-600 shrink-0"
        aria-label="Mark message as read"
        @click="emit('read', message)"
      >Mark read</Button>
      <Button
        variant="ghost"
        size="sm"
        class="text-slate-400 hover:text-slate-700 shrink-0"
        :class="{ 'ml-auto': !canMarkRead }"
        aria-label="Dismiss message"
        @click="emit('dismiss', message)"
      >Dismiss</Button>
    </div>
  </div>
</template>
