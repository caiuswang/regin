<script setup>
// A task's relationships to the project-management platform the team already
// runs. regin's own task manager stays deliberately small — no assignee, no
// cycle, no comment thread — and this panel is where that detail is read from
// whoever does own it.
//
// The panel renders nothing platform-specific: it asks the server which
// trackers are configured and what each wants pasted, so a second adapter
// appears here with no edit.
import { onMounted, ref } from 'vue'
import Card from '../Card.vue'
import Button from '../ui/Button.vue'
import Input from '../ui/Input.vue'
import Icon from '../ui/Icon.vue'
import TaskLinkCard from './TaskLinkCard.vue'
import { useTaskLinks } from '../../composables/useTaskLinks.js'

const props = defineProps({
  taskId: { type: Number, required: true },
})

const { links, configured, placeholder, loading, error, busyId,
        load, attach, refresh, detach } = useTaskLinks()

const draft = ref('')
const attachError = ref('')

async function onAttach() {
  const text = draft.value.trim()
  if (!text) return
  attachError.value = await attach(props.taskId, text)
  if (!attachError.value) draft.value = ''
}

async function onRefresh(linkId) {
  attachError.value = await refresh(props.taskId, linkId)
}

async function onDetach(linkId) {
  attachError.value = await detach(props.taskId, linkId)
}

onMounted(() => load(props.taskId))
</script>

<template>
  <Card>
    <h2 class="card-header">Linked issues</h2>

    <form class="tel-attach" data-testid="task-link-attach" @submit.prevent="onAttach">
      <Input
        v-model="draft"
        class="tel-input"
        :placeholder="placeholder"
        :disabled="!configured.length"
        aria-label="Issue URL or identifier"
        data-testid="task-link-input"
      />
      <Button
        type="submit" variant="secondary"
        :disabled="!configured.length || !draft.trim() || busyId !== 0"
        data-testid="task-link-submit"
      >
        <Icon name="plus" :size="13" />
        <span>Link</span>
      </Button>
    </form>

    <p v-if="attachError" class="tel-error" role="alert" data-testid="task-link-attach-error">
      {{ attachError }}
    </p>
    <p v-if="error" class="tel-error" role="alert" data-testid="task-link-load-error">
      {{ error }}
    </p>

    <p v-if="loading" class="tel-note">reading links…</p>
    <p v-else-if="!links.length" class="tel-note" data-testid="task-links-empty">
      This task is not linked to an issue in another tracker.
    </p>
    <ul v-else class="tel-list" data-testid="task-links">
      <TaskLinkCard
        v-for="link in links"
        :key="link.id"
        :link="link"
        :busy="busyId === link.id"
        @refresh="onRefresh"
        @detach="onDetach"
      />
    </ul>
  </Card>
</template>

<style scoped>
.tel-attach {
    display: flex;
    flex-wrap: wrap;
    gap: 0.375rem;
    margin-bottom: 0.5rem;
}
.tel-input {
    flex: 1 1 16rem;
    min-width: 0;
}
.tel-note {
    margin: 0.25rem 0 0;
    font-size: 0.75rem;
    color: var(--color-fg-faint);
}
.tel-error {
    margin: 0.25rem 0 0;
    font-size: 0.75rem;
    color: var(--color-danger-fg, var(--color-fg-muted));
    overflow-wrap: anywhere;
}
.tel-list {
    display: flex;
    flex-direction: column;
    gap: 0.5rem;
    list-style: none;
    padding: 0;
    margin: 0.5rem 0 0;
}
</style>
