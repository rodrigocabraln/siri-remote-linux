import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from siri_remote_linux.bluetooth.bluez import BlueZBackend, OperationCancelled
from siri_remote_linux.supervisor import Supervisor


class WaitAndScanTests(unittest.TestCase):
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
