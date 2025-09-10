docker-diff
===========

Docker Image Comparison Database Manager. Scans Docker images, stores file listings and comparisons in SQLite, and provides a simple CLI to query results.

Quick Start
-----------

- Install dependencies and project env (uv):
  - `uv sync`
- Run the CLI:
  - Local: `uv run docker_diff_pkg/cli.py list images`
  - After install: `docker-diff list images`

Database Backends
-----------------

This project supports two SQLite-compatible backends:

- Local SQLite file via Python `sqlite3` (default) at `docker_images.db`.
- Remote Turso via `libsql` Python client.

The database schema initializes automatically on first run from `docker_diff_pkg/schema.sql`.

Using Turso (libsql)
--------------------

1) Ensure dependency is installed
- The project declares `libsql-client` in `pyproject.toml`. If you updated sources, run `uv sync`.

2) Set environment variables
- URL: `TURSO_DATABASE_URL` (preferred) or `LIBSQL_URL`
- Token: `TURSO_AUTH_TOKEN` or `LIBSQL_AUTH_TOKEN`

Examples (macOS/Linux):
- Prefer HTTPS (avoids WebSocket restrictions):
  - `export TURSO_DATABASE_URL="https://<your-db>.turso.io"`
  - `export TURSO_AUTH_TOKEN="<your-token>"`
- WebSocket (Hrana) alternative:
  - `export TURSO_DATABASE_URL="libsql://<your-db>.turso.io"`
  - `export TURSO_AUTH_TOKEN="<your-token>"`

3) Run a command
- `uv run docker_diff_pkg/cli.py list images`
- or: `python -m docker_diff_pkg.cli list images`

Troubleshooting
---------------

- WebSocket handshake errors (e.g., `WsServerHandshakeError`):
  - Cause: `libsql://` uses WebSockets (Hrana). Corporate proxies/firewalls often block `wss`.
  - Fix: Switch to HTTPS: set `TURSO_DATABASE_URL="https://<db>.turso.io"` and keep the same token.
  - Optional: adjust proxies: unset `HTTP(S)_PROXY`, or set `NO_PROXY="*.turso.io,localhost,127.0.0.1"`.

- Command hangs and does not exit:
  - The libsql client runs a background event loop thread. The CLI now closes the client on exit. If embedding `DockerImageDB` in your own code, call `db.close()` when done.

- Auth failures (401/403):
  - Ensure the token belongs to the target DB and hasn’t expired.

CLI Overview
------------

- Scan images: `docker-diff scan <image...>`
- Compare images: `docker-diff compare <image...> [--name NAME]`
- List images: `docker-diff list images`
- List comparisons: `docker-diff list comparisons`
- Summary: `docker-diff summary <id>`
- Unique files: `docker-diff unique <id> [--limit N]`

Development
-----------

- Build: `make build`
- Clean: `make clean`
- Integration Test: `make integration-test`
- Add dev dep: `uv add --group dev <package>`

Notes
-----

- Python 3.10+ with type hints.
- SQLite with proper FKs and indexes; schema in `docker_diff_pkg/schema.sql`.
- DB access is abstracted to support both backends without changing CLI usage.
