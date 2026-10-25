Fix regin defect #185, reported against feature #110 (Pipeline panel page redesign):

Features table clips its Defects/Active columns at >=1024px when the triage rail sits beside it: at 1024px a feature row measures scrollWidth 528 vs clientWidth 298 and .ftt-body overflow:hidden hides the overflow (no pane scroll, so responsive.spec does not catch it). FeatureTrackTable's column tiers key off the viewport (@media 1280), not the table's own width; a container query on .ftt would fix both this and the 768-1023 tier.
reported in session 6e9bbdb8-5009-4057-bb54-790ceeabf468