# Vashion — Architecture Graph
**Companion to:** Build_PRD.md
**Last Updated:** 2026-03-27

---

```mermaid
graph TD
    subgraph WEB["Web Layer — TypeScript (127.0.0.1:7474)"]
        UI["Canvas Frontend\nVSH-014"]
        WS["WebSocket Bridge\nVSH-015"]
        API["Canvas Backend\nVSH-013\n(single validated entry point)"]
    end

    subgraph BRAIN["Brain Layer — Python (127.0.0.1:7475)"]
        BRAIN_ENTRY["Brain Entry\n/goals /events /approvals"]
        MCP["LLM Gateway (MCP)\nVSH-009\n30s socket timeout\nconn-refused|auth-reject|hang → degraded"]
        GOAL["Goal Loop\nVSH-010"]
        MEM_I["Memory & Context Intelligence\nVSH-011"]
        HB_CTX["Heartbeat Context Awareness\nVSH-012"]
    end

    subgraph CORE["Core Layer — Rust (unix: /tmp/vashion-core.sock)"]
        SHELL["Shell Engine\nVSH-001"]
        DOCKER["Docker Lifecycle\nVSH-002"]
        FW["Policy Firewall\nVSH-003"]
        FILES["File Operations\nVSH-004"]
        AUTH["Auth & Model Registry\nVSH-005"]
        HB["Heartbeat & Event Bus\nVSH-006"]
        CHK["Checkpoint & Recovery\nVSH-007"]
        MEM_S["Memory Store\nVSH-008\nwrite queue serialized"]
    end

    subgraph GOVERN["Governance — read at startup"]
        SOUL["Soul.md"]
        BEH["Behavior.md"]
    end

    subgraph OBS["Observability"]
        OTEL["OTel Collector\nlocalhost:4317"]
        JAEGER["Jaeger\ndev only"]
    end

    subgraph PERSIST["Persistence"]
        DB["SQLite\n~/.vashion/memory.db"]
        CKFILES["Checkpoint Files\n~/.vashion/checkpoints/"]
        TOKEN["Bootstrap Token\n~/.vashion/auth/core.token"]
    end

    LLM["LLM\nAnthropic → OpenAI → Ollama"]
    PLUGINS["Plugins\nSlack VSH-016 · Telegram VSH-017\nsingle host process"]

    %% Web internal
    UI <-->|WebSocket| WS
    UI <-->|HTTP| API
    WS -->|same validation pipeline| API

    %% Web → Brain (single entry)
    API -->|REST traceparent + request_id| BRAIN_ENTRY
    BRAIN_ENTRY --> GOAL
    BRAIN_ENTRY --> HB_CTX

    %% Brain internal
    GOAL --> MCP
    GOAL --> MEM_I
    HB_CTX -->|urgent = priority-0 goal| GOAL
    HB_CTX -->|notable = episodic write| MEM_I
    MEM_I --> MCP

    %% Brain → LLM
    MCP <-->|MCP tool calls| LLM

    %% Brain → Core (Unix socket + session token)
    MCP -->|Unix socket + token| SHELL
    MCP -->|Unix socket + token| DOCKER
    MCP -->|Unix socket + token| FILES
    MCP -->|Unix socket + token| MEM_S

    %% Core → Brain (push) — all webhook calls authenticated with session token
    HB -->|Webhook POST /events Bearer token + traceparent| BRAIN_ENTRY
    FW -->|Webhook POST /approvals Bearer token + retry queue| BRAIN_ENTRY
    CHK -->|dirty startup urgent event| HB
    BRAIN_ENTRY -.->|reconnect: pending approvals query Unix socket| MEM_S

    %% Core internal — admission control before spawn
    SHELL -->|ActionDescriptor| FW
    DOCKER -->|ActionDescriptor| FW
    FILES -->|ActionDescriptor| FW
    FW -->|Tier 1: plan_token execute| SHELL
    FW -->|Tier 2: plan_token pending — do not spawn| SHELL
    FW -->|Tier 3: denied + reason — no execution| SHELL
    FW -->|Tier 1: plan_token execute| DOCKER
    FW -->|Tier 2: plan_token pending — do not spawn| DOCKER
    FW -->|Tier 3: denied + reason — no execution| DOCKER
    FW -->|Tier 1: plan_token execute| FILES
    FW -->|Tier 2: plan_token pending — do not spawn| FILES
    FW -->|Tier 3: denied + reason — no execution| FILES
    FW --> AUTH
    FW --> CHK
    FW -->|system events| MEM_S

    %% Persistence
    MEM_S --- DB
    CHK --- CKFILES
    AUTH --- TOKEN
    MCP -.->|reads token at startup| TOKEN

    %% Plugins (through validated entry)
    PLUGINS -->|webhook auth validated| API

    %% Governance
    SOUL -->|read at startup| GOAL
    BEH -->|read at startup| GOAL
    BEH -->|Tier 2 auto-approve logic| BRAIN_ENTRY

    %% Observability
    CORE -->|OTLP| OTEL
    BRAIN -->|OTLP| OTEL
    WEB -->|OTLP| OTEL
    OTEL --> JAEGER

    %% Styles
    classDef webLayer fill:#1a3a5c,stroke:#4a9eff,color:#fff
    classDef brainLayer fill:#1a3d1a,stroke:#4aff4a,color:#fff
    classDef coreLayer fill:#3d1a1a,stroke:#ff4a4a,color:#fff
    classDef govern fill:#3d3000,stroke:#ffcc00,color:#fff
    classDef obs fill:#2a2a2a,stroke:#888,color:#fff
    classDef persist fill:#1a1a3d,stroke:#8888ff,color:#fff
    classDef external fill:#1a1a2e,stroke:#9a9aff,color:#fff

    class UI,WS,API webLayer
    class BRAIN_ENTRY,MCP,GOAL,MEM_I,HB_CTX brainLayer
    class SHELL,DOCKER,FW,FILES,AUTH,HB,CHK,MEM_S coreLayer
    class SOUL,BEH govern
    class OTEL,JAEGER obs
    class DB,CKFILES,TOKEN persist
    class LLM,PLUGINS external
```

