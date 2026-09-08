"""Eventos comunes entre perfiles, navegación y salida."""
from dataclasses import dataclass
from enum import Enum


class ButtonAction(str, Enum):
    PRESSED = "PRESIONADO"
    RELEASED = "SOLTADO"


class TouchAction(str, Enum):
    START = "INICIO"
    MOVE = "MOVER"
    END = "FIN"


@dataclass(frozen=True)
class ButtonEvent:
    action: ButtonAction
    name: str


@dataclass(frozen=True)
class TouchEvent:
    action: TouchAction
    x: int
    y: int
    pressure: int
    dx: int = 0
    dy: int = 0
