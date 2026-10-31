"""Soccer's reading of a dead ball. The rules live in the sport plugin so
the event stage knows nothing about throw-ins; these pin what each visible
signature is called, and that the plugin says when it cannot tell."""

from sideline.sports import get_sport
from sideline.sports.base import DeadBall, RestartCall


def _obs(**over) -> DeadBall:
    base = dict(
        t_ms=60_000, still_s=2.0, taker_track=7, taker_cls="player", taker_team=None, held=False,
        ball_rel_y=0.95, ball_u=0.5, ball_v=0.6, n_visible=8, nearest_h=1.2, n_near=3,
        extreme_h=-2.0, release_speed_h=6.0, since_period_start_s=600.0, first_in_period=False,
        roles_known=True,
    )
    base.update(over)
    return DeadBall(**base)


def test_ball_held_high_is_a_throw_in_and_in_a_keepers_hands_a_claim():
    soccer = get_sport("soccer")
    call = soccer.classify_restart(_obs(held=True, ball_rel_y=0.1))
    assert isinstance(call, RestartCall) and call.type == "throw_in" and call.confidence >= 0.8
    assert soccer.classify_restart(_obs(held=True, still_s=0.6)).confidence < 0.8
    assert soccer.classify_restart(_obs(held=True, taker_cls="goalkeeper")).type == "keeper_claim"


def test_keeper_with_a_grounded_ball_is_a_goal_kick():
    assert get_sport("soccer").classify_restart(_obs(taker_cls="goalkeeper")).type == "goal_kick"


def test_first_dead_ball_of_a_period_is_the_kick_off():
    soccer = get_sport("soccer")
    assert soccer.classify_restart(_obs(first_in_period=True, since_period_start_s=12.0)).type == "kick_off"
    # Twenty minutes in, "first" means the earlier ones were missed, not that this is a kick-off.
    assert soccer.classify_restart(_obs(first_in_period=True, since_period_start_s=1200.0)).type != "kick_off"
    assert soccer.classify_restart(_obs(first_in_period=True, since_period_start_s=None)).type != "kick_off"


def test_alone_with_everyone_beyond_the_ball_is_a_corner_only_when_keepers_are_told_apart():
    soccer = get_sport("soccer")
    alone = dict(nearest_h=4.0, n_near=0, extreme_h=3.0)
    assert soccer.classify_restart(_obs(**alone)).type == "corner"
    call = soccer.classify_restart(_obs(roles_known=False, **alone))
    assert call.type == "restart" and "goal kick" in call.reason


def test_grounded_ball_among_players_is_a_free_kick():
    soccer = get_sport("soccer")
    wall = soccer.classify_restart(_obs(n_near=4))
    spread = soccer.classify_restart(_obs(n_near=0, nearest_h=2.5, extreme_h=-1.0))
    assert wall.type == spread.type == "free_kick" and wall.confidence > spread.confidence


def test_nobody_within_reach_is_an_unnamed_restart():
    call = get_sport("soccer").classify_restart(_obs(taker_track=None, taker_cls=None, nearest_h=None))
    assert call.type == "restart" and call.confidence < 0.5


def test_grammar_lists_the_restart_types_as_event_types():
    soccer = get_sport("soccer")
    g = soccer.event_grammar(soccer.surface(105, 68))
    assert {"throw_in", "corner", "goal_kick", "free_kick", "kick_off", "keeper_claim", "restart"} <= set(g.event_types)
    assert set(g.restart_types) <= set(g.event_types) and "touch" in g.event_types
    assert g.dead_ball_min_s > 0 and g.release_speed_h > 0
