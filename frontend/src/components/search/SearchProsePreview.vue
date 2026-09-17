<script setup>
// A prose document — a wiki page, a pattern, a session's narration — read as
// the markdown it is. SearchLinePreview stays for bodies that must NOT
// reflow (diffs, shell output, file text); printing both through that one
// renderer is what made a wiki page read as `## Symptom`.
import { computed, nextTick, ref, watch } from 'vue'
import { hitLines, inlineSpans, proseBlocks, splitOnQuery } from '../../utils/searchDocs.js'

const props = defineProps({
  doc: { type: Object, required: true },
  query: { type: String, default: '' },
  // Which of the body's hit lines the stepper is parked on.
  hitIndex: { type: Number, default: 0 },
})

const hits = computed(() => hitLines(props.doc?.body || '', props.query))

const cursorLine = computed(() => {
  const hit = hits.value[props.hitIndex]
  return hit ? hit.index : -1
})

const blocks = computed(() => {
  const all = proseBlocks(props.doc?.body || '')
  // A leading heading that repeats the document's own name is a duplicate of
  // what the preview header already shows at display size — but only when it
  // actually says the same thing. Keying on depth alone dropped a real first
  // section from any page whose body opens at `##`.
  const title = String(props.doc?.title || '').trim().toLowerCase()
  const lead = all[0]
  const body = lead && lead.type === 'h' && lead.text.trim().toLowerCase() === title
    ? all.slice(1)
    : all

  return body.map((block, i) => {
    const holdsCursor = cursorLine.value >= block.from && cursorLine.value <= block.to
    if (block.type === 'h') {
      return { ...block, key: `${block.from}-h`, count: countUnder(body, i), holdsCursor }
    }
    return {
      ...block,
      key: `${block.from}-${block.type}`,
      holdsCursor,
      parts: inlineSpans(block.text).map((part, p) => ({
        code: part.code,
        strong: part.strong,
        key: p,
        runs: splitOnQuery(part.text, props.query).map((run, r) => ({ ...run, key: r })),
      })),
      has: block.text.toLowerCase().includes(props.query.toLowerCase()) && !!props.query,
    }
  })
})

// A heading owns the hit lines between it and the next heading — the number
// that tells a reader whether a section is worth scrolling to.
function countUnder(body, at) {
  const from = body[at].from
  const next = body.slice(at + 1).find(b => b.type === 'h')
  const to = next ? next.from : Infinity
  return hits.value.filter(h => h.index > from && h.index < to).length
}

// Code and emphasis nest, so the tag is picked per axis rather than from one
// enum — `**`session_spans`**` is a <strong> wrapping a <code>.
const inlineTag = part => (part.code ? 'code' : 'span')

const cursorEl = ref(null)
// A function ref, not a string one: inside `v-for` a string ref collects an
// array of every matching node, and the point here is the single block the
// stepper is parked in.
function bindCursor(el, holds) {
  if (holds) cursorEl.value = el
}

watch(() => [props.doc?.id, props.hitIndex], async () => {
  await nextTick()
  cursorEl.value?.scrollIntoView({ block: 'nearest' })
}, { immediate: true })
</script>

<template>
  <div class="prose">
    <template v-for="block in blocks" :key="block.key">
      <h3 v-if="block.type === 'h'" class="prose-head">
        {{ block.text }}
        <span v-if="block.count" class="prose-head-count">
          {{ block.count }} {{ block.count === 1 ? 'line' : 'lines' }}
        </span>
      </h3>
      <component
        :is="block.type === 'li' ? 'li' : 'p'"
        v-else
        :ref="el => bindCursor(el, block.holdsCursor)"
        class="prose-block"
        :class="{ 'is-hit': block.has, 'is-cursor': block.holdsCursor }"
      >
        <component
          :is="inlineTag(part)"
          v-for="part in block.parts"
          :key="part.key"
          :class="{ 'is-strong': part.strong }"
        >
          <template v-for="run in part.runs" :key="run.key">
            <mark v-if="run.hit" :class="{ 'is-current': block.holdsCursor }">{{ run.text }}</mark>
            <template v-else>{{ run.text }}</template>
          </template>
        </component>
      </component>
    </template>

    <p v-if="!blocks.length" class="prose-empty">No preview lines for this result.</p>
  </div>
</template>

<style scoped>
.prose { padding-bottom: 0.5rem; }

.prose-block {
  margin: 0 0 var(--sp-gap-block, 0.6875rem);
  font-size: var(--sp-t-body, 0.8125rem);
  line-height: var(--sp-lh-body, 1.62);
  color: var(--color-fg-muted);
  overflow-wrap: anywhere;
  scroll-margin: 2.5rem 0;
}

li.prose-block { margin-left: 1rem; margin-bottom: 0.3125rem; }

/* A rail, never a fill: a block holding six hits used to paint as a solid
   amber slab, which is the complaint this whole pass started from. */
.prose-block.is-hit {
  position: relative;
  padding-left: 0.8125rem;
  color: var(--color-fg);
}

.prose-block.is-hit::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0.25rem;
  bottom: 0.25rem;
  width: 2px;
  border-radius: 1px;
  background: var(--search-hit-line-bar);
}

.prose-block.is-cursor::before { background: var(--color-primary); }

li.prose-block.is-hit { padding-left: 0.8125rem; margin-left: 1rem; }

.prose-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin: 1.375rem 0 0.5rem;
  font-size: var(--sp-t-micro, 0.6875rem);
  line-height: var(--sp-lh-micro, 1.4);
  font-weight: 700;
  letter-spacing: 0.085em;
  text-transform: uppercase;
  color: var(--color-fg-subtle);
}

.prose-head::after { content: ""; flex: 1; height: 1px; background: var(--color-border-subtle); }

.prose-head:first-child { margin-top: 0; }

.prose-head-count {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.93em;
  font-weight: 600;
  letter-spacing: 0;
  text-transform: none;
  color: var(--color-fg-faint);
  font-variant-numeric: tabular-nums;
}

mark {
  background: var(--search-hit-bg);
  color: var(--search-hit-fg);
  box-shadow: inset 0 -1.5px 0 var(--search-hit-rule);
  font-weight: 650;
  border-radius: 2px;
  padding: 0 1px;
  font: inherit;
}

/* The one hit the stepper is parked on. Its block already carries the rail,
   so this is what says WHICH hit inside a paragraph holding four. */
mark.is-current {
  background: var(--search-hit-cur-bg);
  box-shadow: 0 0 0 2px var(--search-hit-cur-ring);
}

.is-strong { font-weight: 700; color: var(--color-fg); }

code {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: 0.93em;
  background: var(--color-surface-2);
  color: var(--color-fg);
  padding: 0.5px 4px;
  border-radius: 3px;
}

.prose-empty {
  margin: 0;
  font-size: var(--sp-t-micro, 0.6875rem);
  color: var(--color-fg-faint);
}
</style>
