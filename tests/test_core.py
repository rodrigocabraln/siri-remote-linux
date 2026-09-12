import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from siri_remote_linux.config import Settings, load_settings, parse_settings, save_identity
from siri_remote_linux.events import ButtonAction, ButtonEvent, TouchAction, TouchEvent
from siri_remote_linux.input.keyboard import Keyboard
from siri_remote_linux.navigation import Navigation
from siri_remote_linux.remotes.base import RemoteProfile
from siri_remote_linux.remotes.a2540 import (A2540Profile, HID, REPORT, TouchDecoder,
    a2540_layout, validate_report_reference, value_handle)
from siri_remote_linux.service import service_unit
from siri_remote_linux.supervisor import Backoff, ErrorGrouper, Supervisor
from siri_remote_linux.cli import choose, doctor_command, forget_command, setup_command


class FakeDevice:
    def __init__(self, *args, **kwargs): self.events = []; self.closed = False
    def write(self, *event): self.events.append(event)
    def syn(self): self.events.append("syn")
    def close(self): self.closed = True


class ConfigurationTests(unittest.TestCase):
    def test_functional_defaults(self):
        value = Settings()
        self.assertEqual((value.long_press_ms, value.touch_step,
                          value.touch_interval_ms, value.touch_settle_ms), (300, 18, 90, 50))
        self.assertEqual((value.key_center, value.key_center_long, value.key_tv_long, value.key_siri),
                         ("KEY_ENTER", "KEY_ENTER", "KEY_LEFTMETA+KEY_D", "KEY_F12"))
        self.assertEqual((value.key_back_long, value.key_siri_long), ("NONE",) * 2)

    def test_types_and_comments(self):
        value = parse_settings("TOUCH_STEP=6 # x\nTOUCH_INVERT_Y=true\nBUTTON_DEBOUNCE_MS=0")
        self.assertEqual(value.touch_step, 6); self.assertTrue(value.touch_invert_y)

    def test_pressure_settings_validation(self):
        value = parse_settings("TOUCH_PRESSURE_ON=12\nTOUCH_PRESSURE_OFF=5")
        self.assertEqual((value.touch_pressure_on, value.touch_pressure_off), (12, 5))
        for text in ("TOUCH_PRESSURE_ON=0", "TOUCH_PRESSURE_OFF=256",
                     "TOUCH_PRESSURE_ON=4\nTOUCH_PRESSURE_OFF=5"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_settings(text)

    def test_invalid_numbers(self):
        for text in ("TOUCH_STEP=nan", "TOUCH_STEP=0", "LONG_PRESS_MS=99",
                     "REPEAT_INTERVAL_MS=2", "VOLUME_REPEAT_INTERVAL_MS=2"):
            with self.subTest(text=text), self.assertRaises(ValueError): parse_settings(text)

    def test_volume_repeat_interval_allows_five_ms(self):
        self.assertEqual(parse_settings("VOLUME_REPEAT_INTERVAL_MS=5").volume_repeat_interval_ms, 5)

    def test_invalid_enum_boolean_unknown_and_duplicate(self):
        for text in ("TOUCH_MODE=fast", "TOUCH_INVERT_Y=maybe", "TYPO=1", "TOUCH_STEP=5\nTOUCH_STEP=6"):
            with self.subTest(text=text), self.assertRaises(ValueError): parse_settings(text)

    def test_identity_and_adapter_validation(self):
        value = parse_settings("REMOTE_IDENTITY=aa:bb:cc:dd:ee:ff\nADAPTER=hci2")
        self.assertEqual(value.remote_identity, "AA:BB:CC:DD:EE:FF")
        for text in ("REMOTE_IDENTITY=random", "ADAPTER=bluetooth0"):
            with self.assertRaises(ValueError): parse_settings(text)

    def test_key_and_chord_validation(self):
        value = parse_settings("KEY_CENTER=KEY_SPACE\nKEY_TV_LONG=KEY_LEFTCTRL+KEY_C\n"
                               "KEY_BACK_LONG=KEY_LEFTALT+KEY_B\nKEY_SIRI=NONE")
        self.assertEqual(value.key_tv_long, "KEY_LEFTCTRL+KEY_C")
        self.assertEqual(value.key_back_long, "KEY_LEFTALT+KEY_B")
        for text in ("KEY_BACK=NOPE", "KEY_BACK=KEY_LEFTCTRL+KEY_C",
                     "KEY_SIRI=KEY_F1+KEY_F1", "KEY_SIRI_LONG=KEY_F1+KEY_F1"):
            with self.assertRaises(ValueError): parse_settings(text)

    def test_missing_config_has_repair(self):
        with self.assertRaisesRegex(ValueError, "setup"): load_settings("/definitely/missing")

    def test_optional_double_click_config(self):
        self.assertEqual((Settings().key_back_double, Settings().key_tv_double,
                          Settings().key_siri_double), ("NONE",) * 3)
        settings = parse_settings("DOUBLE_CLICK_MS=60\nKEY_BACK_DOUBLE=KEY_ESC")
        self.assertEqual(settings.double_click_ms, 60)
        with self.assertRaisesRegex(ValueError, "BUTTON_DEBOUNCE_MS"):
            parse_settings("DOUBLE_CLICK_MS=50\nKEY_TV_DOUBLE=KEY_F1")

    def test_identity_persistence_preserves_config(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.env"; path.write_text("TOUCH_STEP=9\nREMOTE_IDENTITY=\n")
            save_identity(path, "aa:bb:cc:dd:ee:ff")
            self.assertEqual(load_settings(path).remote_identity, "AA:BB:CC:DD:EE:FF")
            self.assertEqual(load_settings(path).touch_step, 9)


class ProfileTests(unittest.TestCase):
    @staticmethod
    def packet(x=230, zone=0, y=188, pressure=20):
        return bytes([0, 0, 0, 0, x, zone, y, 1, 1, pressure, 0])

    def test_contract(self): self.assertTrue(issubclass(A2540Profile, RemoteProfile))

    def test_advertisement_detection(self):
        props = {"ManufacturerData": {0x004C: b"x"}, "UUIDs": [HID], "Appearance": 0x03C0, "Address": "AA:BB:CC:DD:EE:FF"}
        self.assertTrue(A2540Profile.matches(props)); self.assertEqual(A2540Profile.stable_identity(props), props["Address"])
        self.assertFalse(A2540Profile.matches({**props, "Class": 1}))

    def test_button_combination_and_partial_release(self):
        profile = A2540Profile(); profile.button_path = "/b"
        self.assertEqual(profile.decode("/b", b"\x40\x00"), [ButtonEvent(ButtonAction.PRESSED, "ATRAS")])
        self.assertEqual(profile.decode("/b", b"\x42\x00"), [ButtonEvent(ButtonAction.PRESSED, "VOLUMEN_ARRIBA")])
        self.assertEqual(profile.decode("/b", b"\x02\x00"), [ButtonEvent(ButtonAction.RELEASED, "ATRAS")])

    def test_duplicate_and_bad_button_report(self):
        profile = A2540Profile(); profile.button_path = "/b"; profile.decode("/b", b"\x00\x02")
        self.assertEqual(profile.decode("/b", b"\x00\x02"), [])
        with self.assertRaises(ValueError): profile.decode("/b", b"\0")

    def test_touch_contact_motion_release(self):
        decoder = TouchDecoder()
        self.assertIsNone(decoder.update(self.packet(pressure=0)))
        self.assertEqual(decoder.update(self.packet()).action, TouchAction.START)
        move = decoder.update(self.packet(x=245, y=190)); self.assertEqual((move.x, move.y, move.dx, move.dy), (1, 2, 1, 2))
        self.assertEqual(decoder.update(self.packet(pressure=0)).action, TouchAction.END)

    def test_touch_wrap_and_zone(self):
        event = TouchDecoder().update(self.packet(x=0, y=0)); self.assertEqual((event.x, event.y), (135, 67))
        self.assertEqual(TouchDecoder().update(self.packet(zone=0x81)).x, 17)

    def test_eighteen_byte_report_breaks_only_touch(self):
        profile = A2540Profile(); profile.button_path = "/b"; profile.touch_path = "/t"
        profile.touch.update(self.packet()); profile.previous = 0x200
        with self.assertRaises(ValueError): profile.decode("/t", bytes(18))
        self.assertIsNone(profile.touch.last); self.assertEqual(profile.previous, 0x200)

    def test_reset_releases_decoder_state(self):
        profile = A2540Profile(); profile.previous = 3; profile.touch.last = (1, 2, 3); profile.reset()
        self.assertEqual(profile.previous, 0); self.assertIsNone(profile.touch.last)

    def test_value_handle(self):
        self.assertEqual(value_handle("/char0038", {"Handle": 0x38}), 0x39)
        self.assertEqual(value_handle("/char0038", {}), 0x39)
        with self.assertRaises(ValueError): value_handle("/bad", {})

    def test_layout_normal_shifted_and_wrong(self):
        chars = self.layout()
        self.assertEqual(a2540_layout(chars), (0x39, 0x4D, "request"))
        self.assertEqual(a2540_layout({key + 1: value for key, value in chars.items()}), (0x3A, 0x4E, "request"))
        with self.assertRaises(RuntimeError): a2540_layout({0x39: chars[0x39]})

    def test_report_reference(self):
        self.assertEqual(validate_report_reference(b"\x01\x01", (1,)), (1, 1))
        with self.assertRaises(RuntimeError): validate_report_reference(b"\x01\x01", (2, 3))

    def test_configure_checks_hid_and_report_references(self):
        chars = self.layout(); objects = {}
        references = {}
        for handle, (path, props) in chars.items():
            props["Service"] = "/hid"; objects.setdefault("/hid", {})["org.bluez.GattService1"] = {"UUID": HID}
            if handle in (0x39, 0x3D, 0x4D):
                descriptor = path + "/desc"; objects[descriptor] = {"org.bluez.GattDescriptor1": {
                    "Characteristic": path, "UUID": "00002908-0000-1000-8000-00805f9b34fb"}}
                references[descriptor] = b"\x01\x01" if handle != 0x4D else b"\xf0\x03"
        profile = A2540Profile(); config = profile.configure(chars, objects, references.__getitem__)
        self.assertEqual((config.button_path, config.touch_path, config.activation), ("/c57", "/c61", b"\xf0\x00"))

    @staticmethod
    def layout():
        uuids = {0x2E: "00002a19-0000-1000-8000-00805f9b34fb", 0x31: "00002a1a-0000-1000-8000-00805f9b34fb",
                 0x35: REPORT, 0x39: REPORT, 0x3D: REPORT, 0x4D: REPORT}
        return {h: (f"/c{h}", {"UUID": u, "Flags": (["notify"] if h in (0x39, 0x3D) else ["write"] if h == 0x4D else [])}) for h, u in uuids.items()}


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.now = 0; self.sent = []; self.nav = Navigation(lambda *x: self.sent.append(x), Settings(touch_step=8, touch_interval_ms=120, touch_settle_ms=0), lambda: self.now)
    def button(self, action, name): self.nav.button(action, name)
    def touch(self, action, x, y=0): self.nav.touch((action, x, y, 20, 0, 0))

    def test_center_is_held_when_bindings_equal(self):
        self.button("PRESIONADO", "CENTRO"); self.button("SOLTADO", "CENTRO")
        self.assertEqual(self.sent, [("Input.CenterLongDown",), ("Input.CenterLongUp",)])

    def test_center_without_long_action_is_immediate_and_never_duplicated(self):
        self.nav.settings = parse_settings("KEY_CENTER_LONG=NONE")
        self.button("PRESIONADO", "CENTRO")
        self.assertEqual(self.sent, [("Input.Select",)])
        self.now = 1; self.nav.tick(); self.button("SOLTADO", "CENTRO")
        self.nav.reset()
        self.assertEqual(self.sent, [("Input.Select",)])

    def test_distinct_center_short_and_long(self):
        self.nav.settings = Settings(key_center_long="KEY_MENU", button_debounce_ms=0)
        self.button("PRESIONADO", "CENTRO"); self.now = .2; self.button("SOLTADO", "CENTRO")
        self.button("PRESIONADO", "CENTRO"); self.now = 1; self.nav.tick(); self.button("SOLTADO", "CENTRO")
        self.assertEqual(self.sent, [("Input.Select",), ("Input.CenterLongDown",), ("Input.CenterLongUp",)])

    def test_tv_short_long_and_siri(self):
        self.button("PRESIONADO", "TV"); self.now = .2; self.button("SOLTADO", "TV")
        self.now = 1; self.button("PRESIONADO", "TV"); self.now = 2; self.nav.tick(); self.button("SOLTADO", "TV")
        self.button("PRESIONADO", "SIRI"); self.button("PRESIONADO", "SIRI")
        self.assertEqual(self.sent, [("Input.Home",), ("Input.TVLong",), ("Input.Siri",)])

    def test_short_release_is_immediate_and_long_is_exclusive(self):
        cases = (
            ("ATRAS", "key_back_long", "Input.Back", "Input.BackLong"),
            ("TV", "key_tv_long", "Input.Home", "Input.TVLong"),
            ("SIRI", "key_siri_long", "Input.Siri", "Input.SiriLong"),
        )
        for name, long_field, short_method, long_method in cases:
            with self.subTest(name=name):
                self.now = 0; self.sent = []
                settings = Settings(button_debounce_ms=50, **{long_field: "KEY_F1"})
                self.nav = Navigation(lambda *x: self.sent.append(x), settings, lambda: self.now)

                self.button("PRESIONADO", name)
                self.assertEqual(self.sent, [])
                self.now = .05; self.button("SOLTADO", name)
                self.assertEqual(self.sent, [(short_method,)])
                self.now = .31; self.nav.tick()

                self.now = 1; self.button("PRESIONADO", name); self.now = 1.31; self.nav.tick()
                self.now = 1.35; self.button("SOLTADO", name)

                self.now = 2; self.button("PRESIONADO", name); self.now = 2.05; self.button("SOLTADO", name)
                self.now = 2.29; self.button("PRESIONADO", name); self.now = 2.31; self.nav.tick()
                self.now = 2.34; self.button("SOLTADO", name)

                self.assertEqual(self.sent, [(short_method,), (long_method,), (short_method,), (short_method,)])

    def test_no_long_binding_sends_on_press_without_duplicates(self):
        for name, field, method in (("ATRAS", "key_back_long", "Input.Back"),
                                    ("TV", "key_tv_long", "Input.Home"),
                                    ("SIRI", "key_siri_long", "Input.Siri")):
            with self.subTest(name=name):
                self.now = 0; self.sent = []
                self.nav = Navigation(lambda *x: self.sent.append(x), Settings(**{field: "NONE"}), lambda: self.now)
                self.button("PRESIONADO", name)
                self.assertEqual(self.sent, [(method,)])
                self.now = 1; self.nav.tick(); self.button("SOLTADO", name)
                self.assertEqual(self.sent, [(method,)])

    def test_long_second_press_preserves_first_short_click(self):
        self.nav.settings = Settings(key_siri_long="KEY_F1", button_debounce_ms=50)
        self.button("PRESIONADO", "SIRI"); self.now = .05; self.button("SOLTADO", "SIRI")
        self.now = .15; self.button("PRESIONADO", "SIRI"); self.now = .46; self.nav.tick()
        self.now = .5; self.button("SOLTADO", "SIRI")
        self.assertEqual(self.sent, [("Input.Siri",), ("Input.SiriLong",)])

    def test_debounce_filters_bounce_without_losing_second_click(self):
        self.nav.settings = Settings(button_debounce_ms=50)
        self.button("PRESIONADO", "ATRAS"); self.now = .02; self.button("SOLTADO", "ATRAS")
        self.now = .04; self.button("PRESIONADO", "ATRAS"); self.now = .05; self.button("SOLTADO", "ATRAS")
        self.now = .10; self.button("PRESIONADO", "ATRAS"); self.now = .12; self.button("SOLTADO", "ATRAS")
        self.assertEqual(self.sent, [("Input.Back",), ("Input.Back",)])

    def test_button_bounce_does_not_block_other(self):
        self.button("PRESIONADO", "ATRAS"); self.button("SOLTADO", "ATRAS"); self.now = .01
        self.button("PRESIONADO", "ATRAS"); self.button("PRESIONADO", "DERECHA"); self.button("SOLTADO", "ATRAS")
        self.assertEqual(self.sent, [("Input.Back",), ("Input.Right",)])

    def test_optional_double_click_wait_and_exclusivity(self):
        for name, field, short, double in (
                ("ATRAS", "key_back_double", "Input.Back", "Input.BackDouble"),
                ("TV", "key_tv_double", "Input.Home", "Input.TVDouble"),
                ("SIRI", "key_siri_double", "Input.Siri", "Input.SiriDouble")):
            with self.subTest(name=name):
                self.now = 0; self.sent = []
                self.nav = Navigation(lambda *x: self.sent.append(x),
                    Settings(double_click_ms=150, **{field: "KEY_ESC"}), lambda: self.now)
                self.button("PRESIONADO", name); self.now = .05; self.button("SOLTADO", name)
                self.now = .19; self.nav.tick(); self.assertEqual(self.sent, [])
                self.now = .21; self.nav.tick(); self.assertEqual(self.sent, [(short,)])
                self.now = 1; self.button("PRESIONADO", name)
                self.now = 1.02; self.button("SOLTADO", name)
                self.now = 1.1; self.button("PRESIONADO", name)
                self.now = 1.12; self.button("SOLTADO", name)
                self.now = 2; self.nav.tick()
                self.assertEqual(self.sent, [(short,), (double,)])

    def test_double_click_is_per_button_and_reset_cancels_pending(self):
        self.nav.settings = Settings(key_tv_double="KEY_ESC")
        self.button("PRESIONADO", "TV"); self.button("SOLTADO", "TV")
        self.button("PRESIONADO", "ATRAS")
        self.assertEqual(self.sent, [("Input.Back",)])
        self.nav.reset(); self.now = 1; self.nav.tick()
        self.assertEqual(self.sent, [("Input.Back",)])

    def test_double_candidate_held_long_preserves_first_click(self):
        self.nav.settings = Settings(key_siri_long="KEY_F1", key_siri_double="KEY_F2")
        self.button("PRESIONADO", "SIRI"); self.now = .05; self.button("SOLTADO", "SIRI")
        self.now = .15; self.button("PRESIONADO", "SIRI")
        self.now = .46; self.nav.tick(); self.button("SOLTADO", "SIRI")
        self.now = 1; self.nav.tick()
        self.assertEqual(self.sent, [("Input.Siri",), ("Input.SiriLong",)])

    def test_repeat_and_no_catchup(self):
        self.button("PRESIONADO", "DERECHA"); self.now = .46; self.nav.tick(); self.now = 5; self.nav.tick()
        self.button("SOLTADO", "DERECHA"); self.now = 6; self.nav.tick()
        self.assertEqual(self.sent, [("Input.Right",), ("Input.Right",), ("Input.Right",)])

    def test_volume_repeat_timing_and_cancellation(self):
        for name, action in (("VOLUMEN_ARRIBA", "volumeup"), ("VOLUMEN_ABAJO", "volumedown")):
            for stop in ("release", "reset", "disable"):
                with self.subTest(name=name, stop=stop):
                    self.now = 0; self.sent = []
                    self.nav = Navigation(lambda *x: self.sent.append(x),
                        Settings(repeat_delay_ms=500, volume_repeat_interval_ms=250), lambda: self.now)
                    expected = ("Input.ExecuteAction", {"action": action})
                    self.button("PRESIONADO", name)
                    self.now = .49; self.nav.tick()
                    self.assertEqual(self.sent, [expected])
                    self.now = .5; self.nav.tick()
                    self.assertEqual(self.sent, [expected] * 2)
                    self.now = .74; self.nav.tick()
                    self.assertEqual(self.sent, [expected] * 2)
                    self.now = .75; self.nav.tick()
                    self.now = 5; self.nav.tick()
                    self.assertEqual(self.sent, [expected] * 4)
                    if stop == "release": self.button("SOLTADO", name)
                    elif stop == "reset": self.nav.reset()
                    else: self.nav.settings = Settings(repeat_enabled=False)
                    self.now = 6; self.nav.tick()
                    self.assertEqual(self.sent, [expected] * 4)
                    self.assertFalse(self.nav.repeat_due)

    def test_volume_repeat_disabled(self):
        self.nav.settings = Settings(repeat_enabled=False)
        self.button("PRESIONADO", "VOLUMEN_ARRIBA")
        self.button("PRESIONADO", "VOLUMEN_ABAJO")
        self.now = 5; self.nav.tick()
        self.assertEqual(self.sent, [("Input.ExecuteAction", {"action": "volumeup"}),
                                     ("Input.ExecuteAction", {"action": "volumedown"})])

    def test_volume_repeat_interval_is_independent(self):
        self.nav.settings = Settings(repeat_delay_ms=100, repeat_interval_ms=100,
                                     volume_repeat_interval_ms=300)
        self.button("PRESIONADO", "DERECHA")
        self.button("PRESIONADO", "VOLUMEN_ARRIBA")
        self.now = .11; self.nav.tick()
        self.now = .22; self.nav.tick()
        self.now = .33; self.nav.tick()
        self.assertEqual(self.sent, [("Input.Right",),
                                     ("Input.ExecuteAction", {"action": "volumeup"}),
                                     ("Input.Right",),
                                     ("Input.ExecuteAction", {"action": "volumeup"}),
                                     ("Input.Right",), ("Input.Right",)])

    def test_continuous_threshold_interval(self):
        self.touch("INICIO", 0); self.touch("MOVER", 9); self.now = .05; self.touch("MOVER", 20)
        self.now = .2; self.touch("MOVER", 21); self.touch("FIN", 21)
        self.assertEqual(self.sent, [("Input.Right",), ("Input.Right",)])

    def test_click_suppresses_touch(self):
        self.touch("INICIO", 0); self.button("PRESIONADO", "DERECHA"); self.button("SOLTADO", "DERECHA")
        self.touch("MOVER", 30); self.touch("FIN", 30); self.assertEqual(self.sent, [("Input.Right",)])

    def test_release_mode_gain_and_invert(self):
        self.nav.settings = Settings(touch_mode="release", touch_step=8, touch_gain_y=2, touch_invert_y=True, touch_settle_ms=0)
        self.touch("INICIO", 0); self.touch("MOVER", 0, 5); self.touch("FIN", 0, 5)
        self.assertEqual(self.sent, [("Input.Down",)])

    def test_wrap_is_discarded(self):
        self.touch("INICIO", 140); self.touch("MOVER", 1); self.touch("FIN", 1); self.assertEqual(self.sent, [])

    def test_axis_lock_and_reversal(self):
        self.nav.settings = Settings(touch_step=10, touch_interval_ms=0, touch_settle_ms=0)
        self.touch("INICIO", 0); self.touch("MOVER", 11, 10); self.touch("MOVER", 11, 20); self.touch("MOVER", 35, 22); self.touch("MOVER", 35, 9)
        self.assertEqual(self.sent, [("Input.Up",)])
        self.touch("MOVER", 35, 8)
        self.assertEqual(self.sent, [("Input.Up",), ("Input.Down",)])

    def test_pressure_only_no_pending_motion(self):
        self.touch("INICIO", 0); self.touch("MOVER", 9); self.now = .05; self.touch("MOVER", 20); self.now = .2
        self.nav.touch(("MOVER", 20, 0, 30, 0, 0)); self.nav.tick(); self.touch("FIN", 20)
        self.assertEqual(self.sent, [("Input.Right",)])

    def test_new_contact_resets_interval(self):
        self.touch("INICIO", 0); self.touch("MOVER", 18); self.touch("FIN", 18); self.now = .01
        self.touch("INICIO", 0); self.touch("MOVER", 18); self.assertEqual(len(self.sent), 2)

    def test_settle_preserves_origin(self):
        self.nav.settings = Settings(touch_settle_ms=160, touch_interval_ms=0, touch_step=10)
        self.touch("INICIO", 100); self.now=.05; self.touch("MOVER", 60); self.now=.15; self.touch("MOVER", 40)
        self.now=.17; self.touch("MOVER", 31); self.touch("MOVER", 20); self.assertEqual(self.sent, [("Input.Left",), ("Input.Left",)])

    def test_short_contact_emits_net_movement(self):
        self.nav.settings = Settings()
        self.touch("INICIO", 50); self.now = .02; self.touch("MOVER", 70)
        self.now = .04; self.touch("FIN", 70)
        self.assertEqual(self.sent, [("Input.Right",)])
        self.assertIsNone(self.nav.origin)

    def test_settle_partial_return_does_not_reverse_direction(self):
        self.nav.settings = Settings()
        self.touch("INICIO", 50); self.now = .02; self.touch("MOVER", 75)
        self.now = .06; self.touch("MOVER", 55); self.touch("FIN", 55)
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.nav.origin)

    def test_small_retreat_does_not_block_forward_step(self):
        self.nav.settings = Settings()
        self.touch("INICIO", 30); self.now = .06; self.touch("MOVER", 50)
        self.now = .09; self.touch("MOVER", 75)
        self.now = .16; self.touch("MOVER", 74)
        self.assertEqual(self.sent, [("Input.Right",), ("Input.Right",)])

    def test_reversal_requires_step_plus_margin(self):
        self.nav.settings = Settings(touch_step=10, touch_reverse_margin=4,
                                     touch_settle_ms=0, touch_interval_ms=0)
        for sign in (-1, 1):
            for vertical in (False, True):
                self.nav.reset(); self.sent.clear()
                def move(action, value):
                    self.touch(action, 0 if vertical else value * sign,
                               value * sign if vertical else 0)
                move("INICIO", 0); move("MOVER", 20); move("MOVER", 9)
                self.assertEqual(len(self.sent), 1)
                move("MOVER", 6)
                forward, reverse = (("Up", "Down") if vertical else ("Right", "Left"))
                if sign < 0: forward, reverse = reverse, forward
                self.assertEqual(self.sent, [("Input." + forward,), ("Input." + reverse,)])

    def test_short_diagonal_contact_cleans_up(self):
        self.nav.settings = Settings()
        self.touch("INICIO", 0); self.now = .02; self.touch("MOVER", 20, 20)
        self.touch("FIN", 20, 20)
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.nav.origin)

    def test_captured_low_pressure_return_only_emits_up(self):
        self.nav.settings = Settings()
        samples = [
            (0, "INICIO", 99, 51, 4), (.012, "MOVER", 96, 46, 3),
            (.024, "MOVER", 93, 40, 3), (.096, "MOVER", 87, 25, 2),
            (.117, "MOVER", 88, 24, 3), (.128, "MOVER", 89, 23, 5),
            (.149, "MOVER", 89, 23, 13), (.160, "MOVER", 89, 24, 16),
            (.174, "MOVER", 89, 25, 19), (.186, "MOVER", 90, 27, 20),
            (.209, "MOVER", 90, 29, 22), (.219, "MOVER", 90, 32, 25),
            (.240, "MOVER", 91, 35, 26), (.251, "MOVER", 92, 38, 27),
            (.262, "MOVER", 92, 41, 27), (.287, "MOVER", 94, 47, 26),
            (.299, "MOVER", 95, 52, 22), (.310, "MOVER", 97, 56, 15),
            (.331, "MOVER", 99, 58, 7), (.342, "MOVER", 100, 60, 3),
            (.353, "MOVER", 102, 61, 1), (.375, "FIN", 102, 61, 0)]
        for t, action, x, y, pressure in samples:
            self.now = t
            self.nav.touch((action, x, y, pressure, 0, 0))
        self.assertEqual(self.sent, [("Input.Up",)])
        self.assertIsNone(self.nav.origin)

    def test_pressure_hysteresis_and_reactivation(self):
        self.nav.settings = Settings(touch_settle_ms=0, touch_interval_ms=0)
        for action, y, pressure in [("INICIO", 0, 10), ("MOVER", 18, 4),
                                    ("MOVER", -20, 3), ("MOVER", -40, 9),
                                    ("MOVER", -40, 10), ("MOVER", -22, 5)]:
            self.nav.touch((action, 0, y, pressure, 0, 0))
        self.assertEqual(self.sent, [("Input.Up",), ("Input.Up",)])

    def test_low_pressure_contact_never_emits_on_release(self):
        for action, y, pressure in [("INICIO", 50, 4), ("MOVER", 0, 3), ("FIN", 0, 0)]:
            self.nav.touch((action, 0, y, pressure, 0, 0))
        self.assertEqual(self.sent, [])
        self.assertIsNone(self.nav.origin)

    def test_release_mode_uses_last_reliable_position(self):
        self.nav.settings = Settings(touch_mode="release", touch_settle_ms=0)
        for action, y, pressure in [("INICIO", 0, 12), ("MOVER", 20, 10),
                                    ("MOVER", -30, 2), ("FIN", -30, 0)]:
            self.nav.touch((action, 0, y, pressure, 0, 0))
        self.assertEqual(self.sent, [("Input.Up",)])

    def test_reset_cancels_held_touch_and_repeat(self):
        self.button("PRESIONADO", "ARRIBA"); self.touch("INICIO", 0); self.nav.reset(); self.now=10; self.nav.tick()
        self.assertEqual(self.sent, [("Input.Up",)]); self.assertIsNone(self.nav.origin); self.assertFalse(self.nav.held)


