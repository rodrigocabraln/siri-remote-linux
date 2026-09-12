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
class OperationCancelled(RuntimeError): pass


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
        self.cancelled = False
        self.subscribed = []
        self.matches = [self.bus.add_signal_receiver(self._changed, signal_name="PropertiesChanged",
                        dbus_interface=PROPS, path_keyword="path")]

    def interface(self, path, interface):
        return self.dbus.Interface(self.bus.get_object(BLUEZ, path), interface)

    def pump(self, seconds=0.05):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if getattr(self, "cancelled", False):
                raise OperationCancelled("Operación cancelada")
            for _ in range(100):
                if not self.context.pending(): break
                self.context.iteration(False)
            time.sleep(0.01)

    def call(self, method, *args, timeout=30):
        if getattr(self, "cancelled", False):
            raise OperationCancelled("Operación cancelada")
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
        try: return self.call(self.interface("/", OBJECTS).GetManagedObjects, timeout=10)[0]
        except OperationCancelled: raise
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
        matches = [(path, props, profile) for path, props, profile in self.devices(False)
                   if str(props.get("Address", "")).upper() == identity]
        if not matches:
            raise RuntimeError(f"No existe el mando {identity} en {self.adapter_path}; comprobá ADAPTER")
        if len(matches) != 1:
            raise RuntimeError(f"Hay varios dispositivos con identidad {identity} en {self.adapter_path}")
        path, props, profile = matches[0]
        if not props.get("Paired") or not props.get("Bonded", True):
            raise RuntimeError(f"El mando {identity} en {self.adapter_path} no está vinculado; ejecutá setup")
        # Setup selected and verified this identity. Resolve it without recent
        # advertising data; connect validates GATT before activation.
        return path, props, profile or A2540Profile

    def start_scan(self):
        self.adapter()
        adapter = self.interface(self.adapter_path, ADAPTER)
        self.call(adapter.SetDiscoveryFilter,
                  self.dbus.Dictionary({"Transport": "le", "DuplicateData": True}, signature="sv"), timeout=10)
        self.call(adapter.StartDiscovery, timeout=10); self.scanning = True

    def stop_scan(self):
        if self.scanning:
            try: self.call(self.interface(self.adapter_path, ADAPTER).StopDiscovery, timeout=5)
            finally: self.scanning = False

    def scan(self, seconds=60, min_rssi=-75, exclude_paths=()):
        self.start_scan(); deadline = time.monotonic() + seconds; found = {}
        try:
            while time.monotonic() < deadline:
                self.pump(0.15)
                for item in self.devices():
                    path, props, profile = item
                    if path in exclude_paths:
                        continue
                    if props.get("Paired") or int(props.get("RSSI", -999)) >= min_rssi: found[path] = item
                # En modo de emparejamiento hay una ventana breve de publicidad:
                # no esperar el timeout una vez que hay un candidato inequívoco.
                if len(found) == 1:
                    return list(found.values())
            return list(found.values())
        finally: self.stop_scan()

    def pair(self, path, agent):
        def properties():
            try:
                return self.interface(path, PROPS).GetAll(DEVICE, timeout=10)
            except Exception as exc:
                if getattr(exc, "get_dbus_name", lambda: "")() in (
                        "org.freedesktop.DBus.Error.UnknownObject",
                        "org.freedesktop.DBus.Error.UnknownInterface",
                        "org.bluez.Error.DoesNotExist"):
                    raise RuntimeError("El mando seleccionado desapareció de BlueZ; "
                                       "volvé a ponerlo en modo de emparejamiento y repetí setup") from exc
                raise
        agent.target = path
        for attempt in range(1, 4):
            props = properties()
            if props.get("Paired"):
                break
            log.info("Emparejando %s (intento %d/3)", props.get("Address", path), attempt)
            try:
                self.call(self.interface(path, DEVICE).Pair, timeout=30)
                break
            except Exception as exc:
                error_name = getattr(exc, "get_dbus_name", lambda: "")()
                att_failure = (error_name == "org.bluez.Error.Failed" and
                               "ATT error: 0x0e" in str(exc))
                if error_name != "org.bluez.Error.AuthenticationCanceled" and not att_failure:
                    raise
                # BlueZ también usa AuthenticationCanceled para un enlace cortado.
                # Releer el estado evita repetir Pair si el vínculo sí se completó.
                props = properties()
                if props.get("Paired"):
                    log.warning("Pair devolvió %s, pero BlueZ confirmó el vínculo; verificando GATT", exc)
                    break
                if att_failure:
                    raise
                if attempt == 3:
                    raise RuntimeError("BlueZ canceló o interrumpió el emparejamiento "
                                       "(AuthenticationCanceled) tras 3 intentos; acercá el mando, "
                                       "activá Atrás + Volumen arriba y repetí setup") from exc
                log.warning("Emparejamiento interrumpido (AuthenticationCanceled); "
                            "reintentando el mismo mando en 1s")
                self.pump(1)
        props = properties()
        if not props.get("Paired") or not props.get("Bonded", props.get("Paired")):
            raise RuntimeError("BlueZ no confirmó Paired y Bonded")
        self.interface(path, PROPS).Set(DEVICE, "Trusted", self.dbus.Boolean(True))
        return props

    def connect(self, path, profile):
        self.device_path, self.profile = path, profile
        device = self.interface(path, DEVICE)
        props = self.call(self.interface(path, PROPS).GetAll, DEVICE, timeout=10)[0]
        deadline = time.monotonic() + 40
        if not props.get("Connected"):
            # Descubrir mientras se despierta el mando permite actualizar su RPA.
            own_scan = not self.scanning
            try:
                if own_scan:
                    self.start_scan()
                try:
                    self.call(device.Connect, timeout=max(1, deadline - time.monotonic()))
                except Exception as exc:
                    name = getattr(exc, "get_dbus_name", lambda: "")()
                    if name == "org.freedesktop.DBus.Error.NoReply" or isinstance(exc, TimeoutError):
                        # El timeout D-Bus no cancela la operación en BlueZ.
                        try: self.call(device.Disconnect, timeout=3)
                        except Exception: pass
                        raise TimeoutError("El mando vinculado no respondió a Connect en 40s; "
                                           "pulsá y soltá un botón y repetí setup. "
                                           "Si sigue sin responder, puede ser necesario renovar el vínculo.") from exc
                    if name != "org.bluez.Error.InProgress":
                        raise
            finally:
                if own_scan:
                    self.stop_scan()
        while time.monotonic() < deadline:
            props = self.call(self.interface(path, PROPS).GetAll, DEVICE, timeout=10)[0]
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
        try: return bool(self.call(self.interface(self.device_path, PROPS).Get,
                                   DEVICE, "Connected", timeout=10)[0])
        except OperationCancelled: raise
        except Exception as exc: raise BlueZUnavailable("Se perdió BlueZ o el adaptador") from exc

    def remove(self, path):
        self.call(self.interface(self.adapter_path, ADAPTER).RemoveDevice,
                  self.dbus.ObjectPath(path), timeout=10)

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
