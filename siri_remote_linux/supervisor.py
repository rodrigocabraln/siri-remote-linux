"""Supervisor persistente: cada fallo reconstruye la sesión BlueZ completa."""
import logging
import signal
import time
import threading

from .bluetooth.bluez import BlueZBackend, Disconnected
from .remotes.a2540 import A2540Profile

log = logging.getLogger("siri_remote")


class Backoff:
    delays = (1, 2, 5, 10)
    def __init__(self): self.index = 0
    def reset(self): self.index = 0
    def next(self):
        value = self.delays[min(self.index, len(self.delays) - 1)]
        self.index += 1
        return value


class ErrorGrouper:
    def __init__(self): self.last = None; self.count = 0
    def report(self, exc):
        message = f"{type(exc).__name__}: {exc}"
        if message == self.last:
            self.count += 1
            if self.count in (2, 5) or self.count % 10 == 0: log.warning("%s (repetido %d veces)", exc, self.count)
        else:
            self.last, self.count = message, 1; log.warning("%s", exc)
    def reset(self): self.last = None; self.count = 0


class Supervisor:
    def __init__(self, settings, output, backend_factory=BlueZBackend, no_touch=False,
                 raw_touch=False, listen_seconds=0, sleep=time.sleep):
        self.settings, self.output, self.backend_factory = settings, output, backend_factory
        self.no_touch, self.raw_touch, self.listen_seconds = no_touch, raw_touch, listen_seconds
        self.sleep = sleep; self.stopping = False; self.backend = None
        self._stop_event = threading.Event()
        self.backoff = Backoff(); self.errors = ErrorGrouper(); self._old_signals = {}

    def stop(self, *_):
        self.stopping = True
        self._stop_event.set()
        if self.backend is not None:
            self.backend.cancelled = True

    def _event(self, event):
        if isinstance(event, Disconnected):
            self.output.release_all(); raise_event = getattr(self.backend, "disconnect_event", None)
            if raise_event is not None: raise_event.append(event)
            return
        if isinstance(event, ValueError):
            # Perfil A2540 rompió continuidad por informe touch no soportado.
            log.debug("TOUCH continuity_lost error=%s", event)
            self.output.navigation.cancel_touch(); return
        self.output.navigation.handle(event)

    def _install_signals(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            try: self._old_signals[signum] = signal.signal(signum, self.stop)
            except ValueError: pass

    def _restore_signals(self):
        for signum, handler in self._old_signals.items(): signal.signal(signum, handler)

    def run(self):
        self._install_signals(); started = time.monotonic()
        try:
            while not self.stopping:
                backend = None
                retry_delay = None
                timer = None
                try:
                    backend = self.backend_factory(adapter=self.settings.adapter,
                        event_handler=self._event, raw_touch=self.raw_touch)
                    self.backend = backend; backend.disconnect_event = []
                    backend.adapter()
                    path, props, profile_class = backend.resolve(self.settings.remote_identity)
                    profile = profile_class(no_touch=self.no_touch)
                    log.info("Conectando %s (%s)", profile.model, self.settings.remote_identity)
                    backend.connect(path, profile)
                    self.backoff.reset(); self.errors.reset()
                    log.info("Mando activo; escuchando eventos")
                    if hasattr(backend, "GLib"):
                        def tick():
                            if not self.stopping: self.output.navigation.tick()
                            return not self.stopping
                        timer = backend.GLib.timeout_add(10, tick)
                    next_connection_check = 0
                    while not self.stopping:
                        backend.pump(0.05)
                        if timer is None: self.output.navigation.tick()
                        if backend.disconnect_event: raise backend.disconnect_event.pop(0)
                        now = time.monotonic()
                        if now >= next_connection_check:
                            if not backend.connected(): raise Disconnected("El mando se desconectó")
                            next_connection_check = now + 1
                        if self.listen_seconds and time.monotonic() - started >= self.listen_seconds:
                            self.stopping = True
                except Exception as exc:
                    if not self.stopping:
                        self.errors.report(exc)
                        retry_delay = self.backoff.next()
                finally:
                    if timer is not None:
                        try: backend.GLib.source_remove(timer)
                        except Exception: pass
                    self.output.release_all()
                    if backend: backend.close()
                    self.backend = None
                if retry_delay is not None and not self.stopping:
                    log.info("Reintento en %d s", retry_delay)
                    if self.sleep is time.sleep:
                        self._stop_event.wait(retry_delay)
                    else:
                        self.sleep(retry_delay)
            return 0
        finally:
            self.output.release_all(); self._restore_signals()
