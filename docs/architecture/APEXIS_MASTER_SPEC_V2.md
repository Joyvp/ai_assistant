# APEXIS Master Specification — Distributed Architecture

**Document version:** 2.0  
**Status:** Phase 0 — planning and architecture  
**Supersedes:** `APEXIS_MASTER_SPEC.md` version 1.0 where deployment boundaries conflict  
**Project:** APEXIS — Adaptive Processing and Extensible Intelligence System

---

# 1. Authoritative Architecture Statement

APEXIS is a distributed personal-assistant platform with two primary nodes:

- **Laptop = Brain Node and Workstation**
- **Raspberry Pi = Headquarters, Core Server, and System of Record**

The laptop performs interactive AI inference, heavy computation, and local-file operations. The Pi remains online, stores durable state, hosts shared services, schedules work, monitors systems, and coordinates nodes.

```text
                        USER
                          │
                          ▼
               APEXIS Desktop App
                 on the Laptop
                          │
             ┌────────────┴────────────┐
             │                         │
             ▼                         ▼
       Local Brain Runtime       Local/Heavy Plugins
       LLM + task planning       Files, PDF, CAD,
                                 image/video, apps
             │                         │
             └────────────┬────────────┘
                          │ Authenticated API
                          ▼
               Raspberry Pi Headquarters
             ┌─────────────────────────────┐
             │ APEXIS Core API             │
             │ Memory and Projects         │
             │ Knowledge Base              │
             │ Research Collector          │
             │ Job Queue and Scheduler     │
             │ Plugin/Node Registry        │
             │ Dashboard Server            │
             │ Logs and Audit              │
             │ Automation                  │
             │ Server Manager              │
             └─────────────────────────────┘
```

The Pi is not merely “part of APEXIS.” It is the headquarters and authoritative control plane. The laptop is the primary interactive intelligence and execution node.

---

# 2. Instructions for Any AI or Developer

- Treat this Version 2 document as authoritative.
- Do not use Pi-only assumptions from the superseded Version 1 document.
- Work one approved phase at a time.
- Do not write production code until the Phase 0 decisions are approved.
- Explain architecture, commands, and risks before changing either device.
- Preserve JoyNode until the owner explicitly authorizes migration or removal.
- Never store passwords, model keys, tokens, private memories, or personal documents in Git or chat.
- Never give the LLM direct operating-system, filesystem, hardware, or network control.
- The LLM proposes structured actions; APEXIS validates and executes them through scoped plugins.
- Do not expose the Pi dashboard, APIs, model endpoint, server management, or file plugins directly to the public internet.
- Never assume the Pi can use the laptop’s model while the laptop is asleep.
- Maintain `STATUS.md`, `CHANGELOG.md`, and Architecture Decision Records.
- Back up known-good state before migrations.

---

# 3. Roles and Responsibilities

## 3.1 Laptop — Brain Node and Workstation

### Runs on the laptop

- APEXIS Desktop App
- Primary LLM/model runtime
- Interactive task planner
- Chat user experience
- Voice input/output later
- Local notifications
- Laptop capability agent
- Local file plugins
- Heavy plugins
- Optional encrypted working cache

### Laptop plugin examples

- Desktop/Documents/Downloads search
- Local PDF reading
- Open or reveal a file
- Folder organization with confirmation
- Image generation
- Image/video processing
- CAD analysis
- Code workspace assistance
- Local application integration

### Laptop privacy rule

The Pi does not receive unrestricted access to the laptop’s filesystem. A local plugin reads approved paths and returns only the result needed for the current task. Uploading a file, extracted text, or derived knowledge to the Pi requires an explicit policy and, when sensitive, user confirmation.

## 3.2 Raspberry Pi — Headquarters and Core Server

### Runs on the Pi