class OutputTests(unittest.TestCase):
    def test_matching_gesture_bindings_hold_until_release(self):
        from evdev import ecodes as e
        for name, field in (("ATRAS", "key_back"), ("TV", "key_tv"), ("SIRI", "key_siri")):
            with self.subTest(name=name):
                keyboard = Keyboard(Settings(**{field: "KEY_ENTER", field + "_long": "KEY_ENTER"}), ui_factory=FakeDevice)
                now = [0]
                keyboard.navigation.clock = lambda: now[0]
                keyboard.navigation.button("PRESIONADO", name)
                self.assertEqual(keyboard.ui.events, [(e.EV_KEY, e.KEY_ENTER, 1), "syn"])
                now[0] = 2; keyboard.navigation.tick()
                keyboard.navigation.button("PRESIONADO", name)
                self.assertEqual(len(keyboard.ui.events), 2)
                keyboard.navigation.button("SOLTADO", name)
                self.assertEqual(keyboard.ui.events[-2:], [(e.EV_KEY, e.KEY_ENTER, 0), "syn"])
                self.assertEqual(len(keyboard.ui.events), 4)
                keyboard.close()

    def test_double_click_prevents_matching_binding_hold(self):
        for name, field in (("ATRAS", "key_back"), ("TV", "key_tv"), ("SIRI", "key_siri")):
            with self.subTest(name=name):
                sent = []; now = [0]
                nav = Navigation(lambda *args: sent.append(args), Settings(**{
                    field: "KEY_ENTER", field + "_long": "KEY_ENTER", field + "_double": "KEY_ESC"}), lambda: now[0])
                nav.button("PRESIONADO", name)
                self.assertEqual(sent, [])
                now[0] = .05; nav.button("SOLTADO", name)
                self.assertEqual(sent, [])
                now[0] = .4; nav.tick()
                self.assertEqual(len(sent), 1)
                self.assertNotIn(sent[0][0], ("Input.HoldDown", "Input.HoldUp"))

    def test_shared_held_key_is_released_only_by_last_button(self):
        from evdev import ecodes as e
        keyboard = Keyboard(Settings(key_back="KEY_ENTER", key_back_long="KEY_ENTER"), ui_factory=FakeDevice)
        nav = keyboard.navigation
        nav.button("PRESIONADO", "CENTRO"); nav.button("PRESIONADO", "ATRAS")
        nav.button("SOLTADO", "CENTRO")
        self.assertEqual(keyboard.ui.events, [(e.EV_KEY, e.KEY_ENTER, 1), "syn"])
        keyboard.release_all()
        self.assertEqual(keyboard.ui.events[-2:], [(e.EV_KEY, e.KEY_ENTER, 0), "syn"])
        self.assertFalse(keyboard.held_codes)
        self.assertFalse(nav.held_gestures)
        keyboard.close()

    def test_held_siri_chord_released_on_close(self):
        from evdev import ecodes as e
        keyboard = Keyboard(Settings(key_siri="KEY_LEFTCTRL+KEY_B", key_siri_long="KEY_LEFTCTRL+KEY_B"), ui_factory=FakeDevice)
        keyboard.navigation.button("PRESIONADO", "SIRI")
        ui = keyboard.ui
        keyboard.close()
        self.assertEqual(ui.events, [
            (e.EV_KEY, e.KEY_LEFTCTRL, 1), "syn", (e.EV_KEY, e.KEY_B, 1), "syn",
            (e.EV_KEY, e.KEY_B, 0), "syn", (e.EV_KEY, e.KEY_LEFTCTRL, 0), "syn"])

    def test_all_optional_actions_can_be_disabled(self):
        from evdev import ecodes as e
        settings = parse_settings("\n".join(f"{key}=NONE" for key in (
            "KEY_CENTER_LONG", "KEY_BACK_LONG", "KEY_TV_LONG", "KEY_SIRI_LONG",
            "KEY_BACK_DOUBLE", "KEY_TV_DOUBLE", "KEY_SIRI_DOUBLE")))
        keyboard = Keyboard(settings, ui_factory=FakeDevice)
        keyboard.navigation.button("PRESIONADO", "CENTRO")
        keyboard.navigation.button("SOLTADO", "CENTRO")
        self.assertEqual(keyboard.ui.events, [
            (e.EV_KEY, e.KEY_ENTER, 1), "syn", (e.EV_KEY, e.KEY_ENTER, 0), "syn"])
        keyboard.close()

    def test_chord_order_and_capabilities(self):
        from evdev import ecodes as e
        captured = []
        def factory(caps, **kwargs): captured.append(caps); return FakeDevice()
        keyboard = Keyboard(Settings(), ui_factory=factory); keyboard.send("Input.TVLong")
        self.assertTrue({e.KEY_SPACE, e.KEY_A, e.KEY_Z, e.KEY_F12}.issubset(captured[0][e.EV_KEY]))
        self.assertEqual(keyboard.ui.events, [(e.EV_KEY,e.KEY_LEFTMETA,1),"syn",(e.EV_KEY,e.KEY_D,1),"syn",(e.EV_KEY,e.KEY_D,0),"syn",(e.EV_KEY,e.KEY_LEFTMETA,0),"syn"])

    def test_gesture_binding_emits_configured_chord(self):
        from evdev import ecodes as e
        keyboard = Keyboard(Settings(key_back_long="KEY_LEFTCTRL+KEY_B"), ui_factory=FakeDevice)
        keyboard.send("Input.BackLong")
        self.assertEqual(keyboard.ui.events, [
            (e.EV_KEY, e.KEY_LEFTCTRL, 1), "syn", (e.EV_KEY, e.KEY_B, 1), "syn",
            (e.EV_KEY, e.KEY_B, 0), "syn", (e.EV_KEY, e.KEY_LEFTCTRL, 0), "syn",
        ])

    def test_tap_and_close(self):
        from evdev import ecodes as e
        keyboard = Keyboard(Settings(), ui_factory=FakeDevice); keyboard.send("Input.Right"); ui = keyboard.ui; keyboard.close()
        self.assertEqual(ui.events[:4], [(e.EV_KEY,e.KEY_RIGHT,1),"syn",(e.EV_KEY,e.KEY_RIGHT,0),"syn"]); self.assertTrue(ui.closed)

    def test_held_collision_and_disconnect_release(self):
        from evdev import ecodes as e
        keyboard = Keyboard(Settings(key_back="KEY_ENTER"), ui_factory=FakeDevice); keyboard.send("Input.CenterLongDown")
        before = list(keyboard.ui.events); keyboard.send("Input.Back"); self.assertEqual(before, keyboard.ui.events)
        keyboard.release_all(); self.assertEqual(keyboard.ui.events[-2:], [(e.EV_KEY,e.KEY_ENTER,0),"syn"])

    def test_dry_run_and_none(self):
        keyboard = Keyboard(Settings(key_siri="NONE"), dry_run=True); keyboard.send("Input.Siri")
        self.assertIsNone(keyboard.ui); keyboard.close()


