<script setup>
// The palette's list column. It renders whichever view the query state is
// in (R1): every kind's group, one kind's Drill, or what an address resolved
// to — plus the notes for a query too short, an answer that is empty, and
// one that could not be asked at all.
import { computed } from 'vue'
import Button from '../ui/Button.vue'
import SearchListAction from './SearchListAction.vue'
import SearchResultSection from './SearchResultSection.vue'
import SearchHome from './SearchHome.vue'
import { KIND_META, MIN_QUERY_CHARS, countText } from '../../utils/searchDocs.js'

const props = defineProps({
  groups: { type: Array, default: () => [] },
  drill: { type: Object, default: null },
  address: { type: Object, default: null },
  home: { type: Object, default: null },
  query: { type: String, default: '' },
  activeId: { type: String, default: null },
  noMatches: { type: Boolean, default: false },
  offline: { type: Boolean, default: false },
  canWiden: { type: Boolean, default: false },
  // The query is too short to run. Distinct from `noMatches`, which means it
  // ran and found nothing — the two want opposite things said.
  held: { type: Boolean, default: false },
  heldLabel: { type: String, default: '' },
})
defineEmits(['select', 'open', 'activate', 'retry', 'retry-all', 'leave-drill', 'widen'])

const label = kind => KIND_META[kind]?.label || kind

// The busiest row in the list sets the bar's full width — this is a relative
// signal ("which of these is densest"), not an absolute one.
const peak = computed(() =>
  Math.max(1, ...props.groups.flatMap(g => g.rows.map(r => r.hits || 0))))

// A failed span group still has the local rows it answered from; saying the
// index failed would hide that they are there, saying nothing would pass a
// window of the session off as the whole of it.
function retryText(group) {
  if (!group.failed) return ''
  if (group.kind === 'span' && group.rows.length) return 'showing loaded spans only · retry'
  return `couldn't reach the ${label(group.kind).toLowerCase()} index · retry`
}

const showOffline = computed(() => props.offline && !props.groups.some(g => g.rows.length))

const missBody = computed(() => {
  const a = props.address
  return a.text.length
    ? `Nothing has that id. Text that mentions “${a.needle}”:`
    : `Nothing has that id. No text mentions “${a.needle}” either.`
})

const drillPending = computed(() => props.drill
  && !props.drill.rows.length && ['pending', 'loading'].includes(props.drill.status))
</script>

