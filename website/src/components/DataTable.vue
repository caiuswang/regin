<script setup>
defineProps({
  columns: { type: Array, required: true },
  rows: { type: Array, required: true },
})
</script>

<template>
  <div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th v-for="col in columns" :key="col.key">{{ col.label }}</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="(row, i) in rows" :key="i">
          <td v-for="col in columns" :key="col.key">
            <code v-if="col.code && row[col.key]">{{ row[col.key] }}</code>
            <template v-else>{{ row[col.key] }}</template>
          </td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
