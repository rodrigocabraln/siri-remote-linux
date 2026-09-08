"""Acceso a BlueZ por D-Bus usando un único bucle GLib."""
import logging
import time

from ..remotes import PROFILES
from ..remotes.a2540 import A2540Profile, value_handle

log = logging.getLogger("siri_remote")
BLUEZ = "org.bluez"
DEVICE = BLUEZ + ".Device1"
ADAPTER = BLUEZ + ".Adapter1"
CHAR = BLUEZ + ".GattCharacteristic1"
DESC = BLUEZ + ".GattDescriptor1"
PROPS = "org.freedesktop.DBus.Properties"
OBJECTS = "org.freedesktop.DBus.ObjectManager"
AGENT = BLUEZ + ".Agent1"
AGENT_MANAGER = BLUEZ + ".AgentManager1"
AGENT_PATH = "/io/github/siri_remote_linux/agent"


def dependencies():
    try:
        import dbus
        import dbus.service
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib
    except ImportError as exc:
        raise RuntimeError("Faltan python3-dbus o python3-gobject/python3-gi") from exc
    return dbus, DBusGMainLoop, GLib


class BlueZUnavailable(RuntimeError): pass
class AdapterUnavailable(RuntimeError): pass
class Disconnected(RuntimeError): pass


def profile_for(properties):
    return next((profile for profile in PROFILES if profile.matches(properties)), None)


