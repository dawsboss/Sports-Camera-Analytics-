"""Dead balls found from fragments, and named by the sport, with no pitch
coordinates anywhere. The scenes are scripted in image pixels the way a
follow-cam would show them: the camera pans, the players stand, the ball
sits still and is then thrown or struck."""

import numpy as np
import pytest

from sideline.events import Frame, camera_shifts_from_people, find_breaks, find_dead_balls, frames_from_rows
from sideline.sports import get_sport

SIZE = (1920, 1080)
H = 60.0          # a player's height in pixels on this footage
DT = 200          # ms between samples at 5 fps


def row(k, tid, cls, x, y, w, h):
    return {"frame_index": k, "t_ms": k * DT, "track_id": tid, "cls": cls,
            "x1": x - w / 2, "y1": y - h, "x2": x + w / 2, "y2": y, "conf": 0.8}


def ball_row(k, tid, x, y, r=5):
    return {"frame_index": k, "t_ms": k * DT, "track_id": tid, "cls": "ball",
            "x1": x - r, "y1": y - r, "x2": x + r, "y2": y + r, "conf": 0.4}


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

    def ball(self, k, x, y, tid=50, r=5):
        self.rows.append(ball_row(k, tid, x + self.shift(k), y, r))


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
    def scene(still):
        s = Scene()
        for k in range(still + 10):
            s.people(k, [(700, 600), (760, 610), (820, 605), (1000, 500), (1300, 520)])   # a wall and two more
            s.people(k, [(560, 640)], ids=[9])
            if k < still:
                s.ball(k, 600, 650)
            else:
                s.ball(k, 600 + 100 * (k - still + 1), 650 - 40 * (k - still + 1))
        return s
    d = _dead(scene(21), grammar)[0]                       # put down and left for 4 s
    assert d.n_near >= 2 and d.still_s == pytest.approx(4.0)
    # A dead ball the plugin is sure of and will not name: among players it
    # is a free kick or a throw-in not yet picked up, and nothing here says
    # which. A reviewer names it; the reason tells her what was seen.
    call = soccer.classify_restart(d)
    assert call.type == "restart" and call.confidence >= 0.7 and "free kick" in call.reason
    # The same ball among the same players after only a second's pause was
    # open play more often than not on real footage, and the call says so.
    brief = soccer.classify_restart(_dead(scene(7), grammar)[0])
    assert brief.type == "restart" and brief.confidence < 0.5 and "open play" in brief.reason


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
    # The second has nobody close around it and nothing in view to name it.
    assert [soccer.classify_restart(d).type for d in dead] == ["kick_off", "restart"]
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


def test_a_ball_missed_for_two_samples_is_judged_by_where_it_turned_up(grammar):
    """The detector misses the ball for a sample or two far more often than
    it sees a release, so a gap is not a strike. Seen again a step away, the
    ball drifted; seen again far off, it was struck, and the speed across
    the gap says so."""
    def scene(reappears_at):
        s = Scene()
        for k in range(24):
            s.people(k, [(700, 600), (1200, 600)])
            s.people(k, [(950, 640)], ids=[9])
            if k < 10:
                s.ball(k, 960, 650)
            elif k >= 12:
                s.ball(k, reappears_at + 8 * (k - 12), 650)   # then rolls on at a walk
        return s
    assert _dead(scene(1000), grammar) == []                # 0.7 heights in 0.6 s: a drift
    struck = _dead(scene(1260), grammar)                    # 5 heights in 0.6 s
    assert len(struck) == 1
    d = struck[0]
    assert d.t_ms == 10 * DT and d.release_speed_h == pytest.approx(300 / H / 0.6) and d.taker_track == 9


