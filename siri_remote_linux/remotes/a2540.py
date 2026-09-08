"""Perfil Apple Siri Remote A2540."""
from dataclasses import dataclass
import re

from .base import RemoteProfile
from ..events import ButtonAction, ButtonEvent, TouchAction, TouchEvent

BLUEZ = "org.bluez"
HID = "00001812-0000-1000-8000-00805f9b34fb"
REPORT = "00002a4d-0000-1000-8000-00805f9b34fb"
REPORT_REFERENCE = "00002908-0000-1000-8000-00805f9b34fb"
BUTTONS = {
    0x0001: "TV", 0x0002: "VOLUMEN_ARRIBA", 0x0004: "VOLUMEN_ABAJO",
    0x0008: "CENTRO", 0x0010: "ENCENDIDO", 0x0020: "SIRI",
    0x0040: "ATRAS", 0x0080: "SILENCIO", 0x0100: "PLAY_PAUSA",
    0x0200: "ARRIBA", 0x0400: "DERECHA", 0x0800: "ABAJO", 0x1000: "IZQUIERDA",
}


def value_handle(path, props):
    declaration = props.get("Handle")
    if declaration is None:
        match = re.search(r"/char([0-9a-fA-F]{4})$", str(path))
        if not match:
            raise ValueError(f"No se puede obtener el handle: {path}")
        declaration = int(match[1], 16)
    return int(declaration) + 1


def a2540_layout(chars):
    matches = []
    for shift in (0, 1):
        expected = {0x2E: "00002a19-0000-1000-8000-00805f9b34fb",
                    0x31: "00002a1a-0000-1000-8000-00805f9b34fb",
                    0x35: REPORT, 0x39: REPORT, 0x3D: REPORT, 0x4D: REPORT}
        if all(h + shift in chars and str(chars[h + shift][1].get("UUID")) == uuid
               for h, uuid in expected.items()):
            matches.append((0x39 + shift, 0x4D + shift))
    if len(matches) != 1:
        raise RuntimeError("Tabla GATT A2540 no reconocida; no se envió la activación.")
    buttons, activation = matches[0]
    if "notify" not in chars[buttons][1].get("Flags", []):
        raise RuntimeError("El informe de botones no permite notificaciones.")
    flags = chars[activation][1].get("Flags", [])
    if "write" in flags:
        return buttons, activation, "request"
    if "write-without-response" in flags:
        return buttons, activation, "command"
    raise RuntimeError("El informe de activación no permite escritura.")


def validate_report_reference(data, allowed_types):
    if len(data) != 2 or data[1] not in allowed_types:
        raise RuntimeError(f"Report Reference HID inesperado: {data.hex(' ')}; no se activa.")
    return data[0], data[1]


class TouchDecoder:
    def __init__(self): self.last = None

    def update(self, data):
        if len(data) != 11:
            raise ValueError(f"Touch: se esperaban 11 bytes, llegaron {len(data)}")
        finger = data[4:11]
        x = int((finger[0] + 255 * (finger[1] & 7) - 230) / 15)
        if x < 0: x += 150
        y = (finger[2] if finger[2] & 128 else finger[2] + 255) - 188
        pressure = finger[5]
        if pressure == 0:
            if self.last is None: return None
            x, y, _ = self.last
            self.last = None
            return TouchEvent(TouchAction.END, x, y, 0)
        previous = self.last
        self.last = (x, y, pressure)
        if previous is None:
            return TouchEvent(TouchAction.START, x, y, pressure)
        if self.last == previous: return None
        return TouchEvent(TouchAction.MOVE, x, y, pressure, x - previous[0], y - previous[1])


@dataclass(frozen=True)
class A2540Configuration:
    button_path: str
    touch_path: str | None
    activation_path: str
    write_type: str
    activation: bytes = b"\xf0\x00"


class A2540Profile(RemoteProfile):
    model = "A2540"

    def __init__(self, no_touch=False):
        self.no_touch = no_touch
        self.button_path = self.touch_path = None
        self.previous = 0
        self.touch = TouchDecoder()

    @classmethod
    def matches(cls, props):
        return (0x004C in props.get("ManufacturerData", {}) and HID in props.get("UUIDs", [])
                and int(props.get("Appearance", 0)) == 0x03C0 and "Class" not in props)

    @classmethod
    def stable_identity(cls, props):
        # BlueZ Address es Identity Address después del vínculo; AddressType no basta.
        address = str(props.get("Address", "")).upper()
        if not re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", address):
            raise ValueError("BlueZ no publicó una identidad Bluetooth válida")
        return address

    def configure(self, chars, objects, read_descriptor):
        buttons, activation, write_type = a2540_layout(chars)
        touch = buttons + 4
        checks = [(buttons, (1,)), (activation, (2, 3))]
        if not self.no_touch:
            if touch not in chars or "notify" not in chars[touch][1].get("Flags", []):
                raise RuntimeError("El informe touch no permite notificaciones; probá --no-touch.")
            checks.append((touch, (1,)))
        for handle, types in checks:
            char_path, props = chars[handle]
            service_path = props.get("Service")
            service = objects.get(service_path, objects.get(str(service_path), {})).get(BLUEZ + ".GattService1", {})
            if str(service.get("UUID")) != HID:
                raise RuntimeError("El informe seleccionado no pertenece al servicio HID.")
            descriptors = [str(path) for path, interfaces in objects.items()
                if str(interfaces.get(BLUEZ + ".GattDescriptor1", {}).get("Characteristic")) == char_path
                and str(interfaces[BLUEZ + ".GattDescriptor1"].get("UUID")) == REPORT_REFERENCE]
            if len(descriptors) != 1:
                raise RuntimeError(f"Falta Report Reference único en {char_path}; no se activa.")
            validate_report_reference(bytes(read_descriptor(descriptors[0])), types)
        self.button_path = chars[buttons][0]
        self.touch_path = None if self.no_touch else chars[touch][0]
        return A2540Configuration(self.button_path, self.touch_path, chars[activation][0], write_type)

    def decode(self, path, value):
        data = bytes(value)
        if path == self.button_path:
            if len(data) != 2:
                raise ValueError("El informe de botones debe tener exactamente 2 bytes")
            current = int.from_bytes(data, "little")
            events = []
            for bit in (1 << n for n in range(16)):
                if (self.previous ^ current) & bit:
                    events.append(ButtonEvent(ButtonAction.PRESSED if current & bit else ButtonAction.RELEASED,
                                              BUTTONS.get(bit, f"DESCONOCIDO_0x{bit:04x}")))
            self.previous = current
            return events
        if path == self.touch_path:
            if len(data) != 11:
                # Una trama A2540 de 18 bytes puede separar dos contactos. Romper continuidad.
                self.touch.last = None
                raise ValueError(f"Touch: se esperaban 11 bytes, llegaron {len(data)}")
            event = self.touch.update(data)
            return [event] if event else []
        return []

    def reset(self):
        self.previous = 0
        self.touch.last = None
