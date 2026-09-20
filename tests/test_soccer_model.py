import pytest

from sideline.contracts import CaptureMode
from sideline.sports import get_sport
from sideline.sports.soccer import PitchModel


def test_full_size_pitch_has_the_laws_of_the_game_lines():
    p = get_sport("soccer").surface(105, 68)
    names = {l.name for l in p.lines()}
    assert {"touchline_top", "touchline_bottom", "goal_line_left", "goal_line_right", "halfway",
            "penalty_left_front", "penalty_right_top", "goal_area_left_front"} <= names
    # Six distinct lines run along the pitch, seven across it.
    assert sorted({l.coordinate for l in p.lines() if l.family == "x"}) == [-34.0, -20.16, -9.16, 9.16, 20.16, 34.0]
    assert sorted({l.coordinate for l in p.lines() if l.family == "y"}) == [-52.5, -47.0, -36.0, 0, 36.0, 47.0, 52.5]


def test_landmarks_sit_on_lines():
    p = PitchModel()
    by_name = {l.name: l.xy for l in p.landmarks()}
    assert by_name["corner_left_top"] == (-52.5, 34.0)
    assert by_name["penalty_spot_right"] == (41.5, 0.0)
    assert by_name["centre_circle_top"] == (0.0, 9.15)


def test_zone_grid_is_18_cells_row_major_from_left_goal_and_bottom_touchline():
    p = PitchModel()
    assert p.zone_index(-52.5, -34) == 0
    assert p.zone_index(52.5, 34) == 17
    assert p.zone_index(0.0, 0.0) == 9
    assert p.zone_index(60, 0) is None


def test_youth_pitch_keeps_boxes_inside_and_can_be_overridden():
    small = PitchModel.from_dimensions(70, 45)
    assert small.penalty_area_width <= 45 and small.penalty_area_depth * 2 <= 70
    custom = PitchModel.from_dimensions(70, 45, penalty_area_depth=12.0, penalty_area_width=30.0)
    assert custom.penalty_area_depth == 12.0
    with pytest.raises(ValueError):
        PitchModel(length=30, width=68)   # two full-size boxes cannot fit


def test_stat_definitions_say_what_followcam_may_ship():
    defs = {d.name: d for d in get_sport("soccer").stat_definitions()}
    assert defs["touches"].available_in(CaptureMode.FOLLOWCAM)
    assert not defs["total_distance"].available_in(CaptureMode.FOLLOWCAM)
    assert defs["distance_covered_visible"].partial_in_followcam


def test_unknown_sport_is_an_error():
    with pytest.raises(KeyError):
        get_sport("cricket")
