<script setup>
// What is open in a project right now: the defects nobody has closed and the
// tasks most recently touched. Each list is capped server-side, so the count
// beside it comes from the rollup, not from the rows it happens to hold.
import Badge from '../Badge.vue'

defineProps({
  defects: { type: Array, required: true },
  defectsOpen: { type: Number, default: 0 },
  tasks: { type: Array, required: true },
  scope: { type: String, required: true },
})

const TASK_COLOR = {
  idea: 'gray', armed: 'purple', queued: 'cyan', running: 'blue',
  done: 'green', failed: 'red',
}

</script>

<template>
  <div class="pow">
    <section class="pow__col" data-testid="project-open-defects">
      <h3 class="section-heading pow__head">
        Open defects <span class="pow__count">{{ defectsOpen }}</span>
      </h3>
      <p v-if="!defects.length" class="pow__empty">Nothing open.</p>
      <ul v-else class="pow__list">
        <li v-for="d in defects" :key="d.id" class="pow__row">
          <router-link :to="`/defects/${d.id}`" class="table-link pow__title">{{ d.symptom }}</router-link>
          <span class="pow__meta">{{ d.feature_title || 'unattributed' }}</span>
        </li>
      </ul>
      <router-link
        v-if="defectsOpen > defects.length"
        :to="{ path: '/defects', query: { project: scope } }"
        class="pow__more"
      >all {{ defectsOpen }} open defects</router-link>
    </section>

    <section class="pow__col" data-testid="project-recent-tasks">
      <h3 class="section-heading pow__head">Recent tasks</h3>
      <p v-if="!tasks.length" class="pow__empty">No tasks filed here.</p>
      <ul v-else class="pow__list">
        <li v-for="t in tasks" :key="t.id" class="pow__row">
          <router-link :to="`/tasks/${t.id}`" class="table-link pow__title">{{ t.title }}</router-link>
          <Badge :color="TASK_COLOR[t.status] || 'gray'" :label="t.status" />
        </li>
      </ul>
      <router-link :to="{ path: '/tasks', query: { project: scope } }" class="pow__more">
        every task in this project
      </router-link>
    </section>
  </div>
</template>

<style scoped>
.pow { display: grid; grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr)); gap: 1.5rem; }
.pow__col { min-width: 0; }
.pow__head { display: flex; align-items: baseline; gap: 0.5rem; }
.pow__count { font-weight: 400; color: var(--color-text-muted, #6b7280); }
.pow__list { list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--color-border, #e5e7eb); }
.pow__row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    padding: 0.4375rem 0.25rem;
    border-bottom: 1px solid var(--color-border, #e5e7eb);
    min-width: 0;
}
.pow__title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.pow__meta {
    flex-shrink: 0;
    max-width: 40%;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    font-size: 0.75rem;
    color: var(--color-text-muted, #6b7280);
}
.pow__empty { font-size: 0.8125rem; color: var(--color-text-muted, #6b7280); }
.pow__more { display: inline-block; margin-top: 0.5rem; font-size: 0.8125rem; }
</style>
