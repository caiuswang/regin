Check to see why this would show 'no usage':

Element:   span.w-20.text-right.font-mono.text-xs

Text:      "no usage"

Route:     /features/CAI-257

Box:       80×16 at (1301, 375)

Selector:  div:nth-child(6) > div:nth-child(3) > ul > li:nth-child(1) > span:nth-child(4)

Diagnose with:

node scripts/dom-measure.mjs \

--route '/features/CAI-257' \

--reveal 'wait:3000' \

--explain 'div:nth-child(6) > div:nth-child(3) > ul > li:nth-child(1) > span:nth-child(4)'