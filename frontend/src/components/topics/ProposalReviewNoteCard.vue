<script setup>
import { computed } from 'vue'
import { fmtLocalDateTime } from '../../utils/traceFormatters'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import ClampedText from '../ui/ClampedText.vue'
import MarkdownContent from '../MarkdownContent.vue'
import { useCopy } from '../../composables/useCopy.js'

const props = defineProps({
  thread: { type: Object, required: true },
  readonly: { type: Boolean, default: false },
  busy: { type: Boolean, default: false },
})

const emit = defineEmits(['regenerate', 'dismiss'])

const { copyText } = useCopy()

// The reviewer writes the note as free-form markdown; copy hands back that same
// source text (all comments joined), not the rendered HTML.
const copyBody = computed(() =>
  (props.thread.comments || []).map((c) => c.body).filter(Boolean).join('\n\n'))

// Recommendation comes from the thread metadata the backend stamps
// (`{recommendation: 'REGENERATE'|'ACCEPT'|'DISMISS'}`); fall back to a
// neutral label if an older note predates the structured field.
const recommendation = computed(() => {
  const value = props.thread?.metadata?.recommendation
  return typeof value === 'string' && value ? value.toUpperCase() : 'REVIEW'
})

const recommendationColor = computed(() => {
  if (recommendation.value === 'ACCEPT') return 'green'
  if (recommendation.value === 'DISMISS') return 'gray'
  if (recommendation.value === 'REGENERATE') return 'yellow'
  return 'blue'
})

const isClosed = computed(() => (
  props.thread.resolution_state === 'resolved'
  || props.thread.resolution_state === 'dismissed'
))

const showActions = computed(() => !props.readonly && !isClosed.value)
</script>

<template>
  <div
    class="rounded border border-indigo-200 bg-indigo-50/60 p-3 space-y-3"
    data-testid="review-note-card"
  >
    <div class="flex items-start justify-between gap-3">
      <div class="flex flex-wrap items-center gap-2">
        <Badge color="purple" label="Automated review" />
        <Badge :color="recommendationColor" :label="recommendation" data-testid="review-note-recommendation" />
        <Badge
          v-if="thread.resolution_state === 'dismissed'"
          color="gray"
          label="dismissed"
        />
        <span v-if="thread.revision_number" class="text-[11px] text-slate-500">
          opened in r{{ thread.revision_number }}
        </span>
      </div>
      <div class="flex items-center gap-1 whitespace-nowrap">
        <Button
          v-if="copyBody"
          variant="ghost"
          size="sm"
          class="gap-1 px-1 py-0.5 text-[11px] text-slate-500 hover:text-slate-800"
          title="Copy review note"
          data-testid="review-note-copy"
          @click.stop="copyText(copyBody)"
        >
          <Icon name="copy" :size="12" class="shrink-0" />
          Copy
        </Button>
        <span class="text-[11px] text-slate-400">{{ fmtLocalDateTime(thread.updated_at) }}</span>
      </div>
    </div>

    <article
      v-for="comment in (thread.comments || [])"
      :key="`review-note-${comment.id}`"
      class="border-l-2 border-indigo-300 pl-3"
    >
      <ClampedText :lines="6">
        <MarkdownContent :markdown="comment.body || ''" class="text-sm text-slate-800" />
      </ClampedText>
    </article>

    <div v-if="showActions" class="flex justify-end gap-2">
      <Button
        variant="secondary"
        size="sm"
        :disabled="busy"
        @click="emit('dismiss')"
      >
        Dismiss
      </Button>
      <Button
        variant="primary"
        size="sm"
        :disabled="busy"
        @click="emit('regenerate')"
      >
        Regenerate
      </Button>
    </div>
  </div>
</template>
