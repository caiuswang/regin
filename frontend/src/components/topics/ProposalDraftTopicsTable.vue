<script setup>
import Badge from '../Badge.vue'
import Card from '../Card.vue'

defineProps({
  draftTopics: { type: Array, default: () => [] },
  selectedDraftTopicId: { type: [String, Number], default: null },
})

const emit = defineEmits(['select'])

function reviewStatusColor(status) {
  if (status === 'accepted' || status === 'merged') return 'green'
  if (status === 'ignored') return 'gray'
  return 'blue'
}
</script>

<template>
  <Card :no-padding="true">
    <div class="topics-panel-header">
      <div>
        <h2>Draft Topics</h2>
        <p class="topics-panel-caption">Review each proposed topic before accepting, merging, or ignoring it.</p>
      </div>
      <Badge color="purple" :label="String(draftTopics.length)" />
    </div>
    <table class="tbl tbl-workbench">
      <thead>
        <tr>
          <th>Label</th>
          <th>Status</th>
          <th class="text-right">Evidence</th>
          <th class="text-right">Refs</th>
          <th class="text-right">Threads</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="topic in draftTopics"
          :key="topic.id"
          class="topics-row-selectable cursor-pointer"
          :class="{ 'tbl-row-active': topic.id === selectedDraftTopicId }"
          @click="emit('select', topic.id)"
        >
          <td>
            <button type="button" class="topics-row-button focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2" @click.stop="emit('select', topic.id)">
              <div class="topics-row-title">{{ topic.label }}</div>
              <div class="topics-row-meta line-clamp-2">{{ topic.intent_preview }}</div>
            </button>
          </td>
          <td><Badge :color="reviewStatusColor(topic.review_status)" :label="topic.review_status" /></td>
          <td class="text-right">{{ topic.evidence_count }}</td>
          <td class="text-right">{{ topic.proposed_ref_count }}</td>
          <td class="text-right">{{ topic.feedback_thread_count || 0 }}</td>
        </tr>
        <tr v-if="!draftTopics.length">
          <td colspan="5" class="text-gray-500">No draft topics in this run.</td>
        </tr>
      </tbody>
    </table>
  </Card>
</template>
