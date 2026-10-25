# Feature #142 — Pipeline 相关操作优化（三期）：创建入口随处可达

> 原始需求：目前在 Feature 和 Task 及其相关操作上有很多不便。比如新建一个关联 Feature 的 Defect 时，在 Feature 页面一般用户很难找到入口；即使进入 Feature 详情页，也需要点击顶部菜单栏（如 Feature、Task、Session 等）跳转到底部，再寻找新建 Defect 的入口，操作很不方便。且当内容较多时，页面滑动距离长，查找更困难。想个办法优化一下这些操作。
>
> 追加需求（task #387）：Feature 页面底下的一些页面内操作新建，缺一些字段，和弹窗也不统一。统一一下目前这些创建方式和交互。Feature 列表页有 Report Defect 按钮，但是没有新建 Task 按钮。
>
> Spec written 2026-10-07 against `master` @ `ea289a81`; R6–R7 added against `1754a1c1`.

## Intent

在 Feature 详情页、Task 详情页和 Features 列表里，为**当前这个 feature** 报一个 defect 或开一个 task，应当**一次点击就能打开表单，不需要滚动**，而且表单里已经填好这个 feature。

同一种东西只有**一个**新建表单：无论从页头、section strip、卡片按钮还是列表展开行进入，打开的都是同一个对话框，字段一样多、交互一样。

## Root cause (as found in code)

| Surface | Today | Gap |
|---|---|---|
| `/features/:id` (`FeatureLedgerView.vue`) | 唯一的 defect 入口是 `FeatureDefects.vue` 卡片里的 `feature-report-defect`，在页面倒数第二张卡片。粘性的 `FeatureSectionNav` 只能跳转，没有操作 | 页面没有往 shell 的 `#pipeline-actions` 槽里 Teleport 任何东西，也没有绑定 `c` 快捷键。`DefectDetailView.vue:91` 两样都做了（`PipelineCreateButton kind="defect"` + `createPrefill`），Feature 详情页没有 |
| `/features/:id`，新建 task | `FeatureTasks.vue` 卡片里的行内表单（第 4 张卡片） | 同样需要先滚动。shell 的 `TaskComposerDialog` 没有 `feature` 预填 prop，即使能打开也不会挂到这个 feature 上 |
| `/tasks/:id` (`TaskDetailView.vue`) | `FeatureDefects :fix-task-id` 排在 body、图片、backlinks、TaskFeature 之后 | 页头的操作区（`task-detail-actions`）没有 report defect；这个页面在 Pipeline shell 之外，所以 shell 的 `NewDefectDialog` 用不到 |
| `/features` 列表的展开行 (`FeatureExpansion.vue`) | 只读地列出被归到该 feature 的 defect | 没有带预填的 report 入口；页头 `New ▾ → Defect` 打开的对话框 feature 为空 |
| `FeatureDefects` 卡片的 `Report defect` | 行内表单：只有 symptom（纯 Textarea，无 `@` 引用）+ 截图 | 少了对话框里的 Suspect paths、`@` 引用；结果只写在卡片里的一行 note，和 shell 的 flash 不一致 |
| `FeatureTasks` 卡片的 `+ task` | 行内表单：title、Stage、Project | 少了对话框里的 Details、Tags、Priority、Images；反过来，`TaskComposerDialog` 没有 Stage，也不带 feature 的 project |
| `FeatureTasks` 卡片的 `Write spec` / `Build` | 行内展开的 Instructions 表单 | 唯一一处不是对话框的"新建"，和其它入口交互不一致 |
| `/features` 展开行 | 有 `+ Report`（defect） | 没有新建 task 的入口 |
| 用对话框创建之后 | Ledger 收到 `pipeline` 帧会重新读取（`usePipelineLive`） | `FeatureDefects` 只在 `feature.id` / `fixTaskId` 变化时重新读取（`FeatureDefects.vue:97`），所以从别处创建的 defect 不会出现在卡片列表里，要刷新页面才行 |

## Shape

```ts
// usePipelineCreate prefill — existing; Task now honours it too
type CreatePrefill = {
  // project_id / project_name present ⇒ the feature's project is the task's starting project
  feature: { id: number; title: string; project_id?: number | null; project_name?: string | null }
} | null

// FeatureLedgerView → shell header + sticky strip
type FeatureCreateEntry = {
  kind: 'defect' | 'task'            // the header's default kind is 'defect'
  prefill: CreatePrefill              // always this page's feature
}

// FeatureSectionNav gains a trailing action slot (not an entry in `entries`)
type SectionNavProps = { entries: Entry[]; endId: string /* + default slot: actions */ }

// TaskComposerDialog
type ComposerProps = { projects; project; feature?: CreatePrefill['feature'] | null }
type ComposerDraft = { title; body; tags; priority; project_id; feature_id;
                       kind: 'task' | 'spec' | 'build' }   // Stage — shown only while a feature is chosen
// POST /api/tasks already accepts feature_id and kind (capture.parse_web_kind) — no API change

// FeatureDefects / FeatureTasks no longer own a create form
type FeatureDefectsEmits = { report: [] }       // parent opens its NewDefectDialog
type FeatureTasksEmits   = { 'new-task': [] }   // parent opens the shell TaskComposerDialog

// FeatureStepDialog — the Write spec / Build verbs, same Dialog chrome as the others
type StepDialogProps = { open: boolean; featureId: number; project: number | null; kind: 'spec' | 'build' }
// POST /api/features/:id/steps { kind, body } → navigate to /tasks/<new>
```

No schema, API, or CLI change. Everything is frontend.

## Rules

### R1 — Entry points (decision table)

| Surface | Control | Opens | Prefilled feature | Visible when |
|---|---|---|---|---|
| `/features/:id` shell header (`#pipeline-actions`) | `PipelineCreateButton kind="defect"`, ▾ Task · Feature · Defect | shell dialog | this feature, for both Defect and Task (not Feature) | `canWrite` |
| `/features/:id` sticky section strip (trailing) | `Report defect` · `New task` (ghost, sm; icon-only < 768px) | shell dialog | this feature | `canWrite` |
| `/features/:id` keyboard | `c` → defect dialog | shell dialog | this feature | `canWrite`, not typing, no overlay open (existing `useTaskShortcuts` guards) |
| `/tasks/:id` header actions (`task-detail-actions`) | `Report defect` | `NewDefectDialog` mounted locally in `TaskDetailView` | the task's feature, or none (triage) | `canWrite` |
| `/features` expanded row (`FeatureExpansion`, non-compact) | `+ Task` · `+ Report` beside "Blamed defects" | shell dialog | that row's feature (+ its project for Task) | `canWrite` |
| `FeatureDefects` card `Report defect` (feature page) | same button, testid `feature-report-defect` | shell `NewDefectDialog` | this feature | `canWrite` |
| `FeatureDefects` card `Report defect` (task page) | same button | `TaskDetailView`'s local `NewDefectDialog` | the task's feature | `canWrite` |
| `FeatureTasks` card `+ task` | same button, testid `feature-new-task` | shell `TaskComposerDialog` | this feature + its project | `canWrite` |
| `FeatureTasks` card `Write spec` / `Build` | same buttons | `FeatureStepDialog` (local to the card) | n/a — a step is always this feature's | `canWrite`; existing step ⇒ link to it, no dialog |

### R2 — Prefill is a chip, not a lock
The prefilled feature appears as the dialog's feature chip, and the operator can clear it. This matches what `NewDefectDialog` already does for the defect-detail prefill. If the chip is cleared, a defect goes to triage and a task is created with no feature.

### R3 — The page reflects the write without a reload, and without polling
After a defect or task is created from any R1 entry, the open page's figures, its section-strip counts, and the `FeatureDefects` / `FeatureTasks` lists show the new row. The trigger is the existing `pipeline` push frame, or the create's own result. Never use a timer or a poll (CLAUDE.md root-cause discipline). `FeatureDefects` must re-read on that trigger, not only when `feature.id` changes.

### R4 — The sticky strip remains one strip
The action cluster sits inside `FeatureSectionNav`'s sticky band, aligned to the trailing edge. The jump links keep their horizontal scroll, and the actions never scroll out with them. At 320px the strip doesn't overflow `.content-scroll` sideways (`dom-measure --overflow`). The actions aren't section entries, so they don't change `aria-current` tracking.

### R5 — Themes
R1 to R4 hold under every registered pipeline theme (`classic`, `pixel` — `frontend/src/pipeline/themes/registry.js`). New controls carry the same `data-pl-part` keys as their siblings, so the pixel theme styles them, and they don't fall back to raw classic chrome.

### R6 — One create form per kind
No card on `/features/:id` or `/tasks/:id` renders an inline create form. Every create opens a dialog built on `ui/Dialog.vue` with `data-pl-part="dialog"`, and the defect and task dialogs are the shell's (or, on `/tasks/:id`, the same component mounted locally).

| Field | Defect dialog | Task dialog |
|---|---|---|
| Text | Symptom (`RefTextarea`, `@` refs) | Title + Details (`RefTextarea`) |
| Feature | chip, clearable | chip, clearable |
| Project | read-only "Project: <feature's>" line while the chosen feature is the prefilled one (a defect has no project of its own) | Select; starts at the prefilled feature's project on a fresh draft, else the shell scope; editable. A project the dialog placed follows the prefill when it moves or is withdrawn; one the operator picked stays |
| Stage | — | Task · Spec · Build, shown only while a feature is chosen; sent as `kind` only then; any change of feature (cleared, re-prefilled, re-picked) drops it back to Task |
| Tags · Priority | — | yes |
| Suspect paths | yes | — |
| Images | paste / drop / pick | paste / drop / pick |

The outcome is reported once, by whoever owns the dialog, as a flash with an Open link: `Defect #N reported|matched an open report on feature #F|in triage`, `Task #N created`. "Deduped" and "opened" remain different sentences. The card keeps no outcome note of its own.

### R7 — Step verbs are a dialog too
`Write spec` / `Build` open `FeatureStepDialog` (Instructions, optional, `RefTextarea` scoped to the feature's project). Submit posts `/features/:id/steps` and navigates to the new task, as before. A verb whose step already exists stays a link to it. The testids `feature-step-body` / `feature-step-submit` move into the dialog unchanged.

### Invariants
- Any control that opens a create form from a page naming a feature passes that feature as `prefill`. No control on such a page opens the form empty.
- A viewer (`role === 'viewer'`) sees none of the R1 controls.
- One dialog per kind exists inside the shell (`PipelineView` owns them). `TaskDetailView` mounts its own `NewDefectDialog` only because it sits outside the shell.

## Non-goals
- Collapsing or folding the long sections (Spec, Sessions). The complaint is reaching the create action, and R1 solves that without changing how the content reads. Follow-up if wanted.
- A `c` chord on `/tasks/:id`.
- Stage verbs (ship / abandon / reopen) in the sticky strip.

## Acceptance (Gherkin — each runs as a Playwright spec)

```gherkin
Scenario: Report a defect from the feature page without scrolling
  Given an operator on /features/<id> scrolled to the top
  When they click the header "New defect" button
  Then the defect dialog opens with the feature chip naming #<id>
  And .content-scroll scrollTop is still 0

Scenario: The strip action is reachable from anywhere on a long page
  Given /features/<id> with ≥ 20 sessions, scrolled to the foot
  Then the section strip's "Report defect" button is in the viewport
  When they click it and submit "x"
  Then the Defects card lists the new defect, the Defects strip count went up by 1, and no page reload happened

Scenario: c opens the prefilled defect dialog
  Given /features/<id> with focus on the page body
  When they press "c"
  Then the defect dialog opens prefilled with #<id>
  But pressing "c" inside the spec editor types a "c"

Scenario: New task from the feature page lands on the feature
  Given /features/<id>
  When they choose "New task" in the strip and submit "t"
  Then the created task has feature_id = <id> (GET /api/tasks/<new>)
  And the Tasks card and its strip count show it

Scenario: Clearing the chip files to triage
  Given the defect dialog opened from /features/<id>
  When they clear the feature chip and submit
  Then the defect has feature_id = null

Scenario: Task page reports from its header
  Given /tasks/<tid> whose feature is #<id>
  When they click "Report defect" in task-detail-actions
  Then the dialog opens prefilled with #<id> and scrollTop is unchanged

Scenario: Features list expanded row
  Given /features with row #<id> expanded
  When they click "+ Report" in its Blamed defects section
  Then the dialog opens prefilled with #<id>

Scenario: Features list expanded row opens a prefilled task
  Given /features with row #<id> expanded
  When they click "+ Task" in its expansion
  Then the task dialog opens with the feature chip naming #<id> and the project of #<id>

Scenario: The Defects card button opens the dialog, not an inline form
  Given /features/<id>
  When they click the Defects card's "Report defect"
  Then the shell defect dialog opens prefilled with #<id>, with Suspect paths and Images fields
  And the card renders no form
  When they submit "y"
  Then a flash names "Defect #<n> reported on feature #<id>" and the card lists it

Scenario: The Tasks card "+ task" opens the composer with Stage and project
  Given /features/<id> whose project is P
  When they click the Tasks card's "+ task"
  Then the task dialog opens prefilled with #<id>, project P, Stage "Task", and Details/Tags/Priority/Images fields
  When they set Stage "Spec" and submit "s"
  Then the POST carries feature_id=<id>, project_id=P, kind="spec"

Scenario: Stage disappears with the feature
  Given the task dialog opened with Stage "Spec" chosen on #<id>
  When they clear the feature chip and submit
  Then the POST carries no kind and no feature_id

Scenario: Write spec opens a dialog
  Given /features/<id> with no spec step
  When they click "Write spec", type instructions and submit
  Then they land on /tasks/<new> whose kind is spec and feature is #<id>

Scenario: Task page card reports through the page's dialog
  Given /tasks/<tid> whose feature is #<id>
  When they click the Defects card's "Report defect"
  Then the page's defect dialog opens prefilled with #<id>
  When they submit a symptom already open on #<id>
  Then the flash says "matched an open report", not "reported"

Scenario Outline: Viewer and themes
  Given role <role> and pipeline theme <theme> at width <w>
  Then the R1 controls are <shown> and /features/<id> has no horizontal overflow
  Examples:
    | role     | theme   | w    | shown   |
    | operator | classic | 1280 | visible |
    | operator | pixel   | 1280 | visible |
    | operator | classic | 320  | visible |
    | viewer   | classic | 1280 | absent  |
```

Gates: `cd frontend && npx vite build`; the specs above plus `feature-ledger`, `feature-index`, `feature-workbench`, `feature-steps`, `feature-lifecycle`, `project-carry`, `ref-mentions`, `task-report-defect`, `pipeline-theme-features`, `responsive` (chromium); BundleEngine clean on every touched `.vue`.


