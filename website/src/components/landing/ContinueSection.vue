<template>
  <section aria-labelledby="cta-h" data-screen-label="Continue" class="land-panel continue-panel">
    <div class="hud-bar land-hud continue-hud">
      <span>WORLD 1 CLEAR</span>
      <span>CONTINUE? ▶ YES</span>
    </div>
    <div class="level-grid continue-grid">
      <div class="continue-main">
        <h2 id="cta-h" class="continue-h2">Still here? Then it's probably time to run it.</h2>
        <p class="land-lead continue-lead">Runs locally — one setup script, then install the hooks to wire it into your agent. Early beta; pin a commit if you need stability.</p>
        <div class="land-ctas">
          <a href="https://github.com/caiuswang/regin" class="key-primary focus-visible:ring">▶ CLONE ON GITHUB</a>
          <RouterLink to="/getting-started" class="land-text-link focus-visible:ring">Read the guide first →</RouterLink>
        </div>
      </div>
      <div class="continue-main">
        <div class="continue-fact">
          <h3 class="continue-h3">WHAT REGIN IS NOT</h3>
          <p class="continue-p">Not a chat UI, not a model, not a hosted service. It assumes you already have a coding agent and a codebase, and runs beside them.</p>
        </div>
        <div class="continue-fact">
          <h3 class="continue-h3">SUPPORTED AGENTS</h3>
          <p class="continue-p"><b class="tone-ink">Claude Code — full:</b> hooks, rules, trace, memory and task runs. <b class="tone-ink">Kimi Code — task runs only</b> (ACP or tmux), experimental.</p>
        </div>
      </div>
    </div>
  </section>
</template>
