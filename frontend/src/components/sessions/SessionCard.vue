<script setup>
// Phone rendering of a session. Same facts and the same visual language as
// SessionListRow, restacked: an 8-column grid can't survive 390px, so the
// identity line leads and the metrics become a two-column definition list.
import { computed } from 'vue'
import { shortTraceId } from '../../utils/traceFormatters.js'
import { fmtRelativeAge, isActiveWithClock, serverAgeMs } from '../../utils/sessionActivity.js'
import { fmtDuration, primaryRepo, timeTitle, titlePreview, totalMs } from '../../utils/sessionRowFormat.js'
import Button from '../ui/Button.vue'
import Checkbox from '../ui/Checkbox.vue'
import SessionTags from '../SessionTags.vue'
import SessionAgentIcon from './SessionAgentIcon.vue'
import SessionContextMeter from './SessionContextMeter.vue'

const props = defineProps({
  s: { type: Object, required: true },
  selected: { type: Boolean, default: false },
  isDeleting: { type: Boolean, default: false },
  isClosing: { type: Boolean, default: false },
  clock: { type: Object, default: null },
})

const emit = defineEmits(['toggle', 'delete', 'close', 'add-tag', 'remove-tag'])

const active = computed(() => isActiveWithClock(props.s, props.clock))
const repo = computed(() => primaryRepo(props.s))
</script>

<template>
  <li class="scard" :class="{ 'scard--selected': selected }">
    <div class="scard__head">
      <Checkbox
        class="scard__check"
        :model-value="selected"
        @update:model-value="(v) => emit('toggle', v)"
        :aria-label="`Select session ${shortTraceId(s.trace_id)}`"
      />
      <SessionAgentIcon :s="s" size="sm" />
      <router-link :to="`/trace/sessions/${s.trace_id}`" class="scard__title focus-visible:outline-2 focus-visible:outline-blue-500">
        <span v-if="s.title">{{ titlePreview(s.title, 90) }}</span>
        <span v-else class="scard__title-empty">no prompt</span>
      </router-link>
    </div>

    <div class="scard__meta">
      <span class="scard__status" :class="active ? 'scard__status--active' : 'scard__status--idle'">
        <span class="scard__dot" aria-hidden="true"></span>
        {{ active ? 'active' : (s.status === 'ended' ? (s.ended_reason === 'manual' ? 'closed' : 'ended') : 'idle') }}
      </span>
      <code class="scard__id">{{ shortTraceId(s.trace_id, 12) }}…</code>
      <span v-if="s.is_test" class="scard__test">test</span>
      <span v-if="repo" class="scard__repo">{{ repo }}</span>
    </div>

    <SessionTags
      class="scard__tags"
      :tags="s.tags || []"
      :trace-id="s.trace_id"
      @add="(slug) => emit('add-tag', slug)"
      @remove="(slug) => emit('remove-tag', slug)"
    />

    <dl class="scard__stats">
      <div><dt>Activity</dt><dd>{{ s.span_count }} spans · {{ s.file_edits }} edits</dd></div>
      <div><dt>Elapsed</dt><dd>{{ fmtDuration(totalMs(s)) }}<template v-if="s.active_work_ms != null"> / {{ fmtDuration(s.active_work_ms) }}</template></dd></div>
      <div><dt>Context</dt><dd><SessionContextMeter :s="s" /></dd></div>
      <div><dt>Last seen</dt><dd :title="timeTitle(s)">{{ fmtRelativeAge(serverAgeMs(s.last_seen, clock)) }}</dd></div>
    </dl>

    <div class="scard__actions">
      <!-- A plain <router-link>, not <Button as="router-link">: Reka's
           Primitive renders the `as` string as a raw element, so the string
           form emits a literal <router-link> tag with no href. -->
      <router-link
        :to="`/live/${s.trace_id}`"
        class="scard__action scard__action--live focus-visible:outline-2 focus-visible:outline-blue-500"
      >Live</router-link>
      <Button
        v-if="s.status !== 'ended'"
        variant="link"
        size="sm"
        class="scard__action focus-visible:outline-2 focus-visible:outline-blue-500"
        :disabled="isClosing"
        @click="emit('close', s)"
      >{{ isClosing ? 'Closing…' : 'Close' }}</Button>
      <Button
        variant="link"
        size="sm"
        class="scard__action scard__action--danger focus-visible:outline-2 focus-visible:outline-blue-500"
        :disabled="isDeleting"
        @click="emit('delete', s)"
      >{{ isDeleting ? 'Deleting…' : 'Delete' }}</Button>
    </div>
  </li>
</template>

<style scoped>
.scard {
  border-bottom: 1px solid var(--color-border-subtle);
  padding: 0.875rem;
}
.scard--selected { background: var(--color-blue-50); }
.scard__head {
  align-items: flex-start;
  display: flex;
  gap: 0.5rem;
}
.scard__check { flex: 0 0 auto; margin-top: 0.125rem; }
.scard__title {
  color: var(--color-fg);
  flex: 1 1 auto;
  font-size: 0.8125rem;
  font-weight: 600;
  min-width: 0;
  overflow-wrap: anywhere;
  text-decoration: none;
}
.scard__title-empty { color: var(--color-fg-faint); font-style: italic; font-weight: 400; }

.scard__meta {
  align-items: center;
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  margin-top: 0.4375rem;
}
.scard__status {
  align-items: center;
  display: inline-flex;
  font-size: 0.59375rem;
  font-weight: 700;
  gap: 0.3125rem;
  letter-spacing: 0.07em;
  text-transform: uppercase;
}
.scard__dot { background: currentColor; border-radius: 9999px; height: 0.3125rem; width: 0.3125rem; }
.scard__status--active { color: var(--color-emerald-700); }
.scard__status--idle { color: var(--color-fg-faint); }
.scard__id {
  color: var(--color-fg-faint);
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.6875rem;
}
.scard__test {
  background: var(--color-amber-100);
  border-radius: 0.25rem;
  color: var(--color-amber-700);
  font-size: 0.59375rem;
  font-weight: 700;
  padding: 0.0625rem 0.3125rem;
  text-transform: uppercase;
}
.scard__repo {
  background: var(--color-surface-2);
  border-radius: 0.375rem;
  color: var(--color-fg-muted);
  font-size: 0.6875rem;
  max-width: 100%;
  overflow: hidden;
  padding: 0.0625rem 0.4375rem;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.scard__tags { margin-top: 0.4375rem; }

.scard__stats {
  display: grid;
  gap: 0.375rem 0.75rem;
  grid-template-columns: 1fr 1fr;
  margin-top: 0.625rem;
}
.scard__stats dt {
  color: var(--color-fg-faint);
  font-size: 0.625rem;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}
.scard__stats dd {
  color: var(--color-fg-muted);
  font-size: 0.75rem;
  margin: 0.0625rem 0 0;
  overflow-wrap: anywhere;
}

.scard__actions {
  display: flex;
  gap: 1rem;
  justify-content: flex-end;
  margin-top: 0.75rem;
}
.scard__action {
  color: var(--color-fg-muted);
  font-size: 0.75rem;
  font-weight: 500;
  text-decoration: none;
}
.scard__action--live { color: var(--color-emerald-700); }
.scard__action--danger { color: var(--color-red-600); }
</style>
