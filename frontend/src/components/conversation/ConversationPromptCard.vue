<script setup>
import { computed } from 'vue'
import PromptBody from '../PromptBody.vue'
import Icon from '../ui/Icon.vue'
import PresentButton from '../present/PresentButton.vue'
import { useCopy } from '../../composables/useCopy.js'
import {
  fmtClock, fullLabel, promptPreviewText, promptPreviewMeta, isUnresolvedPrompt,
} from '../../utils/traceFormatters.js'

// The USER card that heads a turn: avatar tile, `PROMPT · TURN n`, the turn's
// size/disclosure label, and the prompt body as preview or in full.
const props = defineProps({
  prompt: { type: Object, required: true },
  // Per-turn facts derived from the span tree by the feed, which owns it:
  // { turnNumber, eventCount, setAside, latest, foldOpen, expanded, pinnable }.
  meta: { type: Object, required: true },
  // useConversationFolding: { isPromptExpanded, isPromptBodyExpanded,
  // togglePromptBodyExpanded }
  folding: { type: Object, required: true },
  traceId: { type: String, default: '' },
  selected: { type: Boolean, default: false },
  pinned: { type: Boolean, default: false },
  sessionEnded: { type: Boolean, default: false },
})
defineEmits(['click', 'toggle-pin', 'present', 'reveal-folds'])

const { copyText, isCopied } = useCopy()

// A surviving `promptlive-` placeholder is only "stranded" once the session
// can no longer resolve it — i.e. it has ended. While live, the newest
// placeholder is just the in-flight prompt, so gate the unresolved styling on
// the session having ended. See `isUnresolvedPrompt`.
const unresolved = computed(() => props.sessionEnded && isUnresolvedPrompt(props.prompt))
</script>