- APEXIS Core API
- User authentication
- Device/node pairing
- Memory database
- Project database
- Knowledge base
- Source documents approved for Pi storage
- Research collection workers
- Durable job queue
- Scheduler
- Automation coordinator
- Plugin and node registry
- Dashboard server
- Logs and audit records
- Notifications coordinator
- System monitoring
- Brother’s server-manager integration
- Backup and restore services

### Pi authority

The Pi is the authoritative source for:

- projects;
- long-term memory;
- knowledge metadata;
- research jobs;
- automations;
- plugin permissions;
- paired nodes;
- audit records;
- global settings.

A new laptop can install APEXIS Desktop, securely pair with the Pi, and regain approved projects, memory, knowledge, settings, and job history.

---

# 4. Placement Matrix

| Capability | Laptop | Pi Headquarters | Notes |
|---|---:|---:|---|
| Primary LLM inference | ✅ | Optional | Pi fallback is a later decision |
| Desktop chat UI | ✅ | No | Daily control center |
| Web dashboard | Optional client | ✅ | Available to phone/tablet/laptop |
| Local files | ✅ | ❌ by default | Pi never receives blanket access |
| Heavy image/video/CAD | ✅ | ❌ | Laptop CPU/GPU workload |
| Long-term memory | Cache only | ✅ | Pi is authoritative |
| Projects | Cache/view | ✅ | Versioned and backed up |
| Knowledge base | Query/cache | ✅ | Pi is authoritative |
| Research fetching | Optional | ✅ | Pi runs scheduled collection |
| Research summarization | ✅ when online | TBD fallback | See Section 8 |
| Scheduler | Client controls | ✅ | Pi survives laptop sleep |
| Logs/audit | Local temporary logs | ✅ global audit | Secrets redacted |
| Server manager | UI/commands | ✅ coordinator | Remote endpoint still required |
| Voice | ✅ | Optional future satellite | Disabled initially |
| Vision/camera | ✅ initially | Optional future | Explicit permission |

---

# 5. Core Communication Model

## 5.1 Connection

Laptop and Pi communicate using a versioned HTTPS API plus a persistent authenticated channel for events/jobs.

Recommended logical channels:

- REST/JSON for ordinary CRUD operations
- WebSocket for node heartbeat, capability updates, job dispatch, and streaming events
- Server-Sent Events may be used for dashboard-only streams if selected by ADR

## 5.2 Device pairing

The first laptop must be paired with the Pi through a short-lived, one-time code shown locally on the Pi dashboard.

After pairing:

- Each node has a unique ID.
- Each node receives a revocable credential.
- Credentials are stored in the operating system’s secure credential store where possible.
- The Pi records node name, platform, application version, last seen, and approved capabilities.
- Re-pairing or credential rotation requires owner approval.

## 5.3 Laptop heartbeat and capabilities

The laptop sends a periodic heartbeat:

```json
{
  "node_id": "laptop-primary",
  "status": "online",
  "brain": {
    "available": true,
    "provider": "local-or-approved-provider",
    "capabilities": ["chat", "tools", "vision"]
  },
  "plugins": [
    "local_file_search",
    "pdf_reader",
    "image_processing"
  ]
}
```

The Pi must never dispatch a job to a capability the laptop did not advertise and the owner did not approve.

## 5.4 Offline behavior

### Pi online, laptop offline

Available:

- Dashboard
- Memory/knowledge browsing
- Job scheduling
- Research collection
- Source downloads
- Document parsing and indexing
- Server monitoring
- Notifications
- Automations that do not require the brain

Unavailable or queued:

- Primary-model chat
- Heavy local plugins
- Laptop file access
- Heavy summarization unless an approved fallback model exists

### Laptop online, Pi offline

Available in limited mode:

- Local chat with the laptop model
- Local plugins
- Temporary local conversation context

Unavailable or queued:

- Authoritative long-term memory
- Knowledge updates
- Global audit upload
- Pi automations and server services

The desktop may queue approved synchronization records, but it must show a clear **Headquarters Offline** state and avoid pretending data was permanently saved.

### Both online

Full APEXIS functionality is available.

