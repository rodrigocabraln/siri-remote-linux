"""Agente de emparejamiento limitado al dispositivo seleccionado."""
import logging
from .bluez import AGENT, AGENT_MANAGER, AGENT_PATH, BLUEZ, dependencies

log = logging.getLogger("siri_remote")


def create_agent(backend):
    dbus, _, _ = dependencies()

    class PairAgent(dbus.service.Object):
        def __init__(self):
            super().__init__(backend.bus, AGENT_PATH); self.target = None; self.registered = False
        def check(self, device):
            if str(device) != self.target:
                raise dbus.exceptions.DBusException("Dispositivo ajeno", name="org.bluez.Error.Rejected")
        @dbus.service.method(AGENT, in_signature="ou", out_signature="")
        def RequestConfirmation(self, device, passkey): self.check(device); log.info("Confirmación: %06d", passkey)
        @dbus.service.method(AGENT, in_signature="o", out_signature="")
        def RequestAuthorization(self, device): self.check(device)
        @dbus.service.method(AGENT, in_signature="os", out_signature="")
        def AuthorizeService(self, device, uuid): self.check(device)
        @dbus.service.method(AGENT, in_signature="ouq", out_signature="")
        def DisplayPasskey(self, device, passkey, entered): self.check(device)
        @dbus.service.method(AGENT, in_signature="os", out_signature="")
        def DisplayPinCode(self, device, pincode): self.check(device)
        @dbus.service.method(AGENT, in_signature="o", out_signature="s")
        def RequestPinCode(self, device): self.check(device); raise dbus.exceptions.DBusException("PIN inesperado", name="org.bluez.Error.Rejected")
        @dbus.service.method(AGENT, in_signature="o", out_signature="u")
        def RequestPasskey(self, device): self.check(device); raise dbus.exceptions.DBusException("Passkey inesperada", name="org.bluez.Error.Rejected")
        @dbus.service.method(AGENT, in_signature="", out_signature="")
        def Cancel(self): log.info("BlueZ canceló el emparejamiento")
        @dbus.service.method(AGENT, in_signature="", out_signature="")
        def Release(self): pass
        def register(self):
            backend.interface("/org/bluez", AGENT_MANAGER).RegisterAgent(AGENT_PATH, "KeyboardDisplay"); self.registered = True
        def close(self):
            if self.registered:
                try: backend.interface("/org/bluez", AGENT_MANAGER).UnregisterAgent(AGENT_PATH)
                except Exception: pass
            self.remove_from_connection()
    return PairAgent()
