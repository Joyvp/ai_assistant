# ADR-001: Distributed Laptop Brain and Pi Headquarters

- Status: Accepted
- Date: 2026-07-26

## Context

APEXIS must remain useful on existing hardware while keeping durable
memory, knowledge, projects, research, and automation under the owner's
control.

The available laptop is a 2015 Intel MacBook Air with 8 GB RAM. It is
the interactive computer and has access to the owner's approved local
files.

The Raspberry Pi 5 has 4 GB RAM and can remain online continuously.

## Decision

APEXIS will use a distributed architecture.

The laptop is the Brain Node and Workstation. It runs:

- The APEXIS Desktop App
- The primary replaceable AI model
- Interactive task planning
- Local file plugins
- Heavy or user-facing plugins

The Raspberry Pi is Headquarters and the system of record. It runs:

- The APEXIS Core API
- Memory and project databases
- The knowledge base
- Research collection
- Durable jobs and scheduling
- Automation coordination
- Dashboard and system monitoring
- Plugin and node registry
- Audit logs
- Authorized server-management coordination

The laptop and Pi communicate through a secure, versioned, authenticated
local-network protocol.

## Consequences

### Benefits

- A new laptop can reconnect to the existing Pi data.
- Models can be replaced without losing APEXIS knowledge.
- The Pi continues operating while the laptop sleeps.
- Personal laptop files remain locally controlled.
- Heavy work stays off the Pi.
- APEXIS can degrade gracefully when either node is unavailable.

### Costs

- Secure pairing and synchronization are required.
- Offline behavior must be designed explicitly.
- Distributed debugging is more complex than a single application.
- Brain-required jobs must wait while the laptop is asleep.
- Backups are required because the Pi becomes authoritative.

## Rejected alternatives

### Run everything on the Pi

Rejected because the Pi cannot efficiently run the desired large models
or heavy desktop plugins.

### Store everything only on the laptop

Rejected because the laptop sleeps and may eventually be replaced.

### Give the Pi unrestricted laptop access

Rejected because it would violate privacy and expose other macOS users'
files.
