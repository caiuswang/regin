<script setup>
// The features and tasks whose body cites this feature, under its spec (B2).
//
// The server builds the list on every ledger read (`lib/refs/backlinks.py`),
// so a save, a ship or a refresh re-seats it along with everything else, and
// its chips arrive resolved: this card makes no request of its own.
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import Card from '../Card.vue'
import RefChip from '../refs/RefChip.vue'
import { followChip } from '../../utils/refRender.js'

const SHOWN = 20

const props = defineProps({
  // `RefItem[]`: citing features newest first, then citing tasks newest first.
  items: { type: Array, default: () => [] },
})

const router = useRouter()
const shown = computed(() => props.items.slice(0, SHOWN))
const hidden = computed(() => props.items.length - shown.value.length)

function onClick(event) {
  followChip(event, (to) => router.push(to))
}
</script>

<template>
  <Card v-if="items.length" data-testid="feature-backlinks">
    <h2 class="card-header fb-title">Referenced by</h2>
    <ul class="fb-list" @click="onClick">
      <li v-for="item in shown" :key="item.ref" class="fb-item">
        <RefChip :target="item.ref" :item="item" />
      </li>
      <li v-if="hidden" class="fb-more" data-testid="feature-backlinks-more">+{{ hidden }} more</li>
    </ul>
  </Card>
</template>

<style scoped>
.fb-title { margin-bottom: 0.5rem; }
.fb-list {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 0.375rem;
    font-size: 0.8125rem;
}
.fb-item { min-width: 0; max-width: 100%; }
.fb-more { font-size: 0.75rem; color: var(--color-fg-subtle); }
</style>