class BlueZBackend:
    def __init__(self, adapter="hci0", event_handler=None, raw_touch=False):
        self.dbus, mainloop, self.GLib = dependencies()
        mainloop(set_as_default=True)
        self.bus = self.dbus.SystemBus(private=True)
        self.context = self.GLib.MainContext.default()
        self.adapter_name = adapter
        self.adapter_path = "/org/bluez/" + adapter
        self.event_handler = event_handler
        self.raw_touch = raw_touch
        self.profile = self.device_path = None
        self.scanning = False
        self.subscribed = []
        self.matches = [self.bus.add_signal_receiver(self._changed, signal_name="PropertiesChanged",
                        dbus_interface=PROPS, path_keyword="path")]

    def interface(self, path, interface):
        return self.dbus.Interface(self.bus.get_object(BLUEZ, path), interface)

    def pump(self, seconds=0.05):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            for _ in range(100):
                if not self.context.pending(): break
                self.context.iteration(False)
            time.sleep(0.01)

    def call(self, method, *args, timeout=30):
        result, errors = [], []
        pending = method(*args, reply_handler=lambda *reply: result.append(reply),
                         error_handler=errors.append, timeout=timeout)
        deadline = time.monotonic() + timeout + 1
        try:
            while not result and not errors:
                if time.monotonic() >= deadline: raise TimeoutError(f"Sin respuesta durante {timeout}s")
                self.pump()
            if errors: raise errors[0]
            return result[0]
        finally:
            if pending is not None: pending.cancel()

    def objects(self):
        try: return self.interface("/", OBJECTS).GetManagedObjects(timeout=10)
        except Exception as exc: raise BlueZUnavailable("BlueZ no está disponible") from exc

    def adapter(self):
        adapter = self.objects().get(self.adapter_path, {}).get(ADAPTER)
        if adapter is None: raise AdapterUnavailable(f"No existe el adaptador {self.adapter_name}")
        if not adapter.get("Powered"): raise AdapterUnavailable(f"El adaptador {self.adapter_name} está apagado")
        return adapter

    def adapters(self):
        return [(str(path), props) for path, interfaces in self.objects().items()
                if (props := interfaces.get(ADAPTER)) is not None]

    def devices(self, recognized_only=True):
        found = []
        for path, interfaces in self.objects().items():
            props = interfaces.get(DEVICE)
            if props is None or not str(path).startswith(self.adapter_path + "/"): continue
            profile = profile_for(props)
            if recognized_only and profile is None: continue
            found.append((str(path), props, profile))
        return found

    def all_devices(self, recognized_only=True):
        found = []
        for path, interfaces in self.objects().items():
            props = interfaces.get(DEVICE)
            if props is None: continue
            profile = profile_for(props)
            if recognized_only and profile is None: continue
            found.append((str(path), props, profile))
        return found

    def resolve(self, identity):
        identity = identity.upper()
        matches = [(path, props, profile) for path, props, profile in self.devices()
                   if profile.stable_identity(props) == identity and props.get("Paired")]
        if len(matches) != 1: raise RuntimeError(f"No se encontró un único mando vinculado con identidad {identity}")
        return matches[0]

    def start_scan(self):
        self.adapter()
        adapter = self.interface(self.adapter_path, ADAPTER)
        adapter.SetDiscoveryFilter(self.dbus.Dictionary({"Transport": "le", "DuplicateData": True}, signature="sv"))
        self.call(adapter.StartDiscovery, timeout=10); self.scanning = True

    def stop_scan(self):
        if self.scanning:
            try: self.call(self.interface(self.adapter_path, ADAPTER).StopDiscovery, timeout=5)
            finally: self.scanning = False

    def scan(self, seconds=60, min_rssi=-75):
        self.start_scan(); deadline = time.monotonic() + seconds; found = {}
        try:
            while time.monotonic() < deadline:
                self.pump(0.15)
                for item in self.devices():
                    path, props, profile = item
                    if props.get("Paired") or int(props.get("RSSI", -999)) >= min_rssi: found[path] = item
                # En modo de emparejamiento hay una ventana breve de publicidad:
                # no esperar el timeout una vez que hay un candidato inequívoco.
                if len(found) == 1:
                    return list(found.values())
            return list(found.values())
        finally: self.stop_scan()

    def pair(self, path, agent):
        props = self.interface(path, PROPS).GetAll(DEVICE, timeout=10)
        if not props.get("Paired"):
            agent.target = path
            self.call(self.interface(path, DEVICE).Pair, timeout=30)
        props = self.interface(path, PROPS).GetAll(DEVICE, timeout=10)
        if not props.get("Paired") or not props.get("Bonded", props.get("Paired")):
            raise RuntimeError("BlueZ no confirmó Paired y Bonded")
        self.interface(path, PROPS).Set(DEVICE, "Trusted", self.dbus.Boolean(True))
        return props

    def connect(self, path, profile):
        self.device_path, self.profile = path, profile
        device = self.interface(path, DEVICE)
        props = self.interface(path, PROPS).GetAll(DEVICE, timeout=10)
        if not props.get("Connected"):
            try: self.call(device.Connect, timeout=40)
            except Exception as exc:
                if getattr(exc, "get_dbus_name", lambda: "")() != "org.bluez.Error.InProgress": raise
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            props = self.interface(path, PROPS).GetAll(DEVICE, timeout=10)
            if props.get("Connected") and props.get("ServicesResolved"): break
            self.pump(0.2)
        else: raise TimeoutError("No se resolvió GATT en 40s")
        objects = self.objects(); chars = {}
        for obj, interfaces in objects.items():
            if str(obj).startswith(path + "/") and CHAR in interfaces:
                chars[value_handle(obj, interfaces[CHAR])] = (str(obj), interfaces[CHAR])
        def read_descriptor(descriptor):
            result = self.call(self.interface(descriptor, DESC).ReadValue,
                               self.dbus.Dictionary({}, signature="sv"), timeout=10)
            return result[0]
        config = profile.configure(chars, objects, read_descriptor)
        for notify_path in (config.button_path, config.touch_path):
            if notify_path:
                self.call(self.interface(notify_path, CHAR).StartNotify, timeout=10)
                self.subscribed.append(notify_path)
        self.call(self.interface(config.activation_path, CHAR).WriteValue,
                  self.dbus.ByteArray(config.activation),
                  self.dbus.Dictionary({"type": config.write_type}, signature="sv"), timeout=10)
        return config

    def _changed(self, interface, changed, invalidated, path=None):
        path = str(path)
        if interface == DEVICE and path == self.device_path and changed.get("Connected") is not None and not changed["Connected"]:
            if self.event_handler: self.event_handler(Disconnected("El mando se desconectó"))
        if interface == CHAR and self.profile and "Value" in changed:
            data = bytes(changed["Value"])
            if self.raw_touch and path == self.profile.touch_path: log.debug("RAW TOUCH %s", data.hex(" "))
            try: events = self.profile.decode(path, data)
            except ValueError as exc:
                if path == self.profile.touch_path and self.event_handler: self.event_handler(exc)
                log.warning("%s; RAW=%s", exc, data.hex(" ")); return
            for event in events:
                if self.event_handler: self.event_handler(event)

    def connected(self):
        if not self.device_path: return False
        try: return bool(self.interface(self.device_path, PROPS).Get(DEVICE, "Connected", timeout=10))
        except Exception as exc: raise BlueZUnavailable("Se perdió BlueZ o el adaptador") from exc

    def remove(self, path):
        self.call(self.interface(self.adapter_path, ADAPTER).RemoveDevice(self.dbus.ObjectPath(path)), timeout=10)

    def close(self):
        for path in self.subscribed:
            try: self.call(self.interface(path, CHAR).StopNotify, timeout=3)
            except Exception: pass
        self.subscribed.clear()
        try: self.stop_scan()
        except Exception: pass
        for match in self.matches:
            try: match.remove()
            except Exception: pass
        self.matches.clear()
        if self.profile: self.profile.reset()
        try: self.bus.close()
        except Exception: pass
