Fix regin defect #250, reported against feature #124 (Unknown defects):

Sesssion页的Trace span展示Running span有问题，‘Sleep 1' 命令是不可能运行这么长时间的，所以肯定是bug,修复一下

Element:   div.running-card.cursor-pointer.transition-colors.hover:border-emerald-300
Text:      "running 34m16sSHELLBash: sleep 1"
Route:     /trace/sessions/60af5fa1-21d3-418a-8d5d-595285d46b07
Box:       1143×76 at (254, 315)
Selector:  div:has-text("running 34m16sSHELLBash: sleep 1")

Component stack (innermost first):
  src/components/conversation/cards/RunningSpanCard.vue
  src/components/SessionConversationView.vue
  src/views/SessionTraceView.vue
  src/views/TraceView.vue
  src/components/AppLayout.vue

Diagnose with:
node scripts/dom-measure.mjs \
  --route '/trace/sessions/60af5fa1-21d3-418a-8d5d-595285d46b07' \
  --reveal 'wait:3000' \
  --explain 'div:has-text("running 34m16sSHELLBash: sleep 1")'
suspect paths: frontend/src/components/conversation/cards/RunningSpanCard.vue