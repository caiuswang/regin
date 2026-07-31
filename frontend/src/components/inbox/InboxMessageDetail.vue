<script setup>
import { computed } from 'vue'
import MarkdownContent from '../MarkdownContent.vue'
import Button from '../ui/Button.vue'
import InboxDecisionPanel from './InboxDecisionPanel.vue'
import { inboxTypeMeta } from '../../constants/inboxTypes'
import { decisionOwnsBody } from '../../utils/inboxDecision'
import { useCopy } from '../../composables/useCopy.js'

const props = defineProps({
  message: { type: Object, default: null },
  needsDecision: { type: Boolean, default: false },
  showBack: { type: Boolean, default: false },
})
const emit = defineEmits(['read', 'dismiss', 'open', 'back'])

const { copyText } = useCopy()

const typeMeta = computed(() => inboxTypeMeta(props.message?.msg_type))
const isUnread = computed(() => props.message && !props.message.read_at)
const canMarkRead = computed(
  () => isUnread.value && typeof props.message?.id === 'number')

// `send_to_user` links are "file paths / URLs". Only some are navigable: an
// external URL opens in a tab, an in-app absolute path routes in the SPA, and
// a repo-relative file path has no server route at all — rendering that as an
// <a href> produced dead links, so it becomes copy-to-clipboard instead.
function linkKind(href) {
  if (/^(https?:|mailto:)/i.test(href || '')) return 'external'
  if ((href || '').startsWith('/')) return 'route'
  return 'file'
}
const classifiedLinks = computed(() =>
  (props.message?.links || []).map(lnk => ({ ...lnk, kind: linkKind(lnk.href) })))

// Trace ids that group system-event cards but are NOT navigable sessions
// (content-drift lives under "wiki-debt") — routing there shows a blank pane.
// Keep in sync with lib/agent_messages/events.py NON_SESSION_TRACE_IDS.
const NON_SESSION_TRACES = new Set(['wiki-debt'])
const sessionHref = computed(() => {
  const traceId = props.message?.trace_id
  if (!traceId || NON_SESSION_TRACES.has(traceId)) return null
  const base = `/trace/sessions/${traceId}`
  return props.message.span_id ? `${base}?span=${props.message.span_id}` : base
})

// Suppress the markdown ONLY when the panel actually recovered the options and
// is rendering that same text structurally. A plan card, or a bullet-less
// `_format_permission` body, keeps its markdown here — otherwise the message
// would render as nothing at all.
const decisionPanelOwnsBody = computed(
  () => props.needsDecision && decisionOwnsBody(props.message))

const timeLabel = computed(() => {
  if (!props.message?.created_at) return ''
  return new Date(props.message.created_at).toLocaleString([], {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  })
})
</script>

<template>
  <div v-if="!message" class="inbox-detail-empty">
    <svg
      viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"
      stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
    >
      <path d="M22 12h-6l-2 3h-4l-2-3H2" />
      <path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z" />
    </svg>
    <p>Select a message to read it here.</p>
  </div>

  <div v-else class="inbox-detail">
    <header class="inbox-detail-head">
      <div class="inbox-detail-meta">
        <Button
          v-if="showBack"
          variant="ghost"
          size="sm"
          class="inbox-detail-back focus-visible:outline-2 focus-visible:outline-blue-500"
          aria-label="Back to message list"
          @click="emit('back')"
        >
          <svg
            width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
          ><polyline points="15 18 9 12 15 6" /></svg>
          Inbox
        </Button>
        <span class="inbox-pill" :class="typeMeta.pill">{{ typeMeta.label }}</span>
        <span class="inbox-detail-time">{{ timeLabel }}</span>
        <span v-if="isUnread" class="inbox-detail-unread">
          <span class="inbox-detail-unread-dot" aria-hidden="true"></span>Unread
        </span>
      </div>

      <div class="inbox-detail-actions">
        <Button
          v-if="canMarkRead"
          variant="secondary"
          size="sm"
          @click="emit('read', message)"
        >Mark read</Button>
        <Button
          variant="secondary"
          size="sm"
          @click="emit('dismiss', message)"
        >Dismiss</Button>
        <router-link
          v-if="sessionHref"
          :to="sessionHref"
          class="inbox-detail-trace no-underline"
          @click="emit('open', message)"
        >Open in trace</router-link>
      </div>
    </header>

    <!-- Selecting a row swaps this pane with no navigation and no focus move,
         so a screen reader would otherwise get no signal that the content
         changed. -->
    <div class="inbox-detail-scroll inbox-scroll" aria-live="polite" aria-atomic="false">
      <h2 class="inbox-detail-title">{{ message.title || 'Untitled message' }}</h2>

      <div class="inbox-detail-source">
        <router-link v-if="sessionHref" :to="sessionHref" class="inbox-detail-session" @click="emit('open', message)">
          <svg
            viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
          ><polyline points="22 12 18 12 15 21 9 3 6 12 2 12" /></svg>
          <span>{{ message.session_title || message.trace_id }}</span>
        </router-link>
        <span v-else class="inbox-detail-session-plain">{{ message.session_title || message.trace_id }}</span>
        <span v-if="message.agent_type" class="inbox-detail-agent">{{ message.agent_type }}</span>
      </div>

      <InboxDecisionPanel v-if="needsDecision" :message="message" />

      <!-- A question/permission body IS the prompt + options the panel above
           already renders; printing it again duplicates the whole card. -->
      <div v-if="!decisionPanelOwnsBody" class="inbox-detail-body">
        <MarkdownContent :markdown="message.body" />
      </div>

      <ul v-if="classifiedLinks.length" class="inbox-detail-links">
        <li v-for="(lnk, i) in classifiedLinks" :key="i" class="min-w-0 max-w-full">
          <a
            v-if="lnk.kind === 'external'"
            :href="lnk.href"
            target="_blank"
            rel="noopener"
            class="inbox-detail-link focus-visible:outline-2 focus-visible:outline-blue-500"
          ><span class="truncate min-w-0">{{ lnk.label }}</span></a>
          <router-link
            v-else-if="lnk.kind === 'route'"
            :to="lnk.href"
            class="inbox-detail-link"
          ><span class="truncate min-w-0">{{ lnk.label }}</span></router-link>
          <Button
            v-else
            variant="ghost"
            size="sm"
            :title="`Copy path: ${lnk.href}`"
            class="inbox-detail-link inbox-detail-link-file"
            @click="copyText(lnk.href)"
          >
            <svg
              class="w-3 h-3 shrink-0" viewBox="0 0 24 24" fill="none" stroke="currentColor"
              stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
            ><rect x="9" y="9" width="13" height="13" rx="2" ry="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" /></svg>
            <span class="truncate min-w-0">{{ lnk.label }}</span>
          </Button>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.inbox-detail { display: flex; flex-direction: column; height: 100%; min-height: 0; }

