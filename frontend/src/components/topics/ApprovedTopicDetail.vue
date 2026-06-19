<script setup>
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import api from '../../api'
import { useConfirm } from '../../composables/useConfirm'
import Badge from '../Badge.vue'
import Button from '../ui/Button.vue'
import Card from '../Card.vue'
import MarkdownContent from '../MarkdownContent.vue'

const props = defineProps({
  repo: { type: String, required: true },
  data: { type: Object, default: null },
})

const emit = defineEmits(['refresh-all', 'error'])

const route = useRoute()
const router = useRouter()
const { confirm } = useConfirm()

const busyAction = ref('')
function startBusy(action) { busyAction.value = action }
function stopBusy() { busyAction.value = '' }
function isBusy(action = '') {
  if (!busyAction.value) return false
  return action ? busyAction.value === action : true
}

const allTopics = computed(() => props.data?.table || [])
const selectedTopic = computed(() => props.data?.selected_topic || null)
const selectedId = computed(() => props.data?.selected_topic_id || null)

const selectedIndex = computed(() => allTopics.value.findIndex((t) => t.id === selectedId.value))
const prevTopic = computed(() => {
  const i = selectedIndex.value
  return i > 0 ? allTopics.value[i - 1] : null
})
const nextTopic = computed(() => {
  const i = selectedIndex.value
  return i >= 0 && i < allTopics.value.length - 1 ? allTopics.value[i + 1] : null
})

function withQuery(next) {
  return { ...route.query, ...next }
}

function backToList() {
  router.replace({ query: withQuery({ tab: 'wiki', topic: undefined }) })
}

function chooseTopic(id) {
  if (!id) return
  router.replace({ query: withQuery({ tab: 'wiki', topic: id }) })
}

async function downgradeToDraft() {
  if (!selectedId.value) return
  startBusy('downgrade-topic')
  try {
    const result = await api.post(`/repos/${props.repo}/topics/${selectedId.value}/downgrade`, {})
    if (!result.ok) {
      emit('error', result.msg || result.error || 'Revert to draft failed')
      return
    }
    await router.replace({
      query: withQuery({
        tab: 'proposals',
        proposal: result.id,
        // When downgrade appends a new revision onto the origin run, hop
        // straight to that revision so the user doesn't land on the prior
        // applied state. For legacy fresh-proposal results this is
        // undefined and the proposals view selects the latest revision.
        revision: result.revision_id || undefined,
        draft: undefined,
        topic: undefined,
      }),
    })
    emit('refresh-all')
  } catch (err) {
    emit('error', err.message || String(err))
  } finally {
    stopBusy()
  }
}

async function deleteTopic() {
  if (!selectedId.value) return
  const label = selectedTopic.value?.label || selectedId.value
  const ok = await confirm(
    'Delete topic',
    `Permanently delete "${label}" and its wiki? Inbound edges from other topics are pruned. ` +
      'This cannot be undone — use "Downgrade to Draft" to keep it as a reviewable proposal.',
    true,
  )
  if (!ok) return
  startBusy('delete-topic')
  try {
    const result = await api.post(`/repos/${props.repo}/topics/${selectedId.value}/delete`, {})
    if (!result.ok) {
      emit('error', result.msg || result.error || 'Delete failed')
      return
    }
    await router.replace({ query: withQuery({ tab: 'wiki', topic: undefined }) })
    emit('refresh-all')
  } catch (err) {
    emit('error', err.message || String(err))
  } finally {
    stopBusy()
  }
}

function roleColor(role) {
  if (role === 'test') return 'green'
  if (role === 'architecture' || role === 'overview') return 'blue'
  if (role === 'api' || role === 'entrypoint') return 'purple'
  return 'gray'
}
</script>