def test_a_ball_unseen_for_seconds_and_found_where_it_was_is_one_dead_ball(grammar):
    """The detector loses a ball at rest for seconds at a time. Found in
    the same place, it never left: one dead ball, still since it first came
    to rest, timed when it is finally played. Reviewed on the first full
    match, treating the gap as an ending lost a goal kick, a free kick and
    two throw-ins, or reported them seconds before anything happened."""
    def scene(first_rest):
        s = Scene()
        for k in range(40):
            s.people(k, [(700, 600), (1200, 600)])
            s.people(k, [(950, 640)], ids=[9])
            if k < first_rest or 18 <= k < 28:
                s.ball(k, 960, 650)                          # still, lost, still there
            elif k >= 28:
                s.ball(k, 960 + 90 * (k - 27), 650)
        return s
    dead = _dead(scene(8), grammar)
    assert len(dead) == 1 and dead[0].t_ms == 28 * DT and dead[0].release_speed_h is not None
    assert dead[0].still_s == pytest.approx(5.4)
    # Two sightings before it had sat a second are not a ball at rest: the
    # gap is not bridged, and only what was seen afterwards counts.
    short = _dead(scene(3), grammar)
    assert len(short) == 1 and short[0].still_s == pytest.approx(1.8)


def test_a_ball_that_sat_for_seconds_is_dead_however_slowly_it_leaves(grammar):
    """A free kick rolled a step to a team-mate leaves no faster than a
    dribble. After two seconds at rest that is a drift and no restart; after
    `settled_s` the ball was dead whatever became of it."""
    def scene(still):
        s = Scene()
        for k in range(still + 12):
            s.people(k, [(700, 600), (1200, 600)])
            s.people(k, [(950, 640)], ids=[9])
            s.ball(k, 960 + (0 if k < still else 8 * (k - still + 1)), 650)
        return s
    assert _dead(scene(10), grammar) == []
    settled = _dead(scene(int(grammar.settled_s * 5) + 2), grammar)
    assert len(settled) == 1 and settled[0].release_speed_h < grammar.release_speed_h


def test_nothing_before_the_first_whistle_is_an_event(grammar):
    """Balls are put down and struck all through the warm-up. With the
    period starts known, a dead ball well before the first of them is not
    reported; one a few seconds early still is, because the whistle time is
    typed by hand."""
    s = Scene()
    for k in range(120):
        s.people(k, [(700, 600), (1200, 600)])
        s.people(k, [(950, 640)], ids=[9])
        if k < 10 or 80 <= k < 95:
            s.ball(k, 960, 650)
        elif k < 20:
            s.ball(k, 960 + 90 * (k - 9), 650)
        elif k >= 95:
            s.ball(k, 960 + 90 * (k - 94), 650)
    assert [d.t_ms for d in _dead(s, grammar)] == [10 * DT, 95 * DT]
    assert [d.t_ms for d in _dead(s, grammar, period_starts_ms=[20_000])] == [95 * DT]
    assert [d.t_ms for d in _dead(s, grammar, period_starts_ms=[5_000])] == [10 * DT, 95 * DT]


def test_a_ball_back_at_rest_with_the_same_taker_was_never_released(soccer, grammar):
    """A thrower brings the ball from overhead to her chest and holds it
    there before throwing. The quick move between the two reads as a
    release; the ball coming to rest again in the same hands says it was
    not. One throw-in, timed at the throw, dead since she first held it."""
    s = Scene()
    thrower = (400, 700)
    for k in range(30):
        s.people(k, [(600, 500), (800, 520), (1000, 480), (1300, 560)])
        s.people(k, [thrower], ids=[9])
        if k < 8:
            s.ball(k, 400, 700 - H - 16)                     # overhead
        elif k < 21:
            s.ball(k, 400, 700 - H + 24)                     # at her chest, 0.67 heights lower one sample later
        else:
            s.ball(k, 400 + 80 * (k - 20), 700 - H + 24 - 30 * (k - 20))
    dead = _dead(s, grammar)
    assert len(dead) == 1
    d = dead[0]
    assert d.t_ms == 21 * DT and d.taker_track == 9 and d.held and d.still_s == pytest.approx(4.0)
    assert soccer.classify_restart(d).type == "throw_in"
    # With the merge switched off the two holds are two throw-ins.
    frames = frames_from_rows(s.rows, ["ball"])
    assert len(find_dead_balls(frames, grammar, SIZE, same_taker_s=0.0)) == 2