---

# 6. Interactive Chat Flow

```text
User enters request in Desktop App
  ↓
Desktop requests project/memory/knowledge context from Pi
  ↓
Pi returns only authorized, relevant context with IDs/provenance
  ↓
Laptop Brain processes the request locally
  ↓
Brain proposes plugin call if needed
  ↓
Desktop policy engine validates local action
  ↓
Pi policy is consulted for global/high-risk actions
  ↓
User confirms when required
  ↓
Plugin runs on its assigned node
  ↓
Result returns to Laptop Brain
  ↓
Brain creates final response
  ↓
Desktop displays response
  ↓
Approved conversation, memory, artifacts, and audit metadata sync to Pi
```

The model is not APEXIS Core. It is a replaceable Brain Provider running in the Laptop Brain Runtime.

---

# 7. Distributed Plugin System

Every plugin declares where it can execute.

## 7.1 Execution targets

- `desktop` — local files, applications, GPU, microphone, camera
- `core` — Pi knowledge, memory, research, scheduling, monitoring
- `remote` — authorized external agent or management endpoint
- `either` — portable plugin that may run on an approved available node

## 7.2 Example manifest

```toml
id = "local_pdf_reader"
name = "Local PDF Reader"
version = "0.1.0"
api_version = "1"
execution_target = "desktop"
entrypoint = "plugin:PDFReaderPlugin"
timeout_seconds = 30
max_output_bytes = 262144

permissions = [
  "filesystem.read:user_selected_files"
]
```

Pi research plugin:

```toml
id = "research_fetcher"
name = "Research Fetcher"
version = "0.1.0"
api_version = "1"
execution_target = "core"
entrypoint = "plugin:ResearchFetcherPlugin"
timeout_seconds = 60

permissions = [
  "network.https:approved_sources",
  "knowledge.propose"
]
```

## 7.3 Plugin execution rules

- The Pi owns the global registry and approved permission state.
- Each node owns its local plugin runtime.
- A node may refuse a job if its local policy is stricter.
- Desktop file plugins cannot be invoked while the desktop is offline.
- Local file contents are not copied to the Pi unless explicitly approved.
- Third-party plugins run out of process with time and output limits.
- No generic shell plugin in initial versions.
- Destructive operations always require confirmation.

---

# 8. Research While the Laptop Sleeps

This is the key distributed-design constraint.

## 8.1 Tasks the Pi can perform without an LLM

- Run schedules
- Search approved indexes/APIs
- Fetch approved pages and PDFs
- Extract text
- Calculate hashes
- Detect duplicates and changed versions
- Split documents into sections/chunks
- Extract basic metadata
- Store source documents
- Build lexical indexes
- Queue items for review/summarization

## 8.2 LLM summarization modes

APEXIS must support replaceable modes:

### Mode A — Queue for laptop brain (recommended initial mode)

The Pi collects and prepares documents. When the laptop reconnects, it receives bounded summarization jobs. Results return to the Pi knowledge-review queue.

Advantages:

- Uses the strongest local model
- No cloud cost
- Keeps model work on the laptop

Tradeoff:

- Summaries wait until the laptop is online

### Mode B — Approved cloud fallback

The Pi sends approved, size-limited text to an approved cloud model.

Requirements:

- Explicit provider approval
- Cost limits
- Privacy classification
- Secret management
- Redaction rules
- Audit trail

### Mode C — Small Pi-local fallback

The Pi runs a small quantized model for limited classification/summarization.

Requirements:

- Benchmark on the 4 GB Pi
- Strict memory and temperature limits
- Quality evaluation
- No assumption that it matches the laptop model

## 8.3 Required user-visible states

Research items show one of:

```text
Collected
Parsed
Waiting for Brain
Summarizing
Ready for Review
Added to Knowledge
Rejected
Failed
```

The Pi must never claim a document was intelligently summarized when it was only collected or parsed.

---

# 9. Memory and Knowledge Ownership

