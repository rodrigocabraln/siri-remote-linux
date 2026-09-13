import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from siri_remote_linux.bluetooth.bluez import BlueZBackend, Disconnected, OperationCancelled
from siri_remote_linux.supervisor import Supervisor


class WaitAndScanTests(unittest.TestCase):
    def test_supervisor_checks_connection_periodically_and_handles_disconnect_signal(self):
        clock = [0.0]
        instances = []

        class Backend:
            def __init__(self, event_handler, **kwargs):
                self.event_handler = event_handler
                self.disconnect_event = []
                self.checks = 0
                instances.append(self)
            def adapter(self): return {}
            def resolve(self, identity): return "/remote", {}, Mock()
            def connect(self, path, profile): pass
            def pump(self, seconds):
                clock[0] += seconds
                if clock[0] >= 1.2:
                    self.event_handler(Disconnected("Se cortó el enlace"))
            def connected(self):
                self.checks += 1
                return True
            def close(self): pass

        output = Mock()
        supervisor = Supervisor(SimpleNamespace(adapter="hci0", remote_identity="A"),
                                output, backend_factory=Backend,
                                sleep=lambda seconds: setattr(supervisor, "stopping", True))
        with patch("siri_remote_linux.supervisor.time.monotonic", side_effect=lambda: clock[0]):
            supervisor.run()
        self.assertEqual(len(instances), 1)
        self.assertEqual(instances[0].checks, 2)
        self.assertGreater(clock[0], 1.2)
        output.release_all.assert_called()

    def test_connection_check_detects_disconnect_without_signal(self):
        clock = [0.0]
        instances = []

        class Backend:
            def __init__(self, **kwargs):
                self.disconnect_event = []
                self.checks = 0
                instances.append(self)
            def adapter(self): return {}
            def resolve(self, identity): return "/remote", {}, Mock()
            def connect(self, path, profile): pass
            def pump(self, seconds): clock[0] += seconds
            def connected(self):
                self.checks += 1
                return self.checks == 1
            def close(self): pass

        supervisor = Supervisor(SimpleNamespace(adapter="hci0", remote_identity="A"),
                                Mock(), backend_factory=Backend,
                                sleep=lambda seconds: setattr(supervisor, "stopping", True))
        with patch("siri_remote_linux.supervisor.time.monotonic", side_effect=lambda: clock[0]):
            supervisor.run()
        self.assertEqual(instances[0].checks, 2)
        self.assertAlmostEqual(clock[0], 1.05, delta=0.05)

    def test_preserved_device_does_not_end_scan_before_new_device_arrives(self):
        backend = BlueZBackend.__new__(BlueZBackend)
        backend.start_scan = Mock()
        backend.stop_scan = Mock()
        backend.pump = Mock()
        old = ("/preserved", {"Paired": True}, None)
        new = ("/new", {"RSSI": -40}, None)
        backend.devices = Mock(side_effect=[[old], [old, new]])
        self.assertEqual(backend.scan(1, exclude_paths={"/preserved"}), [new])
        self.assertEqual(backend.devices.call_count, 2)
        backend.stop_scan.assert_called_once()

    def test_stop_interrupts_pending_call_and_cancels_reply_wait(self):
        backend = BlueZBackend.__new__(BlueZBackend)
        backend.cancelled = False
        supervisor = Supervisor(SimpleNamespace(), Mock())
        supervisor.backend = backend
        pending = Mock()
        def method(**kwargs):
            supervisor.stop()
            return pending
        with self.assertRaises(OperationCancelled):
            backend.call(method, timeout=40)
        pending.cancel.assert_called_once()
        self.assertTrue(supervisor._stop_event.is_set())

    def test_cancelled_backend_does_not_start_another_operation(self):
        backend = BlueZBackend.__new__(BlueZBackend)
        backend.cancelled = True
        method = Mock()
        with self.assertRaises(OperationCancelled):
            backend.call(method)
        method.assert_not_called()
