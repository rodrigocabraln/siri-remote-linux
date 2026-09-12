"""Teclado uinput, combinaciones y liberación segura."""
import logging
from ..config import CHORD_BINDING_FIELDS, KEY_BINDINGS, validate_binding, validate_key, validate_optional_key
from ..navigation import Navigation

log = logging.getLogger("siri_remote")


class Keyboard:
    def __init__(self, settings, dry_run=False, ui_factory=None):
        self.ui = self.ecodes = None
        self.held_codes = set()
        self.hold_owners = {}
        self.keys = {action: (validate_binding if field in CHORD_BINDING_FIELDS else
                             validate_optional_key if field == "key_center_long" else validate_key)(getattr(settings, field))
                     for action, field in KEY_BINDINGS.items()}
        self.navigation = Navigation(self.send, settings)
        if not dry_run:
            try: from evdev import UInput, ecodes
            except ImportError as exc: raise RuntimeError("Falta python3-evdev") from exc
            self.ecodes = ecodes
            advertised_keys = {getattr(ecodes, key) for binding in self.keys.values()
                               if binding != "NONE" for key in binding.split("+")}

            # xremap considers a device a keyboard only if it advertises at
            # least KEY_SPACE, KEY_A, and KEY_Z. These are capabilities only;
            # Siri Remote never emits them.
            advertised_keys.update({
                ecodes.KEY_SPACE,
                ecodes.KEY_A,
                ecodes.KEY_Z,
            })

            capabilities = {ecodes.EV_KEY: sorted(advertised_keys)}
            try:
                self.ui = (ui_factory or UInput)(capabilities, name="Siri Remote Linux", phys="siri-remote-linux/input0")
            except Exception as exc:
                if exc.__class__.__module__.split(".")[0] != "evdev" and not isinstance(exc, OSError): raise
                raise RuntimeError(f"No se pudo abrir /dev/uinput: {exc}. Ver README.md.") from exc

    def _write(self, code, pressed):
        if pressed: self.held_codes.add(code)
        if self.ui is not None:
            self.ui.write(self.ecodes.EV_KEY, code, int(pressed)); self.ui.syn()
        # Conservar el estado hasta confirmar write y syn para poder reintentar.
        if not pressed: self.held_codes.discard(code)

    def send(self, method, params=None):
        if method in ("Input.CenterLongDown", "Input.CenterLongUp", "Input.HoldDown", "Input.HoldUp"):
            action = "Input.ContextMenu" if method.startswith("Input.Center") else params["action"]
            binding = self.keys[action]
            if binding == "NONE": return
            codes = tuple(getattr(self.ecodes, part) if self.ecodes else part for part in binding.split("+"))
            pressed = method.endswith("Down")
            if pressed == (action in self.hold_owners): return
            log.info("%s %s", binding, "DOWN" if pressed else "UP")
            if pressed:
                self.hold_owners[action] = codes
                for code in codes:
                    if code not in self.held_codes: self._write(code, True)
            else:
                codes = self.hold_owners[action]
                for code in reversed(codes):
                    if code in self.held_codes and not any(
                            code in held for owner, held in self.hold_owners.items() if owner != action):
                        self._write(code, False)
                self.hold_owners.pop(action)
            return
        action = (params or {}).get("action") if method == "Input.ExecuteAction" else method
        binding = self.keys.get(action)
        if not binding or binding == "NONE": return
        log.info("%s", binding)
        if self.ui is None: return
        codes = [getattr(self.ecodes, part) for part in binding.split("+")]
        if any(code in self.held_codes for code in codes): return
        pressed = []
        try:
            for code in codes: pressed.append(code); self._write(code, True)
        finally:
            error = None
            for code in reversed(pressed):
                try: self._write(code, False)
                except OSError as exc: error = exc
            if error: raise error

    def release_all(self):
        try:
            self.navigation.reset()
        except OSError:
            log.exception("No se pudo liberar una tecla durante el reset; reintentando")
            # Completar el reset sin emitir otra salida; las teclas pendientes
            # se liberan abajo, incluso si el primer intento falló.
            self.navigation.center_long_active = False
            self.navigation.reset()
        finally:
            self.hold_owners.clear()
            for code in list(self.held_codes):
                try: self._write(code, False)
                except OSError: log.exception("No se pudo liberar una tecla")

    def close(self):
        try:
            self.release_all()
        finally:
            if self.ui is not None:
                self.ui.close()
                self.ui = None
                self.held_codes.clear()
