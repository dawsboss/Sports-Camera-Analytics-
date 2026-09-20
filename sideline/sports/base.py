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


@dataclass(frozen=True)
class EventGrammar:
    """The constants the event rules run on. The rules themselves are S8's."""

    event_types: tuple[str, ...]
    touch_radius_m: float        # ball within this of a player is a touch
    duel_radius_m: float         # two opponents within this of the ball is a duel
    possession_min_s: float      # shorter than this is a deflection, not possession
    thirds: tuple[float, float]  # x boundaries between defensive/middle/attacking thirds
    scoring_zones: dict[str, tuple[tuple[float, float], tuple[float, float]]]


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

    def surface(self, length: float, width: float, **dims: float) -> SurfaceModel: ...
    def entity_classes(self) -> Sequence[EntityClass]: ...
    def event_grammar(self, surface: SurfaceModel) -> EventGrammar: ...
    def stat_definitions(self) -> Sequence[StatDefinition]: ...