<template>
  <div
    :data-span-id="prompt.span_id"
    class="group relative flex gap-3 rounded-[12px] border border-purple-200 px-3.5 py-3 cursor-pointer hover:border-purple-300 transition-colors"
    :class="[
      meta.expanded ? 'bg-purple-50' : 'bg-purple-50/40',
      selected ? 'event-selected' : '',
      pinned ? 'ring-2 ring-amber-400' : '',
      unresolved ? 'border-dashed !border-amber-300 !bg-amber-50/50' : '',
    ]"
    @click="$emit('click')"
  >
    <button
      v-if="meta.pinnable"
      type="button"
      class="absolute -left-4 top-1.5 z-10 w-5 h-5 flex items-center justify-center rounded text-[11px] leading-none transition-opacity focus-visible:outline-2 focus-visible:outline-amber-400"
      :class="pinned ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 grayscale'"
      :title="pinned ? 'Unpin from view' : 'Pin to view (hold position across updates)'"
      :aria-pressed="pinned"
      @click.stop="$emit('toggle-pin')"
    >📌</button>
    <span
      class="shrink-0 w-[30px] h-[30px] rounded-[9px] bg-gradient-to-br from-purple-600 to-violet-700 text-white text-[11px] font-bold flex items-center justify-center select-none"
      aria-hidden="true"
    >You</span>
    <div class="min-w-0 flex-1">
      <div class="flex flex-wrap items-center gap-x-2 gap-y-1 mb-[3px]">
        <span
          data-testid="conversation-prompt-label"
          class="font-bold uppercase tracking-[0.08em] text-[10px] text-purple-700 shrink-0"
        >Prompt<template v-if="meta.turnNumber"> · Turn {{ meta.turnNumber }}</template></span>
        <span
          v-if="unresolved"
          class="px-1 rounded bg-amber-200/70 text-amber-800 text-[9px] font-semibold uppercase tracking-wider not-italic"
          title="Unresolved — a live prompt placeholder whose real anchor never landed. Usually a scheduled/loop wakeup (delivered as a plain prompt, never anchored) or an interrupted final prompt, not a turn the user typed."
        >unresolved</span>
        <span class="font-mono text-[11px] text-purple-500 shrink-0">{{ fmtClock(prompt.start_time) }}</span>
        <div class="ml-auto flex items-center gap-2 min-w-0">
          <button
            v-if="prompt.attributes?.text && folding.isPromptBodyExpanded(prompt.span_id)"
            type="button"
            class="inline-flex items-center gap-1 transition-colors px-1.5 py-0.5 rounded text-[10px] text-purple-600 hover:bg-purple-200/60 focus-visible:outline-2 focus-visible:outline-purple-400"
            title="Collapse full prompt"
            @click.stop="folding.togglePromptBodyExpanded(prompt.span_id)"
          ><Icon name="chevron-down" :size="12" class="rotate-180" />Collapse</button>
          <button
            v-if="prompt.attributes?.text"
            type="button"
            class="opacity-0 group-hover:opacity-100 transition-opacity px-1.5 py-0.5 rounded text-[10px] text-purple-600 hover:bg-purple-200/60 focus-visible:outline-2 focus-visible:outline-purple-400"
            :class="isCopied(prompt.attributes.text) && 'opacity-100'"
            title="Copy"
            @click.stop="copyText(prompt.attributes.text)"
          >{{ isCopied(prompt.attributes.text) ? 'Copied' : 'Copy' }}</button>
          <PresentButton @click="$emit('present')" />
          <!-- How much work this turn drove, and whether it's open — the
               same affordance as the `show detail ▾` trigger below. The
               count is omitted, not shown as 0, while the turn's subtree
               is still unfetched (only expanded turns are loaded).

               What the mode set aside is named, never counted: a tally of
               spans the reader cannot see reads as an error budget and
               invites counting instead of reading. -->
          <span class="font-mono text-[11px] text-slate-400">
            <!-- Only while the turn is OPEN. A strip is what makes "set aside"
                 and "all spans shown" mean anything, and a collapsed turn has
                 none — it renders chips, so the label described a strip that
                 was not on screen and its button revealed nothing. A folded
                 turn falls through to its event count, which is the question a
                 reader actually has about one. -->
            <template v-if="meta.setAside && meta.expanded">
              <!-- Once the strip is open those rows are on screen, so
                   calling them set aside reads as a lie against what the
                   reader can see. Only the word changes. -->
              <button
                type="button"
                data-testid="turn-set-aside"
                class="rounded px-0.5 text-fg-subtle underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                :title="meta.latest
                  ? 'Put the rest of this turn back on the spine'
                  : 'Reveal this turn\'s set-aside spans in place'"
                @click.stop="$emit('reveal-folds')"
              >{{ meta.foldOpen ? 'all spans shown' : 'set aside' }}</button>
              ·
            </template>
            <!-- The `</template\n>` break is load-bearing. Vue's `condense`
                 drops a newline-only text node between two ELEMENTS, but keeps
                 it as a single space in front of an interpolation — which put
                 `events ·  ▾` in the header. Closing the tag across the line
                 leaves no text node at all. Wrapping the glyph in its own
                 element fixes the spacing too, but then it answers the header's
                 own `/^(\d+ events? · )?[▴▾]$/` locator twice. -->
            <template v-else-if="meta.eventCount">{{ meta.eventCount }} event<template v-if="meta.eventCount !== 1">s</template> · </template
            >{{ meta.expanded ? '▴' : '▾' }}</span>
        </div>
      </div>
      <div
        v-if="prompt.attributes?.text && folding.isPromptBodyExpanded(prompt.span_id)"
        class="max-h-[60vh] overflow-y-auto rounded border border-purple-200/70 bg-white/40 px-2 py-1"
      >
        <PromptBody
          :text="prompt.attributes.expanded_text || prompt.attributes.text"
          :trace-id="traceId"
          :span-id="prompt.span_id"
          :image-indices="prompt.attributes?.image_indices || []"
          force-expanded
        />
      </div>
      <PromptBody
        v-if="prompt.attributes?.text && !folding.isPromptBodyExpanded(prompt.span_id)"
        :text="promptPreviewText(prompt)"
        :trace-id="traceId"
        :span-id="prompt.span_id"
        :image-indices="prompt.attributes?.image_indices || []"
      />
      <div
        v-if="prompt.attributes?.text && !folding.isPromptBodyExpanded(prompt.span_id) && (promptPreviewMeta(prompt).truncated || promptPreviewMeta(prompt).imageCount)"
        class="mt-1 flex flex-wrap items-center gap-2 text-[11px] text-purple-700/70"
      >
        <button
          v-if="promptPreviewMeta(prompt).truncated"
          type="button"
          class="inline-flex items-center gap-1 rounded border border-purple-200 bg-white/70 px-1.5 py-0.5 text-purple-600 hover:bg-purple-200/60 hover:border-purple-300 focus-visible:outline-2 focus-visible:outline-purple-400 transition-colors"
          title="Show full prompt"
          @click.stop="folding.togglePromptBodyExpanded(prompt.span_id)"
        ><Icon name="chevron-down" :size="12" />Show full prompt</button>
        <span
          v-if="promptPreviewMeta(prompt).imageCount"
          class="inline-flex items-center rounded border border-purple-200 bg-white/70 px-1.5 py-0.5"
          :title="promptPreviewMeta(prompt).imageTokens
            ? `Estimated image cost: ~${promptPreviewMeta(prompt).imageTokens} tokens (rolled into the next turn's input_tokens / cache_creation_input_tokens by Anthropic)`
            : null"
        >{{ promptPreviewMeta(prompt).imageCount }} image<span v-if="promptPreviewMeta(prompt).imageCount > 1">s</span><span
          v-if="promptPreviewMeta(prompt).imageTokens"
          class="ml-1 text-purple-600/80"
        >· ~{{ promptPreviewMeta(prompt).imageTokens }} tok</span></span>
      </div>
      <span v-if="!prompt.attributes?.text" class="text-purple-700">{{ fullLabel(prompt) }}</span>
    </div>
  </div>
</template>
