<script setup>
// Full Q&A sheet body for an ask-user-question / permission span in the
// /live card — the tap-through detail behind LiveTailRow's qa mini row and
// the NOW zone's "options ▾". Ported from the desktop AskUserQuestionCard:
// per question a header + all options as ○/✓ with descriptions, the chosen
// option tinted, a free-typed answer as ✎, the denial reason as an amber
// block.
//
// When the span is a PENDING single-question ask AND the agent bridge is
// reachable, the sheet delegates to LiveQaAnswer — the interactive
// select→confirm operator surface (pick · note · type-your-own · chat).
// Multi-question asks and unreachable/completed spans stay read-only —
// blindly walking a multi-question TUI could answer the wrong sub-question.
import { computed } from 'vue'
import LiveQaAnswer from './LiveQaAnswer.vue'
import {
  askOptLabel, askOptDescription, askIsChosen, askFreeText, askNote,
  toolDisplayName,
} from '../../utils/traceFormatters.js'

const props = defineProps({
  span: { type: Object, required: true },
  sessionId: { type: String, default: '' },
  bridgeReachable: { type: Boolean, default: false },
})
defineEmits(['answered'])

const a = computed(() => props.span?.attributes || {})
const isAsk = computed(() => props.span?.name === 'tool.AskUserQuestion')
const pending = computed(() => props.span?.status_code === 'PENDING')
// Same rule as liveRows.permRowModel: the backend never writes decision
// fields onto permission spans — the denial IS the permission.denied span.
const permDenied = computed(() => props.span?.name === 'permission.denied'
  || a.value.decision === 'denied' || !!a.value.denied)
const permRequested = computed(() => (a.value.command_preview
  ? `$ ${a.value.command_preview}`
  : (a.value.requested_permission || `tool.${toolDisplayName(a.value.tool_name || 'tool')}`)))

const questions = computed(() => a.value.questions || [])
// Answerable only for a live single-question ask over a reachable bridge —
// the select TUI shows one question at a time, so a multi-question ask can't
// be safely driven from a snapshot that doesn't know which one is focused.
const canAnswer = computed(() => isAsk.value && pending.value
  && props.bridgeReachable && !!props.sessionId && questions.value.length === 1)

function chosen(q, opt) {
  return !pending.value && askIsChosen(props.span, q, opt)
}
</script>

<template>
  <div v-if="isAsk" data-testid="live-qa-sheet">
    <LiveQaAnswer
      v-if="canAnswer"
      :span="span"
      :session-id="sessionId"
      @answered="$emit('answered')"
    />
    <template v-else>
      <p v-if="pending" class="live-qa-pending-note">
        Waiting for your answer in the agent session — this card is read-only.
      </p>
      <p v-if="!questions.length" class="live-qa-pending-note">
        Loading question…
      </p>
      <div v-for="(q, qi) in questions" :key="qi" class="live-qa-card">
        <div class="live-qa-card-hd">
          <div v-if="q.header" class="live-qa-h">
            {{ q.header }}{{ q.multiSelect ? ' · multi-select' : '' }}
          </div>
          <div class="live-qa-title">{{ q.question }}</div>
        </div>
        <div
          v-for="(opt, oi) in (q.options || [])"
          :key="oi"
          class="live-qa-opt"
          :class="{ 'live-qa-chosen': chosen(q, opt) }"
        >
          <span class="live-qa-optmark">{{ chosen(q, opt) ? '✓' : '○' }}</span>
          <span class="live-qa-optbody">
            <span class="live-qa-optlbl">{{ askOptLabel(opt) }}</span>
            <span v-if="askOptDescription(opt)" class="live-qa-optdesc">
              {{ askOptDescription(opt) }}
            </span>
            <details v-if="opt && opt.preview" class="live-qa-preview" @click.stop>
              <summary>Preview</summary>
              <pre>{{ opt.preview }}</pre>
            </details>
          </span>
        </div>
        <div v-if="askFreeText(span, q)" class="live-qa-opt live-qa-free">
          <span class="live-qa-optmark">✎</span>
          <span class="live-qa-optbody">
            <span class="live-qa-optlbl">{{ askFreeText(span, q) }}</span>
            <span class="live-qa-optdesc">typed answer (Other)</span>
          </span>
        </div>
        <div v-if="askNote(span, q)" class="live-qa-note">Note: {{ askNote(span, q) }}</div>
      </div>
    </template>

    <div v-if="a.denied && a.denial_reason" class="live-qa-deny">
      <div class="live-qa-h">Denied (agent-injected prompt)</div>
      {{ a.denial_reason }}
    </div>
  </div>

  <div v-else data-testid="live-qa-sheet">
    <div class="live-qa-card">
      <div class="live-qa-card-hd">
        <div class="live-qa-h">requested · {{ toolDisplayName(a.tool_name || 'tool') }}</div>
        <div class="live-qa-title live-mono">{{ permRequested }}</div>
      </div>
      <div
        class="live-qa-opt"
        :class="pending ? '' : (permDenied ? 'live-qa-free' : 'live-qa-chosen')"
      >
        <span class="live-qa-optmark">{{ pending ? '…' : (permDenied ? '✗' : '✓') }}</span>
        <span class="live-qa-optbody">
          <span class="live-qa-optlbl">
            {{ pending ? 'Waiting for your decision' : (permDenied ? 'Denied' : 'Granted') }}
          </span>
          <span v-if="a.reason || a.denial_reason" class="live-qa-optdesc">
            {{ a.reason || a.denial_reason }}
          </span>
        </span>
      </div>
    </div>
  </div>
</template>
