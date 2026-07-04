<script setup>
// The grade-run form. Owns its own draft (trace id, tier, provider, the
// per-run axes/aspects/distill dimensions) and emits a fully-built request on
// submit; the parent only fires the POST and reloads. Validation (at least one
// dimension) lives here so the form can flag inline.
import { computed, ref, watch } from 'vue'
import Button from '../ui/Button.vue'
import Select from '../ui/Select.vue'
import Checkbox from '../ui/Checkbox.vue'
import { useFlash } from '../../composables/useFlash'

const props = defineProps({
  providers: { type: Array, default: () => [] },
  // Non-builtin gradeable aspects: [{ key, label }].
  aspects: { type: Array, default: () => [] },
  defaultProvider: { type: String, default: '' },
  grading: { type: Boolean, default: false },
})
const emit = defineEmits(['grade'])
const { flash } = useFlash()

const TIER_OPTIONS = [
  { value: 'screen', label: 'screen — mechanical, instant' },
  { value: 'auto', label: 'auto — escalate if unsure' },
  { value: 'deep', label: 'deep — LLM judge' },
]

const traceId = ref('')
const tier = ref('auto')
const provider = ref('')
const runAxes = ref({ correctness: true, process: true })
const aspectEnabled = ref({})
const distillOnFail = ref(false)

// Aspects only grade on a tier that actually runs the judge.
const aspectsAvailable = computed(() => tier.value !== 'screen')

// Seed the provider from config once, without clobbering a user choice.
watch(() => props.defaultProvider, (v) => {
  if (v && !provider.value) provider.value = v
})

const providerOptions = computed(() => [
  { value: '', label: 'default judge' },
  ...props.providers.map(p => ({ value: p, label: p })),
])

const selectedAxes = computed(() =>
  Object.keys(runAxes.value).filter(a => runAxes.value[a]))
const selectedAspects = computed(() =>
  aspectsAvailable.value
    ? props.aspects.filter(a => aspectEnabled.value[a.key]).map(a => a.key)
    : [])

function submit() {
  const tid = traceId.value.trim()
  if (!tid) {
    flash('enter a trace id to grade', 'error')
    return
  }
  if (!selectedAxes.value.length && !selectedAspects.value.length) {
    flash('select at least one axis or aspect to grade', 'error')
    return
  }
  const body = { tier: tier.value, axes: selectedAxes.value }
  if (provider.value) body.provider = provider.value
  if (distillOnFail.value) body.distill = true
  if (selectedAspects.value.length) body.aspects = selectedAspects.value
  emit('grade', { traceId: tid, tier: tier.value, body, onDone: () => { traceId.value = '' } })
}
</script>

<template>
  <div class="rounded-xl border border-slate-200 bg-white shadow-[0_1px_2px_rgba(15,23,42,0.03)]">
    <div class="border-b border-slate-100 px-4 py-3">
      <h2 class="text-sm font-semibold text-slate-800">Grade a session</h2>
      <p class="mt-0.5 text-xs text-slate-500">
        Paste a trace id and pick what to weigh. <code class="text-[11px]">screen</code> is
        instant; <code class="text-[11px]">deep</code> (and an escalating
        <code class="text-[11px]">auto</code>) runs one judge over every selected dimension.
      </p>
    </div>

    <div class="p-4 space-y-3">
      <!-- Primary row: target + tier + judge + action. -->
      <div class="flex flex-wrap items-center gap-2">
        <input
          v-model="traceId"
          type="text"
          class="flex-1 min-w-48 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm text-slate-900 placeholder:text-slate-400 focus-visible:outline-2 focus-visible:outline-blue-500"
          placeholder="trace id to grade"
          aria-label="Trace id to grade"
          @keyup.enter="submit"
        >
        <Select v-model="tier" :options="TIER_OPTIONS" class="min-w-44" aria-label="Grading tier" />
        <Select
          v-model="provider"
          :options="providerOptions"
          class="min-w-36"
          aria-label="Judge provider"
          :disabled="tier === 'screen'"
          :title="tier === 'screen' ? 'The screen tier uses no judge' : 'Judge provider'"
        />
        <Button variant="primary" :disabled="grading" @click="submit">
          {{ grading ? 'Grading…' : 'Grade session' }}
        </Button>
      </div>

      <!-- Dimension toggles, grouped. -->
      <div class="flex flex-wrap gap-2">
        <fieldset class="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2">
          <legend class="float-none px-0 text-[0.65rem] font-semibold uppercase tracking-wider text-slate-400">Axes</legend>
          <Checkbox v-model="runAxes.correctness" label="correctness" />
          <Checkbox v-model="runAxes.process" label="process" />
        </fieldset>

        <fieldset
          v-if="aspects.length"
          class="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2"
          :class="{ 'opacity-60': !aspectsAvailable }"
          :title="aspectsAvailable ? 'Each graded aspect gets its own verdict' : 'Aspects need the deep or auto tier'"
        >
          <legend class="float-none px-0 text-[0.65rem] font-semibold uppercase tracking-wider text-slate-400">Aspects</legend>
          <Checkbox
            v-for="a in aspects"
            :key="a.key"
            v-model="aspectEnabled[a.key]"
            :label="a.label"
            :disabled="!aspectsAvailable"
          />
        </fieldset>

        <fieldset class="flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-slate-50/60 px-3 py-2">
          <legend class="float-none px-0 text-[0.65rem] font-semibold uppercase tracking-wider text-slate-400">On fail</legend>
          <Checkbox v-model="distillOnFail" label="distill into lessons" />
        </fieldset>
      </div>
    </div>
  </div>
</template>
