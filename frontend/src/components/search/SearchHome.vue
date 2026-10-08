<script setup>
// The empty query (R1 Home). Everywhere it is a desk — Needs me, Active now,
// Active features, Sessions — and inside a session it is that session's
// chain, under a note saying what typing will search instead.
import Button from '../ui/Button.vue'
import SearchResultSection from './SearchResultSection.vue'

defineProps({
  home: { type: Object, required: true },
  activeId: { type: String, default: null },
})
defineEmits(['select', 'open', 'activate', 'retry'])
</script>

<template>
  <div class="search-home" :data-home="home.chain ? 'chain' : 'desk'">
    <div v-if="home.chain" class="home-inside">
      <p class="home-inside-title">Searching inside #{{ home.traceId.slice(0, 8) }}</p>
      <p class="home-inside-body">
        Type to search this session's spans — prompts, thinking, diffs, shell
        output — and the files it touched.
      </p>
    </div>

    <p v-if="home.status === 'pending'" class="home-note">Loading…</p>
    <div v-else-if="home.status === 'failed'" class="home-failed">
      <p class="home-failed-title">Couldn't load {{ home.chain ? 'this session’s chain' : 'your desk' }}</p>
      <Button size="sm" @click="$emit('retry')">Retry</Button>
    </div>

    <SearchResultSection
      v-for="s in home.sections"
      :key="s.key"
      :label="s.label"
      :count="String(s.total)"
      :aside="s.aside"
      :rows="s.rows"
      :more="s.more"
      :active-id="activeId"
      :class="{ 'home-needs': s.tone === 'needs' && s.rows.length }"
      :data-home-group="s.key"
      @select="$emit('select', $event)"
      @open="$emit('open', $event)"
      @activate="$emit('activate', $event)"
    >
      <p v-if="s.calm" class="home-calm">{{ s.calm }}</p>
      <p v-else-if="home.chain && !s.rows.length" class="home-calm">
        regin has no record of this session yet.
      </p>
    </SearchResultSection>
  </div>
</template>

<style scoped>
.home-inside {
  margin: 0.5rem 0.25rem 0.25rem;
  padding: 0.75rem 0.875rem;
  border: 1px dashed var(--color-border-strong, var(--color-border));
  border-radius: 12px;
}

.home-inside-title {
  margin: 0;
  font-size: var(--sp-t-body);
  font-weight: 600;
  color: var(--color-fg);
}

.home-inside-body {
  margin: 0.25rem 0 0;
  font-size: var(--sp-t-meta);
  line-height: 1.55;
  color: var(--color-fg-subtle);
}

.home-note {
  margin: 0;
  padding: 2.75rem 1.25rem;
  text-align: center;
  font-size: var(--sp-t-meta);
  color: var(--color-fg-faint);
}

.home-failed { padding: 2.75rem 1.25rem; text-align: center; }

.home-failed-title {
  margin: 0 0 0.75rem;
  font-size: var(--sp-t-body);
  font-weight: 600;
  color: var(--color-fg);
}

/* Scoped CSS reaches the section component's ROOT element. */
.home-needs {
  margin: 0.375rem 0 0.25rem;
  padding: 0 0.375rem 0.5rem;
  background: var(--color-warning-soft);
  border: 1px solid color-mix(in srgb, var(--color-warning) 30%, transparent);
  border-radius: 12px;
}

.home-calm {
  margin: 0;
  padding: 0 0.75rem 0.25rem;
  font-size: var(--sp-t-meta);
  color: var(--color-fg-subtle);
}
</style>
