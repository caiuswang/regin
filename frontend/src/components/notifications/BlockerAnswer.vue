<script setup>
// The question(s) a parked ask is waiting on, and the operator's staging of an
// answer to each. Lifted out of BlockerPopout because an AskUserQuestion can
// carry SEVERAL questions behind one Submit: the pop-out used to render
// `questions[0]` alone, so the operator picked for tab 1 and the agent stayed
// blocked on the rest with no way to reach them from here.
//
// Staging only — nothing in this component sends. The pop-out's footer owns
// the delivery, for the same reason it always did: the banner is reachable
// from every page in the app, so a one-tap answer would let a mis-click answer
// a stopped agent with no undo.
//
// Answers live in the notification centre keyed by CARD, not here: the pager
// swaps the card under a mounted pop-out, and a local draft would carry one
// agent's picks onto another's question.
import { computed, nextTick, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Textarea from '../ui/Textarea.vue'
import { useNotificationCenter } from '../../composables/useNotificationCenter'
import { askQuestions, isAnswered, answerSummary } from '../../utils/blockerAsk'

const props = defineProps({
  row: { type: Object, required: true },
  disabled: { type: Boolean, default: false },
})

const { blockerAnswers, setBlockerAnswer } = useNotificationCenter()

const questions = computed(() => askQuestions(props.row))
const total = computed(() => questions.value.length)
const stepped = computed(() => total.value > 1)

const at = ref(0)
// The pager can swap in a card with fewer questions than the step we are on.
watch(questions, () => { at.value = 0 })

const question = computed(() => questions.value[at.value] || {})
const options = computed(() => (question.value.options || []).map((option, oi) => ({
  ...option,
  at: oi,
  // regin's own AskUserQuestion convention marks the suggested answer in the
  // label text, so the pill is derived rather than a field the server sends.
  rec: /\(recommended\)/i.test(option.label || ''),
})))

const answer = computed(() => blockerAnswers.value[at.value] || null)
const picked = computed(
  () => (answer.value?.kind === 'option' ? answer.value.index : null))
const typing = computed(() => answer.value?.kind === 'free')
const draft = computed({
  get: () => answer.value?.text || '',
  set: (text) => stage({ kind: 'free', text }),
})

const answeredHere = computed(() => isAnswered(answer.value))
const remaining = computed(
  () => questions.value.filter((_, i) => !isAnswered(blockerAnswers.value[i])).length)

function stage(next) {
  if (props.disabled) return
  setBlockerAnswer(props.row, at.value, next)
}

function pick(option) {
  stage({ kind: 'option', index: option.index, label: option.label })
}

function step(delta) {
  const next = at.value + delta
  if (next >= 0 && next < total.value) at.value = next
}

function jumpTo(index) {
  if (index >= 0 && index < total.value) at.value = index
}

// Roving tabindex: a radiogroup is ONE tab stop, and arrows move within it.
// Without this every option was its own stop and the arrow keys — the first
// thing a screen-reader user tries in a radiogroup — did nothing.
const groupEl = ref(null)

function moveChoice(delta) {
  const count = options.value.length
  if (!count) return
  const from = picked.value === null ? 0 : picked.value + delta
  const next = ((from % count) + count) % count
  pick(options.value[next])
  nextTick(() => groupEl.value?.querySelectorAll('[role="radio"]')[next]?.focus())
}
</script>

<template>
  <div class="blocker-answer">
    <div v-if="stepped" class="blocker-step" data-testid="blocker-step">
      Question {{ at + 1 }} of {{ total }}
      <span v-if="remaining" class="blocker-step-left">{{ remaining }} still to answer</span>
      <span v-else class="blocker-step-done">all answered</span>
    </div>

    <div class="blocker-popout-question">{{ question.question }}</div>
    <p v-if="question.header" class="blocker-popout-context">{{ question.header }}</p>

    <div
      ref="groupEl"
      class="blocker-popout-options"
      role="radiogroup"
      :aria-label="`Answer options for question ${at + 1}`"
    >
      <div v-for="option in options" :key="option.at" class="blocker-optwrap">
        <Button
          variant="ghost"
          size="sm"
          role="radio"
          :aria-checked="picked === option.index"
          :tabindex="option.at === (picked ?? 0) ? 0 : -1"
          :disabled="disabled"
          @keydown.down.prevent="moveChoice(1)"
          @keydown.right.prevent="moveChoice(1)"
          @keydown.up.prevent="moveChoice(-1)"
          @keydown.left.prevent="moveChoice(-1)"
          :class="[
            'blocker-popout-option h-auto w-full items-start justify-start',
            'whitespace-normal px-3 py-2.5 text-start hover:bg-transparent',
            { 'blocker-popout-option-on': picked === option.index },
          ]"
          @click="pick(option)"
        >
          <span class="blocker-popout-radio" aria-hidden="true" />
          <span class="blocker-popout-option-text">
            <span class="blocker-popout-option-label">
              {{ option.label }}
              <span v-if="option.rec" class="blocker-popout-rec">Recommended</span>
            </span>
            <span v-if="option.description" class="blocker-popout-option-desc">
              {{ option.description }}
            </span>
          </span>
        </Button>
        <!-- The option's own comparison blob. Picking between mockups you
             cannot see is what made the operator leave for /live, which has
             shown this since LiveQaAnswer. -->
        <details v-if="option.preview" class="blocker-preview" data-testid="blocker-preview">
          <summary>Preview</summary>
          <pre>{{ option.preview }}</pre>
        </details>
      </div>

      <!-- The TUI's "Type something." entry, in its real position after the
           listed options — an ask whose right answer is none of them is
           otherwise only answerable by walking to the terminal. -->
      <Button
        variant="ghost"
        size="sm"
        role="radio"
        :aria-checked="typing"
        tabindex="-1"
        :disabled="disabled"
        data-testid="blocker-stage-free"
        :class="[
          'blocker-popout-option h-auto w-full items-start justify-start',
          'whitespace-normal px-3 py-2.5 text-start hover:bg-transparent',
          { 'blocker-popout-option-on': typing },
        ]"
        @click="stage({ kind: 'free', text: draft })"
      >
        <span class="blocker-popout-radio" aria-hidden="true" />
        <span class="blocker-popout-option-text">
          <span class="blocker-popout-option-label">Type your own</span>
        </span>
      </Button>
    </div>

    <Textarea
      v-if="typing"
      v-model="draft"
      :rows="2"
      placeholder="Type your own answer…"
      aria-label="Type your own answer"
      :disabled="disabled"
      data-testid="blocker-free-input"
    />

    <!-- The stepper and the pre-send summary exist only for a multi-question
         ask; a single question is the whole card and needs neither. -->
    <template v-if="stepped">
      <div class="blocker-summary" data-testid="blocker-summary">
        <Button
          v-for="(each, index) in questions"
          :key="index"
          variant="ghost"
          size="sm"
          :disabled="disabled"
          :class="['blocker-summary-row', { 'blocker-summary-cur': index === at }]"
          data-testid="blocker-summary-row"
          @click="jumpTo(index)"
        >
          <span class="blocker-summary-n">{{ index + 1 }}</span>
          <span class="blocker-summary-txt">
            {{ answerSummary(each, blockerAnswers[index]) || 'not answered yet' }}
          </span>
        </Button>
      </div>

      <div class="blocker-step-nav">
        <Button
          variant="ghost"
          size="sm"
          :disabled="disabled || at === 0"
          data-testid="blocker-prev"
          @click="step(-1)"
        >← Previous</Button>
        <Button
          variant="ghost"
          size="sm"
          :disabled="disabled || at >= total - 1 || !answeredHere"
          data-testid="blocker-next"
          @click="step(1)"
        >Next →</Button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.blocker-answer {
  display: flex;
  flex-direction: column;
  gap: 0.8rem;
}

