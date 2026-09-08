"""Teclado uinput, combinaciones y liberación segura."""
import logging
from ..config import KEY_BINDINGS, validate_binding, validate_key
from ..navigation import Navigation

log = logging.getLogger("siri_remote")


class Keyboard:
    def __init__(self, settings, dry_run=False, ui_factory=None):
        self.ui = self.ecodes = None
        self.held_codes = set()
        self.keys = {action: (validate_binding if field in ("key_tv_long", "key_siri") else validate_key)(getattr(settings, field))
                     for action, field in KEY_BINDINGS.items()}
        self.navigation = Navigation(self.send, settings)
        if not dry_run:
            try: from evdev import UInput, ecodes
            except ImportError as exc: raise RuntimeError("Falta python3-evdev") from exc
            self.ecodes = ecodes
            capabilities = {ecodes.EV_KEY: sorted({getattr(ecodes, key) for binding in self.keys.values()
                            if binding != "NONE" for key in binding.split("+")})}
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
        if method in ("Input.CenterLongDown", "Input.CenterLongUp"):
            binding = self.keys["Input.ContextMenu"]
            if binding == "NONE": return
            code = getattr(self.ecodes, binding) if self.ecodes else binding
            pressed = method.endswith("Down")
            if pressed == (code in self.held_codes): return
            log.info("%s %s", binding, "DOWN" if pressed else "UP")
            self._write(code, pressed); return
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
            log.exception("No se pudo liberar Centro durante el reset; reintentando")
            # Completar el reset sin emitir otra salida; las teclas pendientes
            # se liberan abajo, incluso si el primer intento falló.
            self.navigation.center_long_active = False
            self.navigation.reset()
        finally:
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
