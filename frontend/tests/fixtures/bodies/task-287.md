Fix regin defect #219, reported against feature #125 (Pipeline所有页面以及操作逻辑评估以及重新设计-二期):

check why would that happens, what does that means for 'checked-yet-not-written' in feature defects part.
Element:   span
Text:      "1 check-not-yet-written"
Route:     /features/125
Box:       126×16 at (1169, 434)
Selector:  span:has-text("1 check-not-yet-written")

Component stack (innermost first):
  src/components/features/FeatureFigures.vue
  src/views/FeatureLedgerView.vue
  src/views/PipelineView.vue
  src/components/AppLayout.vue

Diagnose with:
node scripts/dom-measure.mjs \
  --route '/features/125' \
  --reveal 'wait:3000' \
  --explain 'span:has-text("1 check-not-yet-written")'