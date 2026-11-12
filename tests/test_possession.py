"""Who has the ball, read from fragments alone. The scenes are scripted in
image pixels as in `test_events.py`: a touch is the ball at a player's feet
or in her hands, in her own heights, and a spell is a run of touches that
survives a missed sample and a stride of free ball but not another player."""

import pytest

from sideline.events import frames_from_rows
from sideline.possession import find_possessions, holders, possession_doc
from sideline.sports import get_sport
from tests.test_events import DT, H, Scene

OTHERS = [(300, 500), (900, 520), (1500, 480)]


@pytest.fixture
def grammar():
    soccer = get_sport("soccer")
    return soccer.event_grammar(soccer.surface(105, 68))


def _spells(scene, grammar):
    return find_possessions(frames_from_rows(scene.rows, ["ball"]), grammar)


def test_a_dribble_is_one_spell_while_the_camera_pans(grammar):
    s = Scene(pan=9.0)
    for k in range(12):
        s.people(k, OTHERS)
        x = 600 + 20 * k
        s.people(k, [(x, 700)], ids=[9])
        s.ball(k, x + (25 if k % 3 else 6), 695)          # a stride ahead between touches, never out of reach
    got = _spells(s, grammar)
    assert len(got) == 1
    p = got[0]
    assert p.track_id == 9 and p.start_ms == 0 and p.end_ms == 11 * DT
    assert p.sightings == 12 and p.samples == 12 and not p.held and p.ended == "unseen"


def test_a_pass_is_one_spell_ending_loose_and_the_next_beginning(grammar):
    s = Scene()
    a, b = (500, 700), (1100, 700)
    for k in range(18):
        s.people(k, OTHERS)
        s.people(k, [a, b], ids=[7, 8])
        if k < 6:
            s.ball(k, a[0] + 10, 695)
        elif k < 12:
            s.ball(k, a[0] + 10 + (b[0] - a[0] - 20) * (k - 5) / 7, 695)   # across the gap, at nobody
        else:
            s.ball(k, b[0] - 10, 695)
    first, second = _spells(s, grammar)
    assert (first.track_id, first.ended) == (7, "loose") and first.end_ms == 5 * DT
    assert second.track_id == 8 and second.start_ms == 12 * DT


def test_a_tackle_is_one_spell_taken_by_the_next(grammar):
    s = Scene()
    for k in range(10):
        s.people(k, OTHERS)
        s.people(k, [(600, 700), (640 if k >= 5 else 900, 700)], ids=[7, 8])
        s.ball(k, 606 if k < 5 else 646, 695)
    first, second = _spells(s, grammar)
    assert (first.track_id, first.ended, second.track_id) == (7, "taken", 8)


def test_missed_samples_and_one_sighting_at_someone_else_do_not_end_a_spell(grammar):
    s = Scene()
    for k in range(16):
        s.people(k, OTHERS)
        s.people(k, [(600, 700), (760, 700)], ids=[7, 8])
        if k in (3, 4, 5):
            continue                                       # the detector lost it for 0.6 s
        s.ball(k, 754 if k == 10 else 606, 695)            # one sample at the other player's feet
    got = _spells(s, grammar)
    assert len(got) == 1
    assert got[0].track_id == 7 and got[0].samples == 16 and got[0].sightings == 12


def test_a_ball_unseen_for_longer_than_the_carry_ends_the_spell_unseen(grammar):
    s = Scene()
    gap = int(grammar.possession_carry_s * 1000 / DT) + 2
    for k in range(8 + gap + 8):
        s.people(k, OTHERS)
        s.people(k, [(600, 700)], ids=[7])
        if k < 8 or k >= 8 + gap:
            s.ball(k, 606, 695)
    first, second = _spells(s, grammar)
    assert first.ended == "unseen" and first.end_ms == 7 * DT and second.start_ms == (8 + gap) * DT


def test_the_ball_is_at_the_feet_it_lies_by_not_the_box_it_falls_inside(grammar):
    # One player stands nearer the camera than the ball: it shows inside
    # her box at chest height, where a held ball would be. Another runs
    # with it at her feet. Feet win; the box gap would call them level.
    s = Scene()
    for k in range(6):
        s.people(k, OTHERS)
        s.people(k, [(600, 730)], ids=[7])
        s.people(k, [(640, 700)], ids=[8])
        s.ball(k, 604, 695)
    (p,) = _spells(s, grammar)
    assert p.track_id == 8 and not p.held


def test_a_ball_in_the_hands_is_held_and_a_speck_behind_a_player_is_nobodys(grammar):
    s = Scene()
    for k in range(8):
        s.people(k, OTHERS)
        s.people(k, [(600, 700)], cls="goalkeeper", ids=[1])
        s.ball(k, 600, 700 - 0.7 * H)                      # at her chest
    (p,) = _spells(s, grammar)
    assert p.held and p.cls == "goalkeeper" and p.track_id == 1

    s = Scene()
    for k in range(8):
        s.people(k, [(600, 700)], ids=[7])
        s.ball(k, 606, 695, r=2)                           # 4 px against a 60 px player: a mark on the far fence
    assert _spells(s, grammar) == []


def test_the_document_says_how_much_of_the_footage_had_a_ball_in_it(grammar):
    s = Scene()
    for k in range(10):
        s.people(k, [(600, 700)], ids=[7])
        if k < 6:
            s.ball(k, 606, 695)
    frames = frames_from_rows(s.rows, ["ball"])
    spells = find_possessions(frames, grammar)
    doc = possession_doc(frames, spells, "followcam")
    assert doc["capture_mode"] == "followcam" and doc["sample_count"] == 10 and doc["samples_with_ball"] == 6
    assert doc["samples_in_possession"] == 6 and doc["partial"] is True
    assert holders(doc["spells"], [0, 5 * DT, 6 * DT, -DT]) == [7, 7, None, None]
