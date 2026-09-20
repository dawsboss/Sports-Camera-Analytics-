import numpy as np
import pytest

from sideline.contracts import CaptureMode
from sideline.registration import StaticRegistrar, ThinPlateSpline
from sideline.registration.geometry import apply_h
from sideline.registration.synthetic import camera_homography
from sideline.sports import get_sport

PITCH = get_sport("soccer").surface(105, 68)
SIZE = (3840, 2160)
# A fixed camera high on the halfway line seeing the whole pitch: Mode A.
H_TRUE = camera_homography((0, -60, 18), (0, 0, 0), focal_px=0.6 * SIZE[0], size=SIZE)


HELD_OUT = {"penalty_spot_left", "penalty_spot_right", "centre_circle_top", "centre_circle_bottom",
            "goal_area_left_top_far", "penalty_right_bottom_far"}


def _clicks(n, noise_px=0.0, seed=0, held_out=False):
    """`n` clicked landmarks, or the held-out set an operator never clicks."""
    rng = np.random.default_rng(seed)
    lm = [l for l in PITCH.landmarks() if (l.name in HELD_OUT) == held_out]
    if not held_out:
        lm = [lm[i] for i in rng.choice(len(lm), n, replace=False)]
    pitch = np.array([l.xy for l in lm], float)
    image = apply_h(H_TRUE, pitch) + rng.normal(0, noise_px, (len(pitch), 2))
    return image, pitch


def test_homography_from_a_few_clicked_landmarks():
    image, pitch = _clicks(6, noise_px=1.0)
    reg = StaticRegistrar(PITCH, image, pitch)
    r = reg.register(None, frame_index=3, t_ms=600)
    assert r.method == "static-homography" and r.registered and r.H is not None
    probe = np.array([[SIZE[0] / 2, SIZE[1] * 0.6], [SIZE[0] * 0.2, SIZE[1] * 0.8]])
    assert np.linalg.norm(r.to_pitch(probe) - apply_h(np.linalg.inv(H_TRUE), probe), axis=1).max() < 0.5
    assert r.as_row()["frame_index"] == 3 and len(r.as_row()["h"]) == 9
    assert reg.mode is CaptureMode.STATIC


def test_fewer_than_four_clicks_is_refused():
    image, pitch = _clicks(3)
    with pytest.raises(ValueError):
        StaticRegistrar(PITCH, image, pitch)


def _distort(image_pts, k=-0.06):
    """Barrel distortion about the image centre, the wide-lens case."""
    c = np.array(SIZE) / 2
    d = (image_pts - c) / c[0]
    r2 = (d ** 2).sum(1, keepdims=True)
    return c + d * (1 + k * r2) * c[0]


def test_spline_beats_homography_on_a_distorted_lens():
    image, pitch = _clicks(20, noise_px=0.5)
    image = _distort(image)
    homo = StaticRegistrar(PITCH, image, pitch, method="homography")
    tps = StaticRegistrar(PITCH, image, pitch, method="auto")
    assert tps.method == "static-tps"
    # Held-out landmarks, distorted the same way.
    held_img, held_pitch = _clicks(0, held_out=True)
    held_img = _distort(held_img)
    e_h = np.linalg.norm(homo.register().to_pitch(held_img) - held_pitch, axis=1).mean()
    e_t = np.linalg.norm(tps.register().to_pitch(held_img) - held_pitch, axis=1).mean()
    # Half the homography's error on landmarks the operator never clicked.
    assert e_t < 0.6 * e_h and e_t < 1.0
    assert tps.register().H is None and tps.register().as_row()["h"] is None


def test_spline_round_trips_and_serialises():
    image, pitch = _clicks(16)
    reg = StaticRegistrar(PITCH, image, pitch, method="tps")
    r = reg.register()
    back = r.to_image(r.to_pitch(image[:5]))
    assert np.allclose(back, image[:5], atol=1e-3)
    again = StaticRegistrar.from_dict(PITCH, reg.to_dict())
    assert again.method == "static-tps" and abs(again.residual_m - reg.residual_m) < 1e-9


def test_tps_is_exact_at_its_own_points():
    src = np.random.default_rng(1).uniform(0, 100, (8, 2))
    dst = src * 2 + 3
    assert np.allclose(ThinPlateSpline(src, dst)(src), dst, atol=1e-8)
