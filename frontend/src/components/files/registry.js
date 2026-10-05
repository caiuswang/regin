// The one map from `FileMeta.preview` to a renderer (I6). The server decided
// the kind (lib/files/classify.py); nothing here looks at the bytes or the
// extension again (I7) — a wrong pick is fixed in the classifier, not here.
//
// `mode` is the viewer lifecycle's split: `download_only` kinds never fetch
// content. `source` is the other side of axis B for a kind that also has a
// rendered form. Renderers are async so a view's dependencies (Monaco above
// all) load only when that view mounts (I4).
import { defineAsyncComponent } from 'vue'

const CodeView = defineAsyncComponent(() => import('./FileCodeView.vue'))
const MediaView = defineAsyncComponent(() => import('./FileMediaView.vue'))
const BinaryCard = defineAsyncComponent(() => import('./FileBinaryCard.vue'))
const MarkdownView = defineAsyncComponent(() => import('./FileMarkdownView.vue'))
const HtmlView = defineAsyncComponent(() => import('./FileHtmlView.vue'))
const CsvView = defineAsyncComponent(() => import('./FileCsvView.vue'))
const NotebookView = defineAsyncComponent(() => import('./FileNotebookView.vue'))

const rendered = (component) => ({ mode: 'ready', component, source: CodeView })
const media = { mode: 'ready', component: MediaView }
const downloadOnly = { mode: 'download_only', component: BinaryCard }

export const PREVIEW_REGISTRY = Object.freeze({
  code: { mode: 'ready', component: CodeView },
  markdown: rendered(MarkdownView),
  html: rendered(HtmlView),
  notebook: rendered(NotebookView),
  csv: rendered(CsvView),
  svg: rendered(MediaView),
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
