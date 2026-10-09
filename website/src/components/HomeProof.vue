<script setup>
import traceShot from '../assets/shots/trace-dark.png'
import traceShotMobile from '../assets/shots/trace-dark-mobile.png'
import traceShotWebp from '../assets/shots/trace-dark.webp'
import traceShotMobileWebp from '../assets/shots/trace-dark-mobile.webp'
</script>

<template>
  <section class="section" aria-labelledby="proof-heading">
    <div class="section-head">
      <p class="eyebrow">Observability, up close</p>
      <h2 id="proof-heading">This is regin tracing the session that built this site</h2>
      <p>Not a mockup — the actual session viewer, open on the run that built the first version of this site (2026-07-16). Every tool call recorded and costed, every turn replayable. When a session goes sideways, this is where you find out why.</p>
    </div>
    <figure class="shot-frame">
      <picture>
        <source media="(max-width: 767px)" type="image/webp" :srcset="traceShotMobileWebp" width="1580" height="1420" />
        <source media="(max-width: 767px)" :srcset="traceShotMobile" width="1580" height="1420" />
        <source type="image/webp" :srcset="traceShotWebp" width="2880" height="1800" />
        <img
          :src="traceShot" width="2880" height="1800" loading="lazy"
          alt="The regin session viewer on this site's first build session: span, duration, context-peak, token and cost tiles, the activity strip with each prompt flagged, and the turn list beside the conversation replay."
        />
      </picture>
      <figcaption>Trace <code>3b3adbbc</code>, the site’s first build, in today’s session viewer: 726 spans over 8 human turns, 1h59m with 64% active, $76.34 including sub-agents. The activity strip flags each prompt; the turn list costs each one.</figcaption>
    </figure>
  </section>
</template>
