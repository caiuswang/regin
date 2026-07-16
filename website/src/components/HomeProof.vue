<script setup>
import traceShot from '../assets/shots/trace-dark.png'
</script>

<template>
  <section class="section" aria-labelledby="proof-heading">
    <div class="section-head">
      <h2 id="proof-heading">This is regin tracing the session that built this page</h2>
      <p>Not a mockup — the actual session viewer, open on the actual run. Six turns, every tool call recorded, every token attributed. When a session goes sideways, this is where you find out why.</p>
    </div>
    <figure class="shot-frame">
      <img
        :src="traceShot" width="2880" height="1760" loading="lazy"
        alt="The regin session viewer showing this website's build session: a 386-span trace with the turn list, conversation replay, per-tool token attribution, and a phase timeline."
      />
      <figcaption>Turn 6 of this page’s build — the agent confirms the design brief and regin logs the moment, alongside the timeline, cost rollup, and 15.7M cached tokens it took to get here.</figcaption>
    </figure>
  </section>
</template>