/* The ask's own type. Duplicated in BlockerPopout, which renders the same two
   classes for the shapes that never reach this component (allow/deny, and a
   card no channel can answer) — scoped styles do not cross the boundary. */
.blocker-popout-question {
  font-size: 1rem;
  font-weight: 700;
  letter-spacing: -0.014em;
  line-height: 1.45;
  color: var(--color-fg);
  white-space: pre-line;
}

.blocker-popout-context {
  margin: 0;
  font-size: 0.8125rem;
  color: var(--color-fg-muted);
  line-height: 1.6;
}

.blocker-popout-options {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

/* Scoped under the group so these beat the ghost variant's own hover/border
   utilities, which land at the same specificity as a bare class here. */
.blocker-popout-options .blocker-popout-option {
  gap: 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 0.625rem;
  background: var(--color-surface);
  color: var(--color-fg);
}

.blocker-popout-options .blocker-popout-option:hover:not(:disabled) {
  border-color: var(--color-amber-500);
}

.blocker-popout-options .blocker-popout-option-on,
.blocker-popout-options .blocker-popout-option-on:hover {
  border-color: var(--color-amber-500);
  background: var(--color-amber-50);
}

.blocker-popout-radio {
  flex: none;
  margin-top: 0.2rem;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 2px solid var(--color-border-strong);
}

.blocker-popout-option-on .blocker-popout-radio {
  border-color: var(--color-amber-500);
  background:
    radial-gradient(circle, var(--color-amber-500) 0 3.5px, transparent 3.5px);
}

.blocker-popout-option-text {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
}

.blocker-popout-option-label {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  flex-wrap: wrap;
  font-size: 0.85rem;
  font-weight: 600;
  color: var(--color-fg);
}

.blocker-popout-rec {
  font-size: 0.625rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--color-warning-strong);
  background: var(--color-amber-50);
  border: 1px solid var(--color-amber-300);
  border-radius: 999px;
  padding: 0.05rem 0.4rem;
}

