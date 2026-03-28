# Vashion — CTO Engineering Review, Round 4
**Document:** Build_PRD.md v3.2 + Build_PRD_graph.md (companion)
**Review Date:** 2026-03-27
**Reviewer:** CTO
**Scope:** Verification that R3-O1 (inline diagram sync) and R3-O2 (approvals/pending clarified as Unix socket query) are correctly resolved in v3.2; check for any new issues introduced by the patch; final Phase 1 verdict.

---

## Preamble

Round 3 approved Phase 1 with no blocking conditions and identified two minor items in the v3.1 patch text:

- **R3-O1:** The inline Mermaid diagram embedded in `Build_PRD.md` was out of sync with `Build_PRD_graph.md` — the `/events` and `/approvals` edges from Core to Brain were missing the `Bearer token` and `retry queue` labels that the N2 fix required.
- **R3-O2:** The prose describing the reconnect reconciliation step referred to a `Core /approvals/pending` endpoint using HTTP-style notation, creating ambiguity about whether this was a new HTTP endpoint or a query over the existing Unix socket memory API.

v3.2 claims to resolve both. This review verifies those claims.

All items from R1 through R3, and the ten standing minor items from R2, are not re-examined here. They carry their prior verdicts forward.

---

## Part I — Verification of R3-O1 (Inline Diagram Sync)

### R3-O1 — Recap

R3 found that the inline Mermaid diagram in `Build_PRD.md` (lines 624–626 as of v3.1) still read:

```
HB -->|Webhook POST /events traceparent| BRAIN_ENTRY
FW -->|Webhook POST /approvals| BRAIN_ENTRY
```

The `FW → BRAIN_ENTRY` arrow had no auth or delivery label. The `HB → BRAIN_ENTRY` arrow was missing the "Bearer token" component. The companion document `Build_PRD_graph.md` was already correct.

---

### What v3.2 changed

The inline diagram in `Build_PRD.md` (lines 623–627 of v3.2) now reads:

```
%% Core → Brain (push) — all webhook calls authenticated with session token
HB -->|Webhook POST /events Bearer token + traceparent| BRAIN_ENTRY
FW -->|Webhook POST /approvals Bearer token + retry queue| BRAIN_ENTRY
CHK -->|dirty startup urgent event| HB
BRAIN_ENTRY -.->|reconnect: pending approvals query Unix socket| MEM_S
```

The comment line `%% Core → Brain (push) — all webhook calls authenticated with session token` has been added as a section header. The `/events` edge now carries `Bearer token + traceparent`, matching the companion document exactly. The `/approvals` edge now carries `Bearer token + retry queue`, also matching the companion document exactly.

The companion document `Build_PRD_graph.md` is unchanged from v3.1 and continues to read the same labels. The two diagrams are now identical on these edges.

**Cross-check with companion document (Build_PRD_graph.md lines 80–81):**

```
HB -->|Webhook POST /events Bearer token + traceparent| BRAIN_ENTRY
FW -->|Webhook POST /approvals Bearer token + retry queue| BRAIN_ENTRY
```

The inline diagram and the companion document are now byte-for-byte identical on these two edges.

**One additional observation:** The MCP node label in the inline diagram (line 562) still reads `MCP["LLM Gateway (MCP)\nVSH-009"]` — it does not include the `30s socket timeout\nconn-refused|auth-reject|hang → degraded` annotations that appear in the companion document (`Build_PRD_graph.md` line 17). This discrepancy was not flagged in R3-O1 (R3-O1 concerned only the webhook auth edges), and the MCP timeout and failure-mode behavior is fully specified in prose at VSH-009. This is a residual cosmetic inconsistency between the two diagrams, but it is not a new issue introduced by the v3.2 patch — it predates R3. It is noted here but not elevated.

**R3-O1 verdict: RESOLVED.** The specific edges flagged in R3-O1 are now correctly and consistently labeled across both the inline diagram and the companion document.

---

## Part II — Verification of R3-O2 (Unix Socket vs. HTTP Endpoint for /approvals/pending)

### R3-O2 — Recap

R3 found that VSH-003 and VSH-008 described the reconnect reconciliation step using notation that implied an HTTP endpoint (`Brain calls Core /approvals/pending on reconnect`). The required clarification was: confirm this is a query over the existing Unix socket memory API, not a new HTTP endpoint from Core to Brain; make this explicit in VSH-003 or VSH-008.

