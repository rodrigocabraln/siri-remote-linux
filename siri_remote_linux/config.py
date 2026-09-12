"""Configuración local: config.env se interpreta como datos, no como shell."""
from dataclasses import dataclass, fields, replace
from pathlib import Path
import math
import re

KEY_BINDINGS = {
    "Input.Up": "key_up", "Input.Down": "key_down",
    "Input.Left": "key_left", "Input.Right": "key_right",
    "Input.Select": "key_center", "Input.ContextMenu": "key_center_long",
    "Input.Back": "key_back", "Input.BackLong": "key_back_long",
    "Input.Home": "key_tv",
    "playpause": "key_playpause", "volumeup": "key_volumeup",
    "volumedown": "key_volumedown", "mute": "key_mute",
    "Input.TVLong": "key_tv_long",
    "Input.Siri": "key_siri", "Input.SiriLong": "key_siri_long",
    "Input.BackDouble": "key_back_double", "Input.TVDouble": "key_tv_double",
    "Input.SiriDouble": "key_siri_double",
}

CHORD_BINDING_FIELDS = {
    "key_tv_long", "key_siri",
    "key_back_long", "key_siri_long",
    "key_back_double", "key_tv_double", "key_siri_double",
}


def validate_key(value):
    try:
        from evdev import ecodes
    except ImportError as exc:
        raise RuntimeError("Falta python3-evdev") from exc
    if (not value.startswith("KEY_") or value not in ecodes.ecodes or
            not 0 < ecodes.ecodes[value] < ecodes.KEY_MAX or
            value in ("KEY_MAX", "KEY_CNT")):
        raise ValueError("usar un nombre de tecla evdev válido, por ejemplo KEY_ENTER")
    return value


def validate_binding(value):
    if value == "NONE":
        return value
    parts = value.split("+")
    for part in parts:
        validate_key(part)
    if len(parts) != len(set(parts)):
        raise ValueError("tecla duplicada en combinación")
    return value


def validate_optional_key(value):
    return value if value == "NONE" else validate_key(value)


@dataclass(frozen=True)
class Settings:
    remote_identity: str = ""
    adapter: str = "hci0"
    button_debounce_ms: int = 50
    long_press_ms: int = 300
    double_click_ms: int = 300
    key_back_double: str = "NONE"
    key_tv_double: str = "NONE"
    key_siri_double: str = "NONE"
    repeat_enabled: bool = True
    repeat_delay_ms: int = 450
    repeat_interval_ms: int = 120
    touch_mode: str = "continuous"
    touch_step: float = 18
    touch_interval_ms: int = 90
    touch_settle_ms: int = 50
    touch_pressure_on: int = 10
    touch_pressure_off: int = 4
    touch_gain_x: float = 1
    touch_gain_y: float = 1
    touch_invert_y: bool = False
    touch_axis_lock: bool = True
    touch_reverse_margin: float = 4
    key_up: str = "KEY_UP"
    key_down: str = "KEY_DOWN"
    key_left: str = "KEY_LEFT"
    key_right: str = "KEY_RIGHT"
    key_center: str = "KEY_ENTER"
    key_center_long: str = "KEY_ENTER"
    key_back: str = "KEY_ESC"
    key_back_long: str = "NONE"
    key_tv: str = "KEY_HOME"
    key_playpause: str = "KEY_PLAYPAUSE"
    key_volumeup: str = "KEY_VOLUMEUP"
    key_volumedown: str = "KEY_VOLUMEDOWN"
    key_mute: str = "KEY_MUTE"
    key_tv_long: str = "KEY_LEFTMETA+KEY_D"
    key_siri: str = "KEY_F12"
    key_siri_long: str = "NONE"


