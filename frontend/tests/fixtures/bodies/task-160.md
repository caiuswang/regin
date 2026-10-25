Now, when we input some message and this message is queued, and later we cancel this queued prompt, but somehow it shows when the agent session is idle. Check to see why. You could check the log to find the bug and fix it.

Element:   div.mb-3.rounded-lg.border.border-amber-200
Text:      "⏳ 1 queued Too large for 'Everywhere' and 'This session' tabedit✕"
Route:     /trace/sessions/a3caae24-0a15-4cca-8735-03b9b3dbc20a
Box:       1142×62 at (140, 104)
Selector:  

Component stack (innermost first):
  src/components/TraceQueuedPrompts.vue
  src/views/SessionTraceView.vue
  src/views/TraceView.vue
  src/components/AppLayout.vue

Diagnose with:
node scripts/dom-measure.mjs \
  --route '/trace/sessions/a3caae24-0a15-4cca-8735-03b9b3dbc20a' \
  --reveal 'wait:3000' \
  --explain ''