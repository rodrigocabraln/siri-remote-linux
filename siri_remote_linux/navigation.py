"""Gestos y temporizadores independientes de Bluetooth y uinput."""
import time
import logging
from .events import ButtonEvent, TouchEvent


log = logging.getLogger("siri_remote")

VOLUME_ACTIONS = {"VOLUMEN_ARRIBA": "volumeup", "VOLUMEN_ABAJO": "volumedown"}


GESTURE_BUTTONS = {
    "ATRAS": ("Input.Back", "Input.BackLong", "key_back_long", "Input.BackDouble", "key_back_double"),
    "TV": ("Input.Home", "Input.TVLong", "key_tv_long", "Input.TVDouble", "key_tv_double"),
    "SIRI": ("Input.Siri", "Input.SiriLong", "key_siri_long", "Input.SiriDouble", "key_siri_double"),
}


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
        self.touch_contact = 0
        self.touch_pressure_active = False
        self.center_long_active = False
        self.gesture_start = {}
        self.gesture_long_sent = set()
        self.pending_clicks = {}
        self.double_candidates = set()
        self.held_gestures = set()

    def handle(self, event):
        if isinstance(event, ButtonEvent):
            self.button(event.action.value, event.name)
        elif isinstance(event, TouchEvent):
            self.touch((event.action.value, event.x, event.y, event.pressure, event.dx, event.dy))

    def button(self, action, name):
        now = self.clock()
        log.debug("BUTTON %s %s", action, name)
        self.click_tick(now)
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
            if name in GESTURE_BUTTONS:
                if name in self.held_gestures:
                    self.held_gestures.discard(name); self.held.discard(name)
                    self.send("Input.HoldUp", {"action": GESTURE_BUTTONS[name][0]})
                    return
                self.gesture_tick(name, now)
                self.gesture_start.pop(name, None); self.held.discard(name)
                if name in self.gesture_long_sent:
                    self.gesture_long_sent.discard(name)
                elif self.gesture_requires_release(name):
                    self.complete_gesture_click(name, now)
                return
            if name == "CENTRO" and self.center_start is not None:
                self.center_tick(now); self.held.discard(name); self.center_start = None
                if self.center_long_active:
                    self.send("Input.CenterLongUp"); self.center_long_active = False
                else: self.send("Input.Select")
                return
        if name in GESTURE_BUTTONS and action == "PRESIONADO":
            if self.gesture_is_hold(name):
                self.held.add(name); self.held_gestures.add(name)
                self.send("Input.HoldDown", {"action": GESTURE_BUTTONS[name][0]})
                return
            self.held.add(name); self.gesture_start[name] = now
            self.gesture_long_sent.discard(name)
            if name in self.pending_clicks:
                self.pending_clicks.pop(name); self.double_candidates.add(name)
            if not self.gesture_requires_release(name): self.send(GESTURE_BUTTONS[name][0])
            return
        if action == "PRESIONADO":
            self.held.add(name)
            if name == "CENTRO":
                if self.settings.key_center_long == "NONE": self.send("Input.Select")
                else: self.center_start = now
            else:
                methods = {"ARRIBA": "Up", "ABAJO": "Down", "IZQUIERDA": "Left", "DERECHA": "Right", "ATRAS": "Back"}
                actions = {"PLAY_PAUSA": "playpause", **VOLUME_ACTIONS, "SILENCIO": "mute"}
                if name in methods: self.send("Input." + methods[name])
                elif name in actions: self.send("Input.ExecuteAction", {"action": actions[name]})
        else:
            self.held.discard(name)
        if (name == "CENTRO" and action == "PRESIONADO" and
                self.settings.key_center == self.settings.key_center_long):
            self.center_long_active = True; self.send("Input.CenterLongDown"); return
        if name in ("ARRIBA", "ABAJO", "IZQUIERDA", "DERECHA") or name in VOLUME_ACTIONS:
            if action == "PRESIONADO" and self.settings.repeat_enabled:
                self.repeat_due[name] = now + self.settings.repeat_delay_ms / 1000
            else: self.repeat_due.pop(name, None)

    def gesture_is_hold(self, name):
        _, _, long_field, _, double_field = GESTURE_BUTTONS[name]
        short = getattr(self.settings, long_field.removesuffix("_long"))
        return (short != "NONE" and short == getattr(self.settings, long_field) and
                getattr(self.settings, double_field) == "NONE")

    def gesture_requires_release(self, name):
        _, _, long_field, _, double_field = GESTURE_BUTTONS[name]
        return (getattr(self.settings, long_field) != "NONE" or
                getattr(self.settings, double_field) != "NONE")

    def complete_gesture_click(self, name, now):
        short_method, _, _, double_method, double_field = GESTURE_BUTTONS[name]
        if getattr(self.settings, double_field) == "NONE":
            self.send(short_method)
        elif name in self.double_candidates:
            self.double_candidates.discard(name); self.send(double_method)
        else:
            self.pending_clicks[name] = now + self.settings.double_click_ms / 1000

    def click_tick(self, now):
        for name, due in list(self.pending_clicks.items()):
            if now >= due:
                self.pending_clicks.pop(name)
                self.send(GESTURE_BUTTONS[name][0])

    def gesture_tick(self, name, now):
        _, long_method, long_field, _, _ = GESTURE_BUTTONS[name]
        start = self.gesture_start.get(name)
        if (start is not None and name not in self.gesture_long_sent and
                getattr(self.settings, long_field) != "NONE" and
                (now - start) * 1000 >= self.settings.long_press_ms):
            self.gesture_long_sent.add(name)
            if name in self.double_candidates:
                self.double_candidates.discard(name)
                self.send(GESTURE_BUTTONS[name][0])
            self.send(long_method)

    def center_tick(self, now):
        if self.center_start is not None and not self.center_long_active and (now - self.center_start) * 1000 >= self.settings.long_press_ms:
            self.center_long_active = True; self.send("Input.CenterLongDown")

    def tick(self):
        now = self.clock(); self.click_tick(now); self.center_tick(now)
        for name in list(self.gesture_start): self.gesture_tick(name, now)
        methods = {"ARRIBA": "Up", "ABAJO": "Down", "IZQUIERDA": "Left", "DERECHA": "Right"}
        for name, due in list(self.repeat_due.items()):
            if name not in self.held or not self.settings.repeat_enabled: self.repeat_due.pop(name, None)
            elif now >= due:
                if name in VOLUME_ACTIONS:
                    self.send("Input.ExecuteAction", {"action": VOLUME_ACTIONS[name]})
                else:
                    self.send("Input." + methods[name])
                interval_ms = (self.settings.volume_repeat_interval_ms
                               if name in VOLUME_ACTIONS else self.settings.repeat_interval_ms)
                self.repeat_due[name] = now + interval_ms / 1000

    def reset(self):
        while self.held_gestures:
            name = self.held_gestures.pop()
            self.send("Input.HoldUp", {"action": GESTURE_BUTTONS[name][0]})
        if self.center_long_active:
            self.send("Input.CenterLongUp"); self.center_long_active = False
        self.repeat_due.clear(); self.gesture_start.clear(); self.gesture_long_sent.clear()
        self.released_at.clear()
        self.pending_clicks.clear(); self.double_candidates.clear()
        self.held.clear(); self.ignored.clear(); self.center_start = None; self.cancel_touch()

    def cancel_touch(self):
        self.touch_pressure_active = False
        log.debug("TOUCH contact=%d cancel origin=%s destination=%s",
                  self.touch_contact, self.origin, self.destination)
        self.origin = self.destination = None; self.touch_ready_at = None
        self.touch_axis = None; self.last_touch_step = float("-inf"); self.touch_travel.clear()

    def touch(self, event):
        action, x, y, *extra = event
        pressure = extra[0]
        if action == "INICIO": self.touch_contact += 1
        log.debug("TOUCH contact=%d event=%s x=%s y=%s pressure=%s origin=%s previous=%s axis=%s suppressed=%s travel=%s",
                  self.touch_contact, action, x, y, extra[0] if extra else None,
                  self.origin, self.destination, self.touch_axis, self.suppressed, self.touch_travel)
        if action == "INICIO":
            self.touch_pressure_active = pressure >= self.settings.touch_pressure_on
            log.debug("TOUCH contact=%d pressure_active=%s on=%d off=%d",
                      self.touch_contact, self.touch_pressure_active,
                      self.settings.touch_pressure_on, self.settings.touch_pressure_off)
            self.touch_travel.clear(); self.touch_axis = None; self.last_touch_step = float("-inf")
            self.origin = self.destination = (x, y); self.suppressed = bool(self.held or self.ignored)
            self.touch_ready_at = self.clock() + self.settings.touch_settle_ms / 1000; return
        if self.origin is None:
            log.debug("TOUCH contact=%d ignore=no_contact", self.touch_contact)
            return
        if action == "FIN" and not self.touch_pressure_active:
            self.cancel_touch()
            return
        if action == "MOVER":
            if self.touch_pressure_active and pressure < self.settings.touch_pressure_off:
                log.debug("TOUCH contact=%d pressure_deactivate pressure=%d off=%d",
                          self.touch_contact, pressure, self.settings.touch_pressure_off)
                # Finish at the last reliable position, never at the low-pressure sample.
                self.touch(("FIN", *self.destination, 0, 0, 0))
                self.touch_pressure_active = False
                self.origin = self.destination = (x, y)
                return
            if not self.touch_pressure_active:
                if pressure < self.settings.touch_pressure_on:
                    log.debug("TOUCH contact=%d wait=pressure pressure=%d on=%d",
                              self.touch_contact, pressure, self.settings.touch_pressure_on)
                    return
                self.touch_pressure_active = True
                self.origin = self.destination = (x, y)
                self.touch_axis = None; self.touch_travel.clear()
                self.last_touch_step = float("-inf")
                self.touch_ready_at = self.clock() + self.settings.touch_settle_ms / 1000
                log.debug("TOUCH contact=%d pressure_activate pressure=%d origin=%s",
                          self.touch_contact, pressure, self.origin)
                return
            moved = (x, y) != self.destination
            if abs(x - self.destination[0]) > 75 or abs(y - self.destination[1]) > 100:
                self.suppressed = True
                log.debug("TOUCH contact=%d suppress=coordinate_jump", self.touch_contact)
            self.destination = (x, y)
            if self.clock() < self.touch_ready_at:
                log.debug("TOUCH contact=%d wait=settle", self.touch_contact)
                return
            if not moved: return
        # A short contact may finish before settling; retain its net movement.
        finishing_first_step = action == "FIN" and self.last_touch_step == float("-inf")
        emit = (action == "MOVER" or finishing_first_step) if self.settings.touch_mode == "continuous" else action == "FIN"
        if emit and not self.suppressed:
            dx = (self.destination[0] - self.origin[0]) * self.settings.touch_gain_x
            dy = (self.destination[1] - self.origin[1]) * self.settings.touch_gain_y
            if self.settings.touch_invert_y: dy = -dy
            now = self.clock()
            reason = "first_step" if self.last_touch_step == float("-inf") else "forward"
            log.debug("TOUCH contact=%d evaluate dx=%.2f dy=%.2f step=%.2f interval_elapsed_ms=%.1f",
                      self.touch_contact, dx, dy, self.settings.touch_step, (now - self.last_touch_step) * 1000)
            if self.settings.touch_axis_lock and self.settings.touch_mode == "continuous":
                if self.touch_axis is None:
                    major, minor = max(abs(dx), abs(dy)), min(abs(dx), abs(dy))
                    if major < self.settings.touch_step or major < minor * 1.35:
                        log.debug("TOUCH contact=%d wait=axis_or_threshold", self.touch_contact)
                        if action == "FIN": self.cancel_touch()
                        return
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
                    log.debug("TOUCH contact=%d axis=%s sign=%d peak=%.2f retreat=%.2f reverse_threshold=%.2f",
                              self.touch_contact, axis, sign, peak, retreat,
                              self.settings.touch_step + self.settings.touch_reverse_margin)
                    if retreat >= self.settings.touch_step + self.settings.touch_reverse_margin:
                        reason = "reversal"
                        dx, dy = ((-sign * retreat, 0) if axis == "x" else (0, -sign * retreat))
                    elif (dx if axis == "x" else dy) * sign <= 0:
                        log.debug("TOUCH contact=%d wait=reverse_margin", self.touch_contact)
                        return
            if max(abs(dx), abs(dy)) >= self.settings.touch_step and (now - self.last_touch_step) * 1000 >= self.settings.touch_interval_ms:
                direction = ("Right" if dx > 0 else "Left") if abs(dx) >= abs(dy) else ("Up" if dy > 0 else "Down")
                log.debug("TOUCH contact=%d emit=%s reason=%s event=%s dx=%.2f dy=%.2f",
                          self.touch_contact, direction, reason, action, dx, dy)
                self.send("Input." + direction); self.last_touch_step = now
                if self.settings.touch_mode == "continuous":
                    delta = dx if axis == "x" else dy
                    self.touch_travel[axis] = (1 if delta > 0 else -1, positions[axis])
                self.origin = self.destination
            else:
                log.debug("TOUCH contact=%d wait=step_or_interval", self.touch_contact)
        if action == "FIN":
            self.touch_pressure_active = False
            self.origin = self.destination = None; self.touch_ready_at = None
            self.touch_axis = None; self.touch_travel.clear()
