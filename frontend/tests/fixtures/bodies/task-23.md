Work on Linear issue CAI-212:

<issue identifier="CAI-212">
<title>fix subagent span click issue</title>
<description>
Now whenever we clicked a subagent's span, it seems like it will change the last span showing at focus mode. The span will change to show previous span, and later span are gong forever even we close the subagent view and open it again.

Before: Element:   div.flex.items-baseline.gap-[9px].cursor-pointer

Text:      "ReaduseNotificationCenter.js1ms"

Route:     /trace/sessions/e35f4ad3-3287-49c4-aed9-e8cc7252624b?agent=af7550d056df024a4

Box:       457×32 at (1032, 490)

Selector:  div:has-text("ReaduseNotificationCenter.js1ms")

Component stack (innermost first):

src/components/conversation/cards/InlineToolRow.vue

src/components/SessionConversationView.vue

src/components/TraceAgentPane.vue

src/views/SessionTraceView.vue

src/views/TraceView.vue

src/components/AppLayout.vue

Diagnose with:

node scripts/dom-measure.mjs \

--route '/trace/sessions/e35f4ad3-3287-49c4-aed9-e8cc7252624b?agent=af7550d056df024a4' \

--reveal 'wait:3000' \

--explain 'div:has-text("ReaduseNotificationCenter.js1ms")'

after: 

Element:   div.flex.items-baseline.gap-[9px].cursor-pointer

Text:      "Shellcd /Users/taowang/regin/frontend/src/composables && grep -n "cardKey|stamp"
Route:     /trace/sessions/e35f4ad3-3287-49c4-aed9-e8cc7252624b?agent=af7550d056df024a4

Box:       457×31 at (1032, 490)

Selector:  [data-component="src/components/conversation/cards/BashCard.vue"] div

Component stack (innermost first):

src/components/conversation/cards/BashCard.vue

src/components/SessionConversationView.vue

src/components/TraceAgentPane.vue

src/views/SessionTraceView.vue

src/views/TraceView.vue

src/components/AppLayout.vue

Diagnose with:

node scripts/dom-measure.mjs \

--route '/trace/sessions/e35f4ad3-3287-49c4-aed9-e8cc7252624b?agent=af7550d056df024a4' \

--reveal 'wait:3000' \

--explain '[data-component="src/components/conversation/cards/BashCard.vue"] div'
</description>
<team name="Caiuswang"/>
<project name="regin">agent harness tool</project>
</issue>