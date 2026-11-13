"""Verdicts read through a later events version. A re-run moves, merges and
drops events; what a reviewer said about a moment still holds, and is
restated about whichever new event covers that moment."""

from sideline.verdicts import Seen, carry, seen


def v(verdict, **over):
    return {"verdict": verdict, "corrected_type": None, "corrected_track": None, **over}


def test_seen_reads_the_dead_window_from_the_cue_log():
    rows = [{"t_ms": 10_000, "type": "restart", "track_id": 7}]
    log = [{"still_s": 4.0}]
    assert seen(rows, log) == [Seen(10_000, 6_000, "restart", 7)]


def test_a_verdict_follows_its_moment_onto_the_event_that_covers_it():
    old = [(Seen(10_000, 8_800, "free_kick", 7), v("wrong_type", corrected_type="throw_in")),
           (Seen(30_000, 28_000, "free_kick", 3), v("not_event")),
           (Seen(50_000, 48_000, "kick_off", 5), v("right"))]
    new = [Seen(16_000, 7_000, "restart", 7),        # the same ball, now followed to its real release 6 s later
           Seen(30_200, 29_000, "restart", 3),       # the same false alarm, a sample later
           Seen(50_000, 20_000, "kick_off", 5),      # unchanged
           Seen(90_000, 88_000, "restart", 9)]       # nobody reviewed anything here
    got = carry(old, new, same_tracks=True)
    assert got[16_000] == {**v("wrong_type", corrected_type="throw_in"), "from_t_ms": 10_000}
    assert got[30_200]["verdict"] == "not_event" and got[30_200]["from_t_ms"] == 30_000
    assert 90_000 not in got
    # The long kick-off window also contains 30 s, the false alarm; what was
    # said nearest its release speaks for it.
    assert got[50_000]["verdict"] == "right" and got[50_000]["from_t_ms"] == 50_000


def test_what_was_said_is_restated_against_the_new_name():
    old_event = Seen(10_000, 8_000, "free_kick", 7)
    # She corrected it to a throw-in; the new version calls it a throw-in.
    assert carry([(old_event, v("wrong_type", corrected_type="throw_in"))],
                 [Seen(10_000, 8_000, "throw_in", 7)], True)[10_000]["verdict"] == "right"
    # She said the free kick was right; the new version no longer names it.
    got = carry([(old_event, v("right"))], [Seen(10_000, 8_000, "restart", 7)], True)[10_000]
    assert got["verdict"] == "wrong_type" and got["corrected_type"] == "free_kick"


def test_a_corrected_taker_carries_only_within_the_same_tracking_version():
    old = [(Seen(10_000, 8_000, "throw_in", 7), v("wrong_taker", corrected_track=12))]
    new = [Seen(10_000, 8_000, "throw_in", 7)]
    assert carry(old, new, same_tracks=True)[10_000] == {**v("wrong_taker", corrected_track=12), "from_t_ms": 10_000}
    # Track 12 of another tracking run is somebody else: the kind was right, and that is all that carries.
    assert carry(old, new, same_tracks=False)[10_000]["verdict"] == "right"
    # The new version already names track 12 as the taker.
    assert carry(old, [Seen(10_000, 8_000, "throw_in", 12)], True)[10_000]["verdict"] == "right"


def test_a_corrected_kind_and_taker_carry_together():
    old = [(Seen(10_000, 8_000, "restart", 7), v("wrong_type", corrected_type="throw_in", corrected_track=12))]
    both = {**v("wrong_type", corrected_type="throw_in", corrected_track=12), "from_t_ms": 10_000}
    assert carry(old, [Seen(10_000, 8_000, "restart", 7)], True)[10_000] == both
    # The new version names it a throw-in: only the taker is still wrong.
    assert carry(old, [Seen(10_000, 8_000, "throw_in", 7)], True)[10_000] == {**v("wrong_taker", corrected_track=12), "from_t_ms": 10_000}
    # It names track 12 instead: only the kind is still wrong.
    assert carry(old, [Seen(10_000, 8_000, "restart", 12)], True)[10_000] == {**v("wrong_type", corrected_type="throw_in"), "from_t_ms": 10_000}
    # Another tracking run: the kind carries, the track does not.
    assert carry(old, [Seen(10_000, 8_000, "restart", 7)], False)[10_000] == {**v("wrong_type", corrected_type="throw_in"), "from_t_ms": 10_000}
