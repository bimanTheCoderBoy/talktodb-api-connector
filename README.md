# talktodb-api-connector

Runs a small HTTP API on your machine that reads database settings from a local config file and executes SQL you send it. A command-line tool creates and edits that config, starts or stops the API, and on Windows can register the API as a system service. Nothing in the package ships your data off the machine except SQL you choose to run against your own database.

## Install

Install from PyPI with ```pip install talktodb-api-connector```. The talktodb command is added to your PATH.

## Commands

| Command | Description |
|---------|-------------|
| talktodb config set | Set one setting using a dotted name and a value (for example database.host and localhost). |
| talktodb config ls | Show the current config as JSON; the database password is never printed. |
| talktodb api start | Check config and database driver, print the config, then start the API in a background process and remember its process id. |
| talktodb api stop | Stop the API using the saved process id. |
| talktodb service install | Windows only. Register the API as a Windows service using WinSW; the service is not started automatically. |
| talktodb service remove | Windows only. Unregister that service and remove the WinSW service definition file. |

### Manual run vs Windows service

- **talktodb api start** — Run the API yourself as a background process in your user session.
- **talktodb service install** — Register the same API with Windows so it can start at boot or from Services; each time the service starts, it reads the config file again from disk.

## Configuration

Settings live in a JSON file in your home directory named .talktodb.config. Use talktodb config set with paths such as server.port or database.host. talktodb config ls shows everything except the database password.

### Server (HTTP API)

- **server.port** — TCP port the API listens on (default 2026).
- **server.allowed_origins** — Browser Origin values allowed for cross-origin requests (CORS); default empty list. An empty list means browsers on other sites cannot call the API.

The API always binds to 127.0.0.1 on your machine. That address is not stored in the config file.

### Database

- **database.dialect** — Database kind and async driver (see supported values below); default postgresql.
- **database.host** — Database server hostname or IP; default 127.0.0.1.
- **database.port** — Database server port; default 5432.
- **database.username** — Login user name; default empty.
- **database.password** — Login password; default empty.
- **database.database** — Database name to connect to; default empty.
- **database.read_only** — When true, only read-style SQL (SELECT and similar) is allowed; writes are rejected before they run; default false.

#### Supported dialect values

The CLI maps these names to async SQLAlchemy drivers:

- postgresql or postgres
- mysql or mariadb
- sqlite
- mssql or sqlserver
- oracle

You may also set dialect to a full driver string that already includes a plus sign (for example postgresql+asyncpg). Install the matching Python driver package (such as asyncpg for PostgreSQL) before starting the API.

### Example Commands
```bash
talktodb config set database.dialect postgresql
talktodb config set database.host localhost
talktodb config set database.port 5432
talktodb config set database.username <csv_user>
talktodb config set database.password <password>
talktodb config set database.database talktodb
talktodb config set database.read_only false
talktodb config set server.port 2026
talktodb config set server.allowed_origins []
```