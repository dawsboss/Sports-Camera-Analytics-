import pyarrow as pa
import pytest
from datetime import datetime, timezone

from sideline.contracts import (
    Aggregate, CaptureMode, EVENTS_SCHEMA, MODE_A_ONLY_STATS, PlayerStats, PlayerStatsDoc,
    REGISTRATION_SCHEMA, TRACKS_SCHEMA, Team, ZoneHistogram,
)


def _agg(mode, value=1.0, partial=None):
    return Aggregate(value=value, sample_count=10, capture_mode=mode,
                     partial=(mode is CaptureMode.FOLLOWCAM) if partial is None else partial)


def _player(mode, **over):
    base = dict(
        player_id="p1", team=Team.HOME, capture_mode=mode,
        visible_seconds=_agg(mode, 1200), visible_pct=_agg(mode, 40),
        touches=_agg(mode, 12), duels_contested=_agg(mode, 3),
        position_samples=6000, avg_x=_agg(mode, -10), avg_y=_agg(mode, 4),
        zone_histogram=ZoneHistogram(cells=[0] * 18, sample_count=6000, capture_mode=mode,
                                     partial=mode is CaptureMode.FOLLOWCAM),
        distance_covered_visible=_agg(mode, 3100), confidence_grade="B",
    )
    base.update(over)
    return PlayerStats(**base)


def test_tracks_schema_matches_spec_table():
    assert TRACKS_SCHEMA.names == [
        "t_ms", "match_minute", "track_id", "player_id", "team",
        "x_pitch", "y_pitch", "conf_det", "conf_reg", "visible",
    ]
    assert TRACKS_SCHEMA.field("player_id").nullable, "null until identity resolution succeeds"
    assert not TRACKS_SCHEMA.field("t_ms").nullable


def test_events_schema_matches_spec():
    assert EVENTS_SCHEMA.names == ["t_ms", "type", "x_pitch", "y_pitch", "player_id", "team", "confidence"]


def test_registration_schema_holds_a_nullable_3x3():
    f = REGISTRATION_SCHEMA.field("h")
    assert f.nullable and f.type == pa.list_(pa.float64(), 9)


def test_followcam_player_stats_refuse_impossible_stats():
    for name in MODE_A_ONLY_STATS:
        with pytest.raises(ValueError, match="cannot be shipped from follow-cam"):
            _player(CaptureMode.FOLLOWCAM, **{name: _agg(CaptureMode.FOLLOWCAM, 9000)})
    # Present-but-empty is the contract: the schema is the same in both modes.
    p = _player(CaptureMode.FOLLOWCAM, total_distance=Aggregate(value=None, sample_count=0,
                                                                capture_mode=CaptureMode.FOLLOWCAM, partial=True))
    assert p.total_distance.value is None


def test_followcam_distance_must_be_flagged_partial():
    with pytest.raises(ValueError, match="partial"):
        _player(CaptureMode.FOLLOWCAM, distance_covered_visible=_agg(CaptureMode.FOLLOWCAM, 3100, partial=False))


def test_static_player_stats_may_carry_everything():
    p = _player(CaptureMode.STATIC, total_distance=_agg(CaptureMode.STATIC, 9800, partial=False),
                sprints=_agg(CaptureMode.STATIC, 14, partial=False))
    assert p.total_distance.value == 9800 and not p.distance_covered_visible.partial


def test_aggregate_mode_must_match_player_mode():
    with pytest.raises(ValueError, match="different capture mode"):
        _player(CaptureMode.STATIC, touches=_agg(CaptureMode.FOLLOWCAM, 3))


def test_doc_is_one_mode_throughout():
    p = _player(CaptureMode.FOLLOWCAM)
    doc = PlayerStatsDoc(match_id="m", capture_mode=CaptureMode.FOLLOWCAM,
                         produced_at=datetime.now(timezone.utc), pipeline_version="0.1.0", players={"p1": p})
    assert doc.players["p1"].capture_mode is CaptureMode.FOLLOWCAM
    with pytest.raises(ValueError, match="different capture mode"):
        PlayerStatsDoc(match_id="m", capture_mode=CaptureMode.STATIC,
                       produced_at=datetime.now(timezone.utc), pipeline_version="0.1.0", players={"p1": p})
