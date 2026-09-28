import { reactive } from 'vue'

const dialog = reactive({
  visible: false,
  title: 'Confirm',
  message: 'Are you sure?',
  danger: false,
  links: [],
  resolve: null,
})

// `links` ({ to, label }) are the objects the message names, rendered as
// router links under it so a confirm that names a defect lets you open it.
function confirm(title, message, danger = false, { links = [] } = {}) {
  return new Promise((resolve) => {
    dialog.visible = true
    dialog.title = title || 'Confirm'
    dialog.message = message || 'Are you sure?'
    dialog.danger = danger
    dialog.links = links
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
