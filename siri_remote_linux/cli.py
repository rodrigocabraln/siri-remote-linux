"""Interfaz de línea de comandos."""
import argparse
from dataclasses import replace
import logging
from pathlib import Path
import sys
import time

from .bluetooth.bluez import BlueZBackend, dependencies
from .bluetooth.agent import create_agent
from .config import load_settings, save_identity
from .events import ButtonEvent, TouchEvent
from .input import Keyboard
from .remotes.a2540 import A2540Profile
from .service import service_unit
from .supervisor import Supervisor

log = logging.getLogger("siri_remote")
ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.env"
TEMPLATE = ROOT / "config.env.example"


def add_runtime_options(parser):
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--raw-touch", action="store_true")
    parser.add_argument("--no-touch", action="store_true")
    parser.add_argument("--touch-step", type=float)
    parser.add_argument("--listen-seconds", type=float, default=0)


def choose(items, input_fn=input, output=print):
    if not items: raise RuntimeError("No se encontraron mandos A2540")
    if len(items) == 1: return items[0]
    output("Hay varios mandos:")
    for index, (_, props, profile) in enumerate(items, 1):
        output(f"  {index}. {profile.model} {profile.stable_identity(props)}")
    answer = input_fn("Elegí un número: ").strip()
    if not answer.isdigit() or not 1 <= int(answer) <= len(items): raise RuntimeError("Selección inválida")
    return items[int(answer) - 1]


def setup_command(args, backend_factory=BlueZBackend, input_fn=input, output=print):
    backend = backend_factory(adapter=args.adapter, event_handler=None, raw_touch=args.raw_touch)
    agent = None
    try:
        backend.adapter()
        paired = [item for item in backend.devices() if item[1].get("Paired")]
        if paired:
            selected = choose(paired, input_fn, output)
        else:
            output("Mantené Atrás + Volumen arriba durante unos 5 segundos.")
            candidates = backend.scan(args.seconds, args.min_rssi)
            selected = choose(candidates, input_fn, output)
        path, props, profile_class = selected
        agent = create_agent(backend); agent.register()
        props = backend.pair(path, agent)
        profile = profile_class(no_touch=args.no_touch)
        received = []
        backend.event_handler = lambda event: received.append(event) if isinstance(event, (ButtonEvent, TouchEvent)) else None
        backend.connect(path, profile)
        output("Conexión y GATT verificados. Pulsá un botón para verificar eventos.")
        deadline = time.monotonic() + args.verify_seconds
        while time.monotonic() < deadline and not received: backend.pump(0.1)
        if not received: raise RuntimeError("No llegaron eventos; despertá el mando y repetí setup")
        identity = profile_class.stable_identity(props)
        save_identity(args.config, identity, TEMPLATE)
        output(f"Configuración guardada en {args.config}")
        output("Probá ./siri-remote run; para instalar el servicio consultá README.md y service-unit.")
        return 0
    finally:
        if agent: agent.close()
        backend.close()


def list_command(args, backend_factory=BlueZBackend, output=print):
    backend = backend_factory(adapter=args.adapter)
    try:
        for path, adapter in backend.adapters():
            output(f"{path.rsplit('/', 1)[-1]}: {adapter.get('Address', '?')} Powered={bool(adapter.get('Powered'))}")
        for _, props, profile in backend.all_devices():
            output(f"{profile.model} {profile.stable_identity(props)} Paired={bool(props.get('Paired'))} Connected={bool(props.get('Connected'))}")
        return 0
    finally: backend.close()


def doctor_command(args, backend_factory=BlueZBackend, output=print):
    failures = []
    try: dependencies(); output("OK: D-Bus y GLib")
    except RuntimeError as exc: failures.append(str(exc))
    try:
        settings = load_settings(args.config); output("OK: configuración")
    except Exception as exc: failures.append(str(exc)); settings = None
    try:
        keyboard = Keyboard(settings or load_settings(TEMPLATE), dry_run=False); keyboard.close(); output("OK: /dev/uinput")
    except Exception as exc: failures.append(str(exc))
    backend = None
    try:
        backend = backend_factory(adapter=(settings.adapter if settings else args.adapter)); backend.adapter(); output("OK: BlueZ y adaptador")
        if settings and settings.remote_identity:
            path, props, profile = backend.resolve(settings.remote_identity); output(f"OK: vínculo {profile.model}")
            instance = profile(no_touch=args.no_touch)
            backend.connect(path, instance); output("OK: conexión y acceso GATT")
            events = []; backend.event_handler = events.append
            deadline = time.monotonic() + args.listen_seconds
            while args.listen_seconds and time.monotonic() < deadline and not events: backend.pump(.1)
            if args.listen_seconds:
                output("OK: recepción de eventos" if events else "AVISO: GATT funciona, pero no llegó un evento; pulsá un botón y repetí doctor")
    except Exception as exc: failures.append(str(exc))
    finally:
        if backend: backend.close()
    for failure in failures: output("ERROR: " + failure)
    if failures: output("Repará los errores indicados; consultá la sección Diagnóstico de README.md.")
    return 1 if failures else 0


def forget_command(args, backend_factory=BlueZBackend, input_fn=input, output=print):
    settings = load_settings(args.config) if args.config.exists() else None
    identity = (args.identity or (settings.remote_identity if settings else "")).upper()
    adapter = settings.adapter if settings else "hci0"
    backend = backend_factory(adapter=adapter)
    try:
        if identity:
            path, _, _ = backend.resolve(identity)
        else:
            path, props, profile = choose([item for item in backend.devices() if item[1].get("Paired")], input_fn, output)
            identity = profile.stable_identity(props)
        if not args.yes and input_fn(f"¿Eliminar únicamente el vínculo {identity}? [s/N] ").strip().lower() not in ("s", "si", "sí"):
            output("Cancelado"); return 0
        backend.remove(path); output(f"Vínculo {identity} eliminado")
        return 0
    finally: backend.close()


def run_command(args):
    settings = load_settings(args.config)
    if not settings.remote_identity: raise RuntimeError("Falta REMOTE_IDENTITY; ejecutá ./siri-remote setup")
    if args.touch_step is not None:
        if not 1 <= args.touch_step <= 100: raise ValueError("--touch-step debe estar entre 1 y 100")
        settings = replace(settings, touch_step=args.touch_step)
    output = Keyboard(settings, dry_run=args.dry_run)
    try:
        return Supervisor(settings, output, no_touch=args.no_touch, raw_touch=args.raw_touch,
                          listen_seconds=args.listen_seconds).run()
    finally: output.close()


def parser():
    result = argparse.ArgumentParser(prog="siri-remote", description="Apple Siri Remote como teclado Linux")
    sub = result.add_subparsers(dest="command", required=True)
    setup = sub.add_parser("setup", help="emparejar, verificar y guardar identidad"); add_runtime_options(setup)
    setup.add_argument("--adapter", default="hci0"); setup.add_argument("--seconds", type=int, default=60)
    setup.add_argument("--min-rssi", type=int, default=-75); setup.add_argument("--verify-seconds", type=int, default=15)
    run = sub.add_parser("run", help="ejecutar el supervisor persistente"); add_runtime_options(run)
    doctor = sub.add_parser("doctor", help="diagnosticar instalación"); doctor.add_argument("--config", type=Path, default=DEFAULT_CONFIG); doctor.add_argument("--adapter", default="hci0"); doctor.add_argument("--no-touch", action="store_true"); doctor.add_argument("--listen-seconds", type=float, default=5)
    listing = sub.add_parser("list", help="listar adaptador y mandos"); listing.add_argument("--adapter", default="hci0")
    forget = sub.add_parser("forget", help="eliminar un vínculo con confirmación"); forget.add_argument("identity", nargs="?"); forget.add_argument("--config", type=Path, default=DEFAULT_CONFIG); forget.add_argument("--yes", action="store_true")
    unit = sub.add_parser("service-unit", help="imprimir unidad systemd"); unit.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if getattr(args, "raw_touch", False) else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    try:
        if args.command == "setup": return setup_command(args)
        if args.command == "run": return run_command(args)
        if args.command == "doctor": return doctor_command(args)
        if args.command == "list": return list_command(args)
        if args.command == "forget": return forget_command(args)
        if args.command == "service-unit": print(service_unit(ROOT / "siri-remote", args.config)); return 0
    except KeyboardInterrupt: return 130
    except Exception as exc: log.error("%s", exc); return 1


if __name__ == "__main__": sys.exit(main())
