# ADR-002: Secure Laptop-to-Pi Pairing

- Status: Accepted
- Date: 2026-07-26

## Context

The APEXIS Desktop Agent on the Mac must communicate with APEXIS
Headquarters on the Raspberry Pi.

The connection must remain local by default, survive normal Mac sleep,
support credential revocation, and prevent unknown devices on the home
network from joining APEXIS.

The system must not store permanent credentials in source code, Git,
ordinary configuration files, browser storage, or chat logs.

## Decision

APEXIS will use local HTTPS, certificate fingerprint verification,
a short-lived one-time pairing code, and a revocable per-device token.

## Discovery

The Pi will advertise an APEXIS service using mDNS.

The Desktop Agent will attempt automatic discovery first. Manual Pi IP
entry will remain available when mDNS does not cross a router or access
point.

No public router port-forward will be created.

## Pi identity

During installation, the Pi will create a local TLS certificate and
private key.

The private key will be root-controlled and must never leave the Pi.

The Pi will expose the SHA-256 fingerprint of its certificate through
a local command and authenticated dashboard.

During first pairing, the Desktop Agent will show the fingerprint and
require the owner to compare it with the value obtained directly from
the Pi.

The Mac will pin the approved fingerprint. An unexpected certificate
change will stop the connection and require investigation or re-pairing.

## Pairing code

Pairing must be explicitly opened from the Pi.

The Pi will generate a random, human-readable code with these rules:

- Single use
- Valid for no more than 10 minutes
- Stored only as a secure hash
- Never written to ordinary logs
- Maximum of five failed attempts
- Pairing closes after success or expiration
- Creating a new code invalidates the previous code

The code grants no operating permissions by itself. It only authorizes
creation of a paired-node identity.

## Device credential

After successful pairing, the Pi will return:

- A unique node ID
- A high-entropy random device token
- Initial permission scopes
- API and protocol version information

The plaintext token is returned only once.

The Mac stores the token in macOS Keychain. It must not be stored in:

- Git
- APEXIS source files
- .env files
- shell history
- ordinary logs
- the Pi database as plaintext

The Pi stores only a secure verifier/hash for the token.

## Authentication

All requests use HTTPS.

The Desktop Agent sends the token through the Authorization header.

Initial node scopes are limited to:

- Read Pi health
- Report laptop heartbeat
- Read its own paired-node record
- Negotiate protocol versions

Memory, plugins, files, research, automation, and system-control
permissions are not granted during Phase 1A.

## Heartbeat and sleep

The Mac reports a periodic heartbeat while awake.

If heartbeats stop, the Pi marks the Brain Node offline without deleting
or revoking it.

When the Mac wakes, it reconnects with the existing Keychain credential.
Normal sleep does not require re-pairing.

The Pi queues Brain-required jobs while the Mac is offline.

## Revocation

The owner can revoke a paired node from the Pi CLI or authenticated
dashboard.

Revocation immediately blocks the device token.

A revoked Mac must complete a new one-time pairing before reconnecting.

## Audit requirements

Record:

- Pairing window created
- Successful pairing
- Failed attempts without recording the attempted code
- Credential rotation
- Node revocation
- Certificate change warnings

Do not record plaintext codes or device tokens.

## Future upgrade

Version 1 uses a high-entropy bearer token over pinned HTTPS.

A future version may add mutual TLS or device-generated asymmetric keys
without changing the higher-level node and permission model.

## Rejected alternatives

### Plain HTTP on the home network

Rejected because other local devices could observe credentials or data.

### Permanent shared password

Rejected because it cannot identify or revoke individual devices safely.

### Trust every device on the LAN

Rejected because the local network is not a complete security boundary.

### Put the token in an .env file

Rejected because ordinary files are easier to copy, expose, or commit
accidentally than macOS Keychain.

## Acceptance criteria

- The Mac can pair using one valid unexpired code.
- The same code cannot be reused.
- An incorrect certificate fingerprint prevents pairing.
- Five failed attempts close the pairing window.
- The token never appears in Git or ordinary logs.
- The Pi can revoke the Mac.
- A revoked token is rejected.
- Normal Mac sleep and wake reconnects without re-pairing.
- No APEXIS port is exposed to the public internet.
