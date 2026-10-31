"""Dead balls found from fragments, and named by the sport, with no pitch
coordinates anywhere. The scenes are scripted in image pixels the way a
follow-cam would show them: the camera pans, the players stand, the ball
sits still and is then thrown or struck."""

import numpy as np
import pytest

from sideline.events import Frame, camera_shifts_from_people, find_dead_balls, frames_from_rows
from sideline.sports import get_sport

SIZE = (1920, 1080)
H = 60.0          # a player's height in pixels on this footage
DT = 200          # ms between samples at 5 fps


def row(k, tid, cls, x, y, w, h):
    return {"frame_index": k, "t_ms": k * DT, "track_id": tid, "cls": cls,
            "x1": x - w / 2, "y1": y - h, "x2": x + w / 2, "y2": y, "conf": 0.8}


def ball_row(k, tid, x, y):
    return {"frame_index": k, "t_ms": k * DT, "track_id": tid, "cls": "ball",
            "x1": x - 5, "y1": y - 5, "x2": x + 5, "y2": y + 5, "conf": 0.4}


class Scene:
    """Players standing at fixed pitch spots while the camera pans by `pan`
    px per sample. Everything drawn moves with the pan."""

    def __init__(self, pan=0.0):
        self.rows, self.pan = [], pan
        self.next_tid = 100

    def shift(self, k):
        return self.pan * k

    def people(self, k, spots, cls="player", ids=None):
        for i, (x, y) in enumerate(spots):
            tid = (ids[i] if ids else 1 + i)
            self.rows.append(row(k, tid, cls, x + self.shift(k), y, 24, H))

    def ball(self, k, x, y, tid=50):
        self.rows.append(ball_row(k, tid, x + self.shift(k), y))


@pytest.fixture
def soccer():
    return get_sport("soccer")


@pytest.fixture
def grammar(soccer):
    return soccer.event_grammar(soccer.surface(105, 68))


def _dead(scene, grammar, **kw):
    frames = frames_from_rows(scene.rows, ["ball"])
    return find_dead_balls(frames, grammar, SIZE, **kw)


def test_camera_motion_is_the_median_shift_of_the_people():
    s = Scene(pan=7.0)
    for k in range(3):
        s.people(k, [(300, 600), (900, 650), (1500, 700)])
    frames = frames_from_rows(s.rows, ["ball"])
    shifts = camera_shifts_from_people(frames)
    assert np.allclose(shifts[1], [7, 0]) and np.allclose(shifts[2], [7, 0])


def test_throw_in_is_a_ball_held_high_then_thrown_while_the_camera_pans(soccer, grammar):
    s = Scene(pan=6.0)
    others = [(600, 500), (800, 520), (1000, 480), (1300, 560)]
    thrower = (400, 700)
    for k in range(20):
        s.people(k, others)
        s.people(k, [thrower], ids=[9])
        if k < 10:
            s.ball(k, thrower[0], thrower[1] - H - 4)      # overhead, just above the box top
        else:
            s.ball(k, thrower[0] + 70 * (k - 9), thrower[1] - H - 4 - 30 * (k - 9))
    dead = _dead(s, grammar)
    assert len(dead) == 1
    d = dead[0]
    assert d.held and d.taker_track == 9 and d.ball_rel_y < 0 and d.still_s == pytest.approx(1.8)
    assert d.t_ms == 10 * DT and d.release_speed_h is not None and d.release_speed_h > grammar.release_speed_h
    call = soccer.classify_restart(d)
    assert call.type == "throw_in" and call.confidence >= 0.8


def test_grounded_ball_struck_by_the_keeper_is_a_goal_kick(soccer, grammar):
    s = Scene()
    for k in range(30):
        s.people(k, [(700, 400), (900, 380), (1100, 420), (1400, 450)])
        s.people(k, [(380, 760)], cls="goalkeeper", ids=[9])
        if k < 20:
            s.ball(k, 400, 770)                             # at the keeper's feet
        else:
            s.ball(k, 400 + 120 * (k - 19), 770 - 60 * (k - 19))
    dead = _dead(s, grammar)
    assert len(dead) == 1 and dead[0].taker_cls == "goalkeeper" and not dead[0].held and dead[0].roles_known
    assert soccer.classify_restart(dead[0]).type == "goal_kick"


