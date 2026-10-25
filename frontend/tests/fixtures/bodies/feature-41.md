Work on Linear issue CAI-211:

<issue identifier="CAI-211">
<title>Fix subagent result span</title>
<description>
Now the main conversation's subagent result span shows part of the result,  and not be renderd, fix it.

Element:   div.text-[12.5px].text-slate-700.whitespace-pre-wrap.break-words

Text:      "## Verdict per acceptance item **1. Multi-question ask, step preserved across a "

Route:     /trace/sessions/e35f4ad3-3287-49c4-aed9-e8cc7252624b?agent=af7550d056df024a4

Box:       618×81 at (295, 722)

Selector:  [data-component="src/components/conversation/cards/AgentResultCard.vue"] div:nth-child(2)

Component stack (innermost first):

src/components/conversation/cards/AgentResultCard.vue

src/components/conversation/cards/SubagentCard.vue

<linear-image quality="preview">{"type":"image","attrs":{"src":"https://uploads.linear.app/54080efa-04a2-4da4-8b26-047f666059b9/098e1af8-7fd8-46e3-8031-023d38dea530/71c1697c-a72f-46ed-b75e-b3818a50e1c9?signature=REDACTED","title":"image.png","width":2026,"height":1080}}</linear-image>
</description>
<team name="Caiuswang"/>
<project name="regin">agent harness tool</project>
</issue>