<template>
  <div v-if="!selectedTopic" class="topics-runs-empty">
    <p class="text-sm text-slate-600">
      Topic <code class="text-xs">{{ route.query.topic }}</code> not found.
    </p>
    <Button
      variant="secondary"
      class="mt-3"
      @click="backToList"
    >← Back to topics</Button>
  </div>
  <div v-else class="topics-run-detail">
    <Card>
      <div class="topics-run-header-strip">
        <div class="topics-run-header-left">
          <Button
            variant="link"
            class="topics-back-link"
            @click="backToList"
          >← Back to topics</Button>
          <h2 class="topics-run-title">{{ selectedTopic.label }}</h2>
          <div class="topics-run-header-id">
            <code class="text-xs text-slate-500">{{ selectedTopic.id }}</code>
            <Badge :color="selectedTopic.broken_ref_count ? 'red' : 'green'" :label="selectedTopic.status || 'active'" />
            <span class="text-xs text-slate-500">{{ selectedTopic.ref_count || 0 }} ref{{ (selectedTopic.ref_count || 0) === 1 ? '' : 's' }} · {{ selectedTopic.edge_count || 0 }} edge{{ (selectedTopic.edge_count || 0) === 1 ? '' : 's' }}<span v-if="selectedTopic.broken_ref_count"> · {{ selectedTopic.broken_ref_count }} broken</span></span>
          </div>
        </div>
        <div class="topics-run-header-actions btn-row">
          <Button
            variant="secondary"
            size="sm"
            :disabled="!prevTopic"
            :title="prevTopic ? `Previous: ${prevTopic.label}` : 'No earlier topic'"
            @click="chooseTopic(prevTopic?.id)"
          >← Prev</Button>
          <Button
            variant="secondary"
            size="sm"
            :disabled="!nextTopic"
            :title="nextTopic ? `Next: ${nextTopic.label}` : 'No later topic'"
            @click="chooseTopic(nextTopic?.id)"
          >Next →</Button>
          <span class="topics-run-header-divider" aria-hidden="true"></span>
          <Button
            variant="secondary"
            :disabled="isBusy()"
            @click="downgradeToDraft"
          >{{ isBusy('downgrade-topic') ? 'Downgrading…' : 'Downgrade to Draft' }}</Button>
          <Button
            variant="danger"
            :disabled="isBusy()"
            title="Permanently delete this topic and its wiki"
            @click="deleteTopic"
          >{{ isBusy('delete-topic') ? 'Deleting…' : 'Delete' }}</Button>
        </div>
      </div>
    </Card>

    <Card>
      <div class="space-y-5">
        <div>
          <p class="topics-detail-eyebrow">Intent</p>
          <p class="text-sm text-slate-700 mt-1">{{ selectedTopic.intent }}</p>
        </div>

        <div v-if="selectedTopic.aliases?.length">
          <p class="topics-detail-eyebrow">Aliases</p>
          <div class="flex flex-wrap gap-2 mt-2">
            <Badge v-for="alias in selectedTopic.aliases" :key="alias" color="gray" :label="alias" />
          </div>
        </div>

        <div v-if="selectedTopic.related?.length" class="topics-insight-strip">
          <div class="topics-strip-label">Related topics</div>
          <div class="flex flex-wrap gap-2">
            <Button
              v-for="edge in selectedTopic.related"
              :key="`${edge.type}:${edge.id}`"
              variant="secondary"
              @click="chooseTopic(edge.id)"
            >
              {{ edge.type }}: {{ edge.label }}
            </Button>
          </div>
        </div>

        <div>
          <h3 class="topics-subsection-title">References</h3>
          <table class="tbl">
            <thead><tr><th>Role</th><th>Path</th></tr></thead>
            <tbody>
              <tr v-for="ref in (selectedTopic.refs || [])" :key="`${ref.role}:${ref.path}`">
                <td><Badge :color="roleColor(ref.role)" :label="ref.role" /></td>
                <td><code class="text-xs">{{ ref.path }}</code></td>
              </tr>
              <tr v-if="!(selectedTopic.refs || []).length">
                <td colspan="2" class="text-gray-500">No references recorded.</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="selectedTopic.wiki_content" class="topics-markdown">
          <h3 class="topics-subsection-title">Wiki Preview</h3>
          <MarkdownContent :markdown="selectedTopic.wiki_content" />
        </div>
      </div>
    </Card>
  </div>
</template>
