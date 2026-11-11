from sideline.timeline import Period, Stint, elapsed_s, in_play, match_seconds, on_pitch, periods_from_sm, stints_from_sm

T0 = 1_700_000_000_000
FIRST = Period(1, T0, T0 + 30 * 60_000)                       # 30-minute half
SECOND = Period(2, T0 + 40 * 60_000, None)                    # 10-minute break, second half still open
KICKOFF_VIDEO_MS = 90_000                                     # the match kicked off 90 s into the file


def test_video_time_maps_to_elapsed_match_seconds_across_the_break():
    # Five minutes in: 4 min 30 s of match time.
    assert match_seconds(KICKOFF_VIDEO_MS + 270_000, KICKOFF_VIDEO_MS, [FIRST, SECOND]) == 270.0
    # Half-time counts for nothing: any instant in the break reads 30 minutes.
    assert match_seconds(KICKOFF_VIDEO_MS + 35 * 60_000, KICKOFF_VIDEO_MS, [FIRST, SECOND]) == 1800.0
    # Two minutes into the second half is 32 minutes of match time.
    assert match_seconds(KICKOFF_VIDEO_MS + 42 * 60_000, KICKOFF_VIDEO_MS, [FIRST, SECOND]) == 1920.0


def test_before_kickoff_is_none_and_no_periods_is_none():
    assert match_seconds(10_000, KICKOFF_VIDEO_MS, [FIRST]) is None
    assert match_seconds(KICKOFF_VIDEO_MS, KICKOFF_VIDEO_MS, []) is None


def test_closed_period_does_not_budge_when_the_clock_moves():
    # The `s.end || now` rule: a closed period contributes exactly its length.
    assert elapsed_s(T0 + 10 ** 9, [FIRST]) == 1800.0


def test_in_play_excludes_the_break():
    assert in_play(T0 + 60_000, [FIRST, SECOND])
    assert not in_play(T0 + 35 * 60_000, [FIRST, SECOND])
    assert in_play(T0 + 45 * 60_000, [FIRST, SECOND])


def test_on_pitch_uses_stints_only():
    stints = [Stint("a", 0, 900), Stint("b", 0, None), Stint("c", 900, None)]
    assert on_pitch(100, stints) == {"a", "b"}
    assert on_pitch(900, stints) == {"b", "c"}      # the sub happens at the boundary
    assert on_pitch(2000, stints) == {"b", "c"}


def test_parsing_soccer_manager_nodes():
    periods = periods_from_sm({"0": {"half": 1, "start": T0, "end": T0 + 1000}, "1": {"half": 2, "start": T0 + 5000}})
    assert [p.half for p in periods] == [1, 2] and periods[1].end_ms is None
    stints = stints_from_sm({"s1": {"pid": "p9", "on": 0, "off": 600}, "s2": {"pid": "p3", "on": 600}, "junk": 5})
    assert [(s.player_id, s.on_s, s.off_s) for s in stints] == [("p9", 0.0, 600.0), ("p3", 600.0, None)]


def test_elapsed_maps_back_to_video_time_and_round_trips():
    from sideline.timeline import video_ms_for_elapsed
    # 4 min 30 s of match time is five minutes into the file.
    assert video_ms_for_elapsed(270.0, KICKOFF_VIDEO_MS, [FIRST, SECOND]) == KICKOFF_VIDEO_MS + 270_000
    # 32 minutes of match time is two minutes into the second half, past the ten-minute break.
    assert video_ms_for_elapsed(1920.0, KICKOFF_VIDEO_MS, [FIRST, SECOND]) == KICKOFF_VIDEO_MS + 42 * 60_000
    for e in (0.0, 17.5, 1799.0, 1800.0, 2500.0):
        v = video_ms_for_elapsed(e, KICKOFF_VIDEO_MS, [FIRST, SECOND])
        assert match_seconds(v, KICKOFF_VIDEO_MS, [FIRST, SECOND]) == e
    assert video_ms_for_elapsed(10.0, KICKOFF_VIDEO_MS, []) is None
    # Past the end of a closed match there is no video time.
    assert video_ms_for_elapsed(1801.0, KICKOFF_VIDEO_MS, [FIRST]) is None


def test_taps_from_a_soccer_manager_match_node():
    from sideline.timeline import taps_from_sm
    node = {
        "events": {"e1": {"t": 755, "at": T0 + 1, "side": "us", "kind": "throw", "pid": "p7", "by": "u1"},
                   "e2": {"t": 61, "at": T0, "side": "them", "kind": "corner"},
                   "junk": 3},
        "goals": {"g1": {"t": 900, "at": T0 + 5, "side": "us", "pid": "p9"}},
        "shots": {"s1": {"t": 300, "side": "them", "onTarget": True}},
        "poss": {"x1": {"t": 100, "to": "us"}},
    }
    taps = taps_from_sm(node)
    assert [(t.kind, t.side, t.elapsed_s, t.player_id) for t in taps] == [
        ("corner", "them", 61.0, None), ("poss", "us", 100.0, None), ("shot", "them", 300.0, None),
        ("throw", "us", 755.0, "p7"), ("goal", "us", 900.0, "p9"),
    ]
    assert taps[3].at_ms == T0 + 1 and taps[3].id == "e1" and taps[2].at_ms is None
    assert [t.kind for t in taps_from_sm(node, collections=("events",))] == ["corner", "throw"]