def test_lone_taker_with_everyone_beyond_the_ball_is_a_corner_when_roles_are_known(soccer, grammar):
    def scene(cls_for_one):
        s = Scene()
        for k in range(20):
            s.people(k, [(900, 600), (1000, 650), (1100, 620), (1200, 700), (1050, 560)])
            s.people(k, [(1400, 300)], cls=cls_for_one, ids=[20])      # a keeper, far off
            s.people(k, [(250, 820)], ids=[9])                          # the taker, alone at the flag
            if k < 12:
                s.ball(k, 230, 830)
            else:
                s.ball(k, 230 + 90 * (k - 11), 830 - 50 * (k - 11))
        return s
    d = _dead(scene("goalkeeper"), grammar)[0]
    assert d.nearest_h >= 3 and d.extreme_h >= 2 and d.taker_track == 9
    assert soccer.classify_restart(d).type == "corner"
    # The same scene with a COCO detector, which calls everyone a player: the
    # plugin will not guess between a corner and a goal kick.
    d2 = _dead(scene("player"), grammar)[0]
    assert not d2.roles_known
    call = soccer.classify_restart(d2)
    assert call.type == "restart" and "goal kick" in call.reason


def test_free_kick_has_players_around_the_ball(soccer, grammar):
    s = Scene()
    for k in range(20):
        s.people(k, [(700, 600), (760, 610), (820, 605), (1000, 500), (1300, 520)])   # a wall and two more
        s.people(k, [(560, 640)], ids=[9])
        if k < 10:
            s.ball(k, 600, 650)
        else:
            s.ball(k, 600 + 100 * (k - 9), 650 - 40 * (k - 9))
    d = _dead(s, grammar)[0]
    assert d.n_near >= 2
    assert soccer.classify_restart(d).type == "free_kick"


def test_first_dead_ball_of_the_period_is_the_kick_off_and_the_next_is_not(soccer, grammar):
    s = Scene()
    for k in range(60):
        s.people(k, [(700, 600), (1200, 600), (1000, 400), (1100, 420)])
        s.people(k, [(950, 640)], ids=[9])
        if k < 10 or 30 <= k < 45:
            s.ball(k, 960, 650)
        elif k < 30:
            s.ball(k, 960 + 80 * (k - 9), 650)
        else:
            s.ball(k, 960 + 80 * (k - 44), 650)
    dead = _dead(s, grammar, period_starts_ms=[0])
    assert [d.first_in_period for d in dead] == [True, False]
    assert [soccer.classify_restart(d).type for d in dead] == ["kick_off", "free_kick"]
    # Without a timeline nothing is a kick-off.
    assert all(not d.first_in_period for d in _dead(s, grammar))


def test_a_ball_that_drifts_off_or_is_carried_is_not_a_restart(grammar):
    s = Scene()
    for k in range(30):
        s.people(k, [(700, 600), (1200, 600)])
        s.people(k, [(950, 640)], ids=[9])
        # Still for two seconds, then dribbled away at a walk: no release.
        s.ball(k, 960 + (0 if k < 10 else 8 * (k - 9)), 650)
    assert _dead(s, grammar) == []


def test_a_spare_ball_on_the_touchline_near_nobody_is_ignored(grammar):
    s = Scene(pan=5.0)
    for k in range(30):
        s.people(k, [(700, 600), (1200, 600), (900, 500)])
        s.ball(k, 1700, 1000, tid=77)           # lies there, then pans out of frame
    assert _dead(s, grammar) == []


def test_two_ball_tracks_of_one_restart_merge(soccer, grammar):
    s = Scene()
    for k in range(20):
        s.people(k, [(700, 500), (1200, 500), (1000, 400)])
        s.people(k, [(560, 640)], ids=[9])
        if k < 10:
            s.ball(k, 600, 650, tid=50)
            s.ball(k, 603, 652, tid=51)          # a duplicate detection the tracker kept apart
        else:
            s.ball(k, 600 + 100 * (k - 9), 650, tid=50)
            s.ball(k, 603 + 100 * (k - 9), 652, tid=51)
    assert len(_dead(s, grammar)) == 1


