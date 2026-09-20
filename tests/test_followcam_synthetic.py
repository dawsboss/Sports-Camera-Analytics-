"""The registrar against frames rendered through a known camera.

Real Veo footage is the only thing that answers M1; this pins the machinery
so a change that breaks it is caught before anyone burns a Saturday on the
real thing. The camera is a mast on the halfway line at the near touchline,
which is where a Veo stands.
"""

import numpy as np
import pytest

from sideline.contracts import CaptureMode, REGISTRATION_SCHEMA
from sideline.registration import FollowCamRegistrar
from sideline.registration.geometry import apply_h, surface_error_m
from sideline.registration.synthetic import render_pitch_frame, sideline_camera
from sideline.sports import get_sport

import pyarrow as pa

PITCH = get_sport("soccer").surface(105, 68)
REG = FollowCamRegistrar(PITCH)


def _error_in_view(H_true, reg, size=(1280, 720)):
    """Mean metres between truth and estimate over the pitch that is in frame."""
    W, H = size
    grid = np.stack(np.meshgrid(np.linspace(0, W, 13), np.linspace(0, H, 8)), -1).reshape(-1, 2)
    truth = apply_h(np.linalg.inv(H_true), grid)
    on = np.isfinite(truth).all(1) & (np.abs(truth[:, 0]) <= PITCH.length / 2) & (np.abs(truth[:, 1]) <= PITCH.width / 2)
    return float(np.nanmean(surface_error_m(np.linalg.inv(H_true), reg.H, grid[on])))


@pytest.mark.parametrize("aim_x,aim_y,zoom", [(-38, 0, 1.0), (-42, -5, 1.6), (40, 5, 1.2), (-45, 8, 1.5), (45, -10, 1.2)])
def test_box_views_register_within_a_metre(aim_x, aim_y, zoom):
    H_true = sideline_camera(PITCH, aim_x, aim_y, zoom)
    frame = render_pitch_frame(PITCH, H_true, players=8, seed=1)
    r = REG.register(frame, frame_index=7, t_ms=1400)
    assert r.registered and r.method == "followcam-lines" and r.n_lines >= 4
    assert _error_in_view(H_true, r) < 1.0
    # The half-turn symmetry is broken the right way: the box on the left of
    # the frame is the left box.
    left_goal = r.to_pitch(np.array([[0.0, 700.0]]))[0]
    assert np.sign(left_goal[0]) == np.sign(aim_x)


def test_a_view_with_one_crossing_line_is_reported_not_guessed():
    # Midfield: both touchlines, the halfway line and the circle. Two lines
    # in one direction and one in the other is not enough for four
    # correspondences, so the honest answer is "unregistered", with a reason.
    frame = render_pitch_frame(PITCH, sideline_camera(PITCH, 0, 0, 1.0), players=4, seed=2)
    r = REG.register(frame)
    assert not r.registered and r.mapping is None and r.reason


def test_grass_only_is_unregistrable():
    frame = np.full((720, 1280, 3), (70, 150, 70), np.uint8)
    r = REG.register(frame)
    assert not r.registered and "lines" in r.reason


def test_registration_row_matches_the_artifact_schema():
    H_true = sideline_camera(PITCH, -38, 0, 1.0)
    r = REG.register(render_pitch_frame(PITCH, H_true, seed=1), frame_index=3, t_ms=600)
    table = pa.Table.from_pylist([r.as_row()], schema=REGISTRATION_SCHEMA)
    assert table.num_rows == 1 and len(table.column("h")[0].as_py()) == 9
    assert REG.mode is CaptureMode.FOLLOWCAM


def test_sweep_registers_most_views_and_is_rarely_confidently_wrong():
    """The synthetic stand-in for the spec's Mode B gates: at least 70% of
    views registered, and an accepted registration is wrong by more than
    3 m in fewer than a fifth of cases. Real footage sets the real bar."""
    views = [(ax, ay, z) for ax in (-45, -35, -25, 25, 35, 45) for ay, z in ((0, 1.0), (-10, 1.2))]
    accepted, wrong = 0, 0
    for ax, ay, z in views:
        H_true = sideline_camera(PITCH, ax, ay, z)
        r = REG.register(render_pitch_frame(PITCH, H_true, players=6, seed=int(ax + 100 * z)))
        if r.registered:
            accepted += 1
            if _error_in_view(H_true, r) > 3.0:
                wrong += 1
    assert accepted >= 0.7 * len(views), accepted
    assert wrong <= 0.2 * accepted, wrong
