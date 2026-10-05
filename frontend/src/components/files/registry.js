// The one map from `FileMeta.preview` to a renderer (I6). The server decided
// the kind (lib/files/classify.py); nothing here looks at the bytes or the
// extension again (I7) — a wrong pick is fixed in the classifier, not here.
//
// `mode` is the viewer lifecycle's split: `download_only` kinds never fetch
// content. Renderers are async so a view's dependencies (Monaco above all)
// load only when that view mounts (I4).
import { defineAsyncComponent } from 'vue'

const CodeView = defineAsyncComponent(() => import('./FileCodeView.vue'))
const MediaView = defineAsyncComponent(() => import('./FileMediaView.vue'))
const BinaryCard = defineAsyncComponent(() => import('./FileBinaryCard.vue'))

const source = { mode: 'ready', component: CodeView }
const media = { mode: 'ready', component: MediaView }
const downloadOnly = { mode: 'download_only', component: BinaryCard }

// markdown / html / notebook / csv show their source until their rendered
// views land; the source form is always a valid view of a text kind.
export const PREVIEW_REGISTRY = Object.freeze({
  code: source,
  markdown: source,
  html: source,
  notebook: source,
  csv: source,
  svg: media,
  image: media,
  pdf: media,
  audio: media,
  video: media,
  binary: downloadOnly,
  too_large: downloadOnly,
})

export function rendererFor(preview) {
  return PREVIEW_REGISTRY[preview] || downloadOnly
}
