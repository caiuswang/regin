<script setup>
import { ref, watch, nextTick } from 'vue'
import api from '../../api'
import { useConfirm } from '../../composables/useConfirm'
import MarkdownContent from '../MarkdownContent.vue'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

const props = defineProps({
  memoryId: { type: String, default: null },
})
const emit = defineEmits(['changed', 'navigate', 'close'])
const { confirm } = useConfirm()

const memory = ref(null)
const related = ref(null)
const editing = ref(false)
const form = ref({ title: '', body: '', tags: '' })
const bodyTextarea = ref(null)

function autosize() {
  const el = bodyTextarea.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${el.scrollHeight}px`
}

watch(() => props.memoryId, (id) => {
  editing.value = false
  memory.value = null
  related.value = null
  if (id) load(id)
}, { immediate: true })

async function load(id) {
  try {
    const [m, rel] = await Promise.all([
      api.get(`/memory/${id}`),
      api.get(`/memory/${id}/related`),
    ])
    memory.value = m.memory
    related.value = rel
  } catch {
    related.value = { neighbors: [], supersedes: [], superseded_by: null }
  }
}

function startEdit() {
  form.value = {
    title: memory.value.title || '',
    body: memory.value.body,
    tags: (memory.value.tags || []).join(', '),
  }
  editing.value = true
  nextTick(autosize)
}

async function save() {
  await api.patch(`/memory/${memory.value.id}`, {
    title: form.value.title || null,
    body: form.value.body,
    tags: form.value.tags.split(',').map(t => t.trim()).filter(Boolean),
  })
  editing.value = false
  await load(memory.value.id)
  emit('changed')
}

async function approve() {
  await api.post(`/memory/${memory.value.id}/approve`)
  emit('changed')
}

async function retire() {
  await api.post(`/memory/${memory.value.id}/retire`, {})
  emit('changed')
}

async function forget() {
  const ok = await confirm(
    'Forget', 'Permanently delete this memory? This cannot be undone.', true)
  if (!ok) return
  await api.del(`/memory/${memory.value.id}`)
  emit('changed')
}

function timeLabel(iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString([], {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}
</script>

<template>
  <section class="flex-1 min-w-0">
    <div v-if="!memory" class="text-slate-400 text-sm py-24 text-center border border-dashed border-slate-200 rounded-xl">
      Select a memory from the list to read it here.
    </div>
    <article v-else>
      <Button
        variant="link"
        size="sm"
        class="text-slate-500 hover:text-slate-800 hover:no-underline gap-1 mb-4"
        @click="emit('close')"
      ><Icon name="chevron-left" :size="14" />Back to all memories</Button>

      <div class="flex flex-col min-[1600px]:flex-row gap-8 min-[1600px]:gap-12 items-start">
        <div class="w-full min-[1600px]:flex-1 min-w-0">
          <div class="flex items-center gap-2 mb-3">
            <span class="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-slate-100 text-slate-600">{{ memory.kind }}</span>
            <span class="text-[10px] font-mono text-slate-400">{{ memory.tier }}</span>
            <span
              v-if="memory.status !== 'active'"
              class="text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded bg-amber-100 text-amber-800"
            >{{ memory.status }}</span>
          </div>

          <template v-if="editing">
            <input v-model="form.title" type="text" placeholder="Title" class="w-full text-sm border border-slate-200 rounded-md px-2 py-1 mb-2 focus-visible:outline-2 focus-visible:outline-blue-500" />
            <textarea ref="bodyTextarea" v-model="form.body" rows="12" class="w-full text-sm font-mono border border-slate-200 rounded-md px-2 py-1 mb-2 min-h-[12rem] max-h-[70vh] resize-y overflow-auto focus-visible:outline-2 focus-visible:outline-blue-500" @input="autosize"></textarea>
            <input v-model="form.tags" type="text" placeholder="Tags (comma-separated)" class="w-full text-sm border border-slate-200 rounded-md px-2 py-1 mb-2 focus-visible:outline-2 focus-visible:outline-blue-500" />
            <div class="flex gap-3 text-sm">
              <Button variant="link" size="sm" class="font-medium hover:no-underline" @click="save">Save</Button>
              <Button variant="link" size="sm" class="text-slate-400 hover:text-slate-700 hover:no-underline" @click="editing = false">Cancel</Button>
            </div>
          </template>
          <template v-else>
            <h2 v-if="memory.title" class="text-xl font-semibold text-slate-900 mb-3 leading-snug">{{ memory.title }}</h2>
            <div class="text-[15px] text-slate-700 leading-7 mb-5">
              <MarkdownContent :markdown="memory.body" />
            </div>
            <div class="flex flex-wrap gap-3 text-sm">
              <Button v-if="memory.status === 'proposed'" variant="link" size="sm" class="font-semibold text-emerald-600 hover:text-emerald-800 hover:no-underline" @click="approve">Approve</Button>
              <Button variant="link" size="sm" class="text-slate-500 hover:text-slate-800 hover:no-underline" @click="startEdit">Edit</Button>
              <Button v-if="memory.status === 'active'" variant="link" size="sm" class="text-slate-500 hover:text-slate-800 hover:no-underline" @click="retire">Retire</Button>
              <Button variant="link" size="sm" class="text-slate-400 hover:text-red-600 hover:no-underline" @click="forget">Forget</Button>
            </div>
          </template>
        </div>

        <aside class="w-full min-[1600px]:w-72 shrink-0 space-y-5 min-[1600px]:border-l min-[1600px]:border-slate-100 min-[1600px]:pl-6 pt-5 min-[1600px]:pt-0 border-t border-slate-100 min-[1600px]:border-t-0">
          <dl class="text-[11px] text-slate-500 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt class="text-slate-400">importance</dt><dd class="font-mono tabular-nums">{{ memory.importance.toFixed(2) }}</dd>
            <dt class="text-slate-400">recalled</dt><dd class="font-mono tabular-nums">{{ memory.recall_count }}×</dd>
            <dt class="text-slate-400">scope</dt><dd class="font-mono">{{ memory.scope }}</dd>
            <dt class="text-slate-400">created</dt><dd class="font-mono">{{ timeLabel(memory.created_at) }}</dd>
            <dt class="text-slate-400">updated</dt><dd class="font-mono">{{ timeLabel(memory.updated_at) }}</dd>
          </dl>

          <div v-if="(memory.tags || []).length" class="flex flex-wrap gap-1">
            <span v-for="t in memory.tags" :key="t" class="text-[11px] font-mono bg-slate-100 text-slate-500 px-1.5 py-0.5 rounded">#{{ t }}</span>
          </div>

          <router-link
            v-if="memory.source_trace_id"
            :to="`/trace/sessions/${memory.source_trace_id}${memory.source_span_id ? `?span=${memory.source_span_id}` : ''}`"
            class="inline-flex items-center gap-1 text-xs font-mono text-slate-500 hover:text-blue-600 no-underline rounded focus-visible:outline-2 focus-visible:outline-blue-500"
          ><Icon name="arrow-up-right" :size="13" />source session</router-link>

          <div v-if="related?.superseded_by">
            <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-1">Superseded by</p>
            <Button variant="link" size="sm" class="text-left text-slate-600 hover:text-blue-600 hover:no-underline" @click="emit('navigate', related.superseded_by.id)">
              {{ related.superseded_by.title || related.superseded_by.kind }}
            </Button>
          </div>

          <div v-if="related?.supersedes?.length">
            <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-1">Supersedes</p>
            <ul class="space-y-0.5">
              <li v-for="s in related.supersedes" :key="s.id">
                <Button variant="link" size="sm" class="text-left text-slate-600 hover:text-blue-600 hover:no-underline" @click="emit('navigate', s.id)">
                  {{ s.title || s.kind }}
                </Button>
              </li>
            </ul>
          </div>

          <div v-if="related?.topics?.length">
            <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-1">Topics</p>
            <div class="flex flex-wrap gap-1">
              <span v-for="t in related.topics" :key="t.id" class="text-[11px] bg-violet-100 text-violet-700 px-1.5 py-0.5 rounded">{{ t.name }}</span>
            </div>
          </div>

          <div v-if="related?.neighbors?.length">
            <p class="text-[10px] font-semibold uppercase tracking-wider text-slate-400 mb-1">Related</p>
            <ul class="space-y-1">
              <li v-for="n in related.neighbors" :key="n.id" class="flex items-baseline gap-2">
                <span class="text-[10px] font-mono tabular-nums text-slate-400 shrink-0">{{ n.similarity.toFixed(2) }}</span>
                <Button variant="link" size="sm" class="text-left text-slate-600 hover:text-blue-600 hover:no-underline truncate" @click="emit('navigate', n.id)">
                  {{ n.title || n.kind }}
                </Button>
              </li>
            </ul>
          </div>
        </aside>
      </div>
    </article>
  </section>
</template>
