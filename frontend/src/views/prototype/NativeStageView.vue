<script setup>
// PROTOTYPE — throwaway (CAI-269). Do not ship. /prototype/native-stage
//
// The question: what does the ledger show for a feature whose stage cannot be
// stale? Four variants over the same three surfaces — the ledger header, the
// index, a task's link cards — each surface holding a Linear key and a native
// key at once, because the mixed page is the thing that has to stay coherent.
//
// The Linear rows never change between variants. They are the control.
//
// The variant picker is a raw <button>, not the app's <Button> primitive, for
// the reason ProtoSwitcher.vue gave on the last prototype branch: scaffolding
// that looks like the app gets judged as part of it.
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Badge from '../../components/Badge.vue'
import ProtoNativeLinkCard from './ProtoNativeLinkCard.vue'
import ProtoNativeStageLine from './ProtoNativeStageLine.vue'
import { STAGE_COLOR, money } from '../../utils/ledgerFormat'
import { LINEAR, LINKS, NATIVE, UNFETCHED, VARIANTS }
  from './nativeStageFixtures'

const route = useRoute()
const router = useRouter()
const variant = ref(String(route.query.v || 'A').toUpperCase())
const rows = [NATIVE, LINEAR, UNFETCHED]

const current = computed(
  () => VARIANTS.find(v => v.key === variant.value) || VARIANTS[0])

function pick(key) {
  variant.value = key
  router.replace({ query: { ...route.query, v: key } })
}
</script>

<template>
  <div class="proto">
    <header class="proto-head">
      <p class="eyebrow">PROTOTYPE · CAI-269 · throwaway</p>
      <h1>A stage that can never be stale</h1>
      <p class="lede">
        <code>regin:42</code> is a parent task. Its <code>issue_cache</code>
        row is rewritten by the same transaction that changes the fact
        (CAI-267), so its stage is current by construction: there is no quota
        to spend, no fetch that can fail, and no age to report.
        <code>CAI-257</code> is a Linear key and can lag by hours.
      </p>
      <div class="pick">
        <button
          v-for="v in VARIANTS" :key="v.key"
          class="pick-btn focus-visible:outline-2 focus-visible:outline-blue-400"
          :class="v.key === variant ? 'is-on' : ''"
          @click="pick(v.key)"
        >{{ v.key }} · {{ v.name }}</button>
      </div>
      <p class="blurb">{{ current.blurb }}</p>
      <p class="keys">keys on — <b>{{ current.keysOn }}</b></p>
    </header>

    <section class="card">
      <h2>1 · The ledger header</h2>
      <p class="sub">
        Two page visits, one after the other. <code>/features/regin:42</code>
        then <code>/features/CAI-257</code>.
      </p>
      <div class="stack">
        <ProtoNativeStageLine
          :feature-key="NATIVE.key" :stage="NATIVE.stage"
          :synced-at="NATIVE.stage_synced_at" :live="true" :variant="variant"
        />
        <hr />
        <ProtoNativeStageLine
          :feature-key="LINEAR.key" :stage="LINEAR.stage"
          :synced-at="LINEAR.stage_synced_at" :variant="variant"
        />
      </div>
    </section>

    <section class="card">
      <h2>2 · The index</h2>
      <p class="sub">
        <code>/features</code> renders no freshness today and no Refresh
        control — <code>stage_synced_at</code> rides in the payload and
        nothing reads it. The only thing telling the two apart is the key.
      </p>
      <table class="index">
        <thead>
          <tr>
            <th scope="col">Feature</th>
            <th scope="col">Stage</th>
            <th scope="col" class="num">Sessions</th>
            <th scope="col" class="num">Cost</th>
            <th scope="col" class="num">Defects</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="row.key">
            <td>
              <span class="key">{{ row.key }}</span>
              <span class="title">{{ row.title || '—' }}</span>
            </td>
            <td>
              <Badge
                v-if="row.stage" :color="STAGE_COLOR[row.stage.stage]"
                :label="row.stage.stage"
              />
              <span v-else class="nostage">stage not fetched</span>
            </td>
            <td class="num">{{ row.sessions || '—' }}</td>
            <td class="num">
              <template v-if="row.cost_usd === null">none declared</template>
              <template v-else>≤ {{ money(row.cost_usd) }}</template>
            </td>
            <td class="num">{{ row.defects.open }} / {{ row.defects.total }}</td>
          </tr>
        </tbody>
      </table>
    </section>

    <section class="card">
      <h2>3 · A task's links</h2>
      <p class="sub">
        Task 51 pasted both refs. The native card's URL is a route in this
        same SPA, so today it would open <code>/tasks/42</code> in a new tab
        behind an external-link glyph.
      </p>
      <ul class="links">
        <ProtoNativeLinkCard
          v-for="link in LINKS" :key="link.id" :link="link"
          :variant="variant"
        />
      </ul>
    </section>

    <section class="card">
      <h2>What the page reads</h2>
      <pre class="payload">{{ JSON.stringify({
        key: NATIVE.key, stage: NATIVE.stage,
        stage_synced_at: NATIVE.stage_synced_at,
        stage_live: NATIVE.stage_live,
      }, null, 2) }}</pre>
      <p class="sub">
        <code>stage_live</code> is the candidate: a capability the provider
        declares and the cache carries, so the page keys on
        <em>can this lag</em> rather than on <code>provider_id</code>. Variant
        A needs no such field at all.
      </p>
    </section>
  </div>