class SupervisorAndCliTests(unittest.TestCase):
    def test_backoff_is_bounded_and_resettable(self):
        backoff = Backoff(); self.assertEqual([backoff.next() for _ in range(6)], [1,2,5,10,10,10]); backoff.reset(); self.assertEqual(backoff.next(), 1)

    def test_service_unit_escapes_spaces_and_percent(self):
        unit = service_unit("/tmp/a b/siri%remote", "/tmp/a b/config.env")
        self.assertIn("WorkingDirectory=/tmp/a b", unit)
        self.assertNotIn('WorkingDirectory="', unit)
        self.assertIn('ExecStart="/tmp/a b/siri%%remote" run --config "/tmp/a b/config.env"', unit)

    def test_choose_multiple_and_invalid(self):
        props = {"Address":"AA:BB:CC:DD:EE:FF"}; items = [("/1",props,A2540Profile),("/2",{**props,"Address":"11:22:33:44:55:66"},A2540Profile)]
        self.assertEqual(choose(items, lambda _: "2", lambda _: None)[0], "/2")
        with self.assertRaises(RuntimeError): choose(items, lambda _: "9", lambda _: None)

    def test_forget_requires_confirmation_and_targets_one(self):
        class Backend:
            removed=[]
            def __init__(self, adapter): pass
            def resolve(self, identity): return "/only", {}, A2540Profile
            def remove(self, path): self.removed.append(path)
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory:
            config=Path(directory)/"c"; config.write_text("REMOTE_IDENTITY=AA:BB:CC:DD:EE:FF\n")
            args=SimpleNamespace(config=config, identity=None, yes=False)
            forget_command(args, Backend, lambda _: "n", lambda _: None); self.assertEqual(Backend.removed, [])
            forget_command(args, Backend, lambda _: "s", lambda _: None); self.assertEqual(Backend.removed, ["/only"])

    def test_setup_persists_identity_after_event(self):
        class Agent:
            target=None
            def register(self): pass
            def close(self): pass
        class Backend:
            def __init__(self, **kwargs): self.event_handler=None
            def adapter(self): return {}
            def devices(self): return [("/new", {"Address":"AA:BB:CC:DD:EE:FF","Paired":True}, A2540Profile)]
            def remove(self, path): pass
            def scan(self, seconds, min_rssi, exclude_paths=()): return self.devices()
            def pair(self,path,agent): return {"Address":"AA:BB:CC:DD:EE:FF","Paired":True,"Bonded":True}
            def connect(self,path,profile): self.event_handler(ButtonEvent(ButtonAction.PRESSED,"ATRAS"))
            def pump(self,n): pass
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory, patch("siri_remote_linux.cli.create_agent", lambda backend: Agent()):
            config=Path(directory)/"config.env"; args=SimpleNamespace(adapter="hci0",raw_touch=False,no_touch=False,seconds=1,min_rssi=-75,verify_seconds=1,config=config)
            self.assertEqual(setup_command(args, Backend, output=lambda _:None), 0)
            self.assertEqual(load_settings(config).remote_identity, "AA:BB:CC:DD:EE:FF")

    def test_doctor_uses_doubles_for_gatt_and_events(self):
        outputs=[]
        class Backend:
            def __init__(self, **kwargs): self.event_handler=None
            def adapter(self): return {}
            def resolve(self, identity): return "/device", {}, A2540Profile
            def connect(self, path, profile): pass
            def pump(self, seconds): self.event_handler(ButtonEvent(ButtonAction.PRESSED,"ATRAS"))
            def close(self): pass
        class FakeKeyboard:
            def __init__(self, *args, **kwargs): pass
            def close(self): pass
        with tempfile.TemporaryDirectory() as directory, patch("siri_remote_linux.cli.Keyboard", FakeKeyboard), patch("siri_remote_linux.cli.dependencies", lambda: (1,2,3)):
            config=Path(directory)/"config.env"; config.write_text("REMOTE_IDENTITY=AA:BB:CC:DD:EE:FF\n")
            args=SimpleNamespace(config=config, adapter="hci0", no_touch=False, listen_seconds=.01)
            self.assertEqual(doctor_command(args, Backend, outputs.append), 0)
            self.assertIn("OK: conexión y acceso GATT", outputs); self.assertIn("OK: recepción de eventos", outputs)

    def test_supervisor_rebuilds_private_path_and_resets_backoff(self):
        paths = []; sleeps = []; attempts = ["adapter", "gatt", "success"]
        class Output:
            def __init__(self): self.navigation=SimpleNamespace(tick=lambda:None, handle=lambda e:None, cancel_touch=lambda:None); self.releases=0
            def release_all(self): self.releases += 1
        class Backend:
            def __init__(self, **kwargs): self.kind=attempts.pop(0); self.disconnect_event=[]
            def adapter(self):
                if self.kind == "adapter": raise RuntimeError("adaptador ausente")
            def resolve(self, identity): paths.append("/private/" + self.kind); return paths[-1], {}, A2540Profile
            def connect(self, path, profile):
                if self.kind == "gatt": raise TimeoutError("GATT")
            def pump(self, seconds): pass
            def connected(self): return False
            def close(self): pass
        output=Output(); settings=Settings(remote_identity="AA:BB:CC:DD:EE:FF")
        supervisor=Supervisor(settings, output, backend_factory=Backend, sleep=lambda n: (sleeps.append(n), setattr(supervisor,"stopping", len(sleeps)>=3)))
        supervisor.run()
        self.assertEqual(paths, ["/private/gatt", "/private/success"])
        self.assertEqual(sleeps, [1, 2, 1]); self.assertGreaterEqual(output.releases, 3)

    def test_disconnect_clears_button_touch_and_held_key(self):
        keyboard=Keyboard(Settings(), dry_run=True); calls=[]
        class Backend:
            def __init__(self, event_handler, **kwargs): self.handler=event_handler; self.disconnect_event=[]
            def adapter(self): return {}
            def resolve(self, identity): return "/changed/path", {}, A2540Profile
            def connect(self, path, profile):
                self.handler(ButtonEvent(ButtonAction.PRESSED,"CENTRO")); self.handler(TouchEvent(TouchAction.START,1,1,20))
            def pump(self, seconds): pass
            def connected(self): return False
            def close(self): calls.append("closed")
        supervisor=Supervisor(Settings(remote_identity="AA:BB:CC:DD:EE:FF"), keyboard, backend_factory=Backend,
                              sleep=lambda n: setattr(supervisor,"stopping",True))
        supervisor.run()
        self.assertFalse(keyboard.navigation.held); self.assertIsNone(keyboard.navigation.origin)
        self.assertFalse(keyboard.held_codes); self.assertEqual(calls, ["closed"])


if __name__ == "__main__": unittest.main()