def test_a_small_object_behind_a_player_is_not_in_her_hands(grammar):
    """A mark on the far fence that the detector takes for a ball sits at the
    head of whoever stands in front of it. It is too small to be a ball at
    her distance, so she is not its taker, and with nobody at it a ball that
    merely vanishes is not a restart."""
    def scene(r):
        s = Scene()
        for k in range(20):
            s.people(k, [(700, 600), (1200, 600), (900, 500)])
            s.people(k, [(400, 700)], ids=[9])
            if k < 12:
                s.ball(k, 400, 700 - H + 6, r=r)             # at her head, then gone
        return s
    assert grammar.object_min_h * H > 4
    assert _dead(scene(2), grammar) == []                    # 4 px across beside a 60 px player
    held = _dead(scene(5), grammar)                          # a real ball there is in her hands
    assert len(held) == 1 and held[0].held and held[0].taker_track == 9


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


def test_a_landmark_in_frame_is_the_camera_motion_and_names_the_restart(soccer, grammar):
    """The same lone taker at the end of the pitch, with a COCO detector
    that calls everyone a player: a corner flag by the ball makes it a
    corner, and the goal frame makes it a goal kick. The flag is fixed
    scenery, so its shift between frames is the pan exactly, even while
    every player drifts."""
    from sideline.events import camera_shifts_from_people

    def scene(landmark, at):
        s = Scene(pan=6.0)
        for k in range(20):
            drift = 3.0 * k
            s.people(k, [(900 + drift, 600), (1000 + drift, 650), (1100 + drift, 620), (1200 + drift, 700), (1050 + drift, 560)])
            s.people(k, [(250, 820)], ids=[9])
            x, y = at
            s.rows.append({"frame_index": k, "t_ms": k * DT, "track_id": 40, "cls": landmark,
                           "x1": x - 20 + s.shift(k), "y1": y - 120, "x2": x + 20 + s.shift(k), "y2": y, "conf": 0.9})
            if k < 12:
                s.ball(k, 230, 830)
            else:
                s.ball(k, 230 + 90 * (k - 11), 830 - 50 * (k - 11))
        return s
    flag_scene = scene("corner_flag", (260, 840))
    frames = frames_from_rows(flag_scene.rows, ["ball"], ["corner_flag", "goal"])
    assert all(len(f.landmarks) == 1 and len(f.people) == 6 for f in frames)
    assert np.allclose(camera_shifts_from_people(frames)[3], [6.0, 0.0])      # the flag, not the drifting players
    dead = find_dead_balls(frames, grammar, SIZE)
    assert len(dead) == 1 and dead[0].landmarks_h["corner_flag"] < 1.0 and not dead[0].roles_known
    assert soccer.classify_restart(dead[0]).type == "corner"
    goal_frames = frames_from_rows(scene("goal", (380, 840)).rows, ["ball"], ["corner_flag", "goal"])
    d = find_dead_balls(goal_frames, grammar, SIZE)[0]
    assert "goal" in d.landmarks_h and soccer.classify_restart(d).type == "goal_kick"


def test_the_taker_is_the_last_one_at_the_ball_not_whoever_stood_by_it(soccer, grammar):
    # One player puts the ball down and waits a stride from it. The
    # detector loses the ball at rest, another player runs up and strikes
    # it, and it is next seen forty metres on. The camera pans throughout,
    # so the spot the ball lay on has to be carried into the later frames.
    s = Scene(pan=5.0)
    others = [(300, 500), (1300, 520), (1500, 480)]
    spot = (800, 700)
    for k in range(22):
        s.people(k, others)
        s.people(k, [(spot[0] - 75, spot[1] + 4)], ids=[7])                    # stood by it while it sat
        run = min(1.0, max(0.0, (k - 8) / 4))
        s.people(k, [(spot[0] + 400 - (400 - 8) * run, spot[1] + 3)], ids=[9])    # arrives at sample 12
        if k < 10:
            s.ball(k, *spot)
        elif k >= 14:
            s.ball(k, spot[0] + 150 * (k - 12), spot[1] - 40)
    (d,) = _dead(s, grammar)
    assert d.taker_track == 9 and not d.held and d.release_speed_h is None

    # With nobody running up, the one who stood nearest while it sat still takes it.
    s.rows = [r for r in s.rows if r["track_id"] != 9]
    (d,) = _dead(s, grammar)
    assert d.taker_track == 7


