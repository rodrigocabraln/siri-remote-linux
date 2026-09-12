"""Generación de unidad systemd de usuario."""
from pathlib import Path


def systemd_quote(value):
    return '"' + str(value).replace("%", "%%").replace("\\", "\\\\").replace('"', '\\"') + '"'


def service_unit(executable, config):
    executable, config = Path(executable).resolve(), Path(config).resolve()
    return f"""[Unit]
Description=Siri Remote Linux
After=bluetooth.service
Wants=bluetooth.service

[Service]
Type=simple
WorkingDirectory={executable.parent}
ExecStart={systemd_quote(executable)} run --config {systemd_quote(config)}
Restart=on-failure
RestartSec=2

[Install]
WantedBy=default.target
"""
