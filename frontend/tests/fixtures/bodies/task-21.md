Work on Linear issue CAI-208:

<issue identifier="CAI-208">
<title>Fix notification blocker refresh issue</title>
<description>
Now we use open a blocker notification to answer the question. It seems like when the blocker request routine called again, then the answer card will refresh cause the card data redener and loss some state. Fix that.

request url: /api/agent-messages/blockers

UI component: 

src/components/notifications/BlockerAnswer.vue

src/components/notifications/BlockerPopout.vue
</description>
<team name="Caiuswang"/>
<label>Bug</label>
<project name="regin">agent harness tool</project>
</issue>