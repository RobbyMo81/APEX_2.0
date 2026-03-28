# FORGE.md — Vashion Project Conventions
# Every FORGE agent reads this at startup (Function 0). No exceptions.

## Project Identity
**Name:** Vashion
**Owner:** Kirk
**Purpose:** Local-first autonomous agent — acts without being asked, remembers without being reminded, interrupts only for decisions that are genuinely Kirk's to make.
**Stage:** Alpha
**Reference Implementation:** JAL (TypeScript-only prototype at /home/spoq/jal) — behavioral spec only, not a code source

---

## Architecture

### Service Map
| Service | Language | Entry Point | Address |
|---|---|---|---|
| `core` | Rust | `core/src/main.rs` | Unix socket `/tmp/vashion-core.sock` |
| `brain` | Python | `brain/src/main.py` | `127.0.0.1:7475` |
| `web` | TypeScript | `web/src/main.ts` | `127.0.0.1:7474` |

### Communication Contracts

| From | To | Protocol | Pattern | Purpose |
|---|---|---|---|---|
| `web` | `brain` | REST (HTTP) | Request/response | User commands, query results |
| `brain` | LLM | MCP | Tool calls, resource access | LLM gateway — structured capability exposure |
| `core` | `brain` | Webhooks (HTTP POST) | Event push | Heartbeat pulses, crash alerts, disk/memory warnings |
| `brain` | `core` | Unix socket | Command/response | Execute shell, manage containers, file ops |

### Why each protocol
- **REST (web → brain):** Simple, synchronous, debuggable. Matches request/response shape of user-initiated commands.
- **MCP (brain → LLM):** Anthropic's protocol for structured LLM-to-tool communication. Brain is the LLM gateway — MCP is what it was built for.
- **Webhooks (core → brain):** Core generates events asynchronously. Push is correct; polling is not. Core should never wait on brain.
- **Unix socket (brain → core):** Low-latency, local-only command channel. No network overhead for the hot path.

### Request Flow
```
web (TS) ──REST──► brain (Python) ──Unix socket──► core (Rust)
                        │                               │
                    MCP tools                    webhook events
                        │                               │
                       LLM                          brain (Python)
```
W3C `traceparent` propagated at every hop. No hop may drop it.

### Deployment
Local-first, on-prem only. No VPS. No cloud. Runs on Kirk's machine.
Process manager: systemd (not PM2, not Docker Compose for prod).

---

## Rust Conventions (`core/`)

- **Formatter:** `rustfmt` — run before every commit, non-negotiable
- **Linter:** `clippy` — curated rules only. Do NOT enable `clippy::pedantic` globally. Enable rules one at a time after review.
- **Dependency audit:** `cargo-deny` — licenses and advisories checked in CI
- **Tests:** `cargo test` — unit tests in-module (`#[cfg(test)]`), integration tests in `core/tests/`
- **Error handling:** `thiserror` for library errors, `anyhow` for binary entry points. No `.unwrap()` in production paths.
- **Async:** `tokio` runtime. All I/O is async.
- **Logging:** `tracing` crate with `tracing-opentelemetry`. Inject `trace_id` and `span_id` from OTel context.
- **No `unsafe`** without an explicit comment explaining why and what invariant is upheld.

---

## Python Conventions (`brain/`)

- **Single config file:** `pyproject.toml` — all tool config lives here, no separate `.cfg` or `setup.py`
- **Formatter:** `ruff format` — run before every commit
- **Linter:** `ruff check --fix` — applied before commit
- **Type checker:** `mypy` or `pyright` — run separately from ruff, required to pass clean
- **Logging:** `structlog` with OTel context injection. Every log entry must carry `trace_id` and `span_id` from the active OTel span.
- **Dependencies:** managed via `uv` or `pip` with `requirements.txt` pinned. No loose version ranges in production.
- **Tests:** `pytest` — test files in `brain/tests/`, named `test_*.py`
- **No bare `except:`** — always catch specific exception types.

---

## TypeScript Conventions (`web/`)

- **Strict mode always:** `"strict": true` in `tsconfig.json`
- **No `any` in public interfaces** — use `unknown` + type guards
- **Formatter:** `prettier` — non-negotiable
- **Linter:** `eslint` with typescript-eslint
- **Type check:** `tsc --noEmit` in CI
- **Logging:** `pino` — structured JSON, never raw `console.log` in production code
- **Tests:** `jest` with `ts-jest`. Test files: `*.test.ts`
- **WebSocket:** `ws` library. All WS handlers must handle disconnect and error events explicitly.

---

## Observability

### The Contract
Every service emits structured logs. Every cross-service call propagates `traceparent`. No service is a black box.

### Trace Propagation
```
web (TS/pino)  →  brain (Python/structlog)  →  core (Rust/tracing)
     ↓                      ↓                         ↓
           OTLP export → OpenTelemetry Collector
                                ↓
                    Jaeger all-in-one (local dev only)
```

- **Header:** W3C `traceparent` (`00-{trace_id}-{span_id}-{flags}`)
- **Export:** OTLP (gRPC or HTTP) to OTel Collector at `localhost:4317`
- **Jaeger:** local dev debugging only — not a core integration layer, not required in prod

### Shared Log Schema
Every log entry across all three services must include:
| Field | Type | Description |
|---|---|---|
| `trace_id` | string | W3C trace ID from active span |
| `span_id` | string | W3C span ID from active span |
| `service.name` | string | `vashion-core` / `vashion-brain` / `vashion-web` |
| `request_id` | string | Per-request UUID (set at web layer, propagated) |
| `duration_ms` | number | For operations with measurable duration |
| `level` | string | `debug` / `info` / `warn` / `error` |
| `timestamp` | ISO8601 | UTC |

Never log credentials, tokens, or PII.

---

## Quality Gates (forge.gates.sh)

Gates run in order. Any failure halts the build.

```
1. core/   → cargo fmt --check && cargo clippy && cargo deny check && cargo test
2. brain/  → ruff check && ruff format --check && mypy brain/src && pytest
3. web/    → eslint && prettier --check && tsc --noEmit && jest --runInBand
4. e2e/    → trace propagation test (web → brain → core, assert traceparent at core)
```

The end-to-end trace propagation test is **mandatory**. It is the integration contract between all three services.

---

## FORGE Memory System (MANDATORY)

**File:** `forge-memory.db` (project root, gitignored, WAL mode)
**Protocol:** `MEMORY_PROTOCOL.md`
**Client:** `forge-memory-client.ts`

Every agent MUST:
1. Call `mem.entry()` before writing any code (reads DB, marks messages read)
2. Call `mem.exit({...})` after quality gates pass (writes discoveries, context, story state)
3. Use `mem.setContext()` for any fact the next agent will need (paths, port numbers, env vars)
4. Use `mem.postMessage('GOTCHA', ...)` for anything that almost broke the build
5. NEVER write credentials, tokens, or API keys to the DB

---

## Environment Variables

- All vars documented in `.env.example` (committed)
- `.env` is gitignored, never committed
- Loaded at entry point only — not scattered through the codebase
- Required vars checked at startup; missing vars = hard crash with clear error message

---

## What NOT to Do

- Do not use PM2 (use systemd)
- Do not deploy to cloud or VPS — this is local-first, on-prem only
- Do not enable `clippy::pedantic` globally in Rust
- Do not use bare `except:` in Python
- Do not use `any` in TypeScript public interfaces
- Do not drop `traceparent` at any service boundary
- Do not use Jaeger as a core integration layer — dev tool only
- Do not write credentials to disk unencrypted
- Do not commit `.env` files
- Do not skip Function 0 gate
- Do not copy code from JAL — use it as behavioral reference only