## 9.1 Memory

The Pi stores the authoritative memory database.

Memory categories:

- projects;
- preferences;
- notes;
- people;
- decisions;
- conversation summaries;
- system state.

Rules:

- Long-term memory is explicit or governed by a visible policy.
- Sensitive memory requires confirmation.
- Memories include provenance and timestamps.
- Owner can view, edit, export, archive, expire, and delete.
- Laptop caches are not authoritative and must be encrypted or easily clearable.

## 9.2 Knowledge

The Pi stores:

- approved source documents;
- document versions;
- hashes;
- extracted text;
- chunks;
- source metadata;
- citations;
- summaries;
- topic tags;
- freshness and trust status.

Local laptop documents remain local unless the owner chooses **Add to APEXIS Knowledge**.

---

# 10. Dashboard and Desktop App

## 10.1 Desktop App

Primary daily interface:

- Chat
- Model status and selection
- Pi connection state
- Local plugin activity
- Tool confirmation dialogs
- Local file picker
- Voice later
- Local notifications
- Offline mode
- Sync status

The Desktop App must not be a simple remote browser wrapper if local file and heavy plugin integration are required. The exact framework remains a Phase 0 decision.

## 10.2 Pi Dashboard

Available at a local address such as:

```text
https://apexis.local
```

Pages:

- Headquarters overview
- Memory and projects
- Knowledge
- Research queue
- Plugins and nodes
- Automations
- Brother’s server manager
- System status
- Logs and audit
- Settings

Chat from the Pi dashboard is optional in Version 1. If enabled, it must clearly show whether the Laptop Brain is online. It may forward chat to the paired laptop or use an explicitly approved fallback provider.

---

# 11. Security Boundaries

## 11.1 Trust zones

```text
Zone 1: Pi Core and private database
Zone 2: Paired Laptop Brain Node
Zone 3: Reviewed plugins
Zone 4: Untrusted web/PDF content
Zone 5: Remote server-management endpoints
Zone 6: Other LAN clients
```

Cross-zone communication requires authenticated, validated interfaces.

## 11.2 Required protections

- Dedicated unprivileged Linux user on Pi
- Local authentication
- Argon2id password hashing
- HTTPS/local TLS where practical
- CSRF protection
- Rate limits
- Per-node revocable credentials
- Plugin capability permissions
- Confirmation for destructive/sensitive actions
- Structured audit log
- Secret redaction
- No public port forwarding
- Remote access through an approved VPN
- File path sandboxing
- SSRF and prompt-injection defenses

## 11.3 Laptop file security

- User-selected files or approved roots only
- Read-only by default
- Canonical path validation
- No silent background scanning of the entire laptop
- No upload to Pi without policy/confirmation
- Overwrite, move, and delete require confirmation and recovery strategy

---

# 12. Brother’s Server Manager Boundary

The Pi headquarters may provide the dashboard, authorization, scheduling, logs, and command coordination. However, the Pi is located at the family home while the servers are elsewhere.

Therefore, remote power control requires one of:

- BIOS automatic power-on after AC restoration;
- server BMC such as iDRAC/iLO/IPMI reachable through an authorized VPN;
- Wake-on-LAN through a remote VPN-capable router/controller;
- a separate authorized endpoint physically located with the servers.

```text
APEXIS Pi Headquarters
  ↓ authenticated request over private VPN
Remote authorized management endpoint
  ↓
Server power-on
```

The Server Manager must be independently tested before APEXIS integration. APEXIS must not expose BMC interfaces or power-control pages publicly. Power-on may be allowed; hard power-off must require exceptional confirmation and should normally be omitted.

---

# 13. Repository Layout

A monorepo can contain both shared contracts and node applications.

