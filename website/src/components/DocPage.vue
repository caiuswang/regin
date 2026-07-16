<script setup>
defineProps({
  title: { type: String, required: true },
  lead: { type: String, default: '' },
  toc: { type: Array, default: () => [] },
})
</script>

<template>
  <div class="docs-layout">
    <article>
      <slot name="subnav" />
      <h1 class="page-title">{{ title }}</h1>
      <p v-if="lead" class="page-lead">{{ lead }}</p>
      <slot />
    </article>
    <aside v-if="toc.length" class="toc">
      <div class="toc-inner">
        <h2>On this page</h2>
        <ul>
          <li v-for="item in toc" :key="item.id">
            <a :href="`#${item.id}`" class="focus-visible:ring">{{ item.label }}</a>
          </li>
        </ul>
      </div>
    </aside>
  </div>
</template>
