remove this annoying inbox badage:

Element:   div.toast-folded
Text:      "1 more folded into the Inbox badge"
Route:     /trace/sessions/fc8d08ff-0df2-4a0e-adbc-28f107e698be
Box:       229×30 at (1089, 636)
Selector:  [data-component="src/components/notifications/NotificationHost.vue"] div

Component stack (innermost first):
  src/components/notifications/NotificationHost.vue
  src/components/AppLayout.vue

Diagnose with:
node scripts/dom-measure.mjs \
  --route '/trace/sessions/fc8d08ff-0df2-4a0e-adbc-28f107e698be' \
  --reveal 'wait:3000' \
  --explain '[data-component="src/components/notifications/NotificationHost.vue"] div'