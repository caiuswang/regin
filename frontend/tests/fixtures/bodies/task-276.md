Fix regin defect #212, reported against feature #124 (Unknown defects):

At 1280x720 with the feed follow-up composer open, the session trace conversation scroll area is 0px tall (scrollHeight 368): the expanded header + stats + activity + composer consume all vertical space, so the conversation is invisible and unscrollable and the composer dock intercepts clicks on rows beneath it. Found while diagnosing present-reply.spec.js:230.
reported in session a45446c6-2b8f-4dcb-9920-0e99c7e24fa1
suspect paths: frontend/src/components/conversation/FollowUpComposer.vue, frontend/src/views/SessionTraceView.vue