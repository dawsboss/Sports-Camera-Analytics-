"""Soccer: the first sport plugin.

The pitch model is parametrised because youth pitches vary by age group. The
defaults are the full-size Laws of the Game; when a coach uploads a match she
supplies the real length and width, and the box dimensions if hers differ.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

from sideline.contracts import CaptureMode, EVENT_TYPES
from sideline.sports.base import (
    DeadBall,
    EntityClass,
    EventGrammar,
    Landmark,
    RestartCall,
    StatDefinition,
    SurfaceCircle,
    SurfaceLine,
)

# How the ball comes back into play. These are the events a coach counts and
# the minutes app already records by hand (corners, throw-ins, goal kicks,
# keeper claims), so they are also the labels the detector is scored against.
# A foul is not here on purpose: it is a referee's judgement with no visible
# signature, and the free kick that follows it is. `restart` is a dead ball
# the rules could see but not name.
RESTART_TYPES: tuple[str, ...] = (
    "throw_in", "corner", "goal_kick", "free_kick", "kick_off", "keeper_claim", "restart",
)

# How long a grounded ball sat says how sure the rules are that play had
# stopped, and nothing about which restart it was. The owner reviewed the
# first 18 minutes of the first full match (8 November, 64 verdicts on S8
# 0.1.0, read through these rules' events): of the grounded dead balls
# his verdicts cover, 10 of the 12 that had sat three seconds were real,
# 3 of the 5 that had sat two, and 9 of the 19 that had only paused.
PLACED_MIN_S = 2.0
SETTLED_S = 3.0


@dataclass(frozen=True)
class PitchModel:
    """Metres, origin at the centre spot. x runs goal to goal, y touchline to
    touchline, so the left goal line is at x = -length/2."""

    length: float = 105.0
    width: float = 68.0
    penalty_area_depth: float = 16.5
    penalty_area_width: float = 40.32
    goal_area_depth: float = 5.5
    goal_area_width: float = 18.32
    centre_circle_radius: float = 9.15
    penalty_spot: float = 11.0
    goal_width: float = 7.32

    def __post_init__(self) -> None:
        if self.length <= 0 or self.width <= 0:
            raise ValueError("pitch dimensions must be positive")
        if self.penalty_area_width > self.width or self.goal_area_width > self.penalty_area_width:
            raise ValueError("box widths must nest inside the pitch width")
        if self.goal_area_depth > self.penalty_area_depth or self.penalty_area_depth * 2 > self.length:
            raise ValueError("box depths must nest inside the pitch length")

    @classmethod
    def from_dimensions(cls, length: float, width: float, **boxes: float) -> "PitchModel":
        """A pitch of the given size. Box dimensions default to full-size values,
        shrunk only when a full-size box would not fit; supply the real ones for
        youth pitches, because the boxes are specified separately from the pitch
        and do not scale with it."""
        defaults = dict(
            penalty_area_depth=min(16.5, length / 2 * 0.4),
            penalty_area_width=min(40.32, width * 0.9),
            goal_area_depth=min(5.5, length / 2 * 0.15),
            goal_area_width=min(18.32, width * 0.5),
            centre_circle_radius=min(9.15, min(length, width) / 5),
            penalty_spot=min(11.0, length / 2 * 0.3),
        )
        defaults.update(boxes)
        return cls(length=length, width=width, **defaults)

    # -- geometry ---------------------------------------------------------

    def lines(self) -> Sequence[SurfaceLine]:
        L, W = self.length / 2, self.width / 2
        pd, pw = self.penalty_area_depth, self.penalty_area_width / 2
        gd, gw = self.goal_area_depth, self.goal_area_width / 2
        out: list[SurfaceLine] = [
            SurfaceLine("touchline_top", (-L, W), (L, W), "x"),
            SurfaceLine("touchline_bottom", (-L, -W), (L, -W), "x"),
            SurfaceLine("goal_line_left", (-L, -W), (-L, W), "y"),
            SurfaceLine("goal_line_right", (L, -W), (L, W), "y"),
            SurfaceLine("halfway", (0, -W), (0, W), "y"),
        ]
        for side, sx in (("left", -1), ("right", 1)):
            x0 = sx * L                    # goal line
            xp = sx * (L - pd)             # penalty area front
            xg = sx * (L - gd)             # goal area front
            out += [
                SurfaceLine(f"penalty_{side}_front", (xp, -pw), (xp, pw), "y"),
                SurfaceLine(f"penalty_{side}_top", (x0, pw), (xp, pw), "x"),
                SurfaceLine(f"penalty_{side}_bottom", (x0, -pw), (xp, -pw), "x"),
                SurfaceLine(f"goal_area_{side}_front", (xg, -gw), (xg, gw), "y", reliable=False),
                SurfaceLine(f"goal_area_{side}_top", (x0, gw), (xg, gw), "x", reliable=False),
                SurfaceLine(f"goal_area_{side}_bottom", (x0, -gw), (xg, -gw), "x", reliable=False),
            ]
        return out

    def circles(self) -> Sequence[SurfaceCircle]:
        return [SurfaceCircle("centre_circle", (0.0, 0.0), self.centre_circle_radius)]

    def landmarks(self) -> Sequence[Landmark]:
        L, W = self.length / 2, self.width / 2
        pd, pw = self.penalty_area_depth, self.penalty_area_width / 2
        gd, gw = self.goal_area_depth, self.goal_area_width / 2
        r = self.centre_circle_radius
        out = [
            Landmark("centre_spot", (0.0, 0.0)),
            Landmark("centre_circle_top", (0.0, r)),
            Landmark("centre_circle_bottom", (0.0, -r)),
            Landmark("halfway_top", (0.0, W)),
            Landmark("halfway_bottom", (0.0, -W)),
        ]
        for side, sx in (("left", -1), ("right", 1)):
            x0, xp, xg = sx * L, sx * (L - pd), sx * (L - gd)
            out += [
                Landmark(f"corner_{side}_top", (x0, W)),
                Landmark(f"corner_{side}_bottom", (x0, -W)),
                Landmark(f"penalty_{side}_top_far", (xp, pw)),
                Landmark(f"penalty_{side}_bottom_far", (xp, -pw)),
                Landmark(f"penalty_{side}_top_near", (x0, pw)),
                Landmark(f"penalty_{side}_bottom_near", (x0, -pw)),
                Landmark(f"goal_area_{side}_top_far", (xg, gw)),
                Landmark(f"goal_area_{side}_bottom_far", (xg, -gw)),
                Landmark(f"goal_area_{side}_top_near", (x0, gw)),
                Landmark(f"goal_area_{side}_bottom_near", (x0, -gw)),
                Landmark(f"penalty_spot_{side}", (sx * (L - self.penalty_spot), 0.0)),
            ]
        return out

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return abs(x) <= self.length / 2 + margin and abs(y) <= self.width / 2 + margin

    def zone_index(self, x: float, y: float) -> Optional[int]:
        """Cell of the 18-cell grid: 6 columns along the length, 3 rows across
        the width, row-major from the left goal line and the bottom touchline."""
        if not self.contains(x, y):
            return None
        col = min(5, int((x + self.length / 2) / (self.length / 6)))
        row = min(2, int((y + self.width / 2) / (self.width / 3)))
        return row * 6 + col

    def thirds(self) -> tuple[float, float]:
        return (-self.length / 6, self.length / 6)


ENTITY_CLASSES: tuple[EntityClass, ...] = (
    EntityClass("player", expected=20, maximum=22),
    EntityClass("goalkeeper", expected=2, maximum=2),
    EntityClass("official", expected=1, maximum=4),
    EntityClass("ball", expected=1, maximum=1, object=True),
    # The scenery that names a restart: a ball by the flag is a corner, a
    # ball in front of the net is a goal kick. No detector emits these yet.
    EntityClass("goal", expected=2, maximum=2, landmark=True),
    EntityClass("corner_flag", expected=4, maximum=4, landmark=True),
)

_BOTH = frozenset({CaptureMode.STATIC, CaptureMode.FOLLOWCAM})
_STATIC_ONLY = frozenset({CaptureMode.STATIC})

STAT_DEFINITIONS: tuple[StatDefinition, ...] = (
    StatDefinition("visible_seconds", "s", "Seconds in frame", _BOTH, False,
                   "The honest denominator for everything else."),
    StatDefinition("visible_pct", "%", "Share of match in frame", _BOTH, False),
    StatDefinition("touches", "count", "Touches", _BOTH, True,
                   "Only touches while in frame; the crop follows the ball so most are."),
    StatDefinition("duels_contested", "count", "Duels", _BOTH, True),
    StatDefinition("avg_x", "m", "Average position (length)", _BOTH, True),
    StatDefinition("avg_y", "m", "Average position (width)", _BOTH, True),
    StatDefinition("zone_histogram", "count", "Time by zone", _BOTH, True,
                   "On follow-cam footage this is where the player was seen, not where she was."),
    StatDefinition("distance_covered_visible", "m", "Distance while in frame", _BOTH, True,
                   "Covers only the seconds the player was in frame."),
    StatDefinition("total_distance", "m", "Distance covered", _STATIC_ONLY, False,
                   "Needs continuous observation."),
    StatDefinition("sprints", "count", "Sprints", _STATIC_ONLY, False,
                   "Needs continuous observation."),
    StatDefinition("top_speed", "m/s", "Top speed", _STATIC_ONLY, False,
                   "Needs continuous observation."),
    StatDefinition("defensive_line_height", "m", "Defensive line height", _STATIC_ONLY, False,
                   "Needs all eleven at once."),
)


class SoccerPlugin:
    sport = "soccer"
    surface_is_grass = True

    def surface(self, length: float = 105.0, width: float = 68.0, **dims: float) -> PitchModel:
        return PitchModel.from_dimensions(length, width, **dims)

    def entity_classes(self) -> Sequence[EntityClass]:
        return ENTITY_CLASSES

    def event_grammar(self, surface: PitchModel) -> EventGrammar:
        L, W = surface.length / 2, surface.width / 2
        pd, pw = surface.penalty_area_depth, surface.penalty_area_width / 2
        return EventGrammar(
            event_types=EVENT_TYPES + RESTART_TYPES,
            touch_radius_m=1.0,
            duel_radius_m=1.5,
            possession_min_s=0.4,
            thirds=surface.thirds(),
            scoring_zones={
                "penalty_area_left": ((-L, -pw), (-L + pd, pw)),
                "penalty_area_right": ((L - pd, -pw), (L, pw)),
            },
            restart_types=RESTART_TYPES,
            # A placed ball sits for seconds; a thrower holds it for one or
            # two. Half a height is about a metre, which is how much a
            # keeper shuffles with the ball at her feet. A thrown or kicked
            # ball leaves at 5 m/s or more, 2.5 heights a second. Priors,
            # until the taps have been scored.
            dead_ball_min_s=1.0,
            still_radius_h=0.5,
            release_speed_h=2.5,
            # Reviewed on the first full match (S8 0.1.0): after the first
            # whistle one of the 24 events marked "not an event" was a ball
            # that had sat three seconds, and eight of the 28 marked real
            # were. A free kick rolled a step sideways after sitting six
            # seconds was one the speed test alone threw away.
            settled_s=SETTLED_S,
            reach_h=1.5,
            held_rel_y=0.45,
            held_above_h=0.3,
            near_h=3.0,
            # A ball is 0.22 m: 0.13 of a 1.7 m player, 0.16 of a 1.4 m one.
            # On the first full match the ball at real restarts measured
            # 0.11 to 0.22 of the taker; a cone behind the touchline and
            # marks on the far fence, taken for a ball at the head or feet
            # of whoever stood in front of them, measured 0.05 to 0.08.
            object_min_h=0.09,
            # A metre, in heights of a 1.7 m player: the heights form of
            # `touch_radius_m`, for frames nobody has registered.
            touch_h=0.6,
            # The first full match saw the release on the next sample at
            # 33 of 317 windows: the ball at rest is lost before it is
            # struck, and whoever stood by it while it sat is often the one
            # who put it down, not the one who ran up. Two seconds is a
            # run-up; much longer and the people estimate of the camera's
            # motion has drifted off the spot. A prior, like the rest.
            strike_window_s=2.0,
            # A dribbled ball runs a stride or two ahead between touches,
            # and the detector misses one or two samples at a time on
            # either real match (worst run 15, which this does not bridge).
            possession_carry_s=1.5,
        )

    def stat_definitions(self) -> Sequence[StatDefinition]:
        return STAT_DEFINITIONS

    def classify_restart(self, obs: DeadBall) -> RestartCall:
        """Name a dead ball from what was visible, with no pitch coordinates.

        Each restart has a physical signature a camera sees without knowing
        where on the pitch it stood. A throw-in is the one restart taken
        with the ball in the hands, above the head. A keeper with the ball
        in hand is a claim, not a restart. A grounded ball by the corner
        flag is a corner; one in front of the goal, or put back by the
        keeper, is a goal kick. The first dead ball of a period is the
        kick-off. What is left is a grounded ball taken by an outfield
        player with no flag or goal in sight: alone, with everyone else
        beyond the ball, that is a corner (or a goal kick where the detector
        cannot tell a keeper from a player, and the call says so).

        Everything else is a `restart`: a dead ball the rules saw and will
        not name. The first full match is why. The old last branch called
        every such ball a free kick, 295 of 317 events; of the 28 the owner
        marked as real, four were named rightly. Every cue a `DeadBall`
        carries was then set against those 28: none separates a throw-in
        waiting to be picked up from a free kick from a goal kick, and
        "placed and left among players" was a free kick 2 times in 12. What
        separates them is where on the pitch the ball lay, which needs the
        touchline and the goal: registration, or the landmark detector. So
        the confidence here says how sure it is that play had stopped, the
        reason says what was around the ball, and the name waits.
        """
        keeper = obs.taker_cls == "goalkeeper"
        if obs.held:
            if keeper:
                return RestartCall("keeper_claim", 0.8, "ball in the keeper's hands")
            sure = 0.9 if obs.still_s >= 1.0 else 0.7
            return RestartCall("throw_in", sure, "ball held high in the taker's box before release")
        flag = obs.landmarks_h.get("corner_flag")
        goal = obs.landmarks_h.get("goal")
        if flag is not None and flag <= 3.0:
            return RestartCall("corner", 0.85, "grounded ball by the corner flag")
        if obs.first_in_period and obs.since_period_start_s is not None and obs.since_period_start_s <= 90.0:
            return RestartCall("kick_off", 0.8, "first dead ball of the period")
        if keeper:
            return RestartCall("goal_kick", 0.7, "the keeper put a grounded ball back into play")
        if goal is not None and goal <= 8.0:
            return RestartCall("goal_kick", 0.7, "grounded ball in front of the goal")
        if obs.taker_track is None:
            return RestartCall("restart", 0.3, "nobody within reach of the still ball")
        alone = obs.nearest_h is None or obs.nearest_h >= 3.0
        beyond = obs.extreme_h is not None and obs.extreme_h >= 2.0
        if alone and beyond and obs.n_visible >= 3:
            if obs.roles_known:
                return RestartCall("corner", 0.6, "taker alone with every other player beyond the ball")
            return RestartCall("restart", 0.5, "corner or goal kick; no keeper class to tell them apart")
        around = ("players close around it, as at a free kick or a throw-in not yet picked up" if obs.n_near >= 2
                  else "room around the taker, as at a goal kick or a corner")
        if obs.still_s >= SETTLED_S:
            return RestartCall("restart", 0.7, f"ball left on the ground for {obs.still_s:.0f} s; {around}")
        if obs.still_s >= PLACED_MIN_S:
            return RestartCall("restart", 0.5, f"ball put down for {obs.still_s:.0f} s; {around}")
        return RestartCall("restart", 0.3, f"ball paused for a second; {around}; may be open play")
