<script setup>
// Choose the defect to KEEP when this one is a duplicate, and see it first.
//
// Open defects of the same feature lead, because a duplicate is most often
// reported against the feature that caused it; the search below them asks
// the server (`GET /api/defects?q=`) so a keeper on another page is reachable.
// The confirm names both ids, because merging deletes the duplicate.
import { computed, onMounted, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import DefectImages from './DefectImages.vue'
import { defectRef } from '../../utils/pipelineFormat'

const props = defineProps({
  defect: { type: Object, required: true },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['confirm', 'cancel'])

const SIZE = 8
const query = ref('')
const sameFeature = ref([])
const found = ref([])
const foundTotal = ref(0)
const keeper = ref(null)
const error = ref('')
const search = ref(null)
let issued = 0

const others = rows => rows.filter(r => r.id !== props.defect.id)

async function read(params) {
  const body = await api.get(`/defects?${new URLSearchParams({
    status: 'open', sort: 'opened:desc', size: String(SIZE), ...params })}`)
  return body
}

async function lookup() {
  const mine = ++issued
  error.value = ''
  const q = query.value.trim()
  try {
    const body = await read(q ? { q } : {})
    if (mine !== issued) return
    const shown = new Set(sameFeature.value.map(r => r.id))
    found.value = others(body.items || []).filter(r => !shown.has(r.id))
    foundTotal.value = body.pagination?.total ?? found.value.length
  } catch (e) {
    if (mine === issued) error.value = e?.message || String(e)
  }
}

onMounted(async () => {
  if (props.defect.feature_id) {
    try {
      const body = await read({ feature_id: String(props.defect.feature_id) })
      sameFeature.value = others(body.items || [])
    } catch (e) {
      error.value = e?.message || String(e)
    }
  }
  await lookup()
  search.value?.focus()
})

const groups = computed(() => [
  { key: 'same', label: 'Same feature', rows: sameFeature.value },
  { key: 'found', label: query.value.trim() ? 'Matches' : 'Other open defects', rows: found.value },
].filter(g => g.rows.length))

function onEscape() {
  if (keeper.value) keeper.value = null
  else emit('cancel')
}
</script>

<template>
  <div class="mp" data-testid="merge-picker">
    <template v-if="!keeper">
      <label class="sr-only" :for="`mp-${defect.id}`">Find the defect to keep</label>
      <Input
        :id="`mp-${defect.id}`"
        ref="search"
        v-model="query"
        type="text"
        placeholder="the defect to KEEP — search its symptom"
        :disabled="busy"
        data-testid="merge-search"
        @input="lookup"
        @keydown.esc.stop.prevent="onEscape"
      />
      <p v-if="error" class="mp-note mp-note--error" role="alert">{{ error }}</p>
      <p v-else-if="!groups.length" class="mp-note">No other open defect matches.</p>
      <div v-for="g in groups" :key="g.key" class="mp-group">
        <p class="mp-label">{{ g.label }}</p>
        <ul class="mp-list">
          <li v-for="row in g.rows" :key="row.id">
            <Button
              variant="ghost" size="sm" class="mp-option"
              :data-testid="`merge-option-${row.id}`"
              @click="keeper = row"
            ><b>{{ defectRef(row.id) }}</b> <span>{{ row.symptom }}</span></Button>
          </li>
        </ul>
      </div>
      <p v-if="foundTotal > found.length" class="mp-note">
        {{ foundTotal - found.length }} more — keep typing to narrow
      </p>
      <Button variant="ghost" size="sm" class="mp-cancel" @click="emit('cancel')">Cancel</Button>
    </template>

    <div v-else class="mp-preview" data-testid="merge-preview">
      <p class="mp-label">Keep {{ defectRef(keeper.id) }}<template v-if="keeper.feature_title"> · {{ keeper.feature_title }}</template></p>
      <p class="mp-symptom">{{ keeper.symptom }}</p>
      <p v-if="keeper.suspect_paths?.length" class="mp-paths">{{ keeper.suspect_paths.join(' · ') }}</p>
      <DefectImages v-if="keeper.images?.length" :defect="keeper" />
      <p class="mp-note">
        #{{ defect.id }} is deleted; its suspect paths, screenshots and fix task move to #{{ keeper.id }},
        which keeps its own wording and round.
      </p>
      <div class="mp-actions">
        <Button
          variant="danger" size="sm" :disabled="busy"
          data-testid="merge-confirm"
          @click="emit('confirm', keeper.id)"
        >Delete #{{ defect.id }}, keep #{{ keeper.id }}</Button>
        <Button variant="ghost" size="sm" :disabled="busy" data-testid="merge-back"
                @click="keeper = null">Back</Button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.mp { display: flex; flex-direction: column; gap: 0.4rem; flex-basis: 100%; min-width: 0; max-width: 44rem; }
.mp-group { display: flex; flex-direction: column; gap: 0.15rem; }
.mp-label {
    font-size: 10.5px; letter-spacing: 0.06em; text-transform: uppercase;
    color: var(--color-fg-faint);
}
.mp-list { display: flex; flex-direction: column; }
.mp-list .mp-option {
    display: flex; justify-content: flex-start; gap: 0.4rem; width: 100%;
    height: auto; min-height: 28px; padding: 0.25rem 0.4rem; text-align: left;
    font-weight: 400; white-space: normal;
}
.mp-option b { font-family: var(--font-mono, monospace); flex-shrink: 0; }
.mp-option span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-width: 0; }
.mp .mp-cancel { align-self: flex-start; }
.mp-preview {
    display: flex; flex-direction: column; gap: 0.35rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-lg);
    background: var(--color-surface-2); padding: 0.5rem 0.65rem;
}
.mp-symptom { font-size: 13px; line-height: 1.5; color: var(--color-fg); overflow-wrap: anywhere; }
.mp-paths { font-family: var(--font-mono, monospace); font-size: 11px; color: var(--color-fg-muted); overflow-wrap: anywhere; }
.mp-note { font-size: 11.5px; color: var(--color-fg-muted); }
.mp-note--error { color: var(--color-danger); }
.mp-actions { display: flex; flex-wrap: wrap; gap: 0.4rem; }
</style>
