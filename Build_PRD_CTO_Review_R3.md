# Vashion — CTO Engineering Review, Round 3
**Document:** Build_PRD.md v3.1 + Build_PRD_graph.md (updated companion)
**Review Date:** 2026-03-27
**Reviewer:** CTO
**Scope:** Verification of N2 and G1 resolutions; re-evaluation of 10 R2 minor items for escalation risk; identification of any new gaps introduced by v3.1 amendments; overall Phase 1 verdict.

---

## Preamble

Round 2 left two blocking items (N2, G1) and ten minor items (N1, N3, N4, N5, G2–G7). The team produced v3.1 specifically to close N2 and G1. This review evaluates whether those closures are complete and correctly specified, checks that none of the ten minor items were inadvertently aggravated by the v3.1 text, and conducts a targeted fresh pass over the new material for any self-introduced problems.

Items R1-B1 through R1-B9 are confirmed resolved in R2 and are not re-examined here.

---

## Part I — Verification of N2 (Core→Brain `/approvals` Webhook)

### N2 — Recap of the blocking requirement

R2 required: (1) authentication mechanism for Core→Brain outbound webhook calls; (2) retry/queue behavior on delivery failure, mirroring VSH-006's urgent queue model; (3) a reconnect reconciliation step so Brain re-surfaces pending approvals after restart.

---

### N2.1 — Authentication mechanism

**Where addressed:** VSH-003 (new subsection "Tier 2 `/approvals` webhook — auth, delivery, and reconnect"), VSH-003 Safety Gates, VSH-013 Safety Gates.

**What the PRD now says:**

> "Core makes an outbound HTTP POST to Brain's entry point at `127.0.0.1:7475/approvals`. This call is authenticated using the same session token as Brain→Core — the token is bidirectional. Core sends `Authorization: Bearer <token>` on every webhook call to Brain; Brain validates the token before processing."

VSH-003 Acceptance Criteria adds: *"Core authenticates every outbound `/approvals` webhook with session token — unauthenticated calls rejected by Brain."*

VSH-013 Safety Gates adds: *"Core→Brain webhook calls on `/approvals` and `/events` validated against session token — Brain rejects calls without valid `Authorization: Bearer <token>` header; these are not public endpoints."*

**Assessment:** The authentication direction is correctly specified. The token is explicitly stated as bidirectional; Core presents it as Bearer on outbound calls; Brain validates before processing. The enforcement statement is placed in both the originating story (VSH-003) and the receiving story (VSH-013). The graph's Key Design Decisions table (bottom two rows) also restates this for N2 and G1, confirming the design intent is captured in the companion document.

One precision note: the VSH-013 Safety Gate places the validation language under the *Canvas Backend* story, but the actual recipient of the `/approvals` and `/events` webhooks is Brain's entry point at `:7475`, not the Canvas Backend at `:7474`. This is a documentation placement quirk — the semantic intent is correct, and VSH-003's own text correctly identifies `127.0.0.1:7475/approvals` as the target. The Safety Gate in VSH-013 should arguably live in a Brain-layer story (VSH-009 or a notional VSH-012/BRAIN_ENTRY spec), but as a documentation placement issue it does not create an implementation gap; the endpoint and validation contract are unambiguous. This is noted but not elevated.

**N2.1 verdict: RESOLVED.**

---

### N2.2 — Delivery guarantee and retry/queue model

**What the PRD now says (VSH-003):**

> "Core retries `/approvals` delivery up to 3 times with exponential backoff. If all retries fail (Brain unreachable), the approval request is written to a local pending-delivery queue in Core alongside the `plan_token` record in MEM_S."

VSH-003 Safety Gates: *"No approval request is silently lost — undelivered requests sit in pending-delivery queue until Brain reconnects."*

**Assessment:** The model mirrors VSH-006's urgent queue model as required. Retry count (3), backoff policy (exponential), fallback mechanism (pending-delivery queue in Core alongside MEM_S `plan_token`), and the no-silent-loss guarantee are all explicitly stated. R2 required parity with VSH-006's urgent queue model; VSH-006 uses "up to 3 times with exponential backoff" and a local urgent queue — the language is directly parallel.

**N2.2 verdict: RESOLVED.**

---

### N2.3 — Reconnect reconciliation (Brain restart re-delivery)

**What the PRD now says (VSH-003):**

> "On Brain reconnect (after rekey or restart), Core reconciliation step: Brain calls Core `/approvals/pending` endpoint and re-fetches all `status: pending` records — Core re-delivers them proactively."

VSH-008 Pending approval state section adds:

> "On Brain restart: Brain calls Core `/approvals/pending` on reconnect; Core re-delivers all `status: pending` records; Brain re-surfaces them to Canvas via WebSocket — no approval prompt silently lost."

VSH-009 Recovery clause (G1 section) also specifies: *"On successful ping, Brain exits degraded state, re-queues `blocked` goals, and re-runs reconciliation (re-fetches pending approvals per N2 fix)."*

The approval state schema in VSH-008 now includes `delivery_attempts` and `last_delivery_at` fields, confirming the delivery tracking is persisted in Core and not ephemeral.

**Assessment:** All three of R2's required elements are present. The reconciliation step is anchored in three stories (VSH-003, VSH-008, VSH-009) with consistent language. VSH-009's recovery clause explicitly ties N2 reconciliation to the G1 degraded-state exit, which is the correct trigger — Brain reconciles on Core reconnect regardless of why degraded state was entered. This is a stronger guarantee than R2 required (R2 only required reconciliation after Brain restart; the PRD now also triggers it after any Core-unreachable recovery).

**N2.3 verdict: RESOLVED — and stronger than required.**

---

### N2 Overall verdict: **FULLY RESOLVED.**

All three sub-requirements (auth, delivery/retry, reconnect reconciliation) are correctly and consistently specified across VSH-003, VSH-008, and VSH-009. The implementation contract is unambiguous.

---

## Part II — Verification of G1 (Unix Socket Liveness and Timeout)

### G1 — Recap of the blocking requirement

R2 required: (1) a per-call timeout with a specific default value; (2) explicit distinction between connection failure and auth failure; (3) defined Goal Loop behavior when Core is unreachable (halt, degraded state, Canvas alert).

---

### G1.1 — Per-call timeout with specific default

**Where addressed:** VSH-009 (new subsection "Unix socket liveness and timeout contract"), VSH-009 Acceptance Criteria, VSH-009 Safety Gates.

**What the PRD now says:**

> "Per-call timeout: 30 seconds (default); configurable via `VASHION_SOCKET_TIMEOUT_S`. Applies to every Unix socket call without exception."

VSH-009 Acceptance Criteria: *"Per-call Unix socket timeout: 30s default — no call waits indefinitely."*

VSH-009 Safety Gates: *"No Unix socket call waits indefinitely — 30s timeout is enforced without exception."*

The graph's MCP node label has been updated to "30s socket timeout / conn-refused|auth-reject|hang → degraded", confirming alignment between graph and prose.

**Assessment:** The specific value (30s) is stated three times in the PRD and once in the graph. The configurable override is provided via environment variable, with the default stated. R2 required "a specific value, not 'configurable' without a default" — this is satisfied.

**G1.1 verdict: RESOLVED.**

---

### G1.2 — Distinction between connection failure and auth failure

**What the PRD now says (VSH-009):**

A three-row table explicitly maps each failure mode to detection mechanism and Brain response:

| Failure mode | Detection | Brain response |
|---|---|---|
| Connection refused (Core not running) | Socket connect fails immediately | Enter degraded state, alert Canvas, halt goal acceptance |
| Auth rejection (epoch mismatch) | Connected, `401`-equivalent returned | Enter degraded state, trigger rekey protocol (VSH-005) |
| Hang (Core alive but unresponsive) | Per-call timeout exceeded | Enter degraded state, alert Canvas, halt goal acceptance |

VSH-009 Safety Gates: *"Timeout and connection-refused never trigger the rekey protocol — only auth rejection does."*

**Assessment:** This is precisely what R2 required. The three failure modes are named, their detection mechanisms distinguished (connect failure vs. connected-then-rejected vs. timeout), and their responses differentiated (connection failure and hang → degraded only; auth rejection → degraded + rekey). The safety gate confirms the critical invariant: the rekey protocol is not triggered by timeouts or connection failures, preventing the infinite-rekey-loop failure mode identified in R2.

**G1.2 verdict: RESOLVED.**

---

### G1.3 — Goal Loop behavior when Core is unreachable

**Where addressed:** VSH-009 (timeout/degraded state description), VSH-010 (new "Core unreachable — Goal Loop behavior" subsection).

**What the PRD now says (VSH-010):**

> "When VSH-009 enters degraded state (Core unreachable), the Goal Loop immediately halts acceptance of new goals from all sources (Web, heartbeat, scheduler). Active goals that have not yet issued a Core call: transition to `blocked`, locks released. Active goals mid-Core-call: the call times out (30s per G1/VSH-009); goal transitions to `failed` with `{ reason: 'core_unreachable' }`. When Core becomes reachable again: Goal Loop resumes, `blocked` goals re-queued at their original priority, operator notified via Canvas."

VSH-009 Degraded state behavior: *"Brain stops accepting new goals from the Goal Loop; in-flight goals that have not yet issued a Core call are paused at `blocked` status; the Canvas health endpoint reflects `core: unreachable`."*

VSH-010 Safety Gates: *"Core unreachable = Goal Loop halts immediately — no goals execute against an unreachable Core."*

**Assessment:** R2 required: halt goal acceptance, enter degraded state, alert via Canvas health endpoint. All three are present. Additionally, the PRD now distinguishes two sub-cases within "active goals during Core outage" — goals that haven't yet called Core (held at `blocked`) vs. goals mid-Core-call (failed closed). This is a correct and more complete specification than R2 required. The recovery path is also specified: `blocked` goals re-queued at original priority on Core return.

**G1.3 verdict: RESOLVED — and more complete than required.**

---

### G1 Overall verdict: **FULLY RESOLVED.**

All three sub-requirements (specific timeout, failure mode distinction, Goal Loop behavior) are correctly specified. The three-row failure-mode table in VSH-009 is a clean, implementation-ready contract. The separation of concerns between VSH-009 (socket behavior) and VSH-010 (Goal Loop policy on Core outage) is architecturally sound.

---

## Part III — Re-evaluation of the 10 R2 Minor Items

For each minor item, the question is: has the v3.1 amendment text aggravated, resolved, or left it unchanged? Items already assessed as minor and flagged for pre-Phase-2 resolution remain in that category unless the new text changes the risk.

---

### N1 — `data_sensitivity=credentials` Tier 3 over-blocks internal Core auth reads (VSH-003, VSH-005)

**Change in v3.1:** None. The Tier 3 classification rules are unchanged. The amendment text is additive (new subsection on webhook auth and delivery).

**Re-evaluation:** The gap identified in R2 persists. Internal Core operations reading `core.token` remain technically Tier 3 under the current classifier if `data_sensitivity=credentials` applies to internal reads. No exemption for Core-internal operations has been stated.

**However:** Phase 1 builds only Core. The VSH-005 token read occurs during Core's own startup sequence — the classifier (VSH-003) evaluates *incoming action requests* from external callers (Brain, via the Unix socket). Core's own internal startup operations are not classified by the firewall in the same call path. This architectural reality reduces the practical risk of N1 during Phase 1. The ambiguity remains relevant for Phase 2 integration testing, when Brain may request operations that read credential-adjacent data.

**Severity: Unchanged — MINOR.** Not escalated. Must still be clarified before VSH-005 and VSH-003 integration tests in Phase 2.

---

### N3 — Approval decision path (user → Canvas → ?) ambiguous (VSH-013, VSH-015)

**Change in v3.1:** VSH-008 now states: *"On Brain restart: Brain calls Core `/approvals/pending` on reconnect; Core re-delivers all `status: pending` records; Brain re-surfaces them to Canvas via WebSocket — no approval prompt silently lost."* VSH-003 specifies that Core delivers approval *requests* to Brain. VSH-015 lists "Tier 2 approval/denial" as messages received from the client.

**Re-evaluation:** The N2 fix clarified the Core→Brain approval *request* delivery path. But N3's concern was the reverse path: user approves/denies in Canvas → where does that decision go? The v3.1 text does not explicitly specify whether the approval *decision* travels Web → Brain → Core, or Web → Core directly.

VSH-015 confirms the WS receives approval/denial from the client. VSH-013 states it proxies "all requests to Brain" — but approval decisions are a special case where the actual state change lives in Core (VSH-008). The routing of the decision from Web to Core remains unspecified in the PRD text.

**Severity: Unchanged — MINOR.** The v3.1 amendments did not aggravate this; they also did not resolve it. It remains a Phase 2/3 integration boundary concern.

---

### N4 — `sudo` block scope overstated for shell-mode invocations (VSH-001)

**Change in v3.1:** None. VSH-001 is unchanged.

**Re-evaluation:** The gap persists. VSH-001 states "`sudo` blocked unconditionally at this layer" without specifying whether this covers shell-mode scripts containing `sudo`, or `sudo`-equivalent commands (`pkexec`, `doas`). Phase 1 builds this story; the overstatement exists in the Phase 1 deliverable.

**Severity: Unchanged — MINOR.** The language overstates the guarantee for shell-mode invocations. This is a Phase 1 story, but the practical risk is low because shell-mode requires an explicit flag and the scope of Phase 1 testing is controlled.

---

### N5 — `BEH → BRAIN_ENTRY` auto-approve arrow implies Brain has independent approval authority (VSH-003, VSH-009)

**Change in v3.1:** The graph's `BEH →|Tier 2 auto-approve logic| BRAIN_ENTRY` edge is unchanged in the companion `Build_PRD_graph.md`. The PRD inline diagram (lines 648) also retains this edge verbatim.

**Re-evaluation:** This is the same structural ambiguity identified in R2. The v3.1 amendments did not address it. With the N2 fix now clarifying that Core initiates approval *requests* to Brain and Brain re-surfaces them to Canvas, the question of whether Brain has *independent auto-approval authority* (as the graph's `BEH → BRAIN_ENTRY` edge implies) becomes marginally more relevant — because if Brain does have independent authority, it must validate against its own Behavior.md policy before forwarding to the user, and this would need to be sequenced with Core's `AutoApprovalPolicy`. The current text leaves this unresolved.

**Severity: Unchanged — MINOR.** The v3.1 amendments did not aggravate the core ambiguity but increased its relevance slightly due to the N2 clarifications. Still not a Phase 1 blocker. Recommend resolving before Phase 2 Brain implementation begins, as VSH-009/VSH-010 authors will need clarity on whether Brain participates in the auto-approval decision.

---

### G2 — Brain entry point `:7475` and Unix socket have no defined rate limiting (VSH-009, VSH-013)

**Change in v3.1:** None. Rate limiting remains defined only at the Canvas Backend layer (VSH-015).

**Re-evaluation:** The N2 fix established that Core's webhook calls to `127.0.0.1:7475/approvals` are authenticated (bearer token). This slightly reduces the risk profile for G2: an unauthenticated local process cannot generate a valid approval request flood, because Brain now validates the token. However, a compromised Brain process — or a script with access to the token file — could still flood `:7475` directly. The "local-only trust boundary" assumption remains unstated as a documented design decision.

**Severity: Unchanged — MINOR.** Authentication on Core→Brain calls slightly reduces, but does not eliminate, the concern. Still requires a documented security assumption or explicit rate limiting decision.

---

### G3 — Checkpoint format, schema version, integrity check unspecified (VSH-007)

**Change in v3.1:** None. VSH-007 is unchanged.

**Re-evaluation:** Unchanged. The on-disk checkpoint format, schema versioning, and integrity check remain unspecified. Phase 1 builds this story. The gap is real but not a Phase 1 architectural blocker — the checkpoint can be built with a format and version field even without a PRD mandate, and added to the spec retroactively before Phase 2.

**Severity: Unchanged — MINOR.**

---

### G4 — Memory context budget: no default value, communication mechanism, narrative edge case (VSH-008, VSH-011)

**Change in v3.1:** None in the relevant stories. VSH-008 and VSH-011 context budget text is unchanged.

**Re-evaluation:** Unchanged. The context budget default, how Brain learns it, per-goal vs per-session scope, and the narrative truncation contradiction remain unspecified. Phase 2 story (VSH-011) is not built in Phase 1, so this does not block Phase 1.

**Severity: Unchanged — MINOR.**

---

### G5 — LLM fallback chain: no provider-enabled check, no Goal Loop behavior on full halt (VSH-009, VSH-010)

**Change in v3.1:** VSH-010 now has a "Core unreachable" subsection. This is specifically for Core outage, not LLM outage. VSH-009's LLM failure mode text is unchanged: *"retry once → fallback chain (Anthropic → OpenAI → Ollama) → halt."*

**Re-evaluation:** The Core outage behavior is now fully specified (G1 fix). However, the LLM-specific halt behavior remains underspecified. If all three LLM providers fail, VSH-009 says "halt" and VSH-010 does not define what the Goal Loop does — in contrast to the Core-outage case which is now fully defined. This asymmetry is newly visible because G1's fix sets a higher specificity standard for failure-mode handling that G5 does not yet meet.

**Severity: Remains MINOR but worth flagging.** The G1 fix creates an implicit expectation that LLM halt behavior should be equally specified. The gap is a Phase 2 issue (Brain layer), not Phase 1.

---

### G6 — SQLite memory.db: no size management, WAL checkpoint on shutdown (VSH-008)

**Change in v3.1:** None. VSH-008 is amended only in the pending approval state section.

**Re-evaluation:** Unchanged. The approval state schema now includes two additional fields (`delivery_attempts`, `last_delivery_at`), which increases the per-record size slightly — but this is negligible relative to the broader concern about unbounded database growth from episodic entries and audit logs.

**Severity: Unchanged — MINOR.**

---

### G7 — Soul.md / Behavior.md: no versioning, no integrity hash, hot-reload policy undefined (VSH-010, VSH-012)

**Change in v3.1:** None. Governance file handling is unchanged.

**Re-evaluation:** Unchanged. VSH-010 still reads: *"Soul.md / Behavior.md loaded at startup — missing file is a hard error."* The hot-reload policy and integrity-check questions remain open.

**Severity: Unchanged — MINOR.**

---

### Summary of minor item re-evaluation

No minor item has escalated to blocking as a result of the v3.1 amendments. G5 is noted as having increased prominence due to the contrast with the now-fully-specified G1 behavior, but it remains minor. All ten minor items remain in the pre-Phase-2 resolution queue as originally categorized.

---

## Part IV — New Gaps Introduced by v3.1 Amendments

A fresh pass over the v3.1-added text identifies the following.

---

### R3-O1 — Graph diagram in PRD body is inconsistent with the companion graph document on webhook auth labels (VSH-003, VSH-013) — MINOR

**Issue:** The companion document `Build_PRD_graph.md` has been updated. Its `HB` node label now reads: `HB →|Webhook POST /events Bearer token + traceparent| BRAIN_ENTRY` and `FW →|Webhook POST /approvals Bearer token + retry queue| BRAIN_ENTRY` — correctly reflecting the N2 auth and delivery guarantee fix.

However, the inline Mermaid diagram embedded directly in `Build_PRD.md` (lines 624–626) still reads:

```
HB -->|Webhook POST /events traceparent| BRAIN_ENTRY
FW -->|Webhook POST /approvals| BRAIN_ENTRY
```

The `FW → BRAIN_ENTRY` arrow has no auth or delivery label whatsoever. The `HB → BRAIN_ENTRY` arrow is missing the "Bearer token" component. The inline diagram was not synchronized with the companion document when the N2 amendment was applied.

**Risk:** Implementation teams reading the PRD's embedded diagram (the more likely reference during day-to-day development) will not see the authentication or retry queue requirements on these edges. They may implement unauthenticated webhook calls from Core to Brain, recreating exactly the vulnerability N2 was designed to close.

**Required amendment:** Update the inline Mermaid diagram in `Build_PRD.md` to match the companion document's edge labels — specifically, add `Bearer token` to both the `/events` and `/approvals` arrows, and add the `retry queue` label to the `/approvals` arrow. Alternatively, remove the inline diagram and add a reference to `Build_PRD_graph.md` as the canonical graph.

**Severity: MINOR** — Does not introduce a new architectural gap (the prose in VSH-003 is clear), but creates a documentation inconsistency that will cause implementation confusion. Given that N2 was a blocking item, ensuring its fix is consistently represented in all diagram artifacts is important.

---

### R3-O2 — `BRAIN_ENTRY → MEM_S` reconnect call path is implied but not graphed (VSH-003, VSH-008, VSH-009) — MINOR

**Issue:** VSH-003 and VSH-008 specify that Brain calls Core `/approvals/pending` on reconnect. VSH-009 specifies that Brain does this as part of the degraded-state recovery ping cycle. This creates a Brain→Core call path for approval reconciliation.

The graph (both the companion document and the inline diagram) shows Brain→Core communication only via `MCP →|Unix socket + token|` to SHELL, DOCKER, FILES, MEM_S. The reconciliation call — Brain calling Core's `/approvals/pending` REST-like endpoint — is not a Unix socket call to MEM_S; it is a query that presumably also goes through the Unix socket memory API (VSH-008 states Brain accesses MEM_S exclusively via Unix socket). But the reconciliation trigger (degraded-state exit) and the specific call (`GET /approvals/pending`) are not represented in the graph.

This is a minor omission — the call is captured in prose — but worth noting for completeness.

**Required clarification:** Confirm that the `/approvals/pending` query is served by MEM_S via the existing Unix socket memory API (not a separate HTTP endpoint from Core to Brain). If so, no new endpoint is needed; the reconciliation is just a MEM_S query on reconnect, which is already graphed. The PRD's use of `/approvals/pending` notation implies an HTTP endpoint rather than a Unix socket query — this should be made explicit in VSH-008 or VSH-003.

**Severity: MINOR** — Implementation teams need clarity on whether this is a new Core endpoint or a query over the existing Unix socket API.

---

## Part V — Summary Scorecard

### Blocking Items Resolution

| Item | R2 description | R3 status |
|---|---|---|
| N2 | Core→Brain `/approvals` webhook: no auth, no delivery guarantee, no reconnect re-delivery | **FULLY RESOLVED** |
| G1 | Unix socket: no per-call timeout, no hang detection, no Goal Loop behavior on Core outage | **FULLY RESOLVED** |

---

### Minor Items from R2 — Re-evaluation

| ID | R2 description | R3 status | Phase target |
|---|---|---|---|
| N1 | `credentials` Tier 3 over-blocks Core-internal auth reads | Unchanged — MINOR | Pre-Phase-2 |
| N3 | Approval decision path (user → Web → ?) routing unspecified | Unchanged — MINOR | Pre-Phase-2/3 boundary |
| N4 | `sudo` block scope overstated for shell-mode invocations | Unchanged — MINOR | Pre-Phase-1 test |
| N5 | `BEH → BRAIN_ENTRY` implies Brain has independent auto-approval authority | Unchanged — MINOR | Pre-Phase-2 |
| G2 | Brain `:7475` and Unix socket rate limiting undocumented | Unchanged — MINOR | Pre-Phase-2 |
| G3 | Checkpoint format, schema version, integrity check unspecified | Unchanged — MINOR | Pre-Phase-1 deploy |
| G4 | Context budget: no default, no communication mechanism, narrative edge case | Unchanged — MINOR | Pre-Phase-2 |
| G5 | LLM fallback: no provider-enabled check, Goal Loop behavior on LLM halt unspecified | Unchanged — MINOR | Pre-Phase-2 |
| G6 | SQLite growth management and WAL checkpoint on shutdown unspecified | Unchanged — MINOR | Pre-production deploy |
| G7 | Governance files: no versioning, no integrity hash, hot-reload policy undefined | Unchanged — MINOR | Pre-Phase-2 |

No minor item has escalated to blocking.

---

### New Items Introduced by v3.1

| ID | Description | Severity | Stories affected |
|---|---|---|---|
| R3-O1 | Inline Mermaid diagram in `Build_PRD.md` not synchronized with companion graph — `/approvals` and `/events` edges missing auth and retry labels | MINOR | VSH-003, VSH-013 |
| R3-O2 | `/approvals/pending` reconciliation call: HTTP endpoint vs Unix socket API ambiguity unresolved | MINOR | VSH-003, VSH-008, VSH-009 |

Both new items are minor. Neither introduces an architectural gap — the prose specifications are clear. They are documentation consistency and implementation-clarity issues.

---

## Part VI — Overall Verdict

**APPROVED FOR PHASE 1 IMPLEMENTATION.**

This is a clean approval with no blocking conditions.

N2 and G1 are correctly and fully resolved. The team did not produce superficial patches — the amendments are substantive, internally consistent, and reinforced across multiple stories. Specifically:

- The N2 fix introduces a complete webhook auth and delivery contract in VSH-003, reinforced by VSH-008's approval state schema (which now tracks delivery attempts and timestamps) and VSH-009's degraded-state recovery clause (which ties N2 reconciliation to the G1 recovery ping). These three stories are now coherent as a system.
- The G1 fix introduces a three-row failure-mode table in VSH-009 that distinguishes connection failure, auth failure, and hang — each with a distinct, non-overlapping response. VSH-010's new "Core unreachable" subsection specifies Goal Loop behavior with the same level of specificity as the scheduler contract established to resolve R1-B6. The cross-reference between VSH-009 and VSH-010 is explicit and correct.

None of the ten R2 minor items have escalated to blocking. G5 is noted as having slightly increased salience due to the specificity standard set by the G1 fix, but it remains a Phase 2 concern.

The two new minor items (R3-O1, R3-O2) are documentation issues, not architectural gaps. R3-O1 in particular should be corrected promptly — an out-of-sync diagram on a resolved blocking item is a maintenance liability.

**Recommended next actions:**

1. Correct R3-O1 (inline diagram sync) in a patch edit before any implementation work begins on VSH-003 or VSH-006. This is a 10-minute edit with no design decision required.
2. Clarify R3-O2 (Unix socket vs HTTP endpoint for `/approvals/pending`) in VSH-008 or VSH-003 before VSH-008 implementation begins.
3. Resolve N3, N5, G3, and G4 before Phase 2 implementation begins — as recommended in R2, these sit at Phase 1/2 integration boundaries and will cause rework if left open.
4. Resolve N4 before Phase 1 acceptance testing — the `sudo` language overstates the guarantee for the story being built.

Phase 1 (VSH-001 through VSH-008) may proceed.

---

*End of CTO Engineering Review — Round 3*
