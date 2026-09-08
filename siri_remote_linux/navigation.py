"""Gestos y temporizadores independientes de Bluetooth y uinput."""
import time
from .events import ButtonEvent, TouchEvent


class Navigation:
    def __init__(self, send, settings, clock=time.monotonic):
        self.send, self.settings, self.clock = send, settings, clock
        self.origin = self.destination = None
        self.suppressed = False
        self.held = set()
        self.center_start = None
        self.released_at = {}
        self.ignored = set()
        self.last_touch_step = float("-inf")
        self.repeat_due = {}
        self.touch_ready_at = None
        self.touch_axis = None
        self.touch_travel = {}
        self.center_long_active = False
        self.tv_start = None
        self.tv_long_sent = False

    def handle(self, event):
        if isinstance(event, ButtonEvent):
            self.button(event.action.value, event.name)
        elif isinstance(event, TouchEvent):
            self.touch((event.action.value, event.x, event.y, event.pressure, event.dx, event.dy))

    def button(self, action, name):
        now = self.clock()
        if action == "PRESIONADO":
            self.suppressed = True
            if name in self.held or name in self.ignored: return
            if (now - self.released_at.get(name, float("-inf"))) * 1000 < self.settings.button_debounce_ms:
                self.ignored.add(name); return
        else:
            if name in self.ignored:
                self.ignored.remove(name); return
            if name not in self.held: return
            self.released_at[name] = now
            if name == "TV":
                self.tv_tick(now)
                if not self.tv_long_sent: self.send("Input.Home")
                self.tv_start = None; self.held.discard(name); return
            if name == "CENTRO" and self.center_start is not None:
                self.center_tick(now); self.held.discard(name); self.center_start = None
                if self.center_long_active:
                    self.send("Input.CenterLongUp"); self.center_long_active = False
                else: self.send("Input.Select")
                return
        if name == "TV" and action == "PRESIONADO":
            self.held.add(name); self.tv_start = now; self.tv_long_sent = False; return
        if name == "SIRI" and action == "PRESIONADO":
            self.held.add(name); self.send("Input.Siri"); return
        if action == "PRESIONADO":
            self.held.add(name)
            if name == "CENTRO": self.center_start = now
            else:
                methods = {"ARRIBA": "Up", "ABAJO": "Down", "IZQUIERDA": "Left", "DERECHA": "Right", "ATRAS": "Back"}
                actions = {"PLAY_PAUSA": "playpause", "VOLUMEN_ARRIBA": "volumeup",
                           "VOLUMEN_ABAJO": "volumedown", "SILENCIO": "mute"}
                if name in methods: self.send("Input." + methods[name])
                elif name in actions: self.send("Input.ExecuteAction", {"action": actions[name]})
        else:
            self.held.discard(name)
        if (name == "CENTRO" and action == "PRESIONADO" and
                self.settings.key_center == self.settings.key_center_long):
            self.center_long_active = True; self.send("Input.CenterLongDown"); return
        if name in ("ARRIBA", "ABAJO", "IZQUIERDA", "DERECHA"):
            if action == "PRESIONADO" and self.settings.repeat_enabled:
                self.repeat_due[name] = now + self.settings.repeat_delay_ms / 1000
            else: self.repeat_due.pop(name, None)

    def tv_tick(self, now):
        if self.tv_start is not None and not self.tv_long_sent and (now - self.tv_start) * 1000 >= self.settings.long_press_ms:
            self.tv_long_sent = True; self.send("Input.TVLong")

    def center_tick(self, now):
        if self.center_start is not None and not self.center_long_active and (now - self.center_start) * 1000 >= self.settings.long_press_ms:
            self.center_long_active = True; self.send("Input.CenterLongDown")

    def tick(self):
        now = self.clock(); self.center_tick(now); self.tv_tick(now)
        methods = {"ARRIBA": "Up", "ABAJO": "Down", "IZQUIERDA": "Left", "DERECHA": "Right"}
        for name, due in list(self.repeat_due.items()):
            if name not in self.held or not self.settings.repeat_enabled: self.repeat_due.pop(name, None)
            elif now >= due:
                self.send("Input." + methods[name])
                self.repeat_due[name] = now + self.settings.repeat_interval_ms / 1000

    def reset(self):
        if self.center_long_active:
            self.send("Input.CenterLongUp"); self.center_long_active = False
        self.repeat_due.clear(); self.tv_start = None; self.tv_long_sent = False
        self.held.clear(); self.ignored.clear(); self.center_start = None; self.cancel_touch()

    def cancel_touch(self):
        self.origin = self.destination = None; self.touch_ready_at = None
        self.touch_axis = None; self.last_touch_step = float("-inf"); self.touch_travel.clear()

    def touch(self, event):
        action, x, y, *_ = event
        if action == "INICIO":
            self.touch_travel.clear(); self.touch_axis = None; self.last_touch_step = float("-inf")
            self.origin = self.destination = (x, y); self.suppressed = bool(self.held or self.ignored)
            self.touch_ready_at = self.clock() + self.settings.touch_settle_ms / 1000; return
        if self.origin is None: return
        if action == "MOVER":
            moved = (x, y) != self.destination
            if abs(x - self.destination[0]) > 75 or abs(y - self.destination[1]) > 100: self.suppressed = True
            self.destination = (x, y)
            if self.clock() < self.touch_ready_at:
                self.origin = self.destination; return
            if not moved: return
        emit = action == "MOVER" if self.settings.touch_mode == "continuous" else action == "FIN"
        if emit and not self.suppressed:
            dx = (self.destination[0] - self.origin[0]) * self.settings.touch_gain_x
            dy = (self.destination[1] - self.origin[1]) * self.settings.touch_gain_y
            if self.settings.touch_invert_y: dy = -dy
            now = self.clock()
            if self.settings.touch_axis_lock and self.settings.touch_mode == "continuous":
                if self.touch_axis is None:
                    major, minor = max(abs(dx), abs(dy)), min(abs(dx), abs(dy))
                    if major < self.settings.touch_step or major < minor * 1.35: return
                    self.touch_axis = "x" if abs(dx) > abs(dy) else "y"
                if self.touch_axis == "x": dy = 0
                else: dx = 0
            axis = self.touch_axis or ("x" if abs(dx) >= abs(dy) else "y")
            if self.settings.touch_mode == "continuous":
                positions = {"x": x * self.settings.touch_gain_x,
                             "y": y * self.settings.touch_gain_y * (-1 if self.settings.touch_invert_y else 1)}
                for tracked, (sign, peak) in list(self.touch_travel.items()):
                    if (positions[tracked] - peak) * sign > 0: self.touch_travel[tracked] = (sign, positions[tracked])
                if axis in self.touch_travel:
                    sign, peak = self.touch_travel[axis]; retreat = (peak - positions[axis]) * sign
                    if retreat > 0:
                        if retreat < max(self.settings.touch_step, self.settings.touch_reverse_margin): return
                        dx, dy = ((-sign * retreat, 0) if axis == "x" else (0, -sign * retreat))
            if max(abs(dx), abs(dy)) >= self.settings.touch_step and (now - self.last_touch_step) * 1000 >= self.settings.touch_interval_ms:
                direction = ("Right" if dx > 0 else "Left") if abs(dx) >= abs(dy) else ("Up" if dy > 0 else "Down")
                self.send("Input." + direction); self.last_touch_step = now
                if self.settings.touch_mode == "continuous":
                    delta = dx if axis == "x" else dy
                    self.touch_travel[axis] = (1 if delta > 0 else -1, positions[axis])
                self.origin = self.destination
        if action == "FIN":
            self.origin = self.destination = None; self.touch_ready_at = None
            self.touch_axis = None; self.touch_travel.clear()