</template>

<style scoped>
.proto {
    max-width: 64rem; margin: 0 auto; padding-block: 1.5rem 5rem;
    padding-inline: 1rem; display: flex; flex-direction: column; gap: 1rem;
}
.proto-head { display: flex; flex-direction: column; gap: 0.5rem; }
.eyebrow {
    font-size: 10.5px; letter-spacing: 0.08em; font-weight: 700;
    color: var(--color-fg-faint);
}
h1 { font-size: 1.4rem; font-weight: 700; color: var(--color-fg); }
h2 { font-size: 0.9rem; font-weight: 600; color: var(--color-fg); }
.lede, .sub, .blurb, .keys {
    font-size: 12px; color: var(--color-fg-muted); max-width: 60ch;
}
.keys { color: var(--color-fg-subtle); }
.pick { display: flex; flex-wrap: wrap; gap: 0.35rem; margin-top: 0.25rem; }
.pick-btn {
    border: 1px solid var(--color-border); border-radius: 999px;
    background: var(--color-surface); color: var(--color-fg-muted);
    font-size: 11.5px; padding: 0.2rem 0.6rem; cursor: pointer;
}
.pick-btn.is-on {
    background: var(--color-fg); color: var(--color-surface);
    border-color: var(--color-fg);
}
.card {
    border: 1px solid var(--color-border); border-radius: var(--radius-xl);
    background: var(--color-surface); padding: 1rem 1.1rem;
    display: flex; flex-direction: column; gap: 0.6rem;
}
.stack { display: flex; flex-direction: column; gap: 0.75rem; }
hr { border: 0; border-top: 1px dashed var(--color-border); }
.index { width: 100%; border-collapse: collapse; font-size: 12px; }
.index th {
    text-align: left; font-size: 10.5px; text-transform: uppercase;
    letter-spacing: 0.05em; color: var(--color-fg-faint);
    padding-bottom: 0.35rem; border-bottom: 1px solid var(--color-border);
}
.index td {
    padding: 0.5rem 0.4rem 0.5rem 0;
    border-bottom: 1px solid var(--color-border-subtle);
    color: var(--color-fg-muted);
}
.index .num { text-align: right; font-variant-numeric: tabular-nums; }
.key {
    font-family: var(--font-mono, monospace); font-weight: 600;
    color: var(--color-primary); margin-right: 0.5rem;
}
.title { color: var(--color-fg-muted); }
.nostage {
    font-size: 11px; font-style: italic; color: var(--color-fg-faint);
}
.links { display: flex; flex-direction: column; gap: 0.5rem; padding: 0; }
.payload {
    font-family: var(--font-mono, monospace); font-size: 11px;
    background: var(--color-surface-2); border-radius: var(--radius-lg);
    padding: 0.6rem 0.75rem; overflow-x: auto; color: var(--color-fg-muted);
}
</style>