.blocker-popout-option-desc {
  font-size: 0.75rem;
  line-height: 1.5;
  color: var(--color-fg-muted);
}

.blocker-step {
  display: flex;
  align-items: baseline;
  gap: 0.5rem;
  font-size: 0.7rem;
  font-weight: 700;
  letter-spacing: 0.04em;
  text-transform: uppercase;
  color: var(--color-fg-muted);
}

.blocker-step-left { color: var(--color-warning-strong); }
.blocker-step-done { color: var(--color-success-strong, var(--color-fg-muted)); }

.blocker-optwrap {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.blocker-preview {
  margin-inline-start: 1.9rem;
  font-size: 0.72rem;
  color: var(--color-fg-muted);
}

.blocker-preview summary {
  cursor: pointer;
  padding: 0.15rem 0;
}

/* The preview is arbitrary agent-authored text: it scrolls inside its own box
   rather than growing the card, and never widens the dialog. */
.blocker-preview pre {
  margin: 0.25rem 0 0;
  max-height: 14rem;
  overflow: auto;
  padding: 0.5rem 0.65rem;
  border: 1px solid var(--color-border);
  border-radius: 0.5rem;
  background: var(--color-surface-subtle, var(--color-surface));
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.7rem;
  line-height: 1.5;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}

.blocker-summary {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  padding-top: 0.3rem;
  border-top: 1px solid var(--color-border);
}

.blocker-summary .blocker-summary-row {
  height: auto;
  width: 100%;
  justify-content: flex-start;
  gap: 0.5rem;
  padding: 0.3rem 0.4rem;
  font-size: 0.75rem;
  color: var(--color-fg-muted);
  white-space: normal;
  text-align: start;
}

.blocker-summary .blocker-summary-cur { color: var(--color-fg); }

.blocker-summary-n {
  flex: none;
  min-width: 1.1rem;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.blocker-summary-txt {
  flex: 1;
  min-width: 0;
  overflow-wrap: anywhere;
}

.blocker-step-nav {
  display: flex;
  align-items: center;
  gap: 0.4rem;
}
</style>
