<script setup>
// The reflect consolidation pass, surfaced as its ordered stages so the Doctor
// panel can *operate* the memory engine, not just report on it. Each stage
// shows its cost (LLM vs deterministic), the live setting that governs it (for
// the ones that have one — bound straight to the agent-memory settings block),
// and its outcome from the most recent run.
import { ref, reactive, computed, onMounted } from 'vue'
import api from '../../api'
import Select from '../ui/Select.vue'
import Checkbox from '../ui/Checkbox.vue'

const props = defineProps({
  reflecting: { type: Boolean, default: false },
  // The response from the last POST /api/memory/reflect, or null before a run.
  lastResult: { type: Object, default: null },
})

// `surface` is the prompt-skeleton slug an LLM stage dispatches to — its agent
// and prompt are managed in the Prompts view, so the pipeline links there
// rather than duplicating the binding.
const STAGES = [
  { n: 1, name: 'dedup / merge', kind: 'auto', sub: 'cosine · text fallback',
    counts: (r) => [['merged', r.merged]] },
  { n: 2, name: 'contradiction', kind: 'llm', sub: 'judges shared-referent + gray-zone pairs',
    surface: 'memory-reflect-contradiction',
    counts: (r) => [['resolved', r.contradictions], ['obsoleted', r.obsoleted]],
    control: { type: 'check', key: 'contradiction_scan_enabled', label: 'sweep' } },
  { n: 3, name: 'promote', kind: 'llm', sub: 'model-decided fate',
    surface: 'memory-reflect-promote',
    counts: (r) => [['promoted', r.promoted], ['held', r.held], ['dropped', r.dropped]],
    control: { type: 'select', key: 'promote_mode', options: ['heuristic', 'ambiguous', 'all'] },
    extra: { type: 'check', key: 'promote_allow_retire', label: 'may retire' } },
  { n: 4, name: 'forget · decay · flag-stale', kind: 'auto', sub: 'age + recall signals',
    counts: (r) => [['decayed', r.decayed], ['forgotten', r.forgotten], ['flagged', r.flagged_stale]] },
  { n: 5, name: 'synthesis', kind: 'llm', sub: '≥3-row clusters → one rule',
    surface: 'memory-reflect-synthesis',
    counts: (r) => [['synthesized', r.synthesized], ['topics', r.topics]],
    control: { type: 'check', key: 'synthesis_enabled', label: 'enabled' } },
  { n: 6, name: 'digest', kind: 'llm', sub: 'per-scope standing briefing',
    surface: 'memory-reflect-digest',
    counts: (r) => [['digests', r.digests]],
    control: { type: 'check', key: 'digest_enabled', label: 'enabled' } },
  { n: 7, name: 'embed · edges', kind: 'auto', sub: 'dense model · similarity links',
    counts: (r) => [['embedded', r.embedded], ['edges', r.edges]] },
]

// Mirror EVERY exposed agent-memory field, not just the pipeline's four: the
// settings PUT persists exactly the block payload it is given, so a partial
// (single-key) body drops the unspecified fields. We re-send the whole block
// on each change — the same contract the Settings view uses.
const config = reactive({})
// surface slug → bound agent id (null = the dispatch's own default).
const agentBySurface = reactive({})
const loading = ref(true)
const error = ref('')

async function load() {
  loading.value = true
  try {
    const data = await api.get('/settings/agent-memory')
    for (const f of data.fields || []) config[f.key] = f.value
  } catch {
    error.value = 'Could not load pipeline settings.'
  } finally {
    loading.value = false
  }
}

async function loadAgents() {
  try {
    const data = await api.get('/prompt-templates?kind=skeleton')
    for (const t of data.templates || []) agentBySurface[t.slug] = t.agent
  } catch {
    /* non-fatal — the pipeline still renders without the agent labels */
  }
}

async function save(key, value) {
  const prev = config[key]
  config[key] = value
  try {
    await api.put('/settings/agent-memory', { ...config })
  } catch {
    config[key] = prev
    error.value = `Could not save ${key}.`
  }
}

const rows = computed(() =>
  STAGES.map((s) => ({
    ...s,
    outcome: props.lastResult ? s.counts(props.lastResult) : null,
    boundAgent: s.surface ? agentBySurface[s.surface] || 'default' : null,
  })))

onMounted(() => {
  load()
  loadAgents()
})
</script>

<template>
  <div class="mt-4">
    <div class="text-[10px] font-semibold uppercase tracking-wider text-fg-faint mb-2">
      Reflect pipeline
    </div>
    <p v-if="error" class="text-xs text-danger mb-2">{{ error }}</p>
    <ul>
      <li
        v-for="s in rows"
        :key="s.n"
        class="grid grid-cols-[1.25rem_1fr_auto] items-center gap-3 py-2 border-b border-border last:border-0"
      >
        <span class="font-mono text-[11px] text-fg-faint tabular-nums">{{ s.n }}</span>
        <div class="min-w-0">
          <div class="flex items-center gap-2 flex-wrap">
            <span class="font-mono text-[13px] font-semibold text-fg">{{ s.name }}</span>
            <span
              class="inline-flex items-center rounded px-1.5 py-0.5 text-[10px] font-mono font-semibold"
              :class="s.kind === 'llm' ? 'bg-surface-2 text-amber-700' : 'bg-surface-2 text-fg-faint'"
            >{{ s.kind === 'llm' ? '◆ LLM' : 'auto' }}</span>
            <template v-if="!loading && s.control">
              <Select
                v-if="s.control.type === 'select'"
                :model-value="config[s.control.key]"
                :options="s.control.options"
                :disabled="reflecting"
                :aria-label="s.control.key"
                class="text-[11px]"
                @update:model-value="(v) => save(s.control.key, v)"
              />
              <Checkbox
                v-else
                :model-value="!!config[s.control.key]"
                :disabled="reflecting"
                :label="s.control.label"
                @update:model-value="(v) => save(s.control.key, v)"
              />
            </template>
            <Checkbox
              v-if="!loading && s.extra"
              :model-value="!!config[s.extra.key]"
              :disabled="reflecting"
              :label="s.extra.label"
              @update:model-value="(v) => save(s.extra.key, v)"
            />
          </div>
          <div class="text-[11px] text-fg-faint mt-0.5">{{ s.sub }}</div>
          <div v-if="s.surface" class="flex items-center gap-1.5 mt-0.5 text-[11px]">
            <span class="text-fg-faint">agent</span>
            <span class="font-mono text-fg-subtle">{{ s.boundAgent }}</span>
            <router-link
              :to="{ path: '/prompt-templates', query: { surface: s.surface } }"
              class="text-link hover:underline"
            >configure →</router-link>
          </div>
        </div>
        <div class="flex items-center gap-2 justify-end">
          <template v-if="s.outcome">
            <span
              v-for="[label, n] in s.outcome"
              :key="label"
              class="font-mono text-[10.5px] text-fg-subtle whitespace-nowrap tabular-nums"
            >{{ n ?? 0 }} {{ label }}</span>
          </template>
          <span v-else class="font-mono text-[10.5px] text-fg-faint">—</span>
        </div>
      </li>
    </ul>
  </div>
</template>
