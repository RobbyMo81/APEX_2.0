# Vashion — CTO Engineering Review, Round 2
**Document:** Build_PRD.md v3 + Build_PRD_graph.md
**Review Date:** 2026-03-27
**Reviewer:** CTO
**Scope:** Verification of 9 R1 blocking items; identification of new gaps; identification of remaining gaps not caught in R1; overall verdict.

---

## Preamble

Round 1 produced 10 review items, 9 of which were blocking. The team applied amendments and produced v3. This review verifies each blocking item individually, then opens a fresh pass over the full document for residual and newly introduced issues.

The architecture graph (Build_PRD_graph.md) has been updated substantially and now matches the PRD's revised control model. The graph is evaluated in parallel with the PRD; discrepancies are called out explicitly.

---

## Part I — Verification of the 9 R1 Blocking Items

---

### R1-B1 — Tier-2 "hold after spawn" → admission control before execution

**R1 requirement:** Change Tier 2 from *spawn then hold* to admission control before execution. The firewall must produce an executable plan token; execution starts only after approval resolution.

**Resolution status: FULLY RESOLVED.**

VSH-001 now reads: *"No process is spawned before FW returns `allowed: true` — admission control, not hold-after-spawn."* The `plan_token` mechanism is defined under the `FirewallReturnContract` section and is consistently applied across VSH-001, VSH-002, VSH-004. The Tier 2 branch explicitly states: *"SHELL does NOT spawn; execution plan is held pending approval; spawn occurs only after FW resolves `plan_token` as approved."* The graph's `FW` node uses the label *"Tier 2: plan_token pending — do not spawn"* on the return arrows to SHELL, DOCKER, and FILES, which matches the prose.

The Key Design Decisions table in the graph document also captures the rationale: *"Admission control before spawn (Tier 2) — plan_token held; no process started before approval resolves — eliminates side-effects-before-suspend problem."*

No residual concern.

---

### R1-B2 — Under-specified ActionDescriptor classification contract

**R1 requirement:** Introduce a deterministic `ActionDescriptor` schema with named fields; define Tier rules against those fields, not prose; Soul.md/Behavior.md tune policy but do not replace the classifier.

**Resolution status: FULLY RESOLVED.**

VSH-003 now contains the full `ActionDescriptor` schema with eight fields: `operation_kind`, `target_scope`, `reversibility`, `privilege_required`, `network_boundary`, `workspace_boundary`, `data_sensitivity`, `estimated_side_effects`. Each takes an explicit enumerated set of values.

Tier rules are stated as deterministic conjunctions and disjunctions against those fields:
- Tier 1 requires: `reversibility=reversible AND privilege_required=none AND workspace_boundary=inside AND data_sensitivity=none AND estimated_side_effects=none|low`
- Tier 2 triggers on any of: `reversibility=irreversible|unknown`, `workspace_boundary=outside`, `estimated_side_effects=high`, `data_sensitivity=user_data|system_config`, `privilege_required=user|elevated`
- Tier 3 triggers on any of: `privilege_required=root`, `network_boundary=external`, `data_sensitivity=credentials`, `operation_kind=auth with mutation`

The Safety Gates section confirms: *"Missing field = Tier 2 minimum; unknown fields never default to Tier 1."* Soul.md/Behavior.md scope is described in the description: *"tune policy — they do not replace the classifier."*

The graph adds `ActionDescriptor` labels on the arrows from SHELL, DOCKER, FILES to FW, confirming implementation intent matches the schema-first model.

No residual concern.

---

### R1-B3 — Auto-approval path lacks governance controls

**R1 requirement:** Explicit `AutoApprovalPolicy` with: allowlisted action classes (deny-by-default), mandatory rationale object, decision hash tying approval to exact action parameters, TTL and single-use nonce, operator-visible audit stream.

**Resolution status: FULLY RESOLVED.**

VSH-003 defines `Tier 2 — AutoApprovalPolicy` with all five required elements:

1. **Allowlist with deny-by-default:** *"Explicit allowlist of action classes eligible for auto-approval (deny-by-default outside allowlist)"*; unlisted classes always escalate to user.
2. **Mandatory rationale object:** Decision record defined as `{ action_descriptor, rationale, decision_hash, approved_by: "brain", timestamp, nonce, ttl_seconds }`.
3. **Decision hash:** *"`decision_hash` ties approval to exact action parameters — a modified action requires a new approval."*
4. **TTL and single-use nonce:** *"Nonce is single-use; TTL default 30s — expired approval requires re-request."*
5. **Operator-visible audit stream:** *"Decision record written to operator-visible audit stream before execution proceeds."*

The acceptance criteria further require: *"`decision_hash` and `nonce` checked before every Tier 2 execution — stale approvals rejected."*

No residual concern.

---

### R1-B4 — Auth bootstrap missing rekey/reconnect protocol

**R1 requirement:** Token epoch, Brain detects auth mismatch, Brain enters degraded state, Brain reloads token and rebinds Unix socket client, pending calls fail closed with structured retry semantics.

**Resolution status: FULLY RESOLVED.**

VSH-005 defines a six-step rekey protocol:
1. Core restarts; writes new token with `epoch+1`.
2. Brain receives auth rejection on next socket call.
3. Brain detects epoch mismatch; enters `degraded` state — no new goal execution.
4. Brain reloads token file; rebinds Unix socket client with new token + epoch.
5. In-flight requests fail closed with `{ error: "core_restart", retry: true }` — caller may retry once after rekey completes.
6. Brain exits degraded state; resumes normal operation.

The Safety Gates confirm: *"Stale epoch rejection is immediate — Brain never retries with a known-bad token"* and *"Degraded state is operator-visible via Canvas health endpoint."*

VSH-009 cross-references this protocol and confirms Brain's responsibilities at the MCP layer, including detection of epoch mismatch.

No residual concern.

---

### R1-B5 — Heartbeat anomaly detection underspecified

**R1 requirement:** Define baseline learning window, minimum sample count before z-score activates, fallback static thresholds, event dedup/coalescing, cooldown windows, escalation state machine.

**Resolution status: FULLY RESOLVED.**

VSH-006 addresses every sub-item:

- **Baseline window:** *"rolling 24h of pulse samples, per-metric (CPU%, mem bytes, per-process)"*
- **Minimum sample count:** *"30 samples (30 min at default interval)"* before z-score activates
- **Cold-start fallback:** *"fall back to static thresholds only — CPU > 90%, mem < 256MB"* during warmup
- **Static thresholds (always active):** Mandatory service down within 2 pulses, disk > 85%, available memory < 512MB
- **Burst suppression:** *"sustained for 2 consecutive pulses"* before anomaly qualifies
- **Multi-process correlation:** If > 3 processes simultaneously exceed threshold, escalate as a single correlated event — not one per process
- **Dedup/coalescing:** *"identical event type from same source suppressed for 5-minute cooldown window after first emission"*
- **Escalation state machine:** `normal → warning (1σ) → urgent (2σ, 2 pulses) → sustained_urgent (> 5 pulses)` with `sustained_urgent` re-escalating regardless of cooldown
- **Cooldown exception:** *"Cooldown does not apply to static threshold breaches — disk > 85% is always re-escalated"*

No residual concern.

---

### R1-B6 — Goal Loop missing concurrency and arbitration rules

**R1 requirement:** Single active mutating goal per protected resource domain, priority queue (urgent > operator > scheduled), explicit preemption rules, resource lock model for workspace/container/system scopes, starvation prevention.

**Resolution status: FULLY RESOLVED.**

VSH-010 now defines a full scheduler contract:

- **Priority queue:** `urgent > operator > scheduled` — explicit.
- **Resource lock model:** Three named scopes — `workspace` (file/shell ops), `container` (Docker), `system` (auth, config, network). A goal must acquire all required locks before transitioning to `active`; held until `complete`, `failed`, or `blocked`.
- **Lock atomicity:** *"acquire all or none"* — no partial lock held.
- **Preemption:** Urgent goals preempt operator goals; active operator goal paused, locks released, re-queued as `queued` after urgent completes. *"Operator goals are never dropped, only deferred."*
- **Starvation prevention:** Scheduled goals starved for > 10 minutes promoted to operator priority.
- **Cancellation precedence:** Urgent cancellation can cancel any active goal; operator cancellation is limited to operator and scheduled goals.
- **Blocked goal behavior:** Locks released when blocked awaiting Tier 2; re-acquired on resume.

The graph's Key Design Decisions table confirms: *"Goal Loop resource lock model — workspace / container / system scopes; urgent preempts operator; scheduled promoted after 10min starvation."*

No residual concern.

---

### R1-B7 — Durable memory promotion pipeline and lifecycle not specified

**R1 requirement:** Define a pipeline (candidate detection, summary normalization, duplication check, operator review bundle, immutable approval record, durable write with provenance backlink). Define deletion/edit semantics.

**Resolution status: FULLY RESOLVED.**

VSH-011 defines the **6-stage durable promotion pipeline** — all stages named exactly as required in R1:
1. Candidate detection (3+ distinct sessions)
2. Summary normalization (canonical form, deduplication-safe)
3. Duplication check (no near-duplicate promoted — hard rejection)
4. Operator review bundle (presented via Canvas Memory panel)
5. Immutable approval record (`{ candidate_id, durable_id, approved_by: "user", timestamp, source_episode_ids[] }` written to audit log)
6. Durable write with provenance (backlink to `source_episode_ids`)

**Durable memory lifecycle** is now defined:
- **Edit:** Immutable after write; update via deprecate-old + write-new-with-backlink chain.
- **Delete:** Operator-initiated only; soft-delete (30-day retention), hard-delete after 30 days; deletion event in audit log.
- **Expiry:** Durable entries do not auto-expire; must be explicitly deprecated or deleted.

Safety Gates confirm no stage may be skipped and duplication check is a hard rejection.

No residual concern.

---

### R1-B8 — Shell security requires structured argv model

**R1 requirement:** Prefer direct `execve`-style argv invocation; typed tool adapters for common operations; minimized environment; explicit cwd policy; explicit allowlist for executables. Use shell only when shell semantics are actually required.

**Resolution status: FULLY RESOLVED.**

VSH-001 now defines the execution model with explicit preference ordering:
- *"Where shell semantics are not required … use direct `execve`-style argv invocation via typed tool adapters — no shell parser, no expansion, no environment inheritance."*
- *"Use shell (bash/zsh) only when shell semantics are explicitly required (pipes, redirection, glob expansion)."*
- Allowlist at `~/.vashion/policy/exec-allowlist.json` — executable not in allowlist rejected before FW classification.
- Explicit `cwd` required on every invocation.
- *"Minimized environment: pass only explicitly declared env vars; strip parent environment by default."*
- Shell invocation mode requires explicit flag — default is structured argv.

The acceptance criteria also specify: *"Command injection check on all inputs before FW classification — FW never sees raw untrusted input."*

No residual concern.

---

### R1-B9 — Approval survivability must be backed by Core, not web process memory

**R1 requirement:** Store pending approval state in Core or a durable control-plane store; Web renders approval state, does not own it; reconciliation after browser refresh and backend restart specified.

**Resolution status: FULLY RESOLVED.**

VSH-008 defines the approval state ownership model explicitly:
- *"All in-flight Tier 2 `plan_token` records stored in MEM_S (Core), not in Web or Brain process memory."*
- *"Web renders approval state by querying Core; it does not own it."*
- *"On Brain restart: pending approvals re-fetched from Core on reconnect — no approval state lost."*
- *"On Web restart/browser refresh: approval state re-loaded from Core via Canvas Backend `/approvals` endpoint."*

The approval state schema is fully defined: `{ plan_token, action_descriptor, decision_hash, nonce, ttl, expires_at, status: pending|approved|denied|expired }`.

VSH-015 cross-references: *"Pending approval state is owned by Core (VSH-008), not Web process memory — disconnect and backend restart do not lose approval state; Web re-fetches on reconnect."*

The graph Key Design Decisions table confirms: *"Pending approval state in Core — Web renders approval state but does not own it — browser refresh and backend restart do not lose pending approvals."*

No residual concern.

---

## Part II — New Gaps Introduced by the Amendments

---

### N1 — `credentials` field in Tier 3 creates a classification gap with `auth` mutation rule (VSH-003) — MINOR

**Issue:** VSH-003 Tier 3 triggers on two distinct clauses that partially overlap: `data_sensitivity=credentials` and `operation_kind=auth with mutation`. However, an auth *read* (e.g., token verification) with `data_sensitivity=credentials` would hit Tier 3 via the first clause, but reads are not mutations. If an operation *reads* a credential file for validation purposes, it will be classified Tier 3 and hard-blocked, which may be incorrect for internal Core operations (e.g., VSH-005 reading `core.token`).

**Risk:** Over-blocking internal auth reads; or implementers special-casing auth reads from the classifier, creating an undocumented bypass path.

**Required clarification:** Specify whether the `data_sensitivity=credentials` Tier 3 rule applies to external-caller-initiated operations only, or also to Core-internal operations. If Core-internal operations are exempt, this exemption must be explicit in the Safety Gates — not an implied exception.

**Severity: MINOR** — does not block Phase 1, but must be resolved before VSH-005 and VSH-003 are integrated.

---

### N2 — `FW → BRAIN_ENTRY` webhook for approvals: no auth or delivery guarantee specified (VSH-003, VSH-013) — BLOCKING

**Issue:** The graph shows `FW →|Webhook POST /approvals| BRAIN_ENTRY`. The PRD does not specify:
1. How this webhook is authenticated. Core pushing to Brain's HTTP endpoint at `127.0.0.1:7475` is an outbound HTTP call from Core. Is it using the same session token as Brain→Core? If so, is the direction of token validation defined?
2. What happens if Brain is unreachable when FW needs to deliver a Tier 2 approval request. VSH-006 defines a retry/queue model for heartbeat events, but VSH-003 has no equivalent for approval-request delivery failures.
3. If Brain is restarted after an approval request is posted but before the user responds, does the pending approval request re-surface? VSH-008 covers the approval *state* surviving restart, but nothing covers Core re-notifying Brain of pending items after reconnect.

**Risk:** A Tier 2 approval request gets posted to Brain, Brain restarts before the user sees it, and the request silently sits as `pending` in Core with no active delivery to the frontend. The operation is blocked indefinitely with no visible prompt.

**Required amendment:** Define the authentication mechanism for Core→Brain webhook calls. Define retry/queue behavior for `/approvals` webhook delivery failures (mirror the VSH-006 urgent queue model). Define a reconnect reconciliation step: when Brain reconnects to Core (after rekey), Core re-delivers all `status: pending` approval records.

**Severity: BLOCKING** — The approval workflow is the core safety primitive. Silent indefinite blocking without visible prompt is a governance failure mode.

---

### N3 — Brain entry point `/approvals` path is underdefined (VSH-013, VSH-015) — MINOR

**Issue:** The `BRAIN_ENTRY` node lists `/goals /events /approvals` as endpoints. VSH-013 references a Canvas Backend `/approvals` endpoint for Web to fetch pending approval state from Core. However:
- It is ambiguous whether `/approvals` on BRAIN_ENTRY is for *receiving approval decisions* from the user (proxied through Web) or for *receiving approval requests* from Core's FW webhook.
- No story explicitly defines the approval decision flow: user clicks approve/deny in Canvas → VSH-015 receives → routes to VSH-013 → forwards where exactly? To Brain? To Core directly?

**Risk:** Implementation teams build the approval response path with ambiguous routing — Web sends the decision to Brain, Brain forwards to Core, or Web sends directly to Core — without a specified contract.

**Required clarification:** Specify the exact approval decision path: user → WS → Canvas Backend → ? (Brain or Core?). If it goes through Brain, define the Brain→Core approval resolution call. If Canvas Backend calls Core directly, state this explicitly and define the endpoint.

**Severity: MINOR** — Does not block core implementation, but will cause integration disputes at Phase 2/3 boundary.

---

### N4 — `sudo` blocked "at this layer" is ambiguous scope (VSH-001) — MINOR

**Issue:** VSH-001 states: *"`sudo` blocked unconditionally at this layer — not delegated to VSH-003."* The phrase "at this layer" implies the SHELL engine itself detects and rejects `sudo`. However, `sudo` could appear in a shell script invoked via the shell invocation mode, or as an argument in a structured argv call where the first argv element is `sudo`. The guarantee only holds if both paths are covered.

**Required clarification:** Confirm whether the `sudo` block applies to: (a) direct invocations where `sudo` appears as the executable name or argv[0], (b) shell-mode scripts that contain `sudo`, and (c) symlinked or renamed `sudo` equivalents (e.g., `pkexec`, `doas`). If (b) and (c) are not covered, this should be called out as a known limitation rather than an unconditional block.

**Severity: MINOR** — Current language overstates the guarantee for shell-mode invocations.

---

### N5 — `BEH →|Tier 2 auto-approve logic| BRAIN_ENTRY` in graph is architecturally ambiguous (VSH-003, VSH-009) — MINOR

**Issue:** The graph shows `Behavior.md` connecting directly to `BRAIN_ENTRY` with the label "Tier 2 auto-approve logic." This implies Behavior.md influences approval decisions at the Brain entry point level, before the Goal Loop or MCP layer is involved. However, VSH-003 defines the `AutoApprovalPolicy` as a Core-side firewall construct. Brain's role in auto-approval is not clearly scoped in the PRD text.

The PRD states the auto-approval allowlist is at `~/.vashion/policy/allowlist.json` (Core-side), and the `AutoApprovalPolicy` decision is described under VSH-003 (Core). Yet the graph suggests Brain/BRAIN_ENTRY is also an actor in the auto-approval decision.

**Risk:** Implementation confusion about who owns the auto-approval decision — Core or Brain. If both layers apply independent approval logic, the decision is non-deterministic. If only one layer does, the graph is misleading.

**Required clarification:** Specify the exact division: does Core make the auto-approve vs escalate decision based on `~/.vashion/policy/allowlist.json`, and Brain merely receives escalated Tier 2 items to surface to the user? Or does Brain have independent auto-approval authority informed by Behavior.md? The current split-brain implication in the graph must be resolved in the PRD text.

**Severity: MINOR** — Does not block Phase 1 (Core-only), but will cause implementation confusion at Phase 2.

---

## Part III — Remaining Gaps Not Caught in Round 1

---

### G1 — No liveness or readiness contract for the Unix socket (VSH-005, VSH-009) — BLOCKING

**Issue:** The Core Unix socket (`/tmp/vashion-core.sock`) is the sole communication path between Brain and Core. VSH-005 and VSH-009 define the token epoch and rekey protocol for *auth* failures, but there is no specification for:
- How Brain detects that the socket is unavailable before an auth rejection (i.e., connection refused vs auth error are different failure modes requiring different handling).
- How long Brain waits on a socket call before declaring Core unreachable.
- What Brain's behavior is if Core is alive but unresponsive (hung, not crashed): does it time out per-call, does it enter degraded state, does it alert?
- Whether the Goal Loop is suspended or continues (with queued operations) if the socket is unresponsive.

**Risk:** Brain hangs indefinitely on a socket call to a hung Core, blocking the Goal Loop with no visible indication to the operator. Or Brain incorrectly enters the epoch-rekey path when the actual problem is a hung process, entering an infinite rekey loop.

**Required amendment:** Define per-call socket timeout (a specific value, not "configurable" without a default). Define the distinction between connection failure and auth failure in Brain's error handling. Define the behavior when socket is unreachable: enter degraded state (matching the epoch-mismatch path), alert via Canvas health endpoint, stop accepting new goals.

**Severity: BLOCKING** — The Unix socket is the system's nervous system. An unspecified timeout/hang model will produce hard-to-diagnose production freezes.

---

### G2 — No rate limiting on Brain entry point or Unix socket (VSH-009, VSH-013) — MINOR

**Issue:** VSH-015 specifies that rate limiting applies equally to WS and REST paths (at the Canvas Backend level). However, there is no rate limiting defined for:
- The Brain entry point at `127.0.0.1:7475` directly — if something on the local machine bypasses Canvas Backend and calls Brain's HTTP port directly.
- The Unix socket — a compromised or buggy Brain process could flood Core with requests.

The threat model is local-only, which reduces risk, but the agent is explicitly designed to be autonomous with plugin webhook ingress. A misbehaving plugin that somehow obtains Brain's address could generate a request flood.

**Required clarification:** Confirm whether Brain's entry point at `:7475` is protected from direct access (e.g., by binding to loopback and relying on OS-level controls), or whether Brain applies its own rate limiting. Similarly, confirm whether Core applies any per-client rate limiting on the Unix socket. If the answer is "local-only trust boundary and we accept the risk," state this explicitly as a documented security assumption.

**Severity: MINOR** — Does not block implementation but should be a documented decision, not an oversight.

---

### G3 — Checkpoint schema not defined; recovery action for unknown fields unspecified (VSH-007) — MINOR

**Issue:** VSH-007 defines what a checkpoint *contains* conceptually (active processes, open Docker operations, pending Tier 2 approvals, auth state reference, heartbeat cursor), but does not define:
- The on-disk format (JSON, CBOR, binary, etc.).
- Schema versioning — if a checkpoint written by version N is read by version N+1 after an upgrade, how are unknown or renamed fields handled.
- Whether the checkpoint is signed or integrity-checked — a corrupt checkpoint that passes the dirty/clean classifier but has invalid internal state could cause worse damage than a clean restart.

**Risk:** A schema-incompatible checkpoint after an upgrade causes a silent misparse that surfaces wrong recovery state to Brain. Or a partially-written checkpoint that survives the atomic rename (highly unlikely but possible on certain filesystems) is treated as valid.

**Required amendment:** Define checkpoint format and a format version field. Define behavior on format mismatch: treat as dirty and discard, or fail hard with operator alert. Define whether checkpoints are integrity-checked (a checksum field is sufficient; full signing is not required).

**Severity: MINOR** — Does not block Phase 1 initial implementation, but must be resolved before the first production deployment.

---

### G4 — Memory context budget enforcement mechanism is unspecified (VSH-008, VSH-011) — MINOR

**Issue:** VSH-008 states: *"Context budget enforced at item granularity — whole items dropped when over budget, never truncated mid-text."* VSH-011 states: *"Context budget: respects VSH-008 limits; lowest-relevance items dropped first."*

Neither story specifies:
- The numerical default for the context budget (in tokens or items).
- How the budget is communicated from Core to Brain (is it a configuration value, a field in the memory API response, or derived from the model registry in VSH-005?).
- Whether the budget is per-goal-invocation or per-session.
- What happens when the session-start narrative itself exceeds budget — is the narrative truncated (violating the "no mid-text truncation" rule) or rejected?

**Risk:** Implementers define an arbitrary default, or Brain and Core use incompatible budget values. The session-start narrative edge case (VSH-011: *"Session-start narrative failure is non-fatal but logged"*) suggests truncation might be acceptable for the narrative — but this contradicts VSH-008's hard enforcement language.

**Required clarification:** Define the default budget value. Define how Brain learns the budget (configuration or registry). Clarify whether the narrative-truncation exception is intentional, and if so, make it explicit in VSH-008's enforcement language (e.g., "session-start narrative may be soft-truncated as a last resort; all other context budget enforcement is hard").

**Severity: MINOR** — Not a blocking architectural issue, but will cause implementation inconsistency.

---

### G5 — LLM fallback chain: no backpressure or loop-break condition defined (VSH-009) — MINOR

**Issue:** VSH-009 defines the LLM failure mode as: *"retry once → fallback chain (Anthropic → OpenAI → Ollama) → halt."* This is a sound baseline, but:
- If Ollama (the last fallback) is also the *active model* that just failed, the retry-once fires against the same model, then the fallback chain starts at Anthropic — potentially calling a provider that was deliberately disabled. The PRD does not clarify whether the fallback chain respects provider enablement status.
- If all three providers fail simultaneously (e.g., network partition for cloud providers + local Ollama not running), "halt" is mentioned, but the Goal Loop behavior on LLM halt is not specified in VSH-010. Does the goal transition to `failed`? Is it re-queued? Does the loop itself stop accepting new goals?

**Required clarification:** Confirm the fallback chain skips providers that are not configured/enabled. Define the Goal Loop's response when the LLM Gateway halts — specifically, what `status` the active goal transitions to and whether new goals are accepted.

**Severity: MINOR** — Edge case, but LLM unavailability is a realistic operational condition and the behavior gap creates operator confusion.

---

### G6 — No eviction or migration path for the SQLite memory database (VSH-008) — MINOR

**Issue:** VSH-008 specifies episodic entries expire after 7 days and checkpoint rotation keeps last 5 files. But there is no specification for:
- Maximum database size or a growth cap.
- What happens when the database grows beyond available disk space.
- Whether the WAL file is checkpointed on clean shutdown (important for recovery guarantees).
- Whether there is a vacuum/compact operation and, if so, who triggers it.

For an always-on autonomous agent with a 60s heartbeat pulse, memory.db will grow continuously. Disk pressure is a realistic operational concern, especially given VSH-006 fires an urgent alert at disk > 85%.

**Required amendment:** Define the database size management strategy: either a maximum row count per table, a periodic VACUUM schedule, or explicit eviction on episodic expiry. Define WAL checkpoint behavior on SIGTERM (clean shutdown). These do not need to be complex; they need to be stated.

**Severity: MINOR** — Not blocking for Phase 1 initial build, but an unmanaged SQLite WAL in a long-running agent is a known operational time bomb.

---

### G7 — Soul.md and Behavior.md have no versioning or integrity check (VSH-010, VSH-012) — MINOR

**Issue:** VSH-010 states: *"Soul.md / Behavior.md loaded at startup — missing file is a hard error."* VSH-012 uses Behavior.md for event classification at runtime. Both files are plain Markdown governance documents. There is no specification for:
- What happens if Soul.md or Behavior.md changes on disk while Vashion is running (hot-reload vs restart required).
- Whether the loaded content is hashed at startup for integrity verification — a governance document modified by an attacker or corrupted mid-session could silently alter agent behavior without any audit trail.
- Whether changes to these files trigger a forced restart or at minimum an operator alert.

**Risk:** A governance document is modified after startup; Vashion continues running on the stale (safe) version with no indication to the operator that the governing policy on disk has diverged. Or conversely, a hot-reload mechanism is assumed by implementers but not specified, leading to inconsistent behavior mid-session.

**Required clarification:** Specify whether governance files are read once at startup (restart required to apply changes) or hot-reloaded. Specify whether a startup hash is stored and disk changes are monitored. If this is intentionally deferred, state it explicitly.

**Severity: MINOR** — Does not block implementation, but governance document integrity is a meaningful security property for an autonomous agent.

---

## Part IV — Summary Scorecard

### R1 Blocking Items — Verification Results

| Item | Description | Status |
|---|---|---|
| R1-B1 | Tier-2 admission control before spawn | FULLY RESOLVED |
| R1-B2 | ActionDescriptor schema and deterministic Tier rules | FULLY RESOLVED |
| R1-B3 | AutoApprovalPolicy with allowlist, hash, nonce, TTL, audit stream | FULLY RESOLVED |
| R1-B4 | Auth bootstrap rekey/reconnect protocol with epoch and degraded state | FULLY RESOLVED |
| R1-B5 | Heartbeat anomaly detection: baseline window, warmup, dedup, state machine | FULLY RESOLVED |
| R1-B6 | Goal Loop scheduler contract: priority queue, lock model, preemption, starvation | FULLY RESOLVED |
| R1-B7 | Durable memory promotion pipeline (6 stages) and lifecycle (edit/delete/expiry) | FULLY RESOLVED |
| R1-B8 | Shell security: structured argv model, allowlist, minimized environment | FULLY RESOLVED |
| R1-B9 | Approval state owned by Core; Web renders only; reconciliation on restart | FULLY RESOLVED |

All 9 previously blocking items are correctly and fully resolved in v3. No partial resolutions.

---

### New and Residual Gaps

| ID | Description | Severity | Story IDs Affected |
|---|---|---|---|
| N1 | `data_sensitivity=credentials` Tier 3 over-blocks internal Core auth reads | MINOR | VSH-003, VSH-005 |
| **N2** | **Core→Brain `/approvals` webhook: no auth, no delivery guarantee, no reconnect re-delivery** | **BLOCKING** | **VSH-003, VSH-006, VSH-008, VSH-013** |
| N3 | Approval decision path (user → Canvas → ?) is ambiguous; routing to Brain vs Core not specified | MINOR | VSH-013, VSH-015 |
| N4 | `sudo` block scope overstated for shell-mode invocations and sudo equivalents | MINOR | VSH-001 |
| N5 | Graph `BEH → BRAIN_ENTRY` auto-approve arrow implies Brain has independent approval authority; contradicts Core-owned AutoApprovalPolicy | MINOR | VSH-003, VSH-009, VSH-010 |
| **G1** | **Unix socket: no per-call timeout, no hang detection, no behavior defined for unresponsive Core** | **BLOCKING** | **VSH-005, VSH-009, VSH-010** |
| G2 | Brain entry point `:7475` and Unix socket have no defined rate limiting; local trust assumption undocumented | MINOR | VSH-009, VSH-013 |
| G3 | Checkpoint format, schema version, and integrity check unspecified | MINOR | VSH-007 |
| G4 | Memory context budget: no default value, no communication mechanism, narrative edge case unresolved | MINOR | VSH-008, VSH-011 |
| G5 | LLM fallback chain: no provider-enabled check, no Goal Loop behavior when fully halted | MINOR | VSH-009, VSH-010 |
| G6 | SQLite memory.db: no size management, no VACUUM policy, WAL checkpoint on shutdown unspecified | MINOR | VSH-008 |
| G7 | Soul.md / Behavior.md: no versioning, no integrity hash, hot-reload policy undefined | MINOR | VSH-010, VSH-012 |

**Blocking items remaining: 2** (N2, G1)
**Minor items remaining: 10** (N1, N3, N4, N5, G2, G3, G4, G5, G6, G7)

---

## Part V — Overall Verdict

**BLOCK PENDING AMENDMENTS — 2 blocking items.**

The team did genuinely excellent work on v3. All 9 R1 blocking items are fully and correctly resolved — not superficially addressed. The `ActionDescriptor` schema, `AutoApprovalPolicy`, rekey protocol, anomaly detection state machine, Goal Loop scheduler, and 6-stage durable memory pipeline are all implemented at the level of specificity that enables unambiguous implementation. The architectural decisions are sound and the graph is now consistent with the PRD text on all previously contested points.

The two remaining blocking items are not regressions — they are gaps that were always present but became visible because the v3 amendments clarified the surrounding architecture enough to expose them.

**N2** (Core→Brain approval webhook delivery) is blocking because the Tier 2 approval flow is the primary safety primitive of the entire system. An unspecified delivery failure mode on the `/approvals` webhook creates a condition where a user-facing approval prompt silently never appears, leaving the agent indefinitely blocked with no visible indication. The fix is well-scoped: mirror the VSH-006 urgent queue model for approval-request delivery, add reconnect reconciliation on Brain restart, and specify the auth mechanism for Core's outbound HTTP call to Brain.

**G1** (Unix socket timeout and hang model) is blocking because an unspecified per-call timeout on the only Core↔Brain communication path will produce hard-to-diagnose production freezes. The Goal Loop, MCP layer, and memory system all depend on this path. A hung Core with no defined timeout behavior will freeze Brain silently. The fix is equally scoped: define a per-call timeout value, distinguish connection failure from auth failure, and specify the Goal Loop behavior (halt goal acceptance, enter degraded state, alert via Canvas health endpoint) when Core is unreachable.

The 10 minor items are real and should be addressed before Phase 3 completion, but none of them are architectural blockers for Phase 1 or Phase 2 implementation to begin.

**Recommended path forward:** Address N2 and G1 in a v3.1 amendment. The minor items (particularly N3, N5, G3, G4) should be resolved before Phase 2 integration begins — not because they block Phase 1, but because they sit at Phase 1/2 integration boundaries and will cause rework if left open.

---

*End of CTO Engineering Review — Round 2*