---

### What v3.2 changed

**VSH-003 (lines 127–128 of v3.2):**

The v3.1 text read:

> "On Brain reconnect (after rekey or restart), Core reconciliation step: Brain calls Core `/approvals/pending` on reconnect; Core re-delivers all `status: pending` records; Brain re-surfaces them to Canvas via WebSocket — no approval prompt silently lost."

The v3.2 text now reads:

> "On Brain reconnect (after rekey or restart), Core reconciliation step: Brain issues a `memory_query { filter: "approvals:pending" }` command over the Unix socket to MEM_S — same socket API used by all Brain→Core calls, not a new HTTP endpoint; Core returns all `status: pending` approval records; Brain re-surfaces them to Canvas via WebSocket"

This is a precise and unambiguous resolution. The mechanism is now specified as a `memory_query` command, the transport is stated as the Unix socket, the architectural note "not a new HTTP endpoint" is explicit, and the query maps to the established `memory_query` tool already listed in VSH-009's Acceptance Criteria (`memory_query` is one of the six enumerated MCP tools at line 333).

**VSH-008 — Pending approval state (lines 291 of v3.2):**

The v3.1 text read:

> "On Brain restart: Brain calls Core `/approvals/pending` on reconnect; Core re-delivers all `status: pending` records; Brain re-surfaces them to Canvas via WebSocket — no approval prompt silently lost."

The v3.2 text now reads:

> "On Brain restart: Brain issues `memory_query { filter: "approvals:pending" }` over the Unix socket on reconnect — no new HTTP endpoint; Core returns all `status: pending` records; Brain re-surfaces them to Canvas via WebSocket — no approval prompt silently lost."

This matches the VSH-003 language precisely. Both originating story (VSH-003) and storing story (VSH-008) now use identical, unambiguous language. The `filter: "approvals:pending"` syntax is consistent with the `memory_query` tool already specified in VSH-009.

**Inline diagram (line 627 of v3.2):**

```
BRAIN_ENTRY -.->|reconnect: pending approvals query Unix socket| MEM_S
```

This dashed edge — new in v3.2 — visually represents the reconciliation call path in the inline diagram and correctly shows Brain querying MEM_S directly (dashed, indicating a query/read path rather than a primary command flow), labeled as a Unix socket operation. The companion document `Build_PRD_graph.md` carries the same edge (line 83).

**Internal consistency check:** VSH-009 Acceptance Criteria (line 333) lists `memory_query` as one of the six MCP tools exposed by the Brain gateway, and the reconciliation call uses this tool — no new interface is required. VSH-008 states Brain has no direct SQLite access (line 285, 299); the Unix socket reconciliation path is consistent with this invariant. VSH-009's degraded-state recovery description (line 329) already stated Brain "re-runs reconciliation (re-fetches pending approvals per N2 fix)" — the v3.2 amendment now specifies the exact mechanism, closing the remaining gap.

**R3-O2 verdict: RESOLVED.** The reconciliation mechanism is now unambiguously specified as a `memory_query { filter: "approvals:pending" }` command over the existing Unix socket API. No new endpoint is implied. The specification is internally consistent across VSH-003, VSH-008, VSH-009, and both diagram artifacts.

---

## Part III — New Issues Introduced by the v3.2 Patch

A fresh pass over the v3.2 changes — the VSH-003 reconnect clause, the VSH-008 pending approval state section, and the inline diagram additions — for any self-introduced problems.

---

### P4-O1 — `ActionDescriptor` arrows in Core internal section: diagram inconsistency between inline and companion (minor observation, not new)

**Observation:** The companion document's Core internal section explicitly labels the FW outbound arrows with `ActionDescriptor`-based outcomes (e.g., `FW →|Tier 1: plan_token execute| SHELL`). The inline diagram uses bare `SHELL --> FW`, `DOCKER --> FW`, `FILES --> FW` arrows (lines 630–635 of v3.2) without the tier classification labels. This predates v3.2 and was not introduced by the current patch. It is noted for completeness but is not a new issue from this review cycle.

---

### P4-O2 — No issues introduced by the v3.2 patch

The two substantive changes in v3.2 — the VSH-003 and VSH-008 prose amendments clarifying `memory_query` over Unix socket — are additive and non-breaking. They do not alter any existing interface, change any story's Acceptance Criteria, or create new dependencies. The inline diagram additions (updated edge labels, new dashed reconciliation edge) are consistent with the prose and with the companion document.

The patch is narrow and well-targeted. No new architectural gaps, no new specification contradictions, and no new implementation ambiguities have been introduced.

**New issues from v3.2: NONE.**

---

## Part IV — Standing Minor Items Status Check

The ten R2 minor items and two R3 minor items are carried forward. The v3.2 patch does not touch any of their underlying concerns. A brief confirmation that none have been inadvertently affected:

| ID | Pre-Phase target | Affected by v3.2? |
|---|---|---|
| N1 | Pre-Phase-2 | No — classifier rules unchanged |
| N3 | Pre-Phase-2/3 | No — approval *decision* routing still unspecified; v3.2 addresses *request* reconciliation only |
| N4 | Pre-Phase-1 test | No — VSH-001 sudo language unchanged |
| N5 | Pre-Phase-2 | No — `BEH → BRAIN_ENTRY` auto-approve ambiguity unchanged |
| G2 | Pre-Phase-2 | No — rate limiting on `:7475` still undocumented |
| G3 | Pre-Phase-1 deploy | No — checkpoint format unchanged |
| G4 | Pre-Phase-2 | No — context budget default unchanged |
| G5 | Pre-Phase-2 | No — LLM halt behavior unchanged |
| G6 | Pre-production | No — SQLite growth management unchanged |
| G7 | Pre-Phase-2 | No — governance file versioning unchanged |
| R3-O1 | Pre-implementation | **RESOLVED in v3.2** |
| R3-O2 | Pre-VSH-008 impl | **RESOLVED in v3.2** |

No minor item has escalated. The ten standing items remain in the pre-Phase-2 resolution queue as originally categorized. The two R3 items are now closed.

---

## Part V — Summary Scorecard

### R3 Minor Items Resolution

| Item | R3 description | R4 status |
|---|---|---|
| R3-O1 | Inline diagram webhook edges missing Bearer token and retry queue labels | **RESOLVED** |
| R3-O2 | `/approvals/pending` reconciliation: HTTP endpoint vs Unix socket API ambiguous | **RESOLVED** |

### Blocking Items (cumulative)

| Round | Item | Status |
|---|---|---|
| R1 | B1–B9 (9 items) | Resolved in v3 |
| R2 | N2, G1 | Resolved in v3.1 |
| R3 | None blocking | — |
| R4 | None blocking | — |

### New Issues This Round

None.

---

## Part VI — Final Verdict

**APPROVED FOR PHASE 1 IMPLEMENTATION. No conditions.**

v3.2 correctly and precisely resolves both R3 minor items.

R3-O1 is resolved cleanly: the inline Mermaid diagram's webhook edges now carry the same `Bearer token` and `retry queue` labels as the companion document, and the section comment makes the auth intent visible to any developer reading the diagram in isolation. The fix required no design decision — it was a documentation sync, and it was executed correctly.

R3-O2 is resolved with more specificity than required: rather than simply confirming "it uses the Unix socket," the v3.2 text supplies the exact API call (`memory_query { filter: "approvals:pending" }`), explicitly states "not a new HTTP endpoint," and applies this language consistently in both VSH-003 and VSH-008. This is the right level of precision — implementers of VSH-008 now have a complete, unambiguous spec for what the reconciliation call looks like, and implementers of VSH-009 can confirm it maps to an already-specified MCP tool.

The patch introduces no new issues. It is narrow, internally consistent, and does not disturb any of the ten standing minor items.

**Recommended next actions (unchanged from R3, with R3-O1 and R3-O2 now struck):**

1. ~~Correct R3-O1 (inline diagram sync)~~ — Done.
2. ~~Clarify R3-O2 (Unix socket vs HTTP for `/approvals/pending`)~~ — Done.
3. Resolve N3, N5, G3, and G4 before Phase 2 implementation begins — these sit at Phase 1/2 integration boundaries and will cause rework if left open.
4. Resolve N4 before Phase 1 acceptance testing — the `sudo` language in VSH-001 still overstates the guarantee for shell-mode invocations.
5. Document the `local-only trust boundary` security assumption for G2 (Brain `:7475` rate limiting) before Phase 2 Brain implementation begins — at minimum, state the assumption explicitly as a design decision in the companion document's Key Design Decisions table.

Phase 1 (VSH-001 through VSH-008) may proceed without reservation.

---

*End of CTO Engineering Review — Round 4*
