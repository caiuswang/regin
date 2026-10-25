Now when develop the feature ledger, it brings a duplicate icon showing with task, fix it.

![image.png](https://uploads.linear.app/54080efa-04a2-4da4-8b26-047f666059b9/d8adcd23-b225-40a1-a33a-65d3eef6a088/8993d18a-6abc-4c01-abb0-5fb39832c678)

Element:   span.sb-item-label

Text:      "Features"

Route:     /trace/sessions/64f9ae14-80be-42b4-bd49-903cfaac9c4f

Box:       167×20 at (67, 565)

Selector:  span:has-text("Features")

Component stack (innermost first):

src/components/ui/Button.vue

src/components/AppSidebar.vue

src/components/AppLayout.vue

Diagnose with:

node scripts/dom-measure.mjs \

--route '/trace/sessions/64f9ae14-80be-42b4-bd49-903cfaac9c4f' \

--reveal 'wait:3000' \

--explain 'span:has-text("Features")'