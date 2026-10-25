/goal-verified-treenav remove 'kept in trace' text from this:

Element:   span.min-w-0.flex-1.truncate.text-left
Text:      "tool calls · thinking · edits · rule checks · replies · kept in the trace"
Route:     /trace/sessions/53e20008-e090-4c9b-80d2-e7409e484646
Box:       865×19 at (528, 600)
Selector:  [data-component="src/components/ui/Button.vue"] span:nth-child(4)

Component stack (innermost first):
  src/components/ui/Button.vue
  src/components/SessionConversationView.vue
  src/views/SessionTraceView.vue
  src/views/TraceView.vue
  src/components/AppLayout.vue

Diagnose with:
node scripts/dom-measure.mjs \
  --route '/trace/sessions/53e20008-e090-4c9b-80d2-e7409e484646' \
  --reveal 'wait:3000' \
  --explain '[data-component="src/components/ui/Button.vue"] span:nth-child(4)'