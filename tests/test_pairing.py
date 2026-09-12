"""Recuperación del emparejamiento sin hardware ni cambios de dispositivo."""
import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from siri_remote_linux.bluetooth.bluez import BlueZBackend
from siri_remote_linux.cli import setup_command
from siri_remote_linux.events import ButtonEvent, ButtonAction
from siri_remote_linux.remotes.a2540 import A2540Profile


class DBusError(Exception):
    def __init__(self, name):
        self.name = name

    def get_dbus_name(self):
        return self.name


class PairingTests(unittest.TestCase):
    def test_remove_passes_method_and_path_to_async_call(self):
        backend = BlueZBackend.__new__(BlueZBackend)
        backend.adapter_path = "/org/bluez/hci0"
        backend.dbus = SimpleNamespace(ObjectPath=str)
        adapter = Mock()
        adapter.RemoveDevice.return_value = None
        backend.interface = Mock(return_value=adapter)
        backend.call = Mock()
        backend.remove("/org/bluez/hci0/dev_60_BE_C4_33_76_01")
        adapter.RemoveDevice.assert_not_called()
        backend.call.assert_called_once_with(adapter.RemoveDevice,
            "/org/bluez/hci0/dev_60_BE_C4_33_76_01", timeout=10)

    def test_setup_removes_selected_bond_before_scan_and_preserves_others(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Mock()
            old = {"Address": "60:BE:C4:33:76:01", "Paired": True}
            other = {"Address": "AA:BB:CC:DD:EE:FF", "Paired": True}
            backend.devices.return_value = [("/old", old, A2540Profile),
                                            ("/other", other, A2540Profile)]
            backend.scan.return_value = [("/other", other, A2540Profile),
                ("/fresh", {"Address": "75:07:9B:F0:65:9E", "Paired": False}, A2540Profile)]
            backend.pair.return_value = {**old, "Bonded": True}
            backend.connect.side_effect = lambda *a: backend.event_handler(
                ButtonEvent(ButtonAction.PRESSED, "ATRAS"))
            config = Path(directory)/"config.env"
            config.write_text("REMOTE_IDENTITY=60:BE:C4:33:76:01\nTOUCH_STEP=22\n")
            args = SimpleNamespace(adapter="hci0", raw_touch=False, no_touch=False,
                seconds=1, min_rssi=-75, verify_seconds=1, config=config)
            with patch("siri_remote_linux.cli.create_agent", return_value=Mock()):
                self.assertEqual(setup_command(args, lambda **kw: backend,
                    input_fn=lambda _: "1", output=lambda _: None), 0)
            backend.remove.assert_called_once_with("/old")
            names = [c[0] for c in backend.method_calls]
            self.assertLess(names.index("remove"), names.index("scan"))
            self.assertLess(names.index("scan"), names.index("pair"))
            self.assertEqual(backend.pair.call_args.args[0], "/fresh")
            self.assertIn("TOUCH_STEP=22", config.read_text())

    def test_failed_fresh_scan_preserves_config_and_does_not_pair_old_bond(self):
        with tempfile.TemporaryDirectory() as directory:
            backend = Mock()
            backend.devices.return_value = [("/old", {
                "Address": "60:BE:C4:33:76:01", "Paired": True}, A2540Profile)]
            backend.scan.return_value = []
            config = Path(directory)/"config.env"
            original = "REMOTE_IDENTITY=60:BE:C4:33:76:01\nTOUCH_STEP=22\n"
            config.write_text(original)
            args = SimpleNamespace(adapter="hci0", raw_touch=False, no_touch=False,
                seconds=1, min_rssi=-75, verify_seconds=1, config=config)
            with self.assertRaisesRegex(RuntimeError, "No se encontraron"):
                setup_command(args, lambda **kw: backend, output=lambda _: None)
            backend.remove.assert_called_once_with("/old")
            backend.pair.assert_not_called()
            backend.close.assert_called_once()
            self.assertEqual(config.read_text(), original)

    def test_setup_recovers_changed_path_only_after_confirmation(self):
        for answer in ("s", "n"):
            with self.subTest(answer=answer), tempfile.TemporaryDirectory() as directory:
                backend = Mock()
                props = {"Address": "60:BE:C4:33:76:01", "Paired": True, "Bonded": True}
                backend.devices.side_effect = [[], [("/identity", props, A2540Profile)]]
                backend.scan.return_value = [("/random", {"Paired": False}, A2540Profile)]
                error = DBusError("org.bluez.Error.Failed")
                error.args = ("Operation failed with ATT error: 0x0e",)
                backend.pair.side_effect = [error, props]
                backend.connect.side_effect = lambda *a: backend.event_handler(
                    ButtonEvent(ButtonAction.PRESSED, "ATRAS"))
                args = SimpleNamespace(adapter="hci0", raw_touch=False, no_touch=False,
                    seconds=1, min_rssi=-75, verify_seconds=1, config=Path(directory)/"config.env")
                with patch("siri_remote_linux.cli.create_agent", return_value=Mock()):
                    if answer == "s":
                        self.assertEqual(setup_command(args, lambda **kw: backend,
                            input_fn=lambda _: answer, output=lambda _: None), 0)
                        self.assertEqual(backend.connect.call_args.args[0], "/identity")
                        self.assertTrue(args.config.exists())
                    else:
                        with self.assertRaisesRegex(RuntimeError, "Selección cancelada"):
                            setup_command(args, lambda **kw: backend,
                                input_fn=lambda _: answer, output=lambda _: None)
                        backend.connect.assert_not_called()
                        self.assertFalse(args.config.exists())
                backend.close.assert_called_once()

    def backend(self, outcomes):
        backend = BlueZBackend.__new__(BlueZBackend)
        props = {"Address": "AA:BB:CC:DD:EE:FF", "Paired": False, "Bonded": False}
        interface = Mock()
        interface.GetAll.side_effect = lambda *a, **kw: dict(props)
        backend.interface = Mock(return_value=interface)
        backend.dbus = SimpleNamespace(Boolean=bool)
        backend.pump = Mock()
        outcomes = iter(outcomes)

        def call(*args, **kwargs):
            outcome = next(outcomes)
            if callable(outcome):
                outcome(props)
            elif outcome:
                raise DBusError(outcome)
            else:
                props.update(Paired=True, Bonded=True)

        backend.call = Mock(side_effect=call)
        return backend, interface

    def test_cancel_then_success_retries_same_target(self):
        backend, interface = self.backend(["org.bluez.Error.AuthenticationCanceled", None])
        agent = SimpleNamespace(target=None)
        self.assertTrue(backend.pair("/selected", agent)["Bonded"])
        self.assertEqual(agent.target, "/selected")
        self.assertEqual(backend.call.call_count, 2)
        backend.pump.assert_called_once_with(1)
        self.assertTrue(all(c.args[0] == "/selected" for c in backend.interface.call_args_list))
        interface.Set.assert_called_once()

    def test_retries_are_bounded_and_do_not_trust_on_failure(self):
        backend, interface = self.backend(["org.bluez.Error.AuthenticationCanceled"] * 3)
        with self.assertRaisesRegex(RuntimeError, "tras 3 intentos"):
            backend.pair("/selected", SimpleNamespace())
        self.assertEqual(backend.call.call_count, 3)
        interface.Set.assert_not_called()

    def test_other_errors_are_not_retried(self):
        for name in ("AuthenticationRejected", "AuthenticationFailed", "InProgress"):
            with self.subTest(name=name):
                backend, interface = self.backend(["org.bluez.Error." + name])
                with self.assertRaises(DBusError):
                    backend.pair("/selected", SimpleNamespace())
                backend.call.assert_called_once()
                backend.pump.assert_not_called()
                interface.Set.assert_not_called()

    def test_canceled_reply_with_completed_bond_does_not_pair_again(self):
        def completed(props):
            props.update(Paired=True, Bonded=True)
            raise DBusError("org.bluez.Error.AuthenticationCanceled")
        backend, interface = self.backend([completed])
        self.assertTrue(backend.pair("/selected", SimpleNamespace())["Bonded"])
        backend.call.assert_called_once()
        backend.pump.assert_not_called()

    def test_disappearing_target_requires_new_setup(self):
        backend, interface = self.backend(["org.bluez.Error.AuthenticationCanceled"])
        interface.GetAll.side_effect = [
            {"Paired": False}, DBusError("org.freedesktop.DBus.Error.UnknownObject")]
        with self.assertRaisesRegex(RuntimeError, "desapareció"):
            backend.pair("/selected", SimpleNamespace())
        backend.call.assert_called_once()
        interface.Set.assert_not_called()

    def test_att_failure_after_bond_continues_without_pairing_again(self):
        def completed(props):
            props.update(Paired=True, Bonded=True)
            error = DBusError("org.bluez.Error.Failed")
            error.args = ("Operation failed with ATT error: 0x0e",)
            raise error
        backend, interface = self.backend([completed])
        self.assertTrue(backend.pair("/selected", SimpleNamespace())["Bonded"])
        backend.call.assert_called_once()
        interface.Set.assert_called_once()

    def test_att_failure_without_bond_is_not_accepted(self):
        def failed(props):
            error = DBusError("org.bluez.Error.Failed")
            error.args = ("Operation failed with ATT error: 0x0e",)
            raise error
        backend, interface = self.backend([failed])
        with self.assertRaises(DBusError):
            backend.pair("/selected", SimpleNamespace())
        backend.call.assert_called_once()
        interface.Set.assert_not_called()

    def test_paired_without_bond_is_not_trusted(self):
        backend, interface = self.backend([])
        interface.GetAll.side_effect = lambda *a, **kw: {"Paired": True, "Bonded": False}
        with self.assertRaisesRegex(RuntimeError, "Bonded"):
            backend.pair("/selected", SimpleNamespace())
        backend.call.assert_not_called()
        interface.Set.assert_not_called()

    def test_connect_timeout_cancels_pending_connection_and_stops_scan(self):
        backend, interface = self.backend([])
        backend.scanning = False
        backend.start_scan = Mock()
        backend.stop_scan = Mock()
        backend.call.side_effect = [({"Connected": False},), DBusError("org.freedesktop.DBus.Error.NoReply"), ()]
        with self.assertRaisesRegex(TimeoutError, "no respondió a Connect"):
            backend.connect("/selected", SimpleNamespace())
        backend.start_scan.assert_called_once()
        backend.stop_scan.assert_called_once()
        self.assertEqual(backend.call.call_args_list[2].args[0], interface.Disconnect)