---

## Legend

| Color | Layer | Language |
|---|---|---|
| Blue | Web | TypeScript |
| Green | Brain | Python |
| Red | Core | Rust |
| Yellow | Governance | Markdown (Soul.md / Behavior.md) |
| Dark blue | Persistence | SQLite / filesystem |
| Grey | Observability | OTel Collector / Jaeger |
| Purple | External | LLM providers / Plugins |

## Key Design Decisions

| Decision | Rationale |
|---|---|
| Single `BRAIN_ENTRY` node | One validated HTTP surface into Brain — WS and REST share the same gate |
| `PLUGINS → API` not `PLUGINS → GOAL` | Plugin webhooks auth-validated at Canvas Backend before Brain sees them |
| `MEM_I → MCP` for Core access | Brain has no direct SQLite connection — all memory I/O through Unix socket |
| `CHK → CKFILES` (disk only) | Checkpoint recovery is independent of database state — survives DB corruption |
| `HB_CTX → GOAL` and `HB_CTX → MEM_I` | Heartbeat awareness has two outbound paths: urgent events become goals, notable events become memory |
| Write queue in `MEM_S` | Core and Brain both write; serialized queue prevents last-write-wins race |
| Token epoch + rekey handshake | Brain self-recovers from Core restart mid-session; stale epoch detected, degraded state entered, token reloaded |
| `ActionDescriptor` schema on FW arrows | Classification is deterministic against typed fields — not prose; missing fields default to Tier 2 minimum |
| Admission control before spawn (Tier 2) | `plan_token` held; no process started before approval resolves — eliminates side-effects-before-suspend problem |
| Pending approval state in Core | Web renders approval state but does not own it — browser refresh and backend restart do not lose pending approvals |
| Durable memory promotion pipeline | 6-stage process with operator review and provenance backlinks — no silent promotion, no orphaned entries |
| Goal Loop resource lock model | workspace / container / system scopes; urgent preempts operator; scheduled promoted after 10min starvation |
| Core→Brain webhook auth (N2) | All `/approvals` and `/events` webhook calls carry `Authorization: Bearer <token>`; Brain validates before processing; undelivered approvals queued in Core and re-delivered on Brain reconnect |
| Unix socket timeout + degraded state (G1) | 30s per-call timeout; conn-refused/hang → degraded state (Goal Loop halts, Canvas alerts, 10s ping recovery); auth-reject → rekey protocol only |
