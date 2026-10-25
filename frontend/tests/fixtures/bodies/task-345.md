Fix regin defect #242, reported against feature #125 (Pipeline所有页面以及操作逻辑评估以及重新设计-二期):

Task Graph目前Task状态已经在运行，但是还是显示Starting
Element:   span.tln-top
Text:      "running#300Starting…Stop"
Route:     /tasks?project=26&view=graph
Box:       163×28 at (391, 673)
Selector:  span:has-text("running#300Starting…Stop")

Component stack (innermost first):
  src/components/tasks/TaskLaneNode.vue
  src/components/tasks/TaskLaneGraph.vue
  src/views/TasksView.vue
  src/views/PipelineView.vue
  src/components/AppLayout.vue

Diagnose with:
node scripts/dom-measure.mjs \
  --route '/tasks?project=26&view=graph' \
  --reveal 'wait:3000' \
  --explain 'span:has-text("running#300Starting…Stop")'