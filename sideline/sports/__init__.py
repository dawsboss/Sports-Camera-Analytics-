"""Sport plugins, looked up by name so no stage imports one directly."""

from __future__ import annotations

from sideline.sports.base import SportPlugin

_REGISTRY: dict[str, SportPlugin] = {}


def register(plugin: SportPlugin) -> SportPlugin:
    _REGISTRY[plugin.sport] = plugin
    return plugin


def get_sport(name: str) -> SportPlugin:
    if name not in _REGISTRY:
        from sideline.sports.soccer import SoccerPlugin  # the built-in

        register(SoccerPlugin())
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"no sport plugin named {name!r}; known: {sorted(_REGISTRY)}") from None
