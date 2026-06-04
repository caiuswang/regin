<script setup>
import Badge from './Badge.vue'

defineProps({
  detail: { type: Object, required: true },
})

function pretty(obj) {
  if (obj === null || obj === undefined) return ''
  return JSON.stringify(obj, null, 2)
}

function relevantSubschema(detail) {
  if (!detail?.schema || !detail?.drift?.field_path) return null
  const parts = detail.drift.field_path.split('.').filter(Boolean)
  let node = detail.schema
  for (let i = 0; i < parts.length - 1; i++) {
    const part = parts[i].replace(/\[\d+\]$/, '')
    if (!node?.properties?.[part]) return node
    node = node.properties[part]
    if (node?.items) node = node.items
  }
  return node
}
</script>

<template>
  <div class="detail-grid">
    <section>
      <div class="detail-heading">Schema</div>
      <dl class="meta-kv">
        <dt>Baseline</dt>
        <dd><code class="cell-code">{{ detail.baseline_path }}</code></dd>
        <dt>Overlay</dt>
        <dd class="meta-kv__overlay">
          <code class="cell-code">{{ detail.overlay_path }}</code>
          <Badge
            v-if="!detail.overlay_exists"
            color="gray" label="not yet"
          />
        </dd>
      </dl>
    </section>
    <section>
      <div class="detail-heading">Current schema (slice)</div>
      <pre class="code-block">{{ pretty(relevantSubschema(detail)) }}</pre>
    </section>
    <section v-if="detail.proposed_change">
      <div class="detail-heading">Ratify would add</div>
      <pre class="code-block code-block-add">{{ pretty({ [detail.proposed_change.leaf]: detail.proposed_change.schema_to_insert }) }}</pre>
    </section>
    <section>
      <div class="detail-heading">Raw payload</div>
      <p v-if="!detail.payload" class="empty-state-inline">
        Not in <code class="cell-code">~/.claude/hook-payloads.jsonl</code> (rotated past).
      </p>
      <pre v-else class="code-block">{{ pretty(detail.payload) }}</pre>
    </section>
  </div>
</template>

<style scoped>
.meta-kv {
  display: grid;
  grid-template-columns: 6rem 1fr;
  gap: 0.25rem 1rem;
  margin: 0;
  font-size: 0.8125rem;
}
.meta-kv dt { color: #94A3B8; font-weight: 500; }
.meta-kv dd { margin: 0; color: #1E293B; min-width: 0; }
.meta-kv__overlay { display: flex; align-items: center; gap: 0.5rem; flex-wrap: wrap; }

/* Code blocks — dark only because they show code/JSON. */
.code-block {
  background: #0F172A; color: #E2E8F0;
  padding: 0.625rem 0.75rem;
  border-radius: 0.5rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.75rem; line-height: 1.55;
  max-height: 22rem;
  overflow: auto; margin: 0;
  white-space: pre;
}
.code-block-add {
  background: #052E2A;
  color: #BBF7D0;
  box-shadow: inset 3px 0 0 #10B981;
}

.detail-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 1rem;
  padding: 0.875rem 1.125rem;
}
.detail-heading {
  font-size: 0.6875rem; font-weight: 600;
  color: #64748B;
  text-transform: uppercase; letter-spacing: 0.06em;
  margin-bottom: 0.375rem;
}

.empty-state-inline {
  font-size: 0.8125rem;
  color: #64748B;
  margin: 0;
}
</style>
