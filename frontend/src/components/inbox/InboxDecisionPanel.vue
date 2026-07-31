<script setup>
import { computed } from 'vue'
import MarkdownContent from '../MarkdownContent.vue'
import { buttonVariants } from '../ui/Button.vue'
import { parseDecisionBody, decisionOwnsBody } from '../../utils/inboxDecision'

const props = defineProps({
  message: { type: Object, required: true },
})

const parsed = computed(() => parseDecisionBody(props.message.body))
const isPlan = computed(() => props.message.msg_key === 'plan-pending')

// The panel speaks for the body only when it recovered real choices; otherwise
// it is just a banner and the markdown below stays authoritative. One shared
// predicate with the detail pane, so the two can't drift into swallowing a
// body neither of them renders.
const rendersBody = computed(() => decisionOwnsBody(props.message))

const liveHref = computed(() => `/live/${props.message.trace_id}`)
</script>

<template>
  <section class="inbox-decision" :aria-label="isPlan ? 'Plan awaiting review' : 'Awaiting your decision'">
    <header class="inbox-decision-head">
      <svg
        class="inbox-decision-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
      >
        <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
        <line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
      </svg>
      <span class="inbox-decision-title">
        {{ isPlan ? 'A plan is waiting for your review' : 'Awaiting your decision — the agent is paused' }}
      </span>
    </header>

    <div v-if="rendersBody && parsed.prose" class="inbox-decision-prompt">
      <MarkdownContent :markdown="parsed.prose" />
    </div>

    <ul v-if="rendersBody" class="inbox-decision-options">
      <li v-for="(opt, i) in parsed.options" :key="i" class="inbox-decision-option">
        <span class="inbox-decision-marker" aria-hidden="true"></span>
        <span class="inbox-decision-label">{{ opt }}</span>
      </li>
    </ul>

    <!-- Actions sit in their own row below the options, never inside the
         clamped prompt: a long prompt fills every visible line and would
         clip the control out of reach. -->
    <div class="inbox-decision-actions">
      <router-link
        :to="liveHref"
        :class="buttonVariants({ variant: 'primary', size: 'sm' })"
        class="no-underline"
      >
        {{ isPlan ? 'Review in live' : 'Answer in live' }}
        <span aria-hidden="true">→</span>
      </router-link>
      <span class="inbox-decision-note">
        The agent resumes as soon as you answer, in the session that asked.
      </span>
    </div>
  </section>
</template>

<style scoped>
.inbox-decision {
    border: 1px solid var(--color-amber-300);
    background: var(--color-amber-50);
    border-radius: var(--radius-xl);
    padding: 14px 16px;
    margin-bottom: 1rem;
}
.inbox-decision-head { display: flex; align-items: center; gap: 8px; min-width: 0; }
.inbox-decision-icon { width: 16px; height: 16px; flex-shrink: 0; color: var(--color-amber-600); }
.inbox-decision-title {
    font-size: 0.8125rem;
    font-weight: 700;
    color: var(--color-amber-800);
    min-width: 0;
}
.inbox-decision-prompt {
    margin: 10px 0 0;
    font-size: 0.875rem;
    font-weight: 600;
    line-height: 1.5;
    color: var(--color-fg);
    overflow-wrap: anywhere;
}
.inbox-decision-options { list-style: none; margin: 12px 0 0; padding: 0; display: grid; gap: 6px; }
.inbox-decision-option {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 9px 12px;
    background: var(--color-surface);
    border: 1px solid var(--color-amber-200);
    border-radius: var(--radius-lg);
}
.inbox-decision-marker {
    flex-shrink: 0;
    width: 13px;
    height: 13px;
    margin-top: 2px;
    border-radius: 9999px;
    border: 1.5px solid var(--color-amber-400);
}
.inbox-decision-label {
    font-size: 0.8125rem;
    color: var(--color-fg);
    min-width: 0;
    overflow-wrap: anywhere;
}
.inbox-decision-actions {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px 12px;
    margin-top: 14px;
}
.inbox-decision-note { font-size: 0.6875rem; color: var(--color-amber-700); min-width: 0; }
</style>
