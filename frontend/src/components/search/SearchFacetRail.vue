<script setup>
// The palette's taxonomy, as a column rather than a band.
//
// Scope, kind and the recent queries used to be two horizontal strips above
// the results plus a legend strip below them — three bands of chrome around
// one pane, and the kind chips still went off the right edge whenever a
// scope offered four of them. Vertically they cost width once and scale with
// the kind list instead of with the viewport.
import Icon from '../ui/Icon.vue'

defineProps({
  scopes: { type: Array, default: () => [] },
  facets: { type: Array, default: () => [] },
  recents: { type: Array, default: () => [] },
  legend: { type: Array, default: () => [] },
})
defineEmits(['scope', 'facet', 'recent'])
</script>

<template>
  <div class="facet-rail">
    <p class="rail-label">Scope</p>
    <div class="scope-group" role="group" aria-label="Search scope">
      <button
        v-for="s in scopes"
        :key="s.value"
        type="button"
        class="scope focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        :class="{ 'is-active': s.active }"
        :aria-pressed="s.active"
        :disabled="s.disabled"
        :title="s.disabled ? 'Open a session trace to search inside it' : undefined"
        @click="$emit('scope', s.value)"
      >{{ s.label }}</button>
    </div>

    <p class="rail-label">Kind</p>
    <button
      v-for="f in facets"
      :key="f.label"
      type="button"
      class="facet focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      :class="{ 'is-active': f.active }"
      :aria-pressed="f.active"
      @click="$emit('facet', f.label)"
    >
      <Icon class="facet-glyph" :name="f.icon" :size="14" />
      <span class="facet-label">{{ f.label }}</span>
      <span class="facet-count">{{ f.count }}</span>
    </button>

    <template v-if="recents.length">
      <p class="rail-label is-spaced">Recent</p>
      <button
        v-for="term in recents"
        :key="term"
        type="button"
        class="facet is-recent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        @click="$emit('recent', term)"
      >
        <Icon class="facet-glyph" name="search" :size="14" />
        <span class="facet-label">{{ term }}</span>
      </button>
    </template>

    <div class="rail-foot">
      <p v-for="hint in legend" :key="hint.label" class="rail-hint">
        <kbd v-for="k in hint.keys" :key="k">{{ k }}</kbd>
        <span>{{ hint.label }}</span>
      </p>
    </div>
  </div>
</template>

<style scoped>
.facet-rail {
  flex: 0 0 11.125rem;
  display: flex;
  flex-direction: column;
  padding: 0.75rem 0.625rem 0.625rem;
  border-right: 1px solid var(--color-border);
  background: var(--color-background);
  overflow-y: auto;
}

.rail-label {
  margin: 0 0 0.4375rem;
  padding: 0 0.5rem;
  font-size: var(--sp-t-micro);
  line-height: var(--sp-lh-micro);
  font-weight: 700;
  letter-spacing: 0.085em;
  text-transform: uppercase;
  color: var(--color-fg-faint);
}

.rail-label.is-spaced { margin-top: 1.125rem; }

/* Stacked, not side by side: 11.125rem cannot hold "This session" and
   "Everywhere" in one row at 12px, and a wrapped segmented control reads as
   two broken buttons rather than as one choice. */
.scope-group {
  display: flex;
  flex-direction: column;
  gap: 1px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 9px;
  padding: 2px;
  gap: 2px;
  margin-bottom: 1rem;
}

.scope {
  width: 100%;
  min-width: 0;
  border: 0;
  background: transparent;
  border-radius: 7px;
  padding: 0.375rem 0.5rem;
  text-align: left;
  font-size: var(--sp-t-meta);
  font-weight: 600;
  color: var(--color-fg-subtle);
  cursor: pointer;
  transition: background-color 180ms, color 180ms;
}

.scope:hover:not(.is-active):not(:disabled) {
  background: var(--color-surface-2);
  color: var(--color-fg);
}

.scope.is-active { background: var(--color-primary); color: var(--color-primary-fg); }

.scope:disabled { opacity: 0.45; cursor: not-allowed; }

.facet {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  width: 100%;
  border: 0;
  background: transparent;
  border-radius: 7px;
  padding: 0.375rem 0.5rem;
  cursor: pointer;
  text-align: left;
  font-size: var(--sp-t-meta);
  font-weight: 500;
  color: var(--color-fg-muted);
  transition: background-color 150ms, color 150ms;
}

.facet:hover { background: var(--color-surface-2); color: var(--color-fg); }

.facet.is-active {
  background: var(--color-info-soft);
  color: var(--color-info-strong);
  font-weight: 650;
}

/* Scoped CSS reaches a child component's ROOT element — the Icon's <svg>. */
.facet-glyph { flex-shrink: 0; opacity: 0.75; }

.facet.is-active .facet-glyph { opacity: 1; }

.facet-label { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.facet-count {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--sp-t-micro);
  font-weight: 600;
  color: var(--color-fg-faint);
  font-variant-numeric: tabular-nums;
}

.facet.is-active .facet-count { color: var(--color-info-strong); opacity: 0.75; }

.rail-foot {
  margin-top: auto;
  padding: 0.75rem 0.5rem 0.125rem;
  border-top: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
}

.rail-hint {
  margin: 0;
  display: flex;
  align-items: center;
  gap: 0.3125rem;
  font-size: var(--sp-t-micro);
  line-height: var(--sp-lh-micro);
  color: var(--color-fg-faint);
}

kbd {
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  font-size: var(--sp-t-kbd);
  background: var(--color-surface-2);
  border: 1px solid var(--color-border);
  color: var(--color-fg-muted);
  padding: 1px 5px;
  border-radius: 4px;
}

/* Below the split the rail stops being a column — it is the only chrome the
   reader has left for scope and kind, so it lies down above the results as a
   single scrollable strip rather than disappearing with the preview. */
@media (max-width: 900px) {
  .facet-rail {
    flex: 0 0 auto;
    flex-direction: row;
    align-items: center;
    gap: 0.375rem;
    padding: 0.5rem 0.75rem;
    border-right: 0;
    border-bottom: 1px solid var(--color-border);
    overflow-x: auto;
    overflow-y: hidden;
  }

  .rail-label,
  .rail-foot { display: none; }

  /* Laid down, the rail has the width it lacked in a column. */
  .scope-group { margin-bottom: 0; flex: none; flex-direction: row; }

  .scope { width: auto; flex: none; padding: 0.3125rem 0.5625rem; }

  .facet { width: auto; flex: none; padding: 0.3125rem 0.5625rem; }

  .facet.is-recent { display: none; }

  .facet-glyph { display: none; }
}
</style>
