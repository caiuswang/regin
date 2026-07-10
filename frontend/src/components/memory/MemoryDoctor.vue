<script setup>
// A health + statistics panel for the memory store — regin's `doctor` idiom
// applied to memory. Reads the `stats` envelope the list endpoint already
// returns (see MemoryView), computes health checks, renders the breakdowns.
// Its one action is an inline `reflect` emit on the consolidation-debt row —
// the same trigger as the header "Run reflect", surfaced where it's advised.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import ReflectPipeline from './ReflectPipeline.vue'

const props = defineProps({
  stats: { type: Object, required: true },
  // Drives the inline Run-reflect button's loading/disabled state; the parent
  // owns the actual reflect call and toggles this.
  reflecting: { type: Boolean, default: false },
  // Same, for the Classify-orphans agent run (the parent owns the POST).
  classifying: { type: Boolean, default: false },
  // The last reflect run's per-stage counts, threaded to the pipeline view.
  lastResult: { type: Object, default: null },
})
const emit = defineEmits(['reflect', 'classify'])

// The editable classifier prompt lives in prompt management under this surface
// slug — the "configure →" link deep-links the Prompts view straight to it.
const CLASSIFY_SURFACE = 'memory-topic-classify'

// `embed_coverage` is null when nothing is embeddable (no active rows), which
// is distinct from 0% (embeddable but unembedded) — keep it null so neither
// the header nor the warning invents a "0%".
const embedPct = computed(() => {
  const c = props.stats.embed_coverage
  return c == null ? null : Math.round(c * 100)
})

// Health checks, worst-first. `level` drives the token color; a store with no
// findings shows a single green "healthy" row so the panel never reads blank.
const checks = computed(() => {
  const s = props.stats
  const debt = s.consolidation_debt || {}
  const out = []
  if ((debt.working_active || 0) > 0) {
    out.push({
      level: 'warn',
      action: 'reflect',
      text: `${debt.working_active} working row(s) awaiting consolidation`,
    })
  }
  if (embedPct.value != null && embedPct.value < 100) {
    out.push({
      level: 'warn',
      text: `embeddings ${embedPct.value}% of active rows — reflect embeds the rest`,
    })
  }
  if ((debt.proposed || 0) > 0) {
    out.push({
      level: 'info',
      text: `${debt.proposed} proposal(s) awaiting your approval`,
    })
  }
  if ((s.orphaned || 0) > 0) {
    out.push({
      level: 'info',
      action: 'classify',
      text: `${s.orphaned} memories unfiled (no topic) — the agentic classifier files them`,
    })
  }
  if (!out.length) {
    out.push({ level: 'ok', text: 'Store is healthy — nothing to consolidate.' })
  }
  return out
})

const LEVEL_CLS = {
  warn: 'text-amber-700',
  info: 'text-fg-subtle',
  ok: 'text-success',
}
const LEVEL_ICON = { warn: '▲', info: '•', ok: '✓' }

// The four count buckets, in lifecycle-then-detail order, each an
// {label -> count} map from stats. Rendered as labeled chip rows.
const groups = computed(() => [
  { label: 'Tier', data: props.stats.by_tier || {} },
  { label: 'Status', data: props.stats.by_status || {} },
  { label: 'Kind', data: props.stats.by_kind || {} },
  { label: 'Scope', data: props.stats.by_scope || {} },
])

const entries = (data) => Object.entries(data).sort((a, b) => b[1] - a[1])
</script>

<template>
  <div class="rounded-lg border border-border bg-surface p-4 text-sm">
    <div class="flex items-center gap-2 mb-3">
      <h2 class="text-sm font-semibold text-fg">Memory doctor</h2>
      <span class="text-xs font-mono text-fg-faint">{{ stats.total || 0 }} rows<template v-if="embedPct != null"> · {{ embedPct }}% embedded</template></span>
    </div>

    <!-- Health checks -->
    <ul class="space-y-1 mb-4">
      <li
        v-for="(c, i) in checks"
        :key="i"
        class="flex items-start flex-wrap gap-2 text-xs"
        :class="LEVEL_CLS[c.level]"
      >
        <span class="flex min-w-0 items-start gap-2">
          <span aria-hidden="true" class="shrink-0 leading-4">{{ LEVEL_ICON[c.level] }}</span>
          <span class="min-w-0">{{ c.text }}</span>
        </span>
        <Button
          v-if="c.action === 'reflect'"
          variant="secondary"
          size="sm"
          class="ml-1 -my-0.5"
          :disabled="reflecting"
          @click="emit('reflect')"
        >{{ reflecting ? 'Running…' : 'Run reflect' }}</Button>
        <template v-if="c.action === 'classify'">
          <Button
            variant="secondary"
            size="sm"
            class="ml-1 -my-0.5"
            :disabled="classifying"
            @click="emit('classify')"
          >{{ classifying ? 'Classifying…' : 'Classify with agent' }}</Button>
          <router-link
            :to="{ path: '/prompt-templates', query: { surface: CLASSIFY_SURFACE } }"
            class="text-link hover:underline whitespace-nowrap"
          >configure →</router-link>
        </template>
      </li>
    </ul>

    <ReflectPipeline :reflecting="reflecting" :last-result="lastResult" />

    <!-- Count breakdowns -->
    <div class="grid gap-3 sm:grid-cols-2">
      <div v-for="g in groups" :key="g.label">
        <div class="text-[10px] font-semibold uppercase tracking-wider text-fg-faint mb-1">{{ g.label }}</div>
        <div v-if="entries(g.data).length" class="flex flex-wrap gap-1.5">
          <span
            v-for="[name, count] in entries(g.data)"
            :key="name"
            class="inline-flex items-center gap-1 rounded bg-surface-2 px-1.5 py-0.5 text-[11px] text-fg-subtle"
          >
            <span class="truncate max-w-[10rem]">{{ name }}</span>
            <span class="font-mono tabular-nums text-fg">{{ count }}</span>
          </span>
        </div>
        <div v-else class="text-[11px] text-fg-faint">—</div>
      </div>
    </div>

    <p v-if="stats.db_path" class="mt-3 text-[10px] font-mono text-fg-faint truncate" :title="stats.db_path">
      {{ stats.db_path }}
    </p>
  </div>
</template>
