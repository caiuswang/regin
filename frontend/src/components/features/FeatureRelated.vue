<script setup>
// The features this one sits beside: the phase it follows, the phases that
// follow it, its related peers, then the features and tasks whose body cites
// it. Peers only — a link never makes one feature the parent of another.
//
// Every write answers with the fresh `links`, which is handed up so the ledger
// that owns them re-seats them; this card keeps no copy of its own.
import { computed, ref } from 'vue'
import api from '../../api'
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import Input from '../ui/Input.vue'
import Select from '../ui/Select.vue'
import RefBacklinks from '../refs/RefBacklinks.vue'
import { RELATED_ANCHOR } from '../../utils/featureLinks'

const props = defineProps({
  featureId: { type: Number, required: true },
  links: { type: Object, default: () => ({}) },
  referencedBy: { type: Array, default: () => [] },
  canWrite: { type: Boolean, default: false },
})
const emit = defineEmits(['changed'])

// `followed_by` is written as the other feature following this one, so the
// request goes out from the other side.
const GROUPS = [
  { key: 'follows', label: 'Follows', kind: 'follows' },
  { key: 'followed_by', label: 'Followed by', kind: 'follows' },
  { key: 'related', label: 'Related', kind: 'related' },
]
const KIND_OPTIONS = [
  { value: 'follows', label: 'This follows' },
  { value: 'followed_by', label: 'Followed by' },
  { value: 'related', label: 'Related to' },
]

const groups = computed(() => GROUPS.map(g => ({ ...g, rows: props.links?.[g.key] || [] })))
const empty = computed(() => groups.value.every(g => !g.rows.length))

const query = ref('')
const kind = ref('follows')
const candidates = ref([])
const searched = ref(false)
const busy = ref(false)
const failed = ref('')

function sides(group, otherId) {
  return group === 'followed_by'
    ? { from: otherId, to: props.featureId }
    : { from: props.featureId, to: otherId }
}

async function search() {
  const text = query.value.trim()
  failed.value = ''
  if (!text) {
    candidates.value = []
    searched.value = false
    return
  }
  const id = /^#?\d+$/.test(text) ? Number(text.replace('#', '')) : null
  let found = []
  try {
    if (id != null) {
      const row = await api.get(`/features/${id}`)
      if (row?.id) found = [{ id: row.id, title: row.title }]
    } else {
      const body = await api.get(`/features?q=${encodeURIComponent(text)}&size=8`)
      found = (body?.items || []).map(r => ({ id: r.id, title: r.title }))
    }
  } catch {
    found = []
  }
  candidates.value = found.filter(r => r.id !== props.featureId)
  searched.value = true
}

async function write(promise) {
  busy.value = true
  failed.value = ''
  try {
    const body = await promise
    if (body?.ok === false) {
      failed.value = body.msg || 'that link was refused'
      return false
    }
    // A link written from the other side answers with THAT feature's links.
    emit('changed')
    return true
  } catch (e) {
    failed.value = `the link could not be sent — ${e?.message || e}`
    return false
  } finally {
    busy.value = false
  }
}

async function add(otherId) {
  const { from, to } = sides(kind.value, otherId)
  const wire = kind.value === 'related' ? 'related' : 'follows'
  const ok = await write(api.post(`/features/${from}/links`, { other_id: to, kind: wire }))
  if (ok) {
    query.value = ''
    candidates.value = []
    searched.value = false
  }
}

function remove(group, otherId) {
  const g = GROUPS.find(x => x.key === group)
  const { from, to } = sides(group, otherId)
  return write(api.del(`/features/${from}/links/${to}?kind=${g.kind}`))
}
</script>

<template>
  <section :id="RELATED_ANCHOR" class="card" data-testid="feature-related">
    <header class="mb-2">
      <h2 class="h2">Related</h2>
      <p class="h2-sub">the phases around this one, and the features it sits beside</p>
    </header>

    <p v-if="empty && !referencedBy.length && !canWrite" class="fr-empty">No related features.</p>

    <dl class="fr-groups">
      <template v-for="g in groups" :key="g.key">
        <div v-if="g.rows.length" class="fr-group" :data-testid="`related-${g.key}`">
          <dt class="fr-label">{{ g.label }}</dt>
          <dd class="fr-rows">
            <span v-for="r in g.rows" :key="r.id" class="fr-chip" :data-testid="`related-${g.key}-${r.id}`">
              <router-link :to="`/features/${r.id}`" class="fr-link focus-visible:outline-2 focus-visible:outline-blue-500">
                #{{ r.id }} {{ r.title }}
              </router-link>
              <span class="fr-stage">{{ r.stage }}</span>
              <Button
                v-if="canWrite"
                variant="ghost"
                size="icon"
                class="fr-remove"
                :aria-label="`Remove link to #${r.id}`"
                :data-testid="`related-remove-${g.key}-${r.id}`"
                :disabled="busy"
                @click="remove(g.key, r.id)"
              ><Icon name="x" :size="11" /></Button>
            </span>
          </dd>
        </div>
      </template>
    </dl>

    <form v-if="canWrite" class="fr-add" data-testid="related-add" @submit.prevent="search">
      <Select v-model="kind" :options="KIND_OPTIONS" aria-label="Kind of link" data-testid="related-kind" />
      <Input v-model="query" class="fr-query" placeholder="feature id or title" aria-label="Find a feature to link" data-testid="related-query" />
      <Button type="submit" variant="secondary" size="sm" data-testid="related-search">Find</Button>
    </form>
    <ul v-if="candidates.length" class="fr-candidates" data-testid="related-candidates">
      <li v-for="c in candidates" :key="c.id">
        <Button variant="ghost" size="sm" :disabled="busy" :data-testid="`related-pick-${c.id}`" @click="add(c.id)">
          <Icon name="plus" :size="11" /> #{{ c.id }} {{ c.title }}
        </Button>
      </li>
    </ul>
    <p v-else-if="searched" class="fr-empty">No feature matches.</p>
    <p v-if="failed" class="fr-fail" role="alert" data-testid="related-refusal">{{ failed }}</p>

    <RefBacklinks :items="referencedBy" compact testid="feature-backlinks" class="mt-2" />
  </section>
</template>

<style scoped>
.card {
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface); padding: 1rem 1.1rem;
}
.h2 { font-size: 0.9rem; font-weight: 600; color: var(--color-fg); }
.h2-sub { font-size: 11.5px; color: var(--color-fg-subtle); }
.fr-groups { display: flex; flex-direction: column; gap: 0.4rem; }
.fr-group { display: flex; gap: 0.6rem; align-items: baseline; flex-wrap: wrap; }
.fr-label {
    flex: none; width: 6.5rem;
    font-size: 10px; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--color-fg-faint);
}
.fr-rows { display: flex; gap: 0.35rem; flex-wrap: wrap; min-width: 0; }
.fr-chip {
    display: inline-flex; align-items: center; gap: 0.35rem; min-width: 0;
    padding: 0.1rem 0.25rem 0.1rem 0.5rem; border-radius: 999px;
    border: 1px solid var(--color-border); font-size: 12.5px;
}
.fr-link { color: var(--color-fg); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.fr-link:hover { text-decoration: underline; }
.fr-stage { font-size: 11px; color: var(--color-fg-subtle); }
.fr-remove { width: 24px; height: 24px; border-radius: 999px; }
.fr-add { display: flex; gap: 0.4rem; align-items: center; flex-wrap: wrap; margin-top: 0.75rem; }
.fr-query { flex: 1 1 12rem; min-width: 0; }
.fr-candidates { display: flex; flex-direction: column; gap: 0.15rem; margin-top: 0.4rem; }
.fr-empty { font-size: 12px; color: var(--color-fg-subtle); margin-top: 0.4rem; }
.fr-fail { margin-top: 0.4rem; font-size: 12px; color: var(--color-red-700); }
</style>
