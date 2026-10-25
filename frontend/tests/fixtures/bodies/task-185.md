Now the right hide detail page took a large space just to show 'details' button', and it's not a common button user want to use, redesign it: maybe small or other UI/UX design.

Element:   button.inline-flex.items-center.justify-center.cursor-pointer
Text:      "Details"
Route:     /trace/sessions/aad08bfc-bc2e-4b58-89dc-396fdf0983cf
Box:       73×30 at (1209, 109)
Selector:  button:has-text("Details")

Component stack (innermost first):
  src/components/ui/Button.vue
  src/views/SessionTraceView.vue
  src/views/TraceView.vue
  src/components/AppLayout.vue

Diagnose with:
node scripts/dom-measure.mjs \
  --route '/trace/sessions/aad08bfc-bc2e-4b58-89dc-396fdf0983cf' \
  --reveal 'wait:3000' \
  --explain 'button:has-text("Details")'