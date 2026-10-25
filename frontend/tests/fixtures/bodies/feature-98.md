# Better image operations in the pipeline

Images already existed on tasks and features when this feature was cut, but the
gestures around them were half-built: bytes could be attached and never named,
named and never previewed, and the one surface where a picture is usually the
whole evidence — a defect report — could not take one at all.

The original report listed three symptoms. All three are fixed, and the work
surfaced a fourth defect in the shared allocator that is deliberately NOT fixed
here (see *Known gap*).

## Model

    AttachedImage = { key, idx:int, url:ObjectURL, name, pending:bool, file:File|null }
    ImageOwner    = 'tasks' | 'features' | 'defects'

`idx` is the whole contract: it is the N in an `![](image:N)` reference, it is
per-owner, and it is allocated by the writer. The change this feature makes is
that a **held** image — bytes pasted into a composer for a row that does not
exist yet — now carries an `idx` too, allocated in the browser and *proposed*
to the server at flush.

## The three owners

| Owner | table | what the bytes are for | on owner delete |
|---|---|---|---|
| task | `agent_task_images` | delivered with every attempt at that task | — |
| feature | `feature_images` | delivered with every slice cut from it | — |
| defect | `defect_images` | evidence the reporter saw; delivered to nothing | CASCADE |

Three tables rather than one with a nullable owner column, because they differ
in what the bytes are *for*; one column carrying three owners would make "whose
image is this" a join away from ambiguous.

## Surfaces

| Surface | attach | ref written at caret | strip | lightbox | preview toggle |
|---|---|---|---|---|---|
| QuickCaptureBubble | held → flush | yes | yes | yes | — |
| TaskComposerDialog | held → flush | yes | yes | yes | — |
| TaskEditSheet | immediate | yes | yes | yes | — |
| TaskBodyEditor (task Details) | via view | yes (paste + drop) | — | — | yes |
| TaskImageGallery (task Images card) | immediate | — | — | yes | — |
| NewFeatureDialog | held → flush | yes | yes | yes | — |
| FeatureSpec | via view | yes (paste + drop) | — | — | yes |
| FeatureImages | immediate | — | yes | yes | — |
| FeatureDefects report form | held → flush | no — attachment only | yes | yes | — |
| DefectRow | — | — | read-only thumbs | yes | — |

The defect form deliberately does **not** write `![](image:N)` into the
symptom. A symptom is the reporter's verbatim wording and is rendered as plain
text, not markdown; routing it through a renderer to make one reference work
would eat every literal mark-shaped character in the sentence.

## Invariants

- One `useAttachedImages` instance per owning **page**, never one per card:
  the markdown reference and the thumbnail strip must never disagree about
  what is attached.
- A held image's `idx` is monotonic and never reissued within a composer
  session, so removing one held image cannot renumber a reference already
  written into the prose.
- `attach(owner, data, want_idx)` treats `want_idx` as a proposal. A taken or
  malformed index falls back to the next free one; the allocator never refuses
  over an index, because a refusal strands bytes the operator can see.
- A reference to an index that is not attached renders as **visible text**
  (`*(image:N — no longer attached)*`), never as a broken `<img>`.
- A list read carries image **metadata only** and costs one batched query, not
  one per row: these are BLOB tables.
- `![](image:N)` is display only. A run receives every attachment beside its
  prompt whether the body names it or not.

## Falsifiable behaviours

    Given the capture-idea composer is open
    When an image is pasted into the Details textarea
    Then `![](image:1)` appears at the caret
    And the thumbnail chip reads `image:1`
    And after saving, the body on the row still resolves to that picture

    Given two images are held in a composer
    When the first is removed
    Then the second still carries the idx its reference names
    And flush asks the server for that idx and gets it

    Given a task's Details card is being edited
    When an image is dropped onto the textarea
    Then it attaches and its reference lands in the draft
    And the Preview toggle renders the picture without saving first

    Given a defect is reported with a pasted screenshot
    Then the defect row shows the thumbnail
    And clicking it opens the picture full size
    And `regin init` and `regin migrate` agree on `defect_images`

## Known gap — the high-water index is reissued

`ImageSet.remove` frees an index, and `_next_idx` counts from `max(idx) + 1`
over the *surviving* rows. A gap punched in the middle therefore stays a gap —
but detaching the **highest** image and attaching another hands the new bytes
the index the old ones had, so an `![](image:N)` already sitting in some prose
resolves to a different picture with nothing to say so.

This is pre-existing and shared by all three owners. Closing it needs a
per-owner high-water counter the tables do not carry, which is a schema change
this feature deliberately did not make. The behaviour is pinned by two tests in
`tests/defects/test_defect_images.py` and stated in `ImageSet.remove`'s
docstring, which previously claimed the opposite.

## Non-goals

- No WYSIWYG editor. Both spec editors stay source-over-preview: agents read
  and write these columns, so the editor hands back the exact bytes it was
  given (a WYSIWYG round trip rewrote 11% of lines and deleted HTML comments).
- Defect images are not delivered to any agent. The fix task cut from a defect
  carries its own.
- No CLI flag for attaching an image; every owner's images are a browser
  gesture.
