"""Fallos reales de write/syn: conservar pendientes y cerrar uinput."""
import unittest
from unittest.mock import patch

from evdev import ecodes as e
from siri_remote_linux.config import Settings
from siri_remote_linux.input.keyboard import Keyboard
from tests.test_core import FakeDevice


class ReleaseFailureTests(unittest.TestCase):
    def test_center_release_retries_after_write_or_syn_failure(self):
        for operation in ('write', 'syn'):
            with self.subTest(operation=operation):
                keyboard = Keyboard(Settings(), ui_factory=FakeDevice)
                keyboard.send('Input.CenterLongDown')
                with patch.object(keyboard.ui, operation, side_effect=OSError('release')):
                    with self.assertRaises(OSError):
                        keyboard.send('Input.CenterLongUp')
                self.assertIn(e.KEY_ENTER, keyboard.held_codes)
                keyboard.send('Input.CenterLongUp')
                self.assertNotIn(e.KEY_ENTER, keyboard.held_codes)
                self.assertEqual(keyboard.ui.events[-2:], [(e.EV_KEY, e.KEY_ENTER, 0), 'syn'])
                keyboard.close()

    def test_reset_retries_center_and_cancels_navigation(self):
        keyboard = Keyboard(Settings(), ui_factory=FakeDevice)
        nav = keyboard.navigation
        nav.button('PRESIONADO', 'CENTRO')
        nav.button('PRESIONADO', 'ARRIBA')
        nav.touch(('INICIO', 0, 0, 20, 0, 0))
        write = keyboard.ui.write
        with patch.object(keyboard.ui, 'write', side_effect=[OSError('release'), None]), self.assertLogs('siri_remote'):
            keyboard.release_all()
        self.assertFalse(keyboard.held_codes)
        self.assertFalse(nav.held)
        self.assertFalse(nav.repeat_due)
        self.assertFalse(nav.center_long_active)
        self.assertIsNone(nav.origin)
        keyboard.close()

    def test_chord_failure_preserves_only_failed_release(self):
        keyboard = Keyboard(Settings(), ui_factory=FakeDevice)
        write = keyboard.ui.write
        def fail_d_release(*event):
            if event == (e.EV_KEY, e.KEY_D, 0):
                raise OSError('release D')
            write(*event)
        with patch.object(keyboard.ui, 'write', side_effect=fail_d_release):
            with self.assertRaises(OSError):
                keyboard.send('Input.TVLong')
            with self.assertLogs('siri_remote'):
                keyboard.release_all()
        self.assertEqual(keyboard.held_codes, {e.KEY_D})
        keyboard.release_all()
        self.assertFalse(keyboard.held_codes)
        self.assertEqual(keyboard.ui.events[-2:], [(e.EV_KEY, e.KEY_D, 0), 'syn'])
        keyboard.close()

    def test_close_closes_device_even_if_reset_raises(self):
        keyboard = Keyboard(Settings(), ui_factory=FakeDevice)
        ui = keyboard.ui
        with patch.object(keyboard.navigation, 'reset', side_effect=RuntimeError('reset')):
            with self.assertRaises(RuntimeError):
                keyboard.close()
        self.assertTrue(ui.closed)
        self.assertIsNone(keyboard.ui)
        keyboard.close()

    def test_close_closes_device_despite_persistent_release_error(self):
        keyboard = Keyboard(Settings(), ui_factory=FakeDevice)
        keyboard.navigation.button('PRESIONADO', 'CENTRO')
        ui = keyboard.ui
        with patch.object(ui, 'write', side_effect=OSError('release')), self.assertLogs('siri_remote'):
            keyboard.close()
        self.assertTrue(ui.closed)
        self.assertIsNone(keyboard.ui)
        self.assertFalse(keyboard.held_codes)
        keyboard.close()
