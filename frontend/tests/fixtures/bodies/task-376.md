补充剩余defects细节到Task中， 并执行修复

## Defects (feature #139, open at 2026-10-07)

| Defect | Symptom | Reproduced | Root cause | Fix |
|---|---|---|---|---|
| D-254 | `pipeline-redesign-shell.spec.js` `test_features_triage_rail_folds` reads `boundingBox()` of the features table and gets `null` under the pixel project | `--project=pixel --repeat-each=16` without a visibility wait: 11/16 fail `Cannot read properties of null (reading 'width')` | `PipelineView.vue` hides `.pl-body` (`v-show="themeReady \|\| !themed"`) while the pixel chunk loads; `toHaveText('3')` passes on hidden text, then the box of a `display:none` table is `null`. Same race in `test_features_stage_tabs_expand_and_triage_keys` (a tab's computed background read `""`) | wait for the measured element to be visible before reading its box/style |
| D-253 | `pipeline-theme-pixel.spec.js` hit-targets / dark / light-contrast fail under the combined pipeline+tasks gate, pass alone | concurrent writer + `--repeat-each=4 --workers=6`: hit-targets desktop fails — pixel snapshot has +1 input (38px), +4 keys (28px) over classic | (a) the classic snapshot is taken once the header's `declare-feature` key shows, before the stage counts, the list and the triage rail load; (b) a mounted tab re-reads on every `pipeline` SSE frame, so other specs' writes change the rows between snapshots (control: 2 re-reads per foreign write) | `featuresSettled()` waits for stage counts, no `list-status[data-state=loading]`, no rail `Loading…`; `freezeLiveData()` answers the `pipeline` topic declaration locally so the server never sends the frame |

Suspect paths: `frontend/tests/pipeline-redesign-shell.spec.js`, `frontend/tests/pipeline-theme-pixel.spec.js`. No app code changed — both are test-ordering defects.

## Outcome
Fixed in f34b4977, merged to f139/integration (cbec50bb). D-253/D-254 closed. Remaining shared-DB races logged as D-258 (live-refresh idle window) and D-259 (list-chassis live count).
