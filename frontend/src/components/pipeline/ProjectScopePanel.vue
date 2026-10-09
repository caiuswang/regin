<script setup>
// One project picked (1d): the breadcrumb head replaces the Projects panel.
// The project's features are the Overview's Features panel, not listed here.
import Button from '../ui/Button.vue'
import Icon from '../ui/Icon.vue'
import SegmentBar from './SegmentBar.vue'
import { loopBar } from '../../utils/pipelineFormat'
import { money } from '../../utils/ledgerFormat'

defineProps({
  head: { type: Object, required: true },
  featuresTo: { type: Object, required: true },
  // The project's settings page; absent where there is no project to manage.
  manageTo: { type: String, default: '' },
})
const emit = defineEmits(['all'])
</script>

<template>
  <section class="pl-panel ps" data-pl-part="panel" data-testid="project-scope" aria-labelledby="ps-title">
    <header class="ps-head" data-testid="project-head">
      <div class="ps-crumb">
        <nav class="ps-trail" aria-label="Breadcrumb">
          <Button variant="link" size="sm" class="ps-trail__all" data-testid="project-head-all" @click="emit('all')">
            {{ head.breadcrumb[0] }}
          </Button>
          <span aria-hidden="true">/</span>
        </nav>
        <div class="ps-title-row">
          <h2 id="ps-title" class="ps-name">{{ head.breadcrumb[1] }}</h2>
          <router-link
            v-if="manageTo" :to="manageTo"
            class="ps-manage focus-visible:outline-2 focus-visible:outline-blue-500"
            data-testid="project-head-manage"
          ><Icon name="settings" :size="13" />Project settings</router-link>
        </div>
      </div>
      <div class="ps-progress">
        <SegmentBar :parts="loopBar(head.stage_bar)" :height="6" />
        <span class="ps-pct">{{ head.shipped_pct == null ? 'nothing live' : `${head.shipped_pct}% shipped` }}</span>
      </div>
      <dl class="ps-figs">
        <div>
          <dt>open tasks</dt>
          <dd><router-link class="ps-link" :to="{ path: '/tasks', query: featuresTo.query }" data-testid="project-head-tasks">{{ head.open_tasks }}</router-link></dd>
        </div>
        <div :class="{ 'is-warn': head.open_defects }">
          <dt>open defects</dt>
          <dd><router-link class="ps-link" :to="{ path: '/defects', query: { ...featuresTo.query, status: 'open' } }" data-testid="project-head-defects">{{ head.open_defects }}</router-link></dd>
        </div>
        <div><dt>spent</dt><dd>{{ head.spent_usd == null ? '—' : money(head.spent_usd) }}</dd></div>
      </dl>
    </header>

  </section>
</template>

<style scoped>
.ps-link { color: inherit; text-decoration: none; }
.ps-link:hover { text-decoration: underline; }
.ps-head {
    display: flex; align-items: center; gap: 1.5rem; flex-wrap: wrap;
    padding: 0.9rem 1.1rem;
}
.ps-crumb { min-width: 0; }
.ps-trail { display: flex; align-items: center; gap: 0.3rem; font-size: 12px; color: var(--color-fg-faint); }
.ps-trail .ps-trail__all { font-size: 12px; color: var(--color-fg-muted); }
.ps-title-row { display: flex; align-items: center; gap: 0.4rem; }
.ps-manage {
    display: inline-flex; align-items: center; gap: 0.3rem; padding: 0.2rem 0.5rem;
    border: 1px solid var(--color-border); border-radius: var(--radius-sm);
    font-size: 12px; font-weight: 600; color: var(--color-fg-muted); text-decoration: none;
}
.ps-manage:hover { color: var(--color-fg); background: var(--color-surface-2); }
.ps-name { font-size: 17px; font-weight: 700; color: var(--color-fg); }
.ps-progress { flex: 1 1 12rem; min-width: 10rem; display: flex; flex-direction: column; gap: 0.25rem; align-items: flex-end; }
.ps-pct { font-size: 12px; font-weight: 600; color: var(--color-fg); }
.ps-figs { display: flex; gap: 1.1rem; font-size: 12.5px; }
.ps-figs div { display: flex; flex-direction: row-reverse; gap: 0.3rem; align-items: baseline; }
.ps-figs dt { color: var(--color-fg-muted); }
.ps-figs dd { font-weight: 700; color: var(--color-fg); }
.ps-figs .is-warn dd, .ps-figs .is-warn dt { color: var(--color-warning-strong); }
</style>