NUMBERS = {
    "TOUCH_PRESSURE_ON": (int, 1, 255), "TOUCH_PRESSURE_OFF": (int, 1, 255),
    "DOUBLE_CLICK_MS": (int, 0, 2000),
    "BUTTON_DEBOUNCE_MS": (int, 0, 2000), "LONG_PRESS_MS": (int, 100, 5000),
    "REPEAT_DELAY_MS": (int, 100, 5000), "REPEAT_INTERVAL_MS": (int, 30, 2000),
    "TOUCH_STEP": (float, 1, 100), "TOUCH_INTERVAL_MS": (int, 0, 2000),
    "TOUCH_SETTLE_MS": (int, 0, 1000), "TOUCH_REVERSE_MARGIN": (float, 0, 100),
    "TOUCH_GAIN_X": (float, 0.1, 10), "TOUCH_GAIN_Y": (float, 0.1, 10),
}
BOOLS = {"TOUCH_INVERT_Y", "REPEAT_ENABLED", "TOUCH_AXIS_LOCK"}


def parse_settings(text, source="config.env"):
    values = {}
    key_fields = {name.upper() for name in KEY_BINDINGS.values()}
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip().strip("\"'")
        try:
            if not separator:
                raise ValueError("falta =")
            if key in key_fields:
                validator = (validate_binding if key.lower() in CHORD_BINDING_FIELDS else
                             validate_optional_key if key == "KEY_CENTER_LONG" else validate_key)
                parsed = validator(value)
            elif key in NUMBERS:
                convert, low, high = NUMBERS[key]
                parsed = convert(value)
                if not math.isfinite(parsed) or not low <= parsed <= high:
                    raise ValueError(f"debe estar entre {low} y {high}")
            elif key == "TOUCH_MODE":
                parsed = value
                if value not in ("continuous", "release"):
                    raise ValueError("usar continuous o release")
            elif key in BOOLS:
                if value.lower() not in ("true", "false"):
                    raise ValueError("usar true o false")
                parsed = value.lower() == "true"
            elif key == "REMOTE_IDENTITY":
                if value and not re.fullmatch(r"(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", value):
                    raise ValueError("usar una identidad Bluetooth como AA:BB:CC:DD:EE:FF")
                parsed = value.upper()
            elif key == "ADAPTER":
                if not re.fullmatch(r"hci[0-9]+", value):
                    raise ValueError("usar un adaptador como hci0")
                parsed = value
            else:
                raise ValueError("opción desconocida")
            name = key.lower()
            if name in values:
                raise ValueError("opción duplicada")
            values[name] = parsed
        except ValueError as exc:
            raise ValueError(f"{source}:{number}: {key}: {exc}") from exc
    known = {item.name for item in fields(Settings)}
    if not set(values) <= known:
        raise AssertionError("parser y Settings no coinciden")
    settings = replace(Settings(), **values)
    if settings.touch_pressure_off > settings.touch_pressure_on:
        raise ValueError(f"{source}: TOUCH_PRESSURE_OFF: debe ser menor o igual que TOUCH_PRESSURE_ON")
    if any(getattr(settings, field) != "NONE" for field in (
            "key_back_double", "key_tv_double", "key_siri_double")):
        if settings.double_click_ms <= settings.button_debounce_ms:
            raise ValueError(f"{source}: DOUBLE_CLICK_MS: debe ser mayor que BUTTON_DEBOUNCE_MS")
    return settings


def load_settings(path):
    path = Path(path)
    if not path.exists():
        raise ValueError(f"No existe {path}. Ejecutá ./siri-remote setup.")
    return parse_settings(path.read_text(encoding="utf-8"), str(path))


def save_identity(path, identity, template_path=None, *, adapter=None):
    """Update the identity and selected adapter while preserving other settings."""
    identity = identity.upper()
    parse_settings(f"REMOTE_IDENTITY={identity}", str(path))
    path = Path(path)
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    elif template_path:
        lines = Path(template_path).read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    updates = {"REMOTE_IDENTITY": identity}
    if adapter is not None:
        parse_settings(f"ADAPTER={adapter}", str(path))
        updates["ADAPTER"] = adapter
    for key, value in updates.items():
        replacement = f"{key}={value}"
        found = False
        for index, line in enumerate(lines):
            if line.split("#", 1)[0].partition("=")[0].strip() == key:
                lines[index], found = replacement, True
        if not found:
            lines.insert(0, replacement)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
