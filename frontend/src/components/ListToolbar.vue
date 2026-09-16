<script setup>
// The narrowing controls a Pipeline list offers, in one order: search, then
// facets, then sort, then reset.
//
// One component rather than one per tab, because the order IS the rule — a
// reader who learns where the stage filter sits on Features must not have to
// learn it again on Defects. The component owns no state: every control emits,
// and the view writes the value into the URL, which is where a list's state
// lives (R1). That is what makes a narrowed list a link.
//
// Search submits on Enter and never per keystroke: a debounce is a timer
// heuristic, and a submit is a navigation.
import Button from './ui/Button.vue'
import Icon from './ui/Icon.vue'
import Input from './ui/Input.vue'
import Select from './ui/Select.vue'

defineProps({
  q: { type: String, default: '' },
  searchLabel: { type: String, required: true },
  searchPlaceholder: { type: String, default: 'search' },
  //: [{ key, label, value, options: [{ value, label }] }] — one Select each.
  facets: { type: Array, default: () => [] },
  //: [{ value, label }] over the tab's own sort keys.
  sortOptions: { type: Array, default: () => [] },
  sortKey: { type: String, default: '' },
  sortDir: { type: String, default: 'desc' },
  // Whether anything is narrowed at all. The control is rendered disabled
  // rather than hidden, so its absence never reads as "nothing to reset here".
  dirty: { type: Boolean, default: false },
})
const emit = defineEmits([
  'update:q', 'submit', 'facet', 'sort-key', 'toggle-dir', 'reset',
])
</script>

<template>
  <div class="toolbar" role="search">
    <Input
      class="w-64"
      :model-value="q"
      :aria-label="searchLabel"
      :placeholder="searchPlaceholder"
      @update:model-value="emit('update:q', $event)"
      @keydown.enter.prevent="emit('submit')"
    />

    <span v-for="facet in facets" :key="facet.key" class="toolbar-facet">
      <Select
        :model-value="facet.value"
        :options="facet.options"
        :aria-label="facet.label"
        @update:model-value="emit('facet', facet.key, $event)"
      />
    </span>

    <span v-if="sortOptions.length" class="toolbar-facet">
      <Select
        :model-value="sortKey"
        :options="sortOptions"
        aria-label="Sort by"
        @update:model-value="emit('sort-key', $event)"
      />
      <Button
        variant="secondary"
        size="sm"
        :aria-label="sortDir === 'asc' ? 'Sorted ascending — switch to descending'
          : 'Sorted descending — switch to ascending'"
        data-testid="toolbar-sort-dir"
        @click="emit('toggle-dir')"
      >
        <Icon :name="sortDir === 'asc' ? 'arrow-up' : 'arrow-down'" :size="12" />
      </Button>
    </span>

    <Button
      variant="ghost"
      size="sm"
      :disabled="!dirty"
      data-testid="toolbar-reset"
      @click="emit('reset')"
    >Reset</Button>
  </div>
</template>

<style scoped>
.toolbar { display: flex; flex-wrap: wrap; align-items: center; gap: 0.5rem; }
.toolbar-facet { display: inline-flex; align-items: center; gap: 0.3rem; }
</style>
