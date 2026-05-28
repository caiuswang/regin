<script setup>
import { useConfirm } from '../composables/useConfirm'

const { dialog, ok, cancel } = useConfirm()

function onOverlay(e) {
  if (e.target === e.currentTarget) cancel()
}

function onKeydown(e) {
  if (e.key === 'Escape') cancel()
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="dialog.visible"
      class="fixed inset-0 bg-black/40 z-50 flex items-center justify-center"
      @click="onOverlay"
      @keydown="onKeydown"
    >
      <div class="bg-white rounded-lg shadow-xl max-w-sm w-full mx-4 overflow-hidden">
        <div class="px-5 pt-5 pb-3">
          <h3 class="text-base font-semibold text-gray-900 mb-1">{{ dialog.title }}</h3>
          <p class="text-sm text-gray-500 whitespace-pre-line">{{ dialog.message }}</p>
        </div>
        <div class="flex justify-end gap-2 px-5 pb-4">
          <button type="button" class="btn btn-secondary text-sm" @click="cancel">Cancel</button>
          <button
            type="button"
            :class="dialog.danger ? 'btn btn-danger text-sm' : 'btn btn-primary text-sm'"
            @click="ok"
          >
            {{ dialog.title }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>
