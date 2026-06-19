<script setup>
import { ref, computed, onMounted } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'

// The individual exemplar 'cases' behind one memory or topic: each row's query
// text, polarity, source, and an ✕ icon to revert that single case (delete-by-id).
// The view + undo surface — shared by the topic-route playground and the
// memory exemplar panel. `kind` is 'topic' | 'memory'.
const props = defineProps({
  kind: { type: String, required: true },
  targetId: { type: String, required: true },
})
const emit = defineEmits(['changed'])

const rows = ref([])
const loading = ref(false)
const busy = ref(0)

// Cases carrying a real query are the useful, scannable ones; queryless cases
// (historical backfill / manual without a prompt) are interchangeable noise, so
// they collapse into a single summary line instead of repeating a dozen times.
const queried = computed(() => rows.value.filter(r => r.query))
const blank = computed(() => rows.value.filter(r => !r.query))
const blankSummary = computed(() => {
  const suppress = blank.value.filter(r => r.polarity <= 0).length
  return { suppress, protect: blank.value.length - suppress }
})

async function reload() {
  loading.value = true
  try {
    const data = await api.get(`/memory/exemplars/${props.kind}/${props.targetId}`)
    rows.value = data.exemplars || []
  } finally {
    loading.value = false
  }
}

async function remove(id) {
  busy.value = id
  try {
    await api.del(`/memory/exemplars/${props.kind}/${id}`)
    await reload()
    emit('changed')
  } finally {
    busy.value = 0
  }
}

// Clear every queryless case at once (delete-by-id has no bulk endpoint).
async function removeBlank() {
  busy.value = -1
  try {
    for (const r of blank.value) {
      await api.del(`/memory/exemplars/${props.kind}/${r.id}`)
    }
    await reload()
    emit('changed')
  } finally {
    busy.value = 0
  }
}

function when(ts) {
  return (ts || '').slice(0, 16).replace('T', ' ')
}

onMounted(reload)
defineExpose({ reload })
</script>

<template>
  <div class="px-3 py-2 bg-slate-50/60">
    <p v-if="loading && !rows.length" class="text-[11px] text-slate-400">loading cases…</p>
    <p v-else-if="!rows.length" class="text-[11px] text-slate-400">no exemplars yet</p>
    <template v-else>
      <ul v-if="queried.length" class="space-y-1">
        <li v-for="r in queried" :key="r.id" class="flex items-center gap-2 text-[12px]">
          <span
            class="text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded shrink-0"
            :class="r.polarity > 0 ? 'bg-emerald-100 text-emerald-700' : 'bg-red-100 text-red-700'"
          >{{ r.polarity > 0 ? 'protect' : 'suppress' }}</span>
          <span class="text-[9px] uppercase tracking-wider text-slate-400 shrink-0">{{ r.source }}</span>
          <span class="text-slate-700 truncate" :title="r.query">{{ r.query }}</span>
          <span class="font-mono text-[10px] text-slate-300 ml-auto shrink-0">{{ when(r.created_at) }}</span>
          <Button
            variant="ghost"
            size="sm"
            class="px-1 h-auto text-slate-400 hover:text-red-600 focus-visible:ring-2 focus-visible:ring-red-400 shrink-0"
            :disabled="busy === r.id"
            aria-label="Revert this case (delete)"
            title="Revert this case (delete)"
            @click="remove(r.id)"
          ><Icon name="x" :size="14" /></Button>
        </li>
      </ul>

      <!-- Queryless cases collapsed: one muted line + a single clear, instead of
           a dozen identical rows. -->
      <div
        v-if="blank.length"
        class="flex items-center gap-2 text-[11px] text-slate-400"
        :class="queried.length ? 'mt-1.5 pt-1.5 border-t border-slate-200/70' : ''"
      >
        <Icon name="archive" :size="13" class="shrink-0 text-slate-300" />
        <span class="truncate">
          {{ blank.length }} {{ blank.length === 1 ? 'case' : 'cases' }} with no recorded query
          <template v-if="blankSummary.suppress && blankSummary.protect">· {{ blankSummary.suppress }} suppress, {{ blankSummary.protect }} protect</template>
          <template v-else-if="blankSummary.suppress">· suppress</template>
          <template v-else>· protect</template>
        </span>
        <Button
          variant="ghost"
          size="sm"
          class="ml-auto px-1.5 h-auto text-slate-400 hover:text-red-600 focus-visible:ring-2 focus-visible:ring-red-400 shrink-0"
          :disabled="busy === -1"
          title="Revert all queryless cases"
          @click="removeBlank"
        >Clear</Button>
      </div>
    </template>
  </div>
</template>