.inbox-detail-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 0.75rem;
    height: 100%;
    padding: 3rem 1.5rem;
    color: var(--color-fg-faint);
    text-align: center;
}
.inbox-detail-empty svg { width: 40px; height: 40px; opacity: 0.6; }
.inbox-detail-empty p { margin: 0; font-size: 0.875rem; }

.inbox-detail-head {
    flex-shrink: 0;
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px 12px;
    padding: 12px 16px;
    border-bottom: 1px solid var(--color-border-subtle);
}
.inbox-detail-meta { display: flex; align-items: center; gap: 8px; min-width: 0; }
.inbox-detail-back { padding-left: 0.25rem; padding-right: 0.5rem; }
.inbox-detail-back:focus-visible { outline: 2px solid var(--color-ring); outline-offset: 2px; }
/* Back only means something in the takeover, where the list is hidden. Both
   panes are on screen above the split, so there is nothing to go back to. */
@media (min-width: 1200px) {
    .inbox-detail-back { display: none; }
}
.inbox-detail-time {
    font-size: 11px;
    color: var(--color-fg-subtle);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    white-space: nowrap;
}
.inbox-detail-unread {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 11px;
    font-weight: 600;
    color: var(--color-primary);
    white-space: nowrap;
}
.inbox-detail-unread-dot {
    width: 6px;
    height: 6px;
    border-radius: 9999px;
    background: var(--color-primary);
}
.inbox-detail-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.inbox-detail-trace {
    display: inline-flex;
    align-items: center;
    height: 1.75rem;
    padding: 0 0.625rem;
    border-radius: var(--radius-lg);
    font-size: 0.75rem;
    font-weight: 500;
    color: var(--color-primary-fg);
    background: linear-gradient(135deg, var(--color-blue-800), var(--color-blue-500));
    white-space: nowrap;
}
.inbox-detail-trace:hover { color: var(--color-primary-fg); text-decoration: none; }
.inbox-detail-trace:focus-visible {
    outline: 2px solid var(--color-ring);
    outline-offset: 2px;
}

.inbox-detail-scroll { padding: 16px 18px 24px; }

.inbox-detail-title {
    margin: 0;
    font-size: 1.25rem;
    font-weight: 700;
    line-height: 1.3;
    color: var(--color-fg);
    overflow-wrap: anywhere;
}
.inbox-detail-source {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px 14px;
    margin: 8px 0 16px;
    min-width: 0;
}
.inbox-detail-session, .inbox-detail-session-plain {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-width: 0;
    max-width: 100%;
    font-size: 0.75rem;
    color: var(--color-fg-subtle);
}
.inbox-detail-session svg { width: 12px; height: 12px; flex-shrink: 0; }
.inbox-detail-session span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.inbox-detail-session:hover { color: var(--color-primary); }
.inbox-detail-agent {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 0.6875rem;
    color: var(--color-fg-faint);
}

/* A message body is arbitrary agent markdown — a wide table or an unwrapped
   code block must scroll inside itself, never widen the pane. */
.inbox-detail-body { font-size: 0.875rem; line-height: 1.65; color: var(--color-fg); min-width: 0; }
.inbox-detail-body :deep(pre) { max-width: 100%; overflow-x: auto; }
.inbox-detail-body :deep(table) { display: block; max-width: 100%; overflow-x: auto; }
.inbox-detail-body :deep(p), .inbox-detail-body :deep(li) { overflow-wrap: anywhere; }
.inbox-detail-body :deep(img) { max-width: 100%; height: auto; }

.inbox-detail-links {
    list-style: none;
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    margin: 18px 0 0;
    padding: 0;
}
.inbox-detail-link {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    max-width: 100%;
    min-width: 0;
    height: auto;
    padding: 3px 8px;
    border-radius: var(--radius-md);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-size: 11px;
    font-weight: 400;
    color: var(--color-info-strong);
    background: var(--color-blue-50);
    text-decoration: none;
}
.inbox-detail-link:hover { text-decoration: none; }
.inbox-detail-link-file { color: var(--color-fg-muted); background: var(--color-surface-2); }
</style>
