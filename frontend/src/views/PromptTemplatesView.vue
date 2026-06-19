<script setup>
import PromptTemplatesPanel from '../components/PromptTemplatesPanel.vue'
import GraderPromptsPanel from '../components/GraderPromptsPanel.vue'
import Tabs from '../components/ui/Tabs.vue'
import { useTabRoute } from '../composables/useTabRoute'

const TABS = [
  { value: 'templates', label: 'Templates' },
  { value: 'grader', label: 'Grader prompts' },
]
const tab = useTabRoute({ default: 'templates', valid: ['templates', 'grader'] })
</script>

<template>
  <div>
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Library</div>
        <h1 class="page-title">Prompts</h1>
        <p class="page-subtitle">
          Reusable topic-proposal fragments and the system prompts the
          session grader's deep (LLM-judge) tier runs.
        </p>
      </div>
    </header>

    <Tabs v-model="tab" :tabs="TABS" variant="underline" class="mb-5" />

    <PromptTemplatesPanel v-if="tab === 'templates'" />
    <GraderPromptsPanel v-else />
  </div>
</template>
