<script setup>
import DocPage from '../components/DocPage.vue'
import SiteIcon from '../components/SiteIcon.vue'
import memoryShot from '../assets/shots/memory-dark.png'
import memoryShotMobile from '../assets/shots/memory-dark-mobile.png'
import memoryShotWebp from '../assets/shots/memory-dark.webp'
import memoryShotMobileWebp from '../assets/shots/memory-dark-mobile.webp'
import { SECTION_PAGES } from '../content/topics.js'

const TOC = [
  { id: 'patterns', label: 'Patterns & skills' },
  { id: 'rules', label: 'Rule engines' },
  { id: 'memory', label: 'Agent memory' },
  { id: 'topics', label: 'Topic wikis' },
]
</script>

<template>
  <DocPage
    title="Guidance"
    lead="The supporting layer under the runs: what the agent is told before it acts, what pushes back when it drifts, and what it remembers from past sessions."
    :toc="TOC"
  >
    <h2 id="patterns">Patterns &amp; skills</h2>
    <p>Patterns are local procedure guides for recurring implementation shapes, deployed into the active provider’s skills directory. When one earns its keep, <code>regin pattern promote</code> packages it as a versioned skill. Dashboard: <code>/patterns</code>, <code>/skills</code>; CLI: <code>regin pattern</code>, <code>regin skills</code>. <RouterLink to="/architecture#feedforward">How it works</RouterLink>.</p>

    <h2 id="rules">Rule engines</h2>
    <p>GritQL, Radon and bundle checkers are wired into the agent’s <code>PostToolUse</code> hook, so a violation is reported to the agent in the same turn as the edit that caused it — a rule is a hook that refuses or pushes back, not prose the agent is asked to remember. Dashboard: <code>/rules</code>; CLI: <code>regin rules</code>, <code>regin hooks</code>. <RouterLink to="/architecture#rule-engines">How the engines work</RouterLink>.</p>

    <h2 id="memory">Agent memory</h2>
    <p>Lessons captured from past sessions are ranked, consolidated and superseded as they go stale, then recalled into later sessions when they match the task. Dashboard: <code>/memory</code>; CLI: <code>regin memory</code>. <RouterLink to="/architecture#memory">How memory compounds</RouterLink>.</p>
    <figure class="shot-frame shot-frame-sm">
      <picture>
        <source media="(max-width: 767px)" type="image/webp" :srcset="memoryShotMobileWebp" width="1280" height="1280" />
        <source media="(max-width: 767px)" :srcset="memoryShotMobile" width="1280" height="1280" />
        <source type="image/webp" :srcset="memoryShotWebp" width="2880" height="1800" />
        <img :src="memoryShot" width="2880" height="1800" loading="lazy" alt="The regin memory browser: cross-session memories filterable by lessons, gotchas, facts and procedures." />
      </picture>
      <figcaption>The live memory store on 2026-10-09: 1,077 active memories. The newest lesson was written by the run that rewrote this site’s Pipeline page.</figcaption>
    </figure>

    <h2 id="topics">Topic wikis</h2>
    <p>Per-repo knowledge as a reviewed graph of topics: an agent proposes drafts against the real code, you approve them, the router hands an agent only the topic its task touches, and drift detection flags pages whose files have moved on. Dashboard: <code>/repos/:name/topics</code>; CLI: <code>regin topics</code>, <code>regin wiki</code>.</p>
    <ul class="mini-list">
      <li v-for="page in SECTION_PAGES" :key="page.to">
        <RouterLink :to="page.to" class="mini-row focus-visible:ring">
          <span class="mini-title">{{ page.to === '/topics' ? 'Topic wikis' : page.label }}</span>
          <span class="mini-body">{{ page.summary ?? 'What a topic is, where the graph lives, and its lifecycle.' }}</span>
          <SiteIcon name="arrow-right" :size="16" />
        </RouterLink>
      </li>
    </ul>
  </DocPage>
</template>
