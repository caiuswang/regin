<script setup>
import traceShot from '../assets/shots/trace-dark.png'
import traceShotMobile from '../assets/shots/trace-dark-mobile.png'
import traceShotWebp from '../assets/shots/trace-dark.webp'
import traceShotMobileWebp from '../assets/shots/trace-dark-mobile.webp'
</script>

<template>
  <section class="section" aria-labelledby="proof-heading">
    <div class="section-head">
      <h2 id="proof-heading">This is regin tracing the session that built this page</h2>
      <p>Not a mockup — the actual session viewer, open on the actual run. Every tool call recorded and costed, every turn replayable. When a session goes sideways, this is where you find out why.</p>
    </div>
    <figure class="shot-frame">
      <picture>
        <source media="(max-width: 767px)" type="image/webp" :srcset="traceShotMobileWebp" width="1580" height="1420" />
        <source media="(max-width: 767px)" :srcset="traceShotMobile" width="1580" height="1420" />
        <source type="image/webp" :srcset="traceShotWebp" width="2016" height="1232" />
        <img
          :src="traceShot" width="2016" height="1232" loading="lazy"
          alt="The regin session viewer showing this website's build session: the turn list, conversation replay, per-tool cost attribution, and a phase timeline."
        />
      </picture>
      <figcaption>Turn 8 of this page’s build: the design critique came back 30/40, and regin logged the moment the agent was told “fix first, then re-critique” — the instruction that produced the page you're reading.</figcaption>
    </figure>
  </section>
</template>
