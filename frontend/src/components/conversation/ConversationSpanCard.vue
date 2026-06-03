<script setup>
import TaskNotificationCard from './cards/TaskNotificationCard.vue'
import ThinkingCard from './cards/ThinkingCard.vue'
import AssistantResponseCard from './cards/AssistantResponseCard.vue'
import AskUserQuestionCard from './cards/AskUserQuestionCard.vue'
import ToolFailureCard from './cards/ToolFailureCard.vue'
import ServerToolCard from './cards/ServerToolCard.vue'
import BashCard from './cards/BashCard.vue'
import DiffCard from './cards/DiffCard.vue'
import ToolSearchCard from './cards/ToolSearchCard.vue'
import RuleCheckRow from './cards/RuleCheckRow.vue'
import LocalCommandCard from './cards/LocalCommandCard.vue'
import WorkflowPhaseBand from './cards/WorkflowPhaseBand.vue'
import SubagentCard from './cards/SubagentCard.vue'
import AgentResultCard from './cards/AgentResultCard.vue'
import WorkflowLaunchRow from './cards/WorkflowLaunchRow.vue'
import InlineToolRow from './cards/InlineToolRow.vue'

// Dispatcher for one descendant span inside an expanded prompt group. The
// v-if/v-else-if chain order + guards are load-bearing: a `tool.Bash` with no
// captured output, an `Edit` with no diff, or an `AskUserQuestion` with no
// questions intentionally fall through to the generic InlineToolRow. The `:ref`
// registration + pin button live in the parent — this component renders only
// the card body and emits `activate` (= select + maybe-fetch) upward.
defineProps({
  span: { type: Object, required: true },
  selectedSpan: { type: Object, default: null },
  // useConversationFolding return
  folding: { type: Object, required: true },
  // useAgentLaunchMerge return
  agentMerge: { type: Object, required: true },
  // run_id → enriched workflow-run summary
  workflowRunsById: { type: Object, default: () => ({}) },
})
defineEmits(['activate'])
</script>

<template>
  <TaskNotificationCard
    v-if="span.name === 'task.notification'"
    :span="span" :selected-span="selectedSpan"
    @activate="$emit('activate', $event)"
  />
  <ThinkingCard
    v-else-if="span.name === 'assistant.thinking'"
    :span="span" :selected-span="selectedSpan"
    @activate="$emit('activate', $event)"
  />
  <AssistantResponseCard
    v-else-if="span.name === 'assistant_response'"
    :span="span" :selected-span="selectedSpan"
    @activate="$emit('activate', $event)"
  />
  <AskUserQuestionCard
    v-else-if="span.name === 'tool.AskUserQuestion' && span.attributes?.questions"
    :span="span" :selected-span="selectedSpan"
    @activate="$emit('activate', $event)"
  />
  <ToolFailureCard
    v-else-if="span.name === 'tool.failure'"
    :span="span" :selected-span="selectedSpan"
    @activate="$emit('activate', $event)"
  />
  <ServerToolCard
    v-else-if="span.attributes?.server_side && span.attributes?.response_text"
    :span="span" :selected-span="selectedSpan"
    @activate="$emit('activate', $event)"
  />
  <BashCard
    v-else-if="span.name === 'tool.Bash' && (span.attributes?.stdout || span.attributes?.stderr || span.attributes?.interrupted || span.attributes?.command)"
    :span="span" :selected-span="selectedSpan" :folding="folding"
    @activate="$emit('activate', $event)"
  />
  <DiffCard
    v-else-if="(span.name === 'tool.Edit' || span.name === 'tool.Write' || span.name === 'tool.MultiEdit') && span.attributes?.diff"
    :span="span" :selected-span="selectedSpan" :folding="folding"
    @activate="$emit('activate', $event)"
  />
  <ToolSearchCard
    v-else-if="span.name === 'tool.ToolSearch'"
    :span="span" :selected-span="selectedSpan" :folding="folding"
    @activate="$emit('activate', $event)"
  />
  <RuleCheckRow
    v-else-if="span.name === 'rule.check'"
    :span="span" :selected-span="selectedSpan"
    @activate="$emit('activate', $event)"
  />
  <LocalCommandCard
    v-else-if="span.name === 'harness.local_command'"
    :span="span" :selected-span="selectedSpan" :folding="folding"
    @activate="$emit('activate', $event)"
  />
  <WorkflowPhaseBand
    v-else-if="span.name === 'workflow.phase'"
    :span="span" :agent-count="agentMerge.agentCountForPhase(span.span_id)"
  />
  <SubagentCard
    v-else-if="span.name === 'subagent.start'"
    :span="span" :selected-span="selectedSpan" :folding="folding" :agent-merge="agentMerge"
    @activate="$emit('activate', $event)"
  />
  <AgentResultCard
    v-else-if="span.name === 'workflow.agent_result'"
    :span="span" :agent-merge="agentMerge" :folding="folding"
  />
  <WorkflowLaunchRow
    v-else-if="span.name === 'tool.Workflow'"
    :span="span" :selected-span="selectedSpan" :folding="folding" :workflow-runs-by-id="workflowRunsById"
    @activate="$emit('activate', $event)"
  />
  <InlineToolRow
    v-else-if="
      span.name.startsWith('tool.')
      || span.name === 'skill.read'
      || span.name === 'skill.invoke'
      || span.name === 'file.edit'
      || span.name === 'plan.edit'
      || span.name.startsWith('subagent.')
    "
    :span="span" :selected-span="selectedSpan"
    @activate="$emit('activate', $event)"
  />
</template>