```text
APEXIS/
├── README.md
├── STATUS.md
├── CHANGELOG.md
├── pyproject.toml
├── docs/
│   ├── architecture/
│   ├── adr/
│   ├── threat_model.md
│   ├── protocol.md
│   └── plugin_api.md
├── shared/
│   ├── schemas/
│   ├── protocol/
│   └── plugin_sdk/
├── core_server/
│   └── src/apexis_core/
│       ├── api/
│       ├── auth/
│       ├── nodes/
│       ├── jobs/
│       ├── scheduler/
│       ├── memory/
│       ├── knowledge/
│       ├── research/
│       ├── plugins/
│       ├── automation/
│       ├── server_manager/
│       ├── storage/
│       └── observability/
├── desktop_app/
│   ├── ui/
│   └── runtime/
│       ├── brain/
│       ├── orchestrator/
│       ├── core_client/
│       ├── local_plugins/
│       ├── permissions/
│       └── cache/
├── plugins/
│   ├── core/
│   ├── desktop/
│   └── examples/
├── deploy/
│   ├── raspberry_pi/
│   └── desktop/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── protocol/
│   ├── end_to_end/
│   └── security/
└── scripts/
```

## Pi runtime data

```text
/opt/apexis-core/
/etc/apexis-core/
/etc/apexis-core/secrets.env
/var/lib/apexis/
/var/lib/apexis/apexis.db
/var/lib/apexis/knowledge/
/var/lib/apexis/documents/
/var/lib/apexis/artifacts/
/var/lib/apexis/backups/
/var/log/apexis/
/run/apexis/
```

## Laptop runtime data

Use platform-native application-support, cache, and credential-store locations. Exact paths depend on macOS/Windows/Linux and the selected desktop framework. Local cache must be disposable without destroying authoritative Pi data.

---

# 14. Revised Development Order

## Phase 0 — Distributed architecture

- [x] Define Laptop Brain / Pi Headquarters split
- [x] Define offline-state behavior
- [x] Define distributed plugin targets
- [x] Identify background summarization constraint
- [ ] Confirm laptop OS, CPU, RAM, and GPU
- [ ] Select local model runtime/provider
- [ ] Select desktop application framework
- [ ] Select Pi API/dashboard stack
- [ ] Select HTTPS and pairing design
- [ ] Select Mode A/B/C for sleeping-laptop research
- [ ] Confirm whether JoyNode remains installed
- [ ] Create UI wireframes
- [ ] Create protocol schemas
- [ ] Create threat model
- [ ] Approve Version 1 scope

## Phase 1 — Core protocol skeleton

Goal: prove secure Laptop ↔ Pi communication before adding AI.

- [ ] Pi core health API
- [ ] Laptop CLI client
- [ ] One-time pairing
- [ ] Revocable node credentials
- [ ] Heartbeat and online/offline status
- [ ] Version negotiation
- [ ] Structured events
- [ ] Tests for disconnect/reconnect

Acceptance:

- Laptop pairs once and reconnects securely.
- Pi dashboard shows laptop online/offline.
- Revoking the laptop blocks future requests.
- No model, memory, or plugins yet.

## Phase 2 — Laptop Brain chat

- [ ] Replaceable Brain Provider interface
- [ ] Mock provider
- [ ] One approved model runtime/provider on laptop
- [ ] Desktop/temporary CLI chat
- [ ] Streaming, timeout, cancellation
- [ ] No tools or long-term memory
- [ ] Pi records only approved operational metadata

Acceptance:

- Chat works while laptop is online.
- Changing provider does not change Pi APIs.
- Pi never executes model-generated code.

## Phase 3 — Desktop App and Pi dashboard

- [ ] Desktop chat application
- [ ] Headquarters connection indicator
- [ ] Pi admin dashboard
- [ ] Authentication
- [ ] Node status
- [ ] Placeholder module navigation
- [ ] Offline mode

## Phase 4 — Distributed plugin manager

- [ ] Global registry on Pi
- [ ] Local runtime on laptop
- [ ] Execution target in manifest
- [ ] Capability advertisements
- [ ] Permission and confirmation flow
- [ ] Calculator reference plugin
- [ ] Error isolation and limits

## Phase 5 — Memory and projects on Pi

- [ ] Authoritative memory/project database
- [ ] Explicit save, retrieve, edit, delete, export
- [ ] Desktop retrieval and sync
- [ ] “Continue JoyNode” test
- [ ] Offline queue behavior

## Phase 6 — Local file and PDF plugins

- [ ] User-selected file access
- [ ] Local PDF extraction
- [ ] No blanket Pi access
- [ ] Optional approved “Add to Knowledge” flow
- [ ] Path and symlink security tests

## Phase 7 — Research collector and knowledge base

- [ ] Durable research jobs on Pi
- [ ] Search/fetch policies
- [ ] HTML/PDF parsing
- [ ] Provenance and citations
- [ ] Deduplication/versioning
- [ ] “Waiting for Brain” queue
- [ ] Laptop summarization worker
- [ ] Human review before knowledge ingestion

## Phase 8 — Scheduler and automation

- [ ] Durable schedules
- [ ] Jobs that work without the laptop
- [ ] Brain-required jobs queued until laptop online
- [ ] Budgets, retries, cancellation, audit
- [ ] Notifications

## Phase 9 — Brother’s server manager

- [ ] Identify remote server and router capabilities
- [ ] Build standalone authorized remote power path
- [ ] Secure VPN connectivity
- [ ] Test independently
- [ ] Add Pi coordinator/dashboard
- [ ] Add APEXIS permission and confirmation
- [ ] Audit every action

This phase may move earlier if the server requirement becomes urgent, but it remains independent of the LLM.

## Later phases

- Voice
- Vision
- Multi-brain routing
- Pi fallback model
- Advanced automations
- Phone companion
- Robotics/home automation

---

# 15. Recommended Version 1

Version 1 should prove the distributed foundation:

- Securely paired Laptop Brain Node and Pi Headquarters
- Laptop model chat
- Desktop chat interface
- Pi dashboard
- Pi project/memory database
- One calculator plugin
- One user-selected local file/PDF plugin
- Explicit save-to-memory
- System health, logs, backup, and restore

Version 1 should exclude:

- Unattended autonomous research
- Voice
- Vision
- Public internet access
- Email sending
- Hard server power-off
- Arbitrary shell execution
- Automatic whole-laptop file scanning
- Multi-model routing
- Silent memory creation

---

# 16. Unresolved Phase 0 Questions

## Laptop

- [ ] Is the laptop macOS, Windows, or Linux?
- [ ] Exact CPU/chip, RAM, GPU, and free storage?
- [ ] Must the model be fully local?
- [ ] Which model runtime is preferred or already installed?
- [ ] Is a cloud fallback allowed?
- [ ] Is the Desktop App required to be cross-platform?

## Pi

- [ ] Will the existing Pi 5 host both JoyNode and APEXIS Core initially?
- [ ] Is an external SSD planned?
- [ ] Should the Pi dashboard be LAN-only?
- [ ] Is VPN access desired later?
- [ ] What data retention and backup destination are approved?

## Research

- [ ] Initial summarization mode: A, B, or C?
- [ ] Approved search sources/providers?
- [ ] Per-job time, page, size, and cost limits?
- [ ] Should collected documents require review before storage?

## Desktop architecture

- [ ] Native framework or local web technology?
- [ ] How are local plugins packaged and updated?
- [ ] Which OS credential store will hold the Pi node credential?
- [ ] What is the limited offline-mode retention policy?

---

# 17. Final Mission Statement

> APEXIS is a distributed assistant platform. The laptop is the Brain Node: it runs the primary AI model, daily Desktop App, local file tools, and heavy computation. The Raspberry Pi is Headquarters: it remains online, preserves memory and knowledge, coordinates research and automation, hosts the dashboard and APIs, records audit history, and manages authorized remote services. The two nodes communicate through secure, versioned, permission-aware interfaces so either side can evolve without discarding the rest of the system.
