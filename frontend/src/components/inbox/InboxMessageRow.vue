<script setup>
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import { inboxTypeMeta } from '../../constants/inboxTypes'

const props = defineProps({
  message: { type: Object, required: true },
  selected: { type: Boolean, default: false },
  needsDecision: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'navigate'])

// Arrow keys traverse the list. Handled on the row (a real <button>) rather
// than on the scroll container, which would read as an undecorated clickable
// card to the convention engine and to assistive tech.
function onKeydown(evt) {
  const step = { ArrowDown: 1, ArrowUp: -1 }[evt.key]
  if (!step) return
  evt.preventDefault()
  emit('navigate', { el: evt.currentTarget, step })
}

const typeMeta = computed(() => inboxTypeMeta(props.message.msg_type))
const isUnread = computed(() => !props.message.read_at)

// The row is a fixed-height triage line, so the preview strips the markdown
// that would otherwise render as literal `#`/`*` noise in a 2-line clamp.
const preview = computed(() => (props.message.body || '')
  .replace(/```[\s\S]*?(```|$)/g, ' ')          // fenced blocks
  .replace(/^\s*\|.*$/gm, ' ')                  // whole table rows, incl. rules
  .replace(/^\s*([-*_]\s*){3,}$/gm, ' ')         // thematic breaks (* * *)
  .replace(/<[^>]+>/g, ' ')                      // inline HTML
  .replace(/`([^`]*)`/g, '$1')
  .replace(/!\[([^\]]*)\]\([^\s)]*\)/g, '$1')
  .replace(/\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)/g, '$1')
  .replace(/^\s*[#>]+\s*/gm, '')
  .replace(/^\s*\d+\.\s+/gm, '· ')
  // List items keep their separation, but as typography rather than a leaked
  // "•" glyph — a question's options are useful triage detail in the preview.
  .replace(/^\s*[•*+-]\s+/gm, '· ')
  .replace(/~~([^~]*)~~/g, '$1')
  .replace(/\*{1,3}([^*]+)\*{1,3}/g, '$1')
  .replace(/(^|[\s(])_{1,2}([^_]+)_{1,2}(?=[\s,.)]|$)/g, '$1$2')
  .replace(/\s+/g, ' ')
  .trim())

const timeLabel = computed(() => {
  if (!props.message.created_at) return ''
  return new Date(props.message.created_at).toLocaleTimeString([], {
    hour: '2-digit', minute: '2-digit',
  })
})
</script>

<template>
  <Button
    variant="ghost"
    class="inbox-row"
    :class="{ 'inbox-row-selected': selected }"
    :aria-current="selected ? 'true' : undefined"
    data-testid="inbox-row"
    @click="emit('select', message)"
    @keydown="onKeydown"
  >
    <span v-if="selected" class="inbox-row-rail" aria-hidden="true"></span>

    <span class="inbox-row-head">
      <span v-if="isUnread" class="inbox-row-dot" title="Unread"></span>
      <span class="inbox-pill" :class="typeMeta.pill">{{ typeMeta.label }}</span>
      <span class="inbox-row-title">{{ message.title || preview || 'Untitled message' }}</span>
      <span class="inbox-row-time">{{ timeLabel }}</span>
    </span>

    <span v-if="message.title && preview" class="inbox-row-preview">{{ preview }}</span>

    <span class="inbox-row-foot">
      <svg
        class="inbox-row-foot-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
      ><polyline points="22 12 18 12 15 21 9 3 6 12 2 12" /></svg>
      <span class="inbox-row-session">{{ message.session_title || message.trace_id }}</span>
      <span v-if="needsDecision" class="inbox-row-needs">Needs decision</span>
    </span>
  </Button>
</template>

<style scoped>
/* Reshapes the <Button> primitive into a full-width stacked row: the base
   variant is an inline-flex, fixed-height, centered control. Unlayered, so
   it wins over Tailwind's layered utilities regardless of source order —
   same technique as `.live-row` in style.css. */
.inbox-row {
    position: relative;
    display: flex;
    flex-direction: column;
    align-items: stretch;
    justify-content: flex-start;
    gap: 3px;
    width: 100%;
    height: auto;
    padding: 10px 12px 10px 16px;
    border: 0;
    border-radius: 12px;
    background: transparent;
    text-align: left;
    white-space: normal;
    font-weight: 400;
    transition: background 0.12s;
}
.inbox-row:hover { background: var(--color-surface-2); }
.inbox-row-selected, .inbox-row-selected:hover { background: var(--color-blue-50); }

.inbox-row-rail {
    position: absolute;
    left: 4px;
    top: 12px;
    bottom: 12px;
    width: 3px;
    border-radius: 3px;
    background: var(--color-primary);
}

.inbox-row-head { display: flex; align-items: center; gap: 7px; min-width: 0; }
.inbox-row-dot {
    flex-shrink: 0;
    width: 7px;
    height: 7px;
    border-radius: 9999px;
    background: var(--color-primary);
}
.inbox-row-title {
    flex: 1;
    min-width: 0;
    font-size: 12.5px;
    font-weight: 700;
    color: var(--color-fg);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.inbox-row-time {
    flex-shrink: 0;
    font-size: 10px;
    /* 10px at --color-fg-faint measured 2.42:1 in light mode, and
       --color-fg-subtle still only reached 4.38:1 against the selected row's
       blue-50 fill — below the 4.5 AA floor for text this small. */
    color: var(--color-fg-muted);
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
}

.inbox-row-preview {
    font-size: 11.5px;
    line-height: 1.45;
    color: var(--color-fg-muted);
    display: -webkit-box;
    -webkit-line-clamp: 2;
    line-clamp: 2;
    -webkit-box-orient: vertical;
    overflow: hidden;
}

.inbox-row-foot { display: flex; align-items: center; gap: 8px; min-width: 0; }
.inbox-row-foot-icon { flex-shrink: 0; width: 11px; height: 11px; color: var(--color-fg-faint); }
.inbox-row-session {
    flex: 1;
    min-width: 0;
    font-size: 10.5px;
    color: var(--color-fg-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
}
.inbox-row-needs {
    flex-shrink: 0;
    display: inline-flex;
    align-items: center;
    font-size: 9.5px;
    font-weight: 700;
    color: var(--color-danger-strong);
    background: var(--color-danger-soft);
    border: 1px solid var(--color-red-200);
    padding: 1px 7px;
    border-radius: 9999px;
}

/* A coarse pointer needs the whole row to clear the 44px tap target even
   when the message has no preview line. */
@media (pointer: coarse) {
    .inbox-row { padding-top: 12px; padding-bottom: 12px; }
}
</style>