<template>
  <div class="search-results content-scroll">
    <!-- With the taxonomy moved into the rail this column is the only thing
         left with nothing in it before the first keystroke, and a blank pane
         beside a populated rail reads as a failed load rather than as a
         waiting one. -->
    <p v-if="held && !groups.length && !drill" class="await-query">
      Type {{ MIN_QUERY_CHARS }} characters to search{{ heldLabel ? ` ${heldLabel}` : '' }}.
    </p>

    <div v-if="showOffline" class="list-note" data-testid="search-offline">
      <p class="list-note-title">Can't reach regin</p>
      <p class="list-note-body">
        Every index request failed, so nothing was searched. Check the
        connection; the query is kept.
      </p>
      <Button size="sm" class="list-note-action" @click="$emit('retry-all')">Retry</Button>
    </div>

    <!-- Narrowing to one session is a filter the reader chose, so an empty
         result under it is a dead end unless widening is offered right here
         rather than as another guess at where the control lives. -->
    <div v-if="noMatches" class="no-matches">
      <p class="no-matches-title">No matches for “{{ query }}”</p>
      <p v-if="canWiden" class="no-matches-body">
        Nothing in this session matches. Span bodies, diffs, shell output and
        prose are all indexed, so the term may simply live elsewhere.
      </p>
      <p v-else class="no-matches-body">
        Features, tasks, defects, every session's prompts, wiki patterns and
        touched files were all searched.
      </p>
      <Button
        v-if="canWiden"
        size="sm"
        class="no-matches-widen"
        @click="$emit('widen')"
      >Search every session</Button>
    </div>

    <template v-if="drill">
      <header class="drill-head">
        <Button size="sm" class="drill-back" @click="$emit('leave-drill')">{{ drill.back }}</Button>
        <span class="drill-label">{{ drill.label }}</span>
        <span class="drill-count">{{ drill.total }}</span>
        <span v-if="drill.query" class="drill-query">matching “{{ drill.query }}”</span>
      </header>
      <SearchResultSection
        :kind="drill.kind"
        :label="drill.label"
        headless
        :rows="drill.rows"
        :pending="drillPending"
        :active-id="activeId"
        :query="query"
        @select="$emit('select', $event)"
        @open="$emit('open', $event)"
      >
        <SearchListAction
          v-if="drill.tail"
          :entry="drill.tail"
          :active="drill.tail.key === activeId"
          @activate="$emit('activate', $event)"
        />
        <p v-else-if="drill.done" class="drill-end">
          {{ drill.total ? `end of list · all ${drill.total} shown` : 'Nothing of this kind matches.' }}
        </p>
      </SearchResultSection>
    </template>

    <SearchHome
      v-else-if="home"
      :home="home"
      :active-id="activeId"
      @select="$emit('select', $event)"
      @open="$emit('open', $event)"
      @activate="$emit('activate', $event)"
      @retry="$emit('retry-all')"
    />

    <template v-else-if="address">
      <div v-if="address.status === 'pending'" class="shimmer-note">resolving…</div>
      <div v-else-if="address.status === 'failed'" class="list-note">
        <p class="list-note-title">Can't reach regin</p>
        <p class="list-note-body">The reference could not be resolved; the query is kept.</p>
        <Button size="sm" class="list-note-action" @click="$emit('retry-all')">Retry</Button>
      </div>
      <div v-if="address.miss" class="list-note is-miss" data-testid="address-miss">
        <p class="list-note-title">{{ address.miss }}</p>
        <p class="list-note-body">{{ missBody }}</p>
      </div>
      <SearchResultSection
        v-if="address.hits.length"
        :label="address.number ? `Id ${address.needle} resolves to` : 'Resolves to'"
        :count="String(address.hits.length)"
        :aside="address.number ? 'one row per kind' : ''"
        :rows="address.hits"
        :active-id="activeId"
        :query="query"
        @select="$emit('select', $event)"
        @open="$emit('open', $event)"
      />
      <SearchResultSection
        v-for="g in address.text"
        :key="g.kind"
        :kind="g.kind"
        :label="g.label"
        :count="String(g.total)"
        :rows="g.rows"
        :more="g.more"
        :active-id="activeId"
        :query="address.needle"
        @select="$emit('select', $event)"
        @open="$emit('open', $event)"
        @activate="$emit('activate', $event)"
      />
    </template>

    <template v-else>
      <SearchResultSection
        v-for="group in groups"
        :key="group.kind"
        :kind="group.kind"
        :label="label(group.kind)"
        :count="countText(group)"
        :rows="group.rows"
        :more="group.more"
        :pending="group.pending"
        :retry-text="retryText(group)"
        :active-id="activeId"
        :query="query"
        :peak="peak"
        @select="$emit('select', $event)"
        @open="$emit('open', $event)"
        @activate="$emit('activate', $event)"
        @retry="$emit('retry', $event)"
      >
        <p v-if="group.kind === 'span' && group.total > group.rows.length" class="group-overflow">
          +{{ group.total - group.rows.length }} more in the trace
        </p>
      </SearchResultSection>
    </template>
  </div>
</template>

<style scoped>
.search-results {
  width: 46%;
  min-width: 320px;
  overflow-y: auto;
  padding: 0.5rem 0.625rem 1rem;
  border-right: 1px solid var(--color-border);
}

.await-query,
.shimmer-note {
  margin: 0;
  padding: 2.75rem 1.25rem;
  text-align: center;
  font-size: var(--sp-t-meta);
  color: var(--color-fg-faint);
}

.no-matches { padding: 2.75rem 1.25rem; text-align: center; }

.no-matches-title,
.list-note-title {
  font-size: var(--sp-t-body);
  font-weight: 600;
  color: var(--color-fg);
  margin: 0;
}

.no-matches-body,
.list-note-body {
  font-size: var(--sp-t-meta);
  color: var(--color-fg-subtle);
  line-height: 1.6;
  margin: 0.375rem auto 0;
  max-width: 24rem;
}

.no-matches-widen,
.list-note-action { margin-top: 0.875rem; }

.list-note {
  margin: 0.5rem 0.25rem;
  padding: 0.875rem 1rem;
  border: 1px dashed var(--color-border-strong, var(--color-border));
  border-radius: 12px;
}

.list-note .list-note-body { margin-left: 0; }

.drill-head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0.25rem 0.5rem;
  border-bottom: 1px solid var(--color-border);
  margin-bottom: 0.375rem;
}

.drill-label {
  font-size: var(--sp-t-micro);
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--color-fg-muted);
  font-weight: 700;
}

.drill-count {
  font-size: var(--sp-t-micro);
  font-weight: 700;
  color: var(--color-fg-muted);
  font-variant-numeric: tabular-nums;
}

.drill-query {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: var(--sp-t-micro);
  color: var(--color-fg-subtle);
}

.drill-end,
.group-overflow {
  font-size: var(--sp-t-micro);
  color: var(--color-fg-subtle);
  padding: 0.5rem 0.75rem 0;
  margin: 0;
  font-family: var(--font-mono, ui-monospace, SFMono-Regular, Menlo, monospace);
}

@media (max-width: 900px) {
  /* min-width has to go with the width: as the only pane it is no longer
     competing with the preview for room, and a 320px floor is wider than the
     panel itself on a small phone — which overflowed the whole modal. */
  .search-results { width: 100%; min-width: 0; border-right: 0; }
}
</style>