def test_on_a_fixed_camera_registration_says_the_camera_did_not_move(soccer, grammar):
    """Most players jog the same way while the ball sits for a goal kick.
    Estimated from the people, that jog reads as a pan and the still ball
    appears to move; read from a static registration, the camera's motion
    is zero and the restart is found. This is why the people estimate is
    the fallback and registration the source when it exists."""
    from sideline.events import camera_shifts_from_people, camera_shifts_from_registration, merge_shifts

    s = Scene(pan=0.0)
    for k in range(30):
        drift = 4.0 * k                                       # 0.2 heights a sample, together
        s.people(k, [(700 + drift, 400), (900 + drift, 380), (1100 + drift, 420), (1400 + drift, 450)])
        s.people(k, [(380, 760)], cls="goalkeeper", ids=[9])  # the keeper stays by the ball
        if k < 20:
            s.ball(k, 400, 770)
        else:
            s.ball(k, 400 + 120 * (k - 19), 770 - 60 * (k - 19))
    frames = frames_from_rows(s.rows, ["ball"])
    # The people estimate mistakes the jog for a pan.
    assert np.allclose(camera_shifts_from_people(frames)[5], [4.0, 0.0])
    assert find_dead_balls(frames, grammar, SIZE) == []
    # A fixed camera's registration is one homography for every frame.
    H_static = np.array([[0.05, 0.0, -48.0], [0.0, 0.05, -27.0], [0.0, 0.0, 1.0]])
    reg = camera_shifts_from_registration(frames, {f.frame_index: H_static for f in frames}, SIZE)
    assert all(np.allclose(r, 0) for r in reg)
    shifts, n = merge_shifts(reg, camera_shifts_from_people(frames))
    assert n == len(frames) - 1
    dead = find_dead_balls(frames, grammar, SIZE, shifts=shifts)
    assert len(dead) == 1 and soccer.classify_restart(dead[0]).type == "goal_kick"


def test_registration_shifts_read_a_pan_and_fall_back_where_frames_are_unregistered():
    from sideline.events import camera_shifts_from_registration, merge_shifts

    s = Scene()
    for k in range(4):
        s.people(k, [(700, 400), (900, 380)])
    frames = frames_from_rows(s.rows, ["ball"])
    # Image->pitch homographies of a camera panning right 10 px a frame: the
    # same pitch point lands 10 px further left in each next frame. Frame 2
    # was never registered, so neither step touching it has a shift.
    def h(k):
        return np.array([[0.05, 0.0, -48.0 + 0.5 * k], [0.0, 0.05, -27.0], [0.0, 0.0, 1.0]])
    reg = camera_shifts_from_registration(frames, {0: h(0), 1: h(1), 3: h(3)}, SIZE)
    assert np.allclose(reg[1], [-10.0, 0.0]) and reg[2] is None and reg[3] is None
    shifts, n = merge_shifts(reg, [np.zeros(2)] * 4)
    assert n == 1 and np.allclose(shifts[1], [-10, 0]) and np.allclose(shifts[2], 0)


def test_scale_is_the_height_of_the_person_nearest_the_ball():
    """A static camera sees the far touchline at half the near one's height.
    The still radius at the far end must be the far player's, not the frame's."""
    from sideline.events import local_height

    s = Scene()
    s.rows.append(row(0, 1, "player", 300, 900, 30, 80))      # near, tall
    s.rows.append(row(0, 2, "player", 1500, 300, 15, 40))     # far, small
    f = frames_from_rows(s.rows, ["ball"])[0]
    assert local_height(f, np.array([1480.0, 300.0]), 60.0) == 40.0
    assert local_height(f, np.array([320.0, 880.0]), 60.0) == 80.0
    assert local_height(Frame(0, 0, [], []), np.zeros(2), 60.0) == 60.0
