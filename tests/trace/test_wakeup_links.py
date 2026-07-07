"""Unit tests for serve-time ScheduleWakeup cross-turn linking."""
from lib.trace.wakeup_links import annotate_wakeup_resumes


def _sw(span_id, t, **attrs):
    return {'id': None, 'span_id': span_id, 'name': 'tool.ScheduleWakeup',
            'start_time': t, 'attributes': dict(attrs)}


def _span(span_id, name, t, **attrs):
    return {'id': None, 'span_id': span_id, 'name': name,
            'start_time': t, 'attributes': dict(attrs)}


def _attrs(spans, span_id):
    return next(s['attributes'] for s in spans if s['span_id'] == span_id)


def test_resume_action_skips_noise_to_first_signal():
    spans = [
        _sw('w1', '01'),
        _span('t1', 'assistant.thinking', '02'),
        _span('t2', 'harness.task_reminder', '03'),
        _span('b1', 'tool.Bash', '04', command_preview='ls'),
    ]
    annotate_wakeup_resumes(spans)
    a = _attrs(spans, 'w1')
    assert a['resume_action'] == 'Bash'
    assert a['resume_span_id'] == 'b1'


def test_taskoutput_resume_names_the_task():
    spans = [
        _sw('w1', '01'),
        _span('o1', 'tool.TaskOutput', '02', task_id='a526f3fe1e908c1c1'),
    ]
    annotate_wakeup_resumes(spans)
    assert _attrs(spans, 'w1')['resume_action'] == 'polled task #a526f3fe1e908c1c1'


def test_stop_wakeup_is_terminal_no_resume():
    spans = [
        _sw('w1', '01', stop=True),
        _span('b1', 'tool.Bash', '02'),
    ]
    annotate_wakeup_resumes(spans)
    assert 'resume_action' not in _attrs(spans, 'w1')


def test_wakeup_at_window_edge_gets_no_link():
    spans = [_span('b1', 'tool.Bash', '01'), _sw('w1', '02')]
    annotate_wakeup_resumes(spans)
    assert 'resume_action' not in _attrs(spans, 'w1')


def test_poll_loop_collapse_stamps_round_and_total():
    # three wakeups that each resume straight into the next = one idle poll-loop,
    # then a real exit into Bash on the last one.
    spans = [
        _sw('w1', '01'),
        _sw('w2', '02'),
        _sw('w3', '03'),
        _span('b1', 'tool.Bash', '04'),
    ]
    annotate_wakeup_resumes(spans)
    assert [_attrs(spans, w)['poll_round'] for w in ('w1', 'w2', 'w3')] == [1, 2, 3]
    assert all(_attrs(spans, w)['poll_total'] == 3 for w in ('w1', 'w2', 'w3'))
    # the run's tail exited into real work, not a reschedule
    assert _attrs(spans, 'w3')['resume_action'] == 'Bash'
    assert _attrs(spans, 'w1')['resume_action'] == 'rescheduled'


def test_harness_internals_are_all_noise():
    # every harness.* subtype is skipped, not just task_reminder/recap.
    spans = [
        _sw('w1', '01'),
        _span('h1', 'harness.local_command', '02'),
        _span('h2', 'harness.tools_delta', '03'),
        _span('r1', 'tool.Read', '04'),
    ]
    annotate_wakeup_resumes(spans)
    assert _attrs(spans, 'w1')['resume_action'] == 'Read'


def test_stop_wakeup_not_pulled_into_poll_loop():
    # a stop wakeup that a prior wakeup resumes into must not become a loop
    # member (no poll_total), and must not inflate the head's count.
    spans = [
        _sw('w1', '01'),
        _sw('w2', '02', stop=True),
    ]
    annotate_wakeup_resumes(spans)
    assert 'poll_total' not in _attrs(spans, 'w2')
    assert 'poll_total' not in _attrs(spans, 'w1')


def test_isolated_wakeup_is_not_a_poll_loop():
    spans = [_sw('w1', '01'), _span('b1', 'tool.Bash', '02')]
    annotate_wakeup_resumes(spans)
    assert 'poll_total' not in _attrs(spans, 'w1')


def test_idempotent():
    spans = [_sw('w1', '01'), _sw('w2', '02'), _span('b1', 'tool.Bash', '03')]
    annotate_wakeup_resumes(spans)
    first = [dict(s['attributes']) for s in spans]
    annotate_wakeup_resumes(spans)
    assert [s['attributes'] for s in spans] == first


def test_empty_and_wakeup_free_are_noops():
    assert annotate_wakeup_resumes([]) == []
    spans = [_span('b1', 'tool.Bash', '01')]
    annotate_wakeup_resumes(spans)
    assert spans[0]['attributes'] == {}
