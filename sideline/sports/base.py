"""The sport plugin boundary.

S0 through S7 contain no soccer-specific logic. Everything a sport needs is
supplied through this interface in four parts: a surface model, entity
classes, an event grammar and stat definitions. The test of whether the
boundary is real: adding basketball should mean a court model and a rules
file, and zero changes to detection, tracking or review code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Protocol, Sequence

from sideline.contracts import CaptureMode

# Straight surface lines come in two families. A line of family "x" runs along
# the length of the surface (constant y); family "y" runs across it (constant
# x). Registration leans on this: lines of one family are parallel on the
# surface, so in an image they share a vanishing point and their order is
# preserved, which is what makes matching detected lines to model lines a
# small search rather than a large one.
Family = Literal["x", "y"]


@dataclass(frozen=True)
class SurfaceLine:
    name: str
    p0: tuple[float, float]
    p1: tuple[float, float]
    family: Optional[Family]
    # Whether it is worth fitting against. Short lines that are hard to see
    # (goal-area edges) still help scoring but are not trusted on their own.
    reliable: bool = True

    @property
    def coordinate(self) -> float:
        """The constant coordinate of a family line: y for "x", x for "y"."""
        if self.family == "x":
            return self.p0[1]
        if self.family == "y":
            return self.p0[0]
        raise ValueError(f"{self.name} has no family")

    @property
    def length(self) -> float:
        return ((self.p1[0] - self.p0[0]) ** 2 + (self.p1[1] - self.p0[1]) ** 2) ** 0.5


@dataclass(frozen=True)
class SurfaceCircle:
    name: str
    centre: tuple[float, float]
    radius: float
    reliable: bool = True


@dataclass(frozen=True)
class Landmark:
    """A named point an operator can click on for static registration."""

    name: str
    xy: tuple[float, float]


class SurfaceModel(Protocol):
    length: float
    width: float

    def lines(self) -> Sequence[SurfaceLine]: ...
    def circles(self) -> Sequence[SurfaceCircle]: ...
    def landmarks(self) -> Sequence[Landmark]: ...
    def contains(self, x: float, y: float, margin: float = 0.0) -> bool: ...
    def zone_index(self, x: float, y: float) -> Optional[int]: ...


@dataclass(frozen=True)
class EntityClass:
    name: str
    expected: Optional[int]     # how many are on the surface in normal play
    maximum: Optional[int]      # hard cap, None if unbounded
    # The object of play (a ball, a puck) rather than a person. Tracking
    # follows people by box overlap and the object by where it went, and the
    # event rules measure everything in heights of the people, so the stages
    # need to know which is which without knowing what the object is called.
    object: bool = False
    # Fixed scenery the sport names (a goal, a corner flag, a basket). Not a
    # person: it sets no height scale and is never a taker. Where one is seen
    # in two frames it is the camera's exact motion, and its distance from a
    # dead ball is a cue the sport's rules read by its name.
    landmark: bool = False


@dataclass(frozen=True)
class EventGrammar:
    """The constants the event rules run on. The rules themselves are S8's.

    The dead-ball constants are in heights of the player nearest the ball
    rather than metres or pixels, so the same rule reads the same on a
    follow-cam frame nobody could register and on a static camera, and so
    the event stage never needs pitch coordinates to find a restart.
    """

    event_types: tuple[str, ...]
    touch_radius_m: float        # ball within this of a player is a touch
    duel_radius_m: float         # two opponents within this of the ball is a duel
    possession_min_s: float      # shorter than this is a deflection, not possession
    thirds: tuple[float, float]  # x boundaries between defensive/middle/attacking thirds
    scoring_zones: dict[str, tuple[tuple[float, float], tuple[float, float]]]
    restart_types: tuple[str, ...]
    dead_ball_min_s: float           # the ball must sit still at least this long
    still_radius_h: float            # within this many heights of one spot
    release_speed_h: float           # then move at least this fast, heights per second
    reach_h: float                   # a taker is a player within this of the ball
    held_rel_y: float                # a ball above this fraction of the taker's box is in hand
    held_above_h: float              # and may sit this far above the box top and still be in hand
    near_h: float                    # other players within this of the ball count as close


@dataclass(frozen=True)
class DeadBall:
    """What the event stage saw at a moment the ball stopped and was then put
    back into play: everything a sport's rules need to name the restart, in
    units that do not depend on the camera. Distances are in heights of the
    taker, which is why no field here is in metres or pixels. Nothing here
    knows which sport it is; the sport's `classify_restart` reads it."""

    t_ms: int                            # when the ball was put back into play
    still_s: float                       # how long it sat still before that
    taker_track: Optional[int]           # the fragment nearest the ball; None if nobody was within reach
    taker_cls: Optional[str]             # that fragment's entity class
    taker_team: Optional[str]            # its team, once team assignment has run
    held: bool                           # the ball sat high in the taker's box while still
    ball_rel_y: Optional[float]          # ball centre within the taker's box: 0 head, 1 feet
    ball_u: float                        # ball position in the frame, 0 left to 1 right
    ball_v: float                        # 0 top to 1 bottom
    n_visible: int                       # other players in frame at release
    nearest_h: Optional[float]           # nearest other player to the ball, in taker heights
    n_near: int                          # other players within the grammar's `near_h` of the ball
    extreme_h: Optional[float]           # how far the ball lies beyond every other player on some axis; negative when it is among them
    landmarks_h: dict[str, float]        # nearest seen landmark of each class, by name, in taker heights; empty when none is in frame
    release_speed_h: Optional[float]     # ball speed just after release, heights per second; None if it left the frame
    since_period_start_s: Optional[float]  # seconds since the period began, when the timeline is known
    first_in_period: bool                # no earlier dead ball in this period
    roles_known: bool                    # the detector tells kinds of people apart at all


@dataclass(frozen=True)
class RestartCall:
    """A sport's reading of a DeadBall: which restart, how sure, and why."""

    type: str
    confidence: float
    reason: str


@dataclass(frozen=True)
class StatDefinition:
    name: str
    unit: str
    display: str
    modes: frozenset[CaptureMode]
    partial_in_followcam: bool
    caveat: str = ""

    def available_in(self, mode: CaptureMode) -> bool:
        return mode in self.modes


class SportPlugin(Protocol):
    sport: str
    # Whether the playing surface is grass. Detection uses it to drop people
    # whose feet are off the surface; a court sport says False and keeps
    # everyone, because the grass mask would find nothing on wood.
    surface_is_grass: bool

    def surface(self, length: float, width: float, **dims: float) -> SurfaceModel: ...
    def entity_classes(self) -> Sequence[EntityClass]: ...
    def event_grammar(self, surface: SurfaceModel) -> EventGrammar: ...
    def stat_definitions(self) -> Sequence[StatDefinition]: ...
    def classify_restart(self, obs: DeadBall) -> RestartCall: ...
