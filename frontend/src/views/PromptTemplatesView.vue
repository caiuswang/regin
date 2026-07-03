<script setup>
import PromptSkeletonsPanel from '../components/PromptSkeletonsPanel.vue'
import PromptTemplatesPanel from '../components/PromptTemplatesPanel.vue'
import Tabs from '../components/ui/Tabs.vue'
import { useTabRoute } from '../composables/useTabRoute'

// The deep-grader judge prompts (grader-correctness / grader-process) are now
// registered skeletons under "Agent prompts", so there is no separate grader
// tab. Grader aspects + judge provider still live in the Grades → Grader
// settings panel (GradeAspectsConfig).
const TABS = [
  { value: 'skeletons', label: 'Agent prompts' },
  { value: 'fragments', label: 'Fragments' },
]
const tab = useTabRoute({ default: 'skeletons', valid: ['skeletons', 'fragments'] })
</script>

<template>
  <div class="max-w-6xl">
    <header class="page-header">
      <div class="page-header-text">
        <div class="page-eyebrow">Library</div>
        <h1 class="page-title">Prompts</h1>
        <p class="page-subtitle">
          The editable system/goal prompts regin pipes to external agents
          (topic proposals, reviewers, memory, and the deep grader judges) and
          the reusable fragments injected into them.
        </p>
      </div>
    </header>

    <Tabs v-model="tab" :tabs="TABS" variant="underline" class="mb-5" />

    <PromptSkeletonsPanel v-if="tab === 'skeletons'" />
    <PromptTemplatesPanel v-else />
  </div>
</template>
