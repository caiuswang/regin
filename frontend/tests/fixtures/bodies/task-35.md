Now when we into the task page, sometimes it will show a warning message showing, find why would that happen and fix it.

Element:   p.task-stale

Text:      "#34 — nothing is behind this run; the runner is gone, so it can be started again"

Route:     /tasks

Box:       1472×26 at (141, 465)

Selector:

Component stack (innermost first):

src/components/tasks/TaskRow.vue

src/components/tasks/TaskGroup.vue

src/components/tasks/TaskListPanel.vue

src/views/TasksView.vue

src/components/AppLayout.vue

Diagnose with:

node scripts/dom-measure.mjs \

--route '/tasks' \

--reveal 'wait:3000' \

--explain ''