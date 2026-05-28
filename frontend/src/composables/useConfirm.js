import { reactive } from 'vue'

const dialog = reactive({
  visible: false,
  title: 'Confirm',
  message: 'Are you sure?',
  danger: false,
  resolve: null,
})

function confirm(title, message, danger = false) {
  return new Promise((resolve) => {
    dialog.visible = true
    dialog.title = title || 'Confirm'
    dialog.message = message || 'Are you sure?'
    dialog.danger = danger
    dialog.resolve = resolve
  })
}

function ok() {
  dialog.visible = false
  if (dialog.resolve) dialog.resolve(true)
  dialog.resolve = null
}

function cancel() {
  dialog.visible = false
  if (dialog.resolve) dialog.resolve(false)
  dialog.resolve = null
}

export function useConfirm() {
  return { dialog, confirm, ok, cancel }
}