def test_whoever_crosses_the_spot_after_the_window_is_not_the_taker(grammar):
    s = Scene()
    spot = (800, 700)
    late = int(grammar.strike_window_s * 1000 / DT) + 3
    for k in range(10 + late + 6):
        s.people(k, [(300, 500), (1300, 520), (1500, 480)])
        s.people(k, [(spot[0] - 20, spot[1] + 4)], ids=[7])
        if k >= 10 + late:
            s.people(k, [(spot[0] + 6, spot[1] + 3)], ids=[9])
        if k < 10:
            s.ball(k, *spot)
    (d,) = _dead(s, grammar)
    assert d.taker_track == 7


def test_a_stretch_with_nobody_moving_is_a_break_whatever_the_camera_does(grammar):
    """Half-time on a follow-cam: the grass never leaves the frame, the
    crop keeps panning a little, and the one thing that changes is that
    nobody is running. Thirty seconds of play, thirty of standing about,
    thirty of play, with the break made short so the scene stays small."""
    from dataclasses import replace

    g = replace(grammar, break_min_s=20.0)
    spots = [(300 + 130 * i, 500 + 15 * (i % 3)) for i in range(10)]

    def scene(pan):
        s = Scene(pan=pan)
        for k in range(450):
            playing = k < 150 or k >= 300
            jog = 8 * (k % 40 if k % 80 < 40 else 80 - k % 80) if playing else 0   # 0.67 heights a second, there and back
            # Half jog one way and half the other: all one way would read as the camera panning.
            s.people(k, [(x + jog * (1 if i % 2 else -1), y) for i, (x, y) in enumerate(spots)])
        return frames_from_rows(s.rows, ["ball"])

    assert find_breaks(scene(0.0), g, window_s=5.0) == [(30_000, 60_000)]
    assert find_breaks(scene(3.0), g, window_s=5.0) == [(30_000, 60_000)]
    # The same lull is not a break when a break must last longer than it did.
    assert find_breaks(scene(0.0), replace(grammar, break_min_s=40.0), window_s=5.0) == []
    # And a film in which nobody ever moves has no usual level to fall below.
    still = Scene()
    for k in range(200):
        still.people(k, spots)
    assert find_breaks(frames_from_rows(still.rows, ["ball"]), g, window_s=5.0) == []


def test_nothing_put_back_into_play_inside_a_break_is_an_event(grammar):
    s = Scene()
    for k in range(120):
        s.people(k, [(700, 600), (1200, 600)])
        s.people(k, [(950, 640)], ids=[9])
        if k < 10 or 80 <= k < 95:
            s.ball(k, 960, 650)
        elif k < 20:
            s.ball(k, 960 + 90 * (k - 9), 650)
        elif k >= 95:
            s.ball(k, 960 + 90 * (k - 94), 650)
    assert [d.t_ms for d in _dead(s, grammar)] == [10 * DT, 95 * DT]
    # A ball knocked about at half-time is not a restart; the one struck as the break ends is.
    assert [d.t_ms for d in _dead(s, grammar, breaks_ms=[(0, 19_000)])] == [95 * DT]
    assert [d.t_ms for d in _dead(s, grammar, breaks_ms=[(0, 95 * DT)])] == [95 * DT]


def test_a_period_is_entered_a_little_before_its_whistle(soccer, grammar):
    """A period's start is typed or tapped at the kick; the dead ball is
    timed at when the ball was last seen at rest, which is earlier."""
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
    dead = _dead(s, grammar, period_starts_ms=[0, 12_000])          # the second release is at 9 s
    assert [soccer.classify_restart(d).type for d in dead] == ["kick_off", "kick_off"]
    assert dead[1].since_period_start_s == 0.0
    late = _dead(s, grammar, period_starts_ms=[0, 15_000])
    assert [soccer.classify_restart(d).type for d in late] == ["kick_off", "restart"]
