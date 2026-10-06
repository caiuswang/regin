<script setup>
// Projects as save slots (Overview 1-4): the same active rows, attention
// sort and links as the classic panel, drawn as one card per project.
import Button from '../../../../components/ui/Button.vue'
import PixelSprite from '../PixelSprite.vue'
import { ago } from '../../../../utils/pipelineFormat'

defineProps({
  panel: { type: Object, required: true },
  compact: { type: Boolean, default: false },
})
const emit = defineEmits(['pick', 'all'])

const to = (path, id, extra = {}) => ({ path, query: { project: String(id), ...extra } })
const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`
const slot = i => `Slot ${String(i + 1).padStart(2, '0')}`

function seen(row) {
  const at = row.last_activity ? ago(row.last_activity) : '—'
  if (at === '—') return 'no activity'
  return at === 'just now' ? at : `${at} ago`
}
</script>

<template>
  <section
    class="pl-panel px-pj"
    :class="{ 'px-pj--compact': compact }"
    data-pl-part="panel"
    data-testid="projects-panel"
    aria-labelledby="pj-title"
  >
    <header class="pl-panel__head" data-pl-part="panel-head">
      <h2 id="pj-title" class="pl-panel__title" data-pl-part="panel-title" data-pl-chapter="1-4">Projects</h2>
      <span class="pl-panel__sub" data-pl-part="panel-meta" data-testid="projects-counts">{{ panel.active }} active · {{ panel.quiet }} quiet</span>
      <Button
        variant="link"
        size="sm"
        class="px-pj__all"
        data-pl-part="panel-link"
        data-testid="projects-all"
        @click="emit('all')"
      >All {{ panel.total }} projects →</Button>
    </header>
    <p v-if="!panel.rows.length" class="pl-panel__empty" data-pl-part="panel-empty" data-testid="projects-empty">
      All {{ panel.total }} projects quiet
    </p>
    <ul v-else class="px-pj__slots">
      <li
        v-for="(row, i) in panel.rows"
        :key="row.id"
        class="px-slot"
        data-pl-part="card"
        :data-testid="`projects-row-${row.id}`"
      >
        <div class="px-slot__head">
          <span>{{ slot(i) }}</span>
          <span>{{ seen(row) }}</span>
        </div>
        <Button
          variant="ghost"
          class="px-slot__pick"
          :data-testid="`projects-pick-${row.id}`"
          @click="emit('pick', String(row.id))"
        >
          <PixelSprite name="bot" :scale="2" />
          <span class="px-slot__name">{{ row.name }}</span>
        </Button>
        <p class="px-slot__figs">
          <router-link
            class="px-slot__link" :to="to('/features', row.id)"
            :data-testid="`projects-features-${row.id}`"
          >⚑ {{ row.stage_bar?.shipped || 0 }}/{{ row.features }} shipped</router-link>
          ·
          <router-link
            class="px-slot__link" :to="to('/tasks', row.id)"
            :data-testid="`projects-tasks-${row.id}`"
          >◎ {{ row.open_tasks }} tasks</router-link>
        </p>
        <p class="px-slot__attention" :class="{ 'is-clear': !row.escaped && !row.open_defects }">
          <PixelSprite v-if="row.escaped || row.open_defects" name="bug" />
          <span :data-testid="`projects-attention-${row.id}`"><router-link
            v-if="row.escaped" class="px-slot__link" :to="to('/features', row.id, { stage: 'escaped' })"
            :data-testid="`projects-escaped-${row.id}`"
          >{{ row.escaped }} escaped</router-link><template v-if="row.escaped && row.open_defects"> · </template><router-link
            v-if="row.open_defects" class="px-slot__link" :to="to('/defects', row.id, { status: 'open' })"
            :data-testid="`projects-defects-${row.id}`"
          >{{ plural(row.open_defects, 'open defect') }}</router-link><template v-if="!row.escaped && !row.open_defects">All clear</template></span>
        </p>
      </li>
    </ul>
  </section>
</template>

<style scoped>
.px-pj .px-pj__all { margin-left: auto; height: auto; padding: 0; }
.px-pj__slots {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr)); gap: 0.75rem;
    padding: 0.9rem 1.1rem 1.1rem;
}
.px-slot {
    display: flex; flex-direction: column; min-width: 0;
    border: 1px solid var(--pl-ink); box-shadow: 2px 2px 0 var(--pl-ink); background: var(--pl-paper);
}
.px-slot__head, .px-slot__figs, .px-slot__attention {
    font-family: var(--pl-font); font-size: 10px; letter-spacing: 0.08em; text-transform: uppercase;
}
.px-slot__head {
    display: flex; justify-content: space-between; gap: 0.5rem;
    padding: 0.35rem 0.6rem; border-bottom: 1px solid var(--pl-ink); color: var(--pl-ink);
}
.px-slot .px-slot__pick {
    justify-content: flex-start; gap: 0.6rem; min-width: 0; height: auto;
    padding: 0.6rem 0.6rem 0.2rem; border-radius: 0; text-align: left; color: var(--pl-ink); background: none;
}
.px-slot__name { font-size: 14px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.px-slot__pick:hover .px-slot__name { text-decoration: underline; }
.px-slot__figs { padding: 0 0.6rem 0.55rem 3.15rem; color: var(--pl-muted); }
.px-slot__attention {
    display: flex; align-items: center; gap: 0.5rem; margin-top: auto;
    padding: 0.4rem 0.6rem; border-top: 1px dashed var(--pl-rule); color: var(--pl-warn);
}
.px-slot__attention.is-clear { color: var(--pl-muted); }
.px-slot__link { color: inherit; text-decoration: none; }
.px-slot__link:hover { text-decoration: underline; }

.px-pj--compact .px-pj__slots { grid-template-columns: minmax(0, 1fr); }
.px-pj--compact .px-slot .px-slot__pick { min-height: 44px; }
</style>
