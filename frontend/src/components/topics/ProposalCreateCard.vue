<script setup>
import { computed, ref, watch } from 'vue'
import api from '../../api'
import Card from '../Card.vue'
import Button from '../ui/Button.vue'
import Select from '../ui/Select.vue'

const props = defineProps({
  repo: { type: String, required: true },
  data: { type: Object, default: null },
  busy: { type: Boolean, default: false },
  compact: { type: Boolean, default: false },
})

const emit = defineEmits(['created', 'error'])

const open = ref(false)
const creating = ref(false)
const selectedProvider = ref('')
const selectedComplexity = ref('auto')
const selectedAgent = ref('')
const topicRequest = ref('')
const selectedTemplateSlugs = ref([])

const selectedProposalProvider = computed(() => props.data?.providers?.find((provider) => provider.id === selectedProvider.value) || null)
const selectedProviderAgents = computed(() => selectedProposalProvider.value?.agents || [])
const availableTemplates = computed(() => {
  const provider = selectedProvider.value
  const templates = props.data?.prompt_templates || []
  return templates.filter((t) => {
    const applies = t.applies_to || []
    return applies.length === 0 || applies.includes(provider)
  })
})
const providerOptions = computed(() => (props.data?.providers || []).map((p) => ({
  value: p.id,
  label: `${p.label}${(p.network && !p.configured) || (p.id === 'external-agent' && !p.configured) ? ' (not configured)' : ''}`,
})))
const createDisabled = computed(() => (
  props.busy || creating.value
  || ((selectedProposalProvider.value?.network || selectedProposalProvider.value?.id === 'external-agent') && !selectedProposalProvider.value?.configured)
))

function selectDefaultProvider() {
  if (!props.data?.providers?.some((provider) => provider.id === selectedProvider.value) && props.data?.providers?.length) {
    selectedProvider.value = props.data.providers.find((provider) => provider.id === 'langchain' && provider.configured)?.id || props.data.providers[0].id
  }
}

function syncAgentForProvider() {
  if (selectedProposalProvider.value?.id === 'external-agent') {
    if (!selectedProviderAgents.value.includes(selectedAgent.value)) {
      selectedAgent.value = selectedProviderAgents.value[0] || ''
    }
  } else {
    selectedAgent.value = ''
  }
}

function toggleTemplate(slug) {
  const set = new Set(selectedTemplateSlugs.value)
  if (set.has(slug)) set.delete(slug)
  else set.add(slug)
  selectedTemplateSlugs.value = Array.from(set)
}

async function createProposal() {
  emit('error', '')
  if (selectedProposalProvider.value?.network && !selectedProposalProvider.value?.configured) {
    emit('error', `${selectedProposalProvider.value.label} is not configured. Set a proposal model/API key or choose External Agent.`)
    return
  }
  creating.value = true
  emit('busy', true)
  try {
    const result = await api.post(`/repos/${props.repo}/topics/proposals`, {
      scope: 'all',
      provider: selectedProvider.value,
      complexity: selectedComplexity.value,
      agent: selectedProposalProvider.value?.id === 'external-agent' ? selectedAgent.value : undefined,
      topic_request: topicRequest.value.trim(),
      prompt_template_ids: selectedTemplateSlugs.value,
    })
    if (!result.ok) {
      emit('error', result.msg || result.error || 'Proposal failed')
      return
    }
    emit('created', result.proposal.id)
  } catch (err) {
    emit('error', err.message || String(err))
  } finally {
    creating.value = false
    emit('busy', false)
  }
}

watch(() => props.data, () => {
  selectDefaultProvider()
  syncAgentForProvider()
}, { immediate: true })

watch(selectedProvider, () => {
  syncAgentForProvider()
  selectedTemplateSlugs.value = []
})
</script>

<template>
  <Button
    v-if="compact && !open"
    variant="secondary"
    class="w-full min-h-11 justify-between"
    @click="open = true"
  >
    <span>Generate a proposal…</span>
    <span class="text-xs text-fg-faint font-normal">{{ selectedProposalProvider?.label || '' }}</span>
  </Button>
  <Card v-else>
    <div class="topics-proposal-controls">
      <Select
        v-model="selectedProvider"
        aria-label="Proposal provider"
        block
        class="topics-input"
        :options="providerOptions"
      />
      <Select
        v-model="selectedComplexity"
        aria-label="Proposal complexity"
        block
        class="topics-input"
        :options="[
          { value: 'auto', label: 'Auto' },
          { value: 'simple', label: 'Simple' },
          { value: 'standard', label: 'Standard' },
          { value: 'complex', label: 'Complex' },
        ]"
      />
      <Select
        v-if="selectedProposalProvider?.id === 'external-agent'"
        v-model="selectedAgent"
        aria-label="External agent"
        block
        class="topics-input"
        :options="selectedProviderAgents"
      />
      <input
        v-model="topicRequest"
        type="text"
        aria-label="Proposal focus or scope"
        class="topics-input topics-input-grow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2"
        placeholder="Optional focus, boundary, or subsystem"
      >
      <Button
        variant="primary"
        :disabled="createDisabled"
        @click="createProposal"
      >
        {{ creating ? 'Working...' : 'Generate Proposal' }}
      </Button>
      <Button
        v-if="compact"
        variant="ghost"
        size="sm"
        class="min-h-9"
        @click="open = false"
      >
        Hide
      </Button>
    </div>
    <div v-if="availableTemplates.length" class="topics-template-chips">
      <span class="topics-template-chips-label">Prompt templates:</span>
      <Button
        v-for="template in availableTemplates"
        :key="template.slug"
        variant="ghost"
        size="sm"
        class="topics-template-chip"
        :class="{ 'topics-template-chip-active': selectedTemplateSlugs.includes(template.slug) }"
        :title="template.description || template.label"
        @click="toggleTemplate(template.slug)"
      >
        {{ template.label }}
      </Button>
      <router-link to="/prompt-templates" class="topics-template-chips-manage">Manage…</router-link>
    </div>
  </Card>
</template>
