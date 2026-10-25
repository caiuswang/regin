Fix regin defect #190, reported against feature #112 (attribute all current defects):

Spec premise wrong: #159 is not gone. docs/feature-pipeline.md was deleted, but commit 5a2ccf2e (feature #93) carried the same false claim into ARCHITECTURE.md:473 ('lib/experiments.py is the tool for that comparison'), and lib/experiments.py is still the pattern-conceal experiment. The '#159 is resolved as gone' scenario would have closed a live defect.
reported in session 005e7856-fd68-418b-b2ac-2b63bb2926c0
suspect paths: ARCHITECTURE.md