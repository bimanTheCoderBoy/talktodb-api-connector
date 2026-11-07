"""Typer CLI for talktodb-api-connector: config and HTTP API (uvicorn) control."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, get_args, get_origin
from xml.sax.saxutils import escape

import typer
from pydantic import ValidationError

from talktodb_api_connector.config import DatabaseConfig, ServerConfig, Settings, load_settings
from talktodb_api_connector.database_registry import _async_drivername

CONFIG_PATH = Path.home() / ".talktodb.config"
PID_PATH = Path.home() / ".talktodb.pid"
SERVICE_XML_PATH = Path.home() / ".talktodb-service.xml"
SERVICE_LOG_DIR = Path.home() / ".talktodb" / "logs"

# Valid keys under ``server`` / ``database`` in ~/.talktodb.config (must match :class:`ServerConfig` / :class:`DatabaseConfig`).
_SERVER_KEYS = frozenset(ServerConfig.model_fields.keys())
_DATABASE_KEYS = frozenset(DatabaseConfig.model_fields.keys())

# Async SQLAlchemy driver suffix -> PyPI / import name (import matches package for these drivers).
_ASYNC_DRIVER_SUFFIX_TO_PACKAGE: dict[str, str] = {
    "asyncpg": "asyncpg",
    "aiomysql": "aiomysql",
    "aiosqlite": "aiosqlite",
    "aioodbc": "aioodbc",
    "oracledb": "oracledb",
}


def _pip_package_for_async_drivername(drivername: str) -> str:
    """
    Map a SQLAlchemy ``dialect+driver`` string (from :func:`_async_drivername`) to a ``pip install`` target.

    Unknown ``+suffix`` falls back to the suffix (with ``-`` replaced by ``_`` only for import; pip uses suffix).
    """
    if "+" not in drivername:
        return drivername
    suffix = drivername.split("+", 1)[1].strip()
    return _ASYNC_DRIVER_SUFFIX_TO_PACKAGE.get(suffix.lower(), suffix)


def _import_module_name_for_package(package: str) -> str:
    """Import path for ``importlib``; usually the package name with hyphens as underscores."""
    return package.replace("-", "_")


def _ensure_async_driver_importable(settings: Settings) -> None:
    """Exit with a clear message if the async DBAPI for the configured dialect cannot be imported."""
    drivername = _async_drivername(settings.database.dialect)
    pip_package = _pip_package_for_async_drivername(drivername)
    mod_name = _import_module_name_for_package(pip_package)
    try:
        importlib.import_module(mod_name)
    except ImportError:
        typer.echo(
            "The async database driver for your configured dialect is not installed.\n"
            f"  database.dialect: {settings.database.dialect!r}\n"
            f"  Resolved SQLAlchemy driver: {drivername!r}\n"
            f"  Required Python module: {mod_name!r}\n\n"
            f"Install it with:\n"
            f"  pip install {pip_package}\n\n"
            "Run that command, then retry:\n"
            "  talktodb api start",
            err=True,
        )
        raise typer.Exit(code=1)

app = typer.Typer(help="TalkToDB API connector — configuration and API process commands.")
config_app = typer.Typer(help="Read and write ~/.talktodb.config (JSON).")
api_app = typer.Typer(help="Start or stop the HTTP API (uvicorn).")
service_app = typer.Typer(help="Windows service (WinSW): install or remove.")

app.add_typer(config_app, name="config")
app.add_typer(api_app, name="api")
app.add_typer(service_app, name="service")


def _default_config_data() -> dict[str, Any]:
    """Default nested on-disk template when the file is missing."""
    return {
        "server": {"port": 2026, "allowed_origins": []},
        "database": {
            "dialect": "postgresql",
            "host": "127.0.0.1",
            "port": 5432,
            "username": "",
            "password": "",
            "database": "",
            "read_only": False,
        },
        "service_enabled": False,
    }


def _read_config_file() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON in {CONFIG_PATH}: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(data, dict):
        typer.echo(f"Config file must be a JSON object: {CONFIG_PATH}", err=True)
        raise typer.Exit(code=1)
    return data


def _write_config_file(data: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _set_service_enabled(enabled: bool) -> None:
    data = _read_config_file() if CONFIG_PATH.is_file() else _default_config_data()
    if not data:
        data = _default_config_data()
    data["service_enabled"] = enabled
    try:
        Settings.model_validate(data)
    except ValidationError as exc:
        typer.echo(f"Invalid config after service flag update:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc
    _write_config_file(data)


def _require_windows() -> None:
    if os.name != "nt":
        typer.echo("talktodb service is only supported on Windows.", err=True)
        raise typer.Exit(code=1)


def _find_winsw_exe() -> str:
    found = shutil.which("winsw") or shutil.which("winsw.exe")
    if not found:
        typer.echo(
            "winsw.exe was not found on PATH. Install WinSW (https://github.com/winsw/winsw) "
            "and ensure winsw.exe is on PATH.",
            err=True,
        )
        raise typer.Exit(code=1)
    return found


def _echo_winsw_failure(proc: subprocess.CompletedProcess[str], action: str) -> None:
    typer.echo(f"WinSW {action} failed (exit code {proc.returncode}).", err=True)
    if proc.stdout and proc.stdout.strip():
        typer.echo("--- WinSW stdout ---", err=True)
        typer.echo(proc.stdout.rstrip(), err=True)
    if proc.stderr and proc.stderr.strip():
        typer.echo("--- WinSW stderr ---", err=True)
        typer.echo(proc.stderr.rstrip(), err=True)


def _run_winsw(winsw: str, subcommand: str, xml_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [winsw, subcommand, str(xml_path)],
        capture_output=True,
        text=True,
        check=False,
    )


def _uvicorn_service_arguments(port: int) -> str:
    return (
        "-m uvicorn talktodb_api_connector.api.main:app "
        f"--host 127.0.0.1 --port {port}"
    )


def _build_winsw_xml(*, executable: str, arguments: str, log_dir: Path) -> str:
    """WinSW service definition (see https://github.com/winsw/winsw)."""
    log_path = str(log_dir.resolve())
    return f"""<?xml version="1.0" encoding="utf-8"?>
<service>
  <id>talktodb-api-connector</id>
  <name>TalkToDB API Connector</name>
  <description>talktodb-api-connector HTTP API (uvicorn)</description>
  <executable>{escape(executable)}</executable>
  <arguments>{escape(arguments)}</arguments>
  <logpath>{escape(log_path)}</logpath>
  <log mode="roll-by-size">
    <sizeThreshold>10485760</sizeThreshold>
    <keepFiles>8</keepFiles>
  </log>
  <startmode>Automatic</startmode>
</service>
"""


def _parse_config_path(path: str) -> tuple[str, str]:
    """Parse ``server.port`` / ``database.host`` into ``(section, key)``."""
    parts = path.strip().lower().split(".")
    if len(parts) != 2:
        raise ValueError(
            f"Unknown config path {path!r}. Use two-part dotted paths only, e.g. "
            "server.port, server.allowed_origins, database.dialect, database.host, database.port, "
            "database.username, database.password, database.database, database.read_only."
        )
    section, key = parts[0], parts[1]
    if section == "server" and key in _SERVER_KEYS:
        return section, key
    if section == "database" and key in _DATABASE_KEYS:
        return section, key
    allowed = ", ".join(sorted(_SERVER_KEYS)) if section == "server" else ", ".join(sorted(_DATABASE_KEYS))
    raise ValueError(
        f"Unknown config path {path!r}. For {section!r} the allowed keys are: {allowed}."
    )


def _set_at_path(nested: dict[str, Any], path: str, value: Any) -> None:
    section, key = _parse_config_path(path)
    nested.setdefault(section, {})
    nested[section][key] = value


def _parse_field_value(section: str, key: str, raw: str) -> Any:
    """Coerce CLI string to the type of ``ServerConfig.<key>`` or ``DatabaseConfig.<key>``."""
    model = ServerConfig if section == "server" else DatabaseConfig
    if key not in model.model_fields:
        typer.echo(
            f"Unknown field {key!r} under {section!r}.",
            err=True,
        )
        raise typer.Exit(code=1)

    ann = model.model_fields[key].annotation
    origin = get_origin(ann)
    args = get_args(ann)

    if ann is bool:
        low = raw.strip().lower()
        if low in {"1", "true", "yes", "on"}:
            return True
        if low in {"0", "false", "no", "off"}:
            return False
            typer.echo(f"Invalid boolean for {section}.{key}: {raw!r}", err=True)
            raise typer.Exit(code=1)

    if ann is int:
        try:
            return int(raw.strip(), 10)
        except ValueError:
            typer.echo(f"Invalid integer for {section}.{key}: {raw!r}", err=True)
            raise typer.Exit(code=1)

    if origin is list and args and args[0] is str:
        stripped = raw.strip()
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            if not isinstance(parsed, list):
                typer.echo(f"Expected a JSON array for {section}.{key}", err=True)
                raise typer.Exit(code=1)
            return [str(x) for x in parsed]
        return [p.strip() for p in stripped.split(",") if p.strip()]

    return raw


@config_app.command("set")
def config_set(field: str, value: str) -> None:
    """Set one value using dotted paths (e.g. server.port, database.host, database.read_only)."""
    path = field.strip()
    try:
        section, key = _parse_config_path(path)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    merged = _read_config_file() if CONFIG_PATH.is_file() else _default_config_data()
    try:
        coerced = _parse_field_value(section, key, value)
        _set_at_path(merged, path, coerced)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1)

    try:
        Settings.model_validate(merged)
    except ValidationError as exc:
        typer.echo(f"Invalid settings after update:\n{exc}", err=True)
        raise typer.Exit(code=1) from exc

    _write_config_file(merged)
    typer.echo(f"Updated {path!r} in {CONFIG_PATH}")


def _strip_password_from_nested(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k == "database" and isinstance(v, dict):
                db = {bk: bv for bk, bv in v.items() if bk != "password"}
                out[k] = _strip_password_from_nested(db)
                continue
            out[k] = _strip_password_from_nested(v)
        return out
    if isinstance(obj, list):
        return [_strip_password_from_nested(x) for x in obj]
    return obj


@config_app.command("ls")
def config_ls() -> None:
    """Print the nested JSON config as stored on disk (password omitted under database)."""
    if not CONFIG_PATH.is_file():
        typer.echo(f"Config file not found: {CONFIG_PATH}", err=True)
        raise typer.Exit(code=1)
    try:
        raw_text = CONFIG_PATH.read_text(encoding="utf-8")
        data = json.loads(raw_text)
    except (OSError, json.JSONDecodeError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if not isinstance(data, dict):
        typer.echo("Config file must be a JSON object.", err=True)
        raise typer.Exit(code=1)
    try:
        Settings.model_validate(data)
    except (ValidationError, ValueError) as exc:
        typer.echo(f"Invalid config file: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    redacted = _strip_password_from_nested(data)
    typer.echo(json.dumps(redacted, indent=2, ensure_ascii=False))


def _pid_running(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(pid) in (out.stdout or "")
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


@service_app.command("install")
def service_install() -> None:
    """Register a Windows service (WinSW) for uvicorn; does not start the service."""
    _require_windows()
    winsw = _find_winsw_exe()
    try:
        settings = load_settings()
    except FileNotFoundError:
        typer.echo(
            f"Config file not found at {CONFIG_PATH}. "
            "Run 'talktodb config set <field> <value>' to create it first.",
            err=True,
        )
        raise typer.Exit(code=1)
    except (ValueError, OSError) as exc:
        typer.echo(f"Config file is missing or invalid: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    SERVICE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    arguments = _uvicorn_service_arguments(settings.server.port)
    xml_text = _build_winsw_xml(
        executable=sys.executable,
        arguments=arguments,
        log_dir=SERVICE_LOG_DIR,
    )
    SERVICE_XML_PATH.write_text(xml_text, encoding="utf-8")

    proc = _run_winsw(winsw, "install", SERVICE_XML_PATH)
    if proc.returncode != 0:
        _echo_winsw_failure(proc, "install")
        raise typer.Exit(code=1)

    _set_service_enabled(True)
    typer.echo(
        f"Registered Windows service using {SERVICE_XML_PATH} (not started). "
        f"Start it from Windows Services or run: {winsw} start {SERVICE_XML_PATH}"
    )


@service_app.command("remove")
def service_remove() -> None:
    """Unregister the WinSW service and delete ~/.talktodb-service.xml."""
    _require_windows()
    winsw = _find_winsw_exe()

    if not SERVICE_XML_PATH.is_file():
        typer.echo(f"No service XML at {SERVICE_XML_PATH}; unsetting service_enabled only.")
        _set_service_enabled(False)
        typer.echo("service_enabled set to false in config.")
        raise typer.Exit(code=0)

    proc = _run_winsw(winsw, "uninstall", SERVICE_XML_PATH)
    if proc.returncode != 0:
        _echo_winsw_failure(proc, "uninstall")
        raise typer.Exit(code=1)

    SERVICE_XML_PATH.unlink(missing_ok=True)
    _set_service_enabled(False)
    typer.echo("Windows service removed and service XML deleted; service_enabled=false in config.")


@api_app.command("start")
def server_start() -> None:
    """Start uvicorn in the background and record PID in ~/.talktodb.pid."""
    if PID_PATH.is_file():
        try:
            old_pid = int(PID_PATH.read_text(encoding="utf-8").strip())
        except ValueError:
            old_pid = None
        if old_pid is not None and _pid_running(old_pid):
            typer.echo(
                f"Server already running (PID {old_pid}). Stop it first or delete {PID_PATH}.",
                err=True,
            )
            raise typer.Exit(code=1)

    try:
        settings = load_settings()
    except FileNotFoundError:
        typer.echo(
            f"Config file not found at {CONFIG_PATH}. "
            "Run 'talktodb config set <field> <value>' to create it first.",
            err=True,
        )
        raise typer.Exit(code=1)
    except (ValueError, OSError) as exc:
        typer.echo(
            f"Config file is missing or invalid: {exc}",
            err=True,
        )
        raise typer.Exit(code=1) from exc

    _ensure_async_driver_importable(settings)

    public = settings.model_dump(mode="json")
    typer.echo("Starting server with this configuration:")
    typer.echo(json.dumps(public, indent=2, ensure_ascii=False))

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "talktodb_api_connector.api.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(settings.server.port),
    ]
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        creationflags=creationflags,
    )
    PID_PATH.write_text(str(proc.pid) + "\n", encoding="utf-8")
    typer.echo(
        f"Started server PID {proc.pid} (127.0.0.1:{settings.server.port}). "
        f"PID file: {PID_PATH}"
    )


@api_app.command("stop")
def server_stop() -> None:
    """Stop the server using the PID stored in ~/.talktodb.pid."""
    if not PID_PATH.is_file():
        typer.echo(f"No PID file at {PID_PATH}; nothing to stop.", err=True)
        raise typer.Exit(code=1)
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except ValueError:
        typer.echo(f"Invalid PID file: {PID_PATH}", err=True)
        raise typer.Exit(code=1)

    if not _pid_running(pid):
        typer.echo(f"Process {pid} not running; removing stale PID file.")
        PID_PATH.unlink(missing_ok=True)
        raise typer.Exit(code=0)

    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=False)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            typer.echo(f"Failed to signal PID {pid}: {exc}", err=True)
            raise typer.Exit(code=1) from exc

    PID_PATH.unlink(missing_ok=True)
    typer.echo(f"Stopped server (PID {pid}).")


def main() -> None:
    """Console script entry — invokes the Typer app."""
    app()


if __name__ == "__main__":
    main()
