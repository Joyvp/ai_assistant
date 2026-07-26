# ADR-003: Phase 1 Technology Stack

- Status: Accepted
- Date: 2026-07-26

## Context

APEXIS must run across two different Python environments:

- Mac Brain Node: Python 3.12 on Intel macOS Monterey
- Pi Headquarters: Python 3.13 on 64-bit Raspberry Pi OS

The initial stack must be lightweight, understandable, testable, and
compatible with both systems.

## Decision

### Language

Phase 1 components will use Python.

The shared code will target Python 3.12 language features so it can run
on both Python 3.12 and Python 3.13.

### Pi Core API

The Pi Core will use:

- FastAPI for the versioned HTTP API
- Uvicorn as the ASGI server
- Pydantic for validated request and response schemas
- SQLite for durable local state
- Python logging with secret redaction
- systemd for production startup and recovery

### Mac client

The initial Mac client will use:

- Python 3.12
- HTTPX for API requests
- A command-line interface during Phase 1A
- macOS Keychain for the future paired-device credential

A graphical Desktop App will be selected in a separate ADR after the
protocol works and compatibility is tested on the 2015 Intel Mac.

### Shared contracts

Shared protocol schemas will be maintained independently from Pi-only
and Mac-only implementation code.

All API routes will be versioned under:

/api/v1/

### Environments and dependencies

The Mac and Pi will use separate Python virtual environments.

Dependencies will not be installed into the operating system's Python
environment.

Initial dependency management will use standard Python virtual
environments and pip for maximum compatibility.

Versions will be pinned after compatibility testing.

### Database

SQLite will be the Version 1 database.

Requirements:

- Foreign keys enabled
- WAL mode when durable tables are introduced
- Busy timeout
- Versioned migrations before storing authoritative user data
- Backups before schema changes

### Initial network test

The first API will expose only a non-sensitive health response.

It may temporarily use ordinary local HTTP for the health test because
it carries no credentials, memory, files, or private information.

Pairing, authentication, memory, and private APIs must not be enabled
until pinned HTTPS is implemented.

Default development port:

8088

No public router port-forward is permitted.

### Testing

Phase 1 will include:

- Unit tests for shared schemas
- API tests
- Mac/Pi protocol compatibility tests
- Authentication tests before pairing is enabled
- Failure and reconnect tests

### Model isolation

The Pi Core must not depend on a specific AI model or model SDK.

Model runtimes remain in the Laptop Brain package behind a replaceable
provider interface.

No model software is required for Phase 1A.

## Rejected alternatives

### Install dependencies into system Python

Rejected because it could damage or conflict with Raspberry Pi OS and
other projects.

### Docker for the first version

Deferred because it adds complexity and resource overhead before the
basic protocol is proven.

### Electron Desktop App immediately

Deferred because it may use unnecessary memory on the 8 GB Intel Mac.

### Begin with the real AI model

Rejected because networking and authentication should be testable
without model uncertainty.

## Acceptance criteria

- The Pi health API runs in an isolated virtual environment.
- The Mac client runs in a separate isolated virtual environment.
- Both sides use the same versioned response schema.
- The Mac can read the Pi health response over the local network.
- No credentials or private data are transmitted during the first test.
- Stopping the test server does not affect JoyNode.
