from sideline.spike import format_report, run_spike


def test_selftest_writes_a_report_and_overlays(tmp_path):
    report = run_spike(None, tmp_path / "out", frames=3, selftest=True)
    assert report.frames == 3 and report.source == "selftest"
    assert (tmp_path / "out" / "report.json").exists()
    assert len(list((tmp_path / "out" / "overlays").glob("*.jpg"))) == 3
    text = format_report(report)
    assert "registered" in text and "gate" in text
    for f in report.per_frame:
        assert f.registered == (f.confidence >= 0.8)
        if f.registered:
            assert f.error_m is not None


def test_real_video_is_sampled_in_bursts_at_five_fps(clip):
    from sideline.spike import sample_frames

    got = [(i, t) for i, t, _ in sample_frames(clip, 8, burst=4)]
    # Two bursts of four, each burst consecutive samples every 5th source frame (25 fps / 5 fps).
    assert len(got) == 8
    assert [i for i, _ in got[:4]] == [0, 5, 10, 15]
    assert got[4][0] > 15 and [i - got[4][0] for i, _ in got[4:]] == [0, 5, 10, 15]
    assert got[1][1] == 200
