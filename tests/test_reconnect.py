"""Restored bonds without advertising data and adapter selection."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from siri_remote_linux.bluetooth.bluez import BlueZBackend, DEVICE
from siri_remote_linux.cli import parser, setup_command
from siri_remote_linux.config import load_settings
from siri_remote_linux.events import ButtonAction, ButtonEvent
from siri_remote_linux.remotes.a2540 import A2540Profile, HID


class ReconnectTests(unittest.TestCase):
    def test_fresh_target_signal_required_despite_cached_advertising(self):
        backend = BlueZBackend.__new__(BlueZBackend)
        backend.device_path = "/target"
        backend.advertisement_path = None
        backend.profile = None
        backend.interface = Mock()
        backend.call = Mock(return_value=({"Connected": False, "RSSI": -40,
                                           "ManufacturerData": {76: b"cached"}},))
        events = iter([("/other", {"RSSI": -30}),
                       ("/target", {"Trusted": True}),
                       ("/target", {"ManufacturerData": {76: b"fresh"}})])
        def pump(_):
            path, changed = next(events)
            backend._changed(DEVICE, changed, [], path)
        backend.pump = Mock(side_effect=pump)
        self.assertFalse(backend.wait_for_advertisement("/target"))
        self.assertEqual(backend.pump.call_count, 3)

    def test_no_advertisement_never_calls_connect_and_stops_scan(self):
        backend = BlueZBackend.__new__(BlueZBackend)
        backend.scanning = False
        backend.interface = Mock()
        backend.call = Mock(return_value=({"Connected": False},))
        backend.start_scan = Mock()
        backend.stop_scan = Mock()
        backend.pump = Mock()
        with patch("siri_remote_linux.bluetooth.bluez.time.monotonic",
                   side_effect=[0, 0, 21]):
            with self.assertRaisesRegex(TimeoutError, "publicidad nueva"):
                backend.connect("/target", Mock())
        self.assertEqual(backend.call.call_count, 1)
        backend.stop_scan.assert_called_once()

    def test_abort_cancels_connection_after_fresh_advertisement(self):
        backend = BlueZBackend.__new__(BlueZBackend)
        backend.scanning = False
        interface = Mock()
        backend.interface = Mock(return_value=interface)
        backend.start_scan = Mock()
        backend.stop_scan = Mock()
        backend.wait_for_advertisement = Mock(return_value=False)
        backend.call = Mock(side_effect=[({"Connected": False},),
                                        RuntimeError("le-connection-abort-by-local"), ()])
        with self.assertRaisesRegex(RuntimeError, "abort-by-local"):
            backend.connect("/target", Mock())
        backend.wait_for_advertisement.assert_called_once_with("/target")
        self.assertEqual(backend.call.call_args_list[1].args[0], interface.Connect)
        self.assertEqual(backend.call.call_args_list[2].args[0], interface.Disconnect)
        backend.stop_scan.assert_called_once()

    def test_automatic_connection_does_not_require_advertisement(self):
        backend = BlueZBackend.__new__(BlueZBackend)
        backend.interface = Mock()
        backend.call = Mock(return_value=({"Connected": True},))
        backend.pump = Mock()
        self.assertTrue(backend.wait_for_advertisement("/target"))
        backend.pump.assert_not_called()

    def backend(self, **changes):
        backend = BlueZBackend.__new__(BlueZBackend)
        backend.adapter_path = "/org/bluez/hci1"
        props = dict(Address="60:BE:C4:33:76:01", Paired=True, Bonded=True,
                     Trusted=True, Appearance=0x03C0, UUIDs=[HID],
                     Modalias="bluetooth:v004Cp0314d0001")
        props.update(changes)
        path = backend.adapter_path + "/dev_60_BE_C4_33_76_01"
        backend.objects = lambda: {path: {DEVICE: props}}
        return backend, path, props

    def test_restored_bond_is_recognized_without_advertising(self):
        backend, path, props = self.backend()
        self.assertEqual(backend.devices(), [(path, props, A2540Profile)])
        self.assertEqual(backend.resolve(props["Address"])[0], path)

    def test_saved_identity_resolves_even_without_profile_metadata(self):
        backend, path, props = self.backend(Modalias="", Appearance=0, UUIDs=[])
        self.assertEqual(backend.devices(), [])
        self.assertEqual(backend.resolve(props["Address"]), (path, props, A2540Profile))

    def test_unbonded_or_wrong_adapter_is_not_accepted(self):
        for changes in ({"Paired": False}, {"Bonded": False}):
            backend, _, props = self.backend(**changes)
            with self.assertRaisesRegex(RuntimeError, "no está vinculado"):
                backend.resolve(props["Address"])
        backend, _, props = self.backend()
        backend.adapter_path = "/org/bluez/hci0"
        with self.assertRaisesRegex(RuntimeError, "hci0.*ADAPTER"):
            backend.resolve(props["Address"])

    def test_setup_selects_and_persists_adapter_and_removes_saved_bond(self):
        for existing, override, expected in ((True, None, "hci1"),
                                              (True, "hci2", "hci2"),
                                              (False, None, "hci0"),
                                              (False, "hci1", "hci1")):
            with self.subTest(existing=existing, override=override), tempfile.TemporaryDirectory() as directory:
                config = Path(directory) / "config.env"
                identity = "60:BE:C4:33:76:01"
                if existing:
                    config.write_text(f"REMOTE_IDENTITY={identity}\nADAPTER = hci1\nTOUCH_STEP=22\n")
                argv = ["setup", "--config", str(config)]
                if override:
                    argv += ["--adapter", override]
                args = parser().parse_args(argv)
                backend = Mock()
                props = dict(Address=identity, Paired=True, Bonded=True)
                # The bond exists but has no profile metadata.
                backend.devices.side_effect = lambda recognized_only=True: (
                    [] if recognized_only else [("/old", props, None)])
                backend.scan.return_value = [("/fresh", props, A2540Profile)]
                backend.pair.return_value = props
                backend.connect.side_effect = lambda *a: backend.event_handler(
                    ButtonEvent(ButtonAction.PRESSED, "ATRAS"))
                factory = Mock(return_value=backend)
                with patch("siri_remote_linux.cli.create_agent", return_value=Mock()):
                    self.assertEqual(setup_command(args, factory, output=lambda _: None), 0)
                self.assertEqual(factory.call_args.kwargs["adapter"], expected)
                settings = load_settings(config)
                self.assertEqual(settings.adapter, expected)
                self.assertEqual(settings.remote_identity, identity)
                if existing:
                    backend.remove.assert_called_once_with("/old")
                    self.assertEqual(settings.touch_step, 22)

    def test_setup_invalid_config_does_not_touch_bluetooth(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.env"
            config.write_text("ADAPTER=invalid\n")
            factory = Mock()
            with self.assertRaises(ValueError):
                setup_command(parser().parse_args(["setup", "--config", str(config)]), factory)
            factory.assert_not_called()
