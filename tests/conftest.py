import cv2
import numpy as np
import pytest

from sideline.registration.synthetic import render_pitch_frame, sideline_camera
from sideline.sports import get_sport


@pytest.fixture(scope="session")
def pitch():
    return get_sport("soccer").surface(105, 68)


def write_synthetic_video(path, pitch, frames=75, fps=25.0, size=(320, 180), blank=()):
    """A short clip of a follow-cam panning past the left box. Frames in
    `blank` show no grass, which is how the half-time test makes a break."""
    w = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    assert w.isOpened()
    for i in range(frames):
        if i in blank:
            frame = np.full((size[1], size[0], 3), (120, 110, 100), np.uint8)
        else:
            H = sideline_camera(pitch, -40 + 10 * np.sin(i / 10), 0, 1.0, size=size)
            frame = render_pitch_frame(pitch, H, size=size, line_px=2, players=3, seed=i)
        w.write(frame)
    w.release()
    return path


@pytest.fixture
def clip(tmp_path, pitch):
    return write_synthetic_video(tmp_path / "clip.mp4", pitch)
