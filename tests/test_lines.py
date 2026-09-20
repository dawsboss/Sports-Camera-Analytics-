import numpy as np

from sideline.registration.lines import LineDetectionConfig, detect_lines, merge_segments, split_families
from sideline.registration.synthetic import render_pitch_frame, sideline_camera
from sideline.sports import get_sport

PITCH = get_sport("soccer").surface(105, 68)


def test_grass_and_paint_are_found_on_a_rendered_frame():
    H = sideline_camera(PITCH, -38, 0, 1.0)
    frame = render_pitch_frame(PITCH, H, players=6, seed=3)
    det = detect_lines(frame)
    h, w = frame.shape[:2]
    # The sky and the stands are not grass; the lower part of the frame is.
    assert det.grass[h - 5, w // 2] == 255 and det.grass[5, w // 2] == 0
    assert 4 <= len(det.lines) <= 12
    # Every merged line lies on paint: its samples are on painted pixels.
    for line in det.lines[:4]:
        s = np.round(line.samples(10)).astype(int)
        s = s[(s[:, 0] >= 0) & (s[:, 0] < w) & (s[:, 1] >= 0) & (s[:, 1] < h)]
        hit = det.line_pixels[s[:, 1], s[:, 0]] > 0
        assert hit.mean() > 0.6


def test_players_in_white_kits_are_not_lines():
    H = sideline_camera(PITCH, -38, 0, 1.0)
    bare = detect_lines(render_pitch_frame(PITCH, H, players=0, seed=3))
    crowded = detect_lines(render_pitch_frame(PITCH, H, players=25, seed=3))
    assert abs(len(crowded.lines) - len(bare.lines)) <= 3


def test_collinear_fragments_merge_across_a_gap():
    segs = np.array([[0, 100, 200, 101], [400, 102, 700, 103], [0, 300, 700, 302]], float)
    lines = merge_segments(segs, LineDetectionConfig(min_line_len=50))
    assert len(lines) == 2
    assert max(l.length for l in lines) > 690


def test_families_split_by_direction():
    segs = np.array([[0, 100, 700, 120], [0, 300, 700, 330], [300, 0, 320, 600], [500, 0, 480, 600]], float)
    a, b = split_families(merge_segments(segs, LineDetectionConfig(min_line_len=50)))
    assert len(a) == 2 and len(b) == 2
