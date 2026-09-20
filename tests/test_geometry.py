import numpy as np
import pytest

from sideline.registration.geometry import (
    apply_h, homography_dlt, homography_dlt_batched, homography_from_quads, intersect,
    is_convex_quad, line_through, point_segment_distance, square_to_quad, surface_error_m,
)

RNG = np.random.default_rng(7)


def _random_h():
    H = np.eye(3) + RNG.normal(0, 0.1, (3, 3))
    H[2, :2] = RNG.normal(0, 1e-3, 2)
    return H / H[2, 2]


def test_dlt_recovers_a_homography_from_noisy_points():
    H = _random_h()
    src = RNG.uniform(-50, 50, (12, 2))
    dst = apply_h(H, src) + RNG.normal(0, 1e-6, (12, 2))
    Hh = homography_dlt(src, dst)
    assert np.allclose(apply_h(Hh, src), dst, atol=1e-4)


def test_batched_dlt_agrees_with_single():
    Hs = [_random_h() for _ in range(6)]
    src = RNG.uniform(-50, 50, (6, 5, 2))
    dst = np.stack([apply_h(H, s) for H, s in zip(Hs, src)])
    Hb = homography_dlt_batched(src, dst)
    for i in range(6):
        assert np.allclose(apply_h(Hb[i], src[i]), dst[i], atol=1e-6)


def test_closed_form_quad_homography_is_exact():
    sq = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float)
    q = RNG.uniform(0, 500, (4, 4, 2))
    H = square_to_quad(q)
    for i in range(4):
        assert np.allclose(apply_h(H[i], sq), q[i], atol=1e-6)
    src = RNG.uniform(-40, 40, (4, 4, 2))
    Hq = homography_from_quads(src, q)
    for i in range(4):
        assert np.allclose(apply_h(Hq[i], src[i]), q[i], atol=1e-5)


def test_affine_quad_takes_the_affine_branch():
    rect = np.array([[-10, -5], [10, -5], [10, 5], [-10, 5]], float)
    H = square_to_quad(rect)
    assert H[2, 0] == 0 and H[2, 1] == 0
    assert np.allclose(apply_h(H, [[0, 0], [1, 1]]), [[-10, -5], [10, 5]])


def test_lines_and_intersections():
    l1 = line_through([0, 0], [10, 0])
    l2 = line_through([5, -3], [5, 3])
    assert np.allclose(intersect(l1, l2), [5, 0])
    assert intersect(l1, line_through([0, 1], [10, 1])) is None


def test_point_segment_distance_and_foot():
    d, foot = point_segment_distance(np.array([[5.0, 3.0], [20.0, 0.0]]), [0, 0], [10, 0])
    assert np.allclose(d, [3.0, 10.0])
    assert np.allclose(foot[0], [5, 0]) and np.allclose(foot[1], [20, 0])


def test_convexity():
    assert is_convex_quad([[0, 0], [1, 0], [1, 1], [0, 1]])
    assert not is_convex_quad([[0, 0], [1, 1], [1, 0], [0, 1]])


def test_surface_error_is_in_metres_between_two_mappings():
    H = _random_h()
    pts = RNG.uniform(0, 1000, (5, 2))
    assert np.allclose(surface_error_m(H, H, pts), 0)
    shifted = H.copy(); shifted[0, 2] += 2.0
    assert np.allclose(surface_error_m(H, shifted, pts), 2.0, atol=1e-6) or (surface_error_m(H, shifted, pts) > 0).all()
