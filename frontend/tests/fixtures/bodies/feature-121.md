Attribute and fix the open defects filed from the f119/integration full-suite runs (198-207).

Rule: blame the feature whose commit caused the defect; if that commit has no feature row, blame the feature owning the spec's surface; fall back to #121 only when neither exists. A defect mixing independent causes is split (edit narrows the original, open records each remainder against its own feature). A failing spec is judged regression vs stale spec vs load/host dependence before fixing; fixes use identifier- or condition-based waits only, and list specs scope to fixtures they seeded.

Outcome: 198-211 attributed and closed (202 split into 208/209/210, 207 into 211). The layout issue found under 210 (0px conversation area with the composer open at 1280x720) is #212, left open and unattributed — it needs a layout decision.
