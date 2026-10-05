<script setup>
// The features and tasks whose body cites one item (B1–B2): under a feature's
// spec, and on a task, defect or session page.
//
// The ledger hands its list in (`items`): it is built on every ledger read,
// so a save, a ship or a refresh re-seats it with everything else. The other
// pages name the item (`target`) and the list is read here. A body starts or
// stops citing something only through a feature or task write, and every
// such write raises a pipeline frame, so the read is repeated on those and
// never polled.
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import api from '../../api'
import Card from '../Card.vue'
import RefChip from './RefChip.vue'
import { followChip } from '../../utils/refRender.js'
import { usePipelineLive } from '../../composables/usePipelineEvents'

const SHOWN = 20

const props = defineProps({
  // `RefItem[]`: citing features newest first, then citing tasks newest first.
  items: { type: Array, default: null },
  // `kind:key` of the cited item, read through `GET /api/refs/backlinks`
  // when no `items` are given.
  target: { type: String, default: '' },
  // One labelled line for a page header, rather than a card.
  compact: { type: Boolean, default: false },
  testid: { type: String, default: 'ref-backlinks' },
})

const router = useRouter()
const fetched = ref([])
let seq = 0

async function load() {
  if (props.items || !props.target) return
  const mine = ++seq
  let found = []
  try {
    const body = await api.get(`/refs/backlinks?ref=${encodeURIComponent(props.target)}`)
    found = Array.isArray(body?.items) ? body.items : []
  } catch {
    // An unanswered read draws nothing rather than claiming nobody cites it.
  }
  if (mine === seq) fetched.value = found
}

watch(() => props.target, () => {
  fetched.value = []
  load()
}, { immediate: true })
usePipelineLive(load)

const list = computed(() => props.items ?? fetched.value)
const shown = computed(() => list.value.slice(0, SHOWN))
const hidden = computed(() => list.value.length - shown.value.length)

function onClick(event) {
  followChip(event, (to) => router.push(to))
}
</script>

<template>
  <component
    :is="compact ? 'div' : Card"
    v-if="list.length"
    :class="compact ? 'rb-line' : null"
    :data-testid="testid"
  >
    <component :is="compact ? 'span' : 'h2'" :class="compact ? 'rb-label' : 'card-header rb-title'">
      Referenced by
    </component>
    <ul class="rb-list" @click="onClick">
      <li v-for="item in shown" :key="item.ref" class="rb-item">
        <RefChip :target="item.ref" :item="item" />
      </li>
      <li v-if="hidden" class="rb-more" :data-testid="`${testid}-more`">+{{ hidden }} more</li>
    </ul>
  </component>
</template>

<style scoped>
.rb-title { margin-bottom: 0.5rem; }
.rb-line {
    display: flex; align-items: center; gap: 0.4rem; flex-wrap: wrap;
    min-width: 0;
}
/* The header's other fact rows (`SessionAttribution`) label themselves this
   way, so the two lines read as one block. */
.rb-label {
    font-size: 10px; font-weight: 600; letter-spacing: 0.06em;
    text-transform: uppercase; color: var(--color-fg-faint);
}
.rb-list {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.375rem;
    min-width: 0;
    font-size: 0.8125rem;
}
.rb-line .rb-list { font-size: 11.5px; }
.rb-item { min-width: 0; max-width: 100%; }
.rb-more { font-size: 0.75rem; color: var(--color-fg-subtle); }
</style>
