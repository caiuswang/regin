# A feature can carry images

A task can carry images (`agent_task_images`): paste a mockup onto the card and
every attempt at that task is launched with the bytes beside its prompt. A
**feature** — the thing the task was sliced out of, the row that actually holds
the spec — cannot. So the screenshot that motivated the whole feature has to be
re-pasted onto each slice by hand, or it simply never reaches the agent, and the
spec says "match the mockup" with no mockup in the context window.

This gives the feature row the same capability, and makes a feature's images
reach every run cut from it.

## Types

```
FeatureImage
  id          int
  feature_id  int      -> features.id
  idx         int      per-feature order, allocated by the writer, never reused
  media_type  str      sniffed from the bytes, never from the caller
  bytes       blob
  byte_size   int
  sha256      str
  created_at  str
  UNIQUE (feature_id, idx)

ImageSummary                       # what a page gets; never the bytes
  { id, idx, media_type, byte_size }

LaunchPayload = [{ media_type, data_b64 }]   # what supervisor.launch_run takes
```

## Delivery

A task's run payload is **resolved at launch, not copied at transcription**:

```
launch_images(task) = feature_images(task.feature_id) ++ task_images(task.id)
                      (empty when task.feature_id is NULL)
```

Copying at transcribe time was rejected: the mockup is the thing most likely to
be *replaced* after the slices exist, and a copy would leave every already-cut
slice carrying the superseded picture with nothing to tell the operator. The
task's `feature_id` is a live identifier — reading through it means correcting
the feature's image corrects the next attempt of every slice.

Feature images come **first**: they are the shared backdrop the slice-specific
shot is read against.

## Invariants

1. **The bytes decide the media type.** `sniff_media_type` on magic bytes; a
   `Content-Type` a paste event claimed is never trusted.
2. **A freed `idx` is never reissued.** `_next_idx` counts from the high-water
   mark, so `(feature, idx)` names the same bytes forever and a thumbnail on
   screen cannot quietly become a different picture. This is what lets the GET
   be cached hard.
3. **A refusal is a 400 naming the reason**, never a silent drop — the operator
   is watching a thumbnail that must either appear or explain itself.
4. **Reads never inline bytes.** The ledger carries summaries; the bytes are a
   separate authenticated GET.
5. **The ceiling is per surface, not per delivery.** `prompt_images_max_count`
   is enforced when attaching to a feature and, separately, when attaching to a
   task. A run of a feature's task may therefore carry up to 2× that count.
   Truncating at delivery was rejected: it would drop, silently, an image the
   operator attached and can see.
6. **A feature is never deleted**, so its images need no cascade.
7. **`![](image:N)` is display only.** A run carries every attachment whether
   the spec names it or not. Making a reference decide delivery would mean a
   spec that forgot to mention its mockup silently stopped shipping it.

## Decision table — POST /api/features/<id>/images

| Condition | Result |
|---|---|
| no such feature | 404 `unknown feature` |
| `data_b64` absent / not a string | 400 field `data_b64` |
| `data_b64` not valid base64 | 400 field `data_b64` |
| bytes are not png/jpeg/gif/webp | 400 `not a supported image (...)` |
| bytes > `prompt_image_max_bytes` | 400 naming the ceiling |
| feature already holds `prompt_images_max_count` | 400 naming the ceiling |
| otherwise | 201 `{image: ImageSummary}` |

## Surface

| Route | Method | Auth | Answers |
|---|---|---|---|
| `/api/features/<id>/images` | POST | editor | `{image}` |
| `/api/features/<id>/images/<idx>` | DELETE | editor | `{removed, idx}` |
| `/api/features/<id>/images/<idx>` | GET | any logged-in role | raw bytes, `private, max-age=86400` |
| `/api/features/<id>` | GET/PATCH | read any / write editor | ledger now carries `images: [ImageSummary]` |

Browser: the feature-ledger page grows an Images card under the spec —
thumbnails, an attach button, and paste/drop anywhere on the card. Its own
card rather than a strip inside the spec, because the spec renders as prose
until somebody presses Edit, and a control that only exists while a textarea
is open hides the images from every reader of the page. The declare dialog
holds pasted bytes in the browser and uploads them once the row exists, so a
feature abandoned mid-typing strands nothing server-side.

The **spec editor is a second attach gesture**, and the only one that can
place a picture: pasting into the open textarea attaches the bytes and writes
`![](image:N)` at the caret, exactly as a task body does. The syntax is
otherwise unguessable, so the edit hint names it and every thumbnail is
captioned with its own `image:N` (absent in a composer, where a held file has
no idx until the row exists). The read view resolves the reference against the
attachments; one whose image is gone renders as visible text rather than a
broken `<img>`, because the author has to see that it went.

One `useAttachedImages('features')` instance lives on the ledger VIEW, not on
either card. The spec's references and the Images card's thumbnails are two
views of one set, and a per-card instance lets them disagree about what is
attached.

## Acceptance

```gherkin
Scenario: images are numbered in order and served on the ledger
  Given a feature
  When a png and then a gif are attached
  Then they are idx 1 and 2 with media types sniffed from the bytes
  And GET /api/features/<id> carries both summaries and no bytes

Scenario: a removed index is never reissued
  Given a feature holding images 1 and 2
  When image 1 is removed and a third is attached
  Then the new image is idx 3

Scenario: a run of a feature's task carries the feature's images first
  Given a feature holding one image
  And an armed task filed under that feature holding one image of its own
  When the task is run
  Then launch_run receives the feature's image, then the task's

Scenario: replacing the feature's image reaches the next attempt
  Given the task above has already run once
  When the feature's image is removed and another attached
  Then the second attempt carries the new bytes, not the old

Scenario: a task with no feature carries only its own images
  When an unfiled task is run
  Then launch_run receives only the task's images

Scenario: writes need an editor and reads need a login
  Then POST/DELETE as a viewer are 403 and the GET as a viewer is 200

Scenario: pasting into the open spec attaches the image and names it
  Given a feature open for spec editing with the caret mid-sentence
  When an image is pasted into the textarea
  Then it is POSTed to the feature
  And `![](image:N)` is written at the caret, not appended
  And the Images card shows it without a reload
  And the saved body carries the reference verbatim

Scenario: two images pasted at once keep their order
  When two images are pasted in one gesture
  Then their references read image:N then image:N+1

Scenario: a reference outlives its image as text
  Given a spec naming `![](image:7)` and no image 7 attached
  Then the read view shows "image:7 — no longer attached", not a broken image

Scenario: a viewer reads the mockups and can change nothing
  Then the thumbnails render, and there is no attach control and no Edit
```

## Non-goals

- No CLI surface. Capture is a human gesture with a clipboard; an agent writing
  a spec has no picture to paste.
- No thumbnails on the feature index — the ledger page is where a feature is
  read.

