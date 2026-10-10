"""Compatibility facade: ``pyprobe.colors`` → ``present/color.py`` (§10.5-2).

The color helpers live in the present layer (ADR A8); this module only
re-exports them under the old import path. Same rule as the offsets
facade: new code inside pyprobe imports ``present.color`` directly.
"""

from .present.color import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    RED,
    RESET,
    YELLOW,
    cyan,
    dim,
    green,
    red,
    should_color,
    yellow_bold,
)

__all__ = [
    "BOLD",
    "CYAN",
    "DIM",
    "GREEN",
    "RED",
    "RESET",
    "YELLOW",
    "cyan",
    "dim",
    "green",
    "red",
    "should_color",
    "yellow_bold",
]
