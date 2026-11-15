from scripts.analyze_saved_moss import clean_output, summary_complete


def test_native_summary_completes_on_silence_without_closing_round():
    events = [
        {'type': 'frame_ack', 'pending_frames': 0},
        {'type': 'output', 'text': '<|round_start|>A per'},
        {'type': 'output', 'text': 'son crosses the path.'},
        {'type': 'output', 'text': '<|silence|>'},
    ]
    raw, text = clean_output(events)
    assert text == 'A person crosses the path.'
    assert summary_complete(raw, text)


def test_silence_alone_and_interrupted_text_are_not_completed_assessments():
    for raw in ('<|silence|>', '<|round_start|>', '<|silence|>A person starts to'):
        _, text = clean_output([{'type': 'output', 'text': raw}])
        assert not summary_complete(raw, text)


def test_display_normalization_preserves_raw_action_for_audit():
    raw, text = clean_output([{'type': 'output', 'text': 'A man carries a box.<|round_end|>'}])
    assert raw == 'A man carries a box.<|round_end|>'
    assert text == 'A person carries a box.'
    assert summary_complete(raw, text)
