When a task (or the feature it was cut from) has images attached and the task is run, the images are delivered to the agent as content blocks beside the launch prompt — the model does see them. The defect was on regin's side: the session's trace showed only the body's `![](image:N)` placeholder, because nothing recorded the launch images against the announced prompt span (they carry no `[Image #N]` marker for the ingest to bind, and an SDK run has no transcript anchor to pick them up from).

The launch prompt span must carry its images: `image_indices` numbered 1..n in delivery order (feature images first, then the task's own), with the bytes stored in `prompt_images` under that same span, so the conversation renders them as thumbnails like a composer paste.

Acceptance:
- test_launch_images_render_on_the_announced_prompt
- test_a_launch_without_images_posts_no_image_rows
- test_launch_run_hands_its_images_to_the_announcement

Non-goals: the `![](image:N)` text in the body stays literal text (its N is the task's own index, which differs from delivery order when the feature has images too); the images render as trailing thumbnails. A launch whose announcement post fails falls back to the runner's own prompt span, which carries no images.
