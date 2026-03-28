Chief Engineer review:

**Executive verdict:** The proposal is materially stronger than a typical first-pass PRD. The layered split is coherent, the control boundaries are mostly explicit, and the team has done real work to collapse ambiguous paths into single validated entry points. I would rate it **Approve with required design amendments before implementation**. The architecture is directionally sound, but several control-plane behaviors are still under-specified in ways that will create rework, deadlocks, or governance drift in implementation.  

The strongest part of the package is the **clear layer ownership model**: Rust core for deterministic execution and storage, Python brain for planning/context, and TypeScript web for the operator surface. The graph and PRD align on the single validated ingress concept, Core↔Brain over Unix socket plus session token, and Brain memory access only through Core rather than direct SQLite access. That is the correct architectural bias for a governed local-first agent.  

Where the team did especially well:

* They removed several common trust-boundary mistakes by forcing WS and REST through the same validation path and routing plugins through Canvas Backend rather than directly into planning. That materially reduces bypass risk.  
* The **serialized memory write queue** is the right call. Without it, Core/Brain concurrent writes to SQLite would become a race-condition factory.  
* The separation of **checkpoint persistence from memory DB persistence** is correct. Crash recovery must survive DB corruption, and the graph/PRD reflect that.  
* The inclusion of quality gates per service plus an end-to-end trace propagation test is a strong engineering signal. 

That said, I would not let the team start broad implementation without resolving the following.

## 1. Highest-risk architectural issue: Tier-2 “hold after spawn”

The PRD says Tier 2 can return pending and that SHELL “holds the spawned process; resumes on approval signal.” That is the weakest control in the current design. A process should **not be spawned before approval** for any Tier 2 action. “Hold after spawn” is semantically messy and operationally dangerous:

* the process may have already performed side effects before you suspend it,
* suspension semantics differ by process type,
* resource handles, child processes, and partial writes become difficult to reason about,
* restart/recovery semantics get much uglier. 

**Required amendment:** Change Tier 2 behavior from *spawn then hold* to **admission control before execution**. The firewall should produce an executable plan token, not a process token. Execution starts only after approval resolution.

## 2. Policy model is conceptually good, but the classification contract is still too soft

The team defines Tier 1/2/3 and ties governance to Soul.md and Behavior.md. Good. But the current language leaves room for inconsistent classifier behavior because “non-trivial system changes” and “context clearly warrants” are policy prose, not machine-verifiable predicates. 

**Required amendment:** Introduce a deterministic `ActionDescriptor` schema and a classification engine contract:

* `operation_kind`
* `target_scope`
* `reversibility`
* `privilege_required`
* `network_boundary`
* `workspace_boundary`
* `data_sensitivity`
* `estimated_side_effects`

Then define Tier rules against those fields, not prose alone. Soul.md and Behavior.md should tune policy, not replace the classifier.

## 3. Brain auto-approval path needs stronger governance controls

The firewall posts Tier-2 approvals to Brain, and Brain may auto-approve if context warrants; otherwise it surfaces to Canvas. This is acceptable only if the auto-approval path is tightly constrained. Right now, the proposal says rationale is written to episodic memory, but it does not specify a **reviewable decision record format**, nor does it define which action classes are ever eligible for auto-approval. 

**Required amendment:**
Create an explicit `AutoApprovalPolicy`:

* allowlisted action classes only,
* deny-by-default outside allowlist,
* mandatory rationale object,
* decision hash tying approval to exact action parameters,
* TTL and single-use nonce,
* operator-visible audit stream.

Without this, “interrupts only for irreversible decisions” will drift into “agent approves risky things because context seemed sufficient.”

## 4. Auth bootstrap/token rotation is not fully closed

Core writes `core.token`, Brain reads it at startup, and the token rotates on each Core restart. Good baseline. But there is no explicit reconnection protocol for the case where:

* Core restarts while Brain is alive,
* Brain holds stale token state,
* in-flight requests straddle restart boundaries.  

**Required amendment:** Add a token epoch and explicit rekey handshake:

* Core exposes token epoch,
* Brain detects auth mismatch,
* Brain enters degraded state,
* Brain reloads token and rebinds Unix socket client,
* pending calls fail closed with structured retry semantics.

## 5. Heartbeat/event subsystem is useful, but anomaly semantics are underspecified

The proposal uses `> 2σ from baseline` for anomalous CPU or memory. That sounds rigorous, but it is not sufficient as written:

* baseline window undefined,
* cold-start behavior undefined,
* sparse-history behavior undefined,
* burst suppression/dedup undefined,
* multi-process correlation undefined. 

**Required amendment:** Define:

* baseline learning window,
* minimum sample count before z-score logic activates,
* fallback static thresholds before warmup completes,
* event dedup/coalescing policy,
* cooldown windows,
* escalation state machine.

Otherwise you will either miss urgent events or spam the Goal Loop.

## 6. Goal Loop needs concurrency and arbitration rules

The Goal Loop currently tracks queued/active/blocked/complete/failed and accepts work from web, heartbeat, and scheduler. That is fine at a narrative level, but not enough for implementation. There is no explicit policy for:

* maximum concurrent goals,
* priority inversion,
* cancellation precedence,
* heartbeat urgent-event preemption,
* resource locking across goals. 

**Required amendment:** Add a scheduler contract:

* single active mutating goal per protected resource domain,
* priority queue with urgent > operator > scheduled,
* explicit preemption rules,
* lock model for workspace/container/system scopes,
* starvation prevention.

Without this, you will get conflicting writes and governance anomalies.

## 7. Memory architecture is promising, but the durable-promotion model needs sharpening

The team correctly requires `user_approved = true` for durable promotion and keeps Brain off direct SQLite access. Good. But “useful across 3+ sessions” is heuristic language; it is not a durable promotion protocol. 

**Required amendment:** Define promotion as a pipeline:

1. candidate detection,
2. summary normalization,
3. duplication check,
4. operator review bundle,
5. immutable approval record,
6. durable write with provenance backlink to source episodic items.

Also define deletion/edit semantics for durable memory. Right now the PRD covers creation but not lifecycle.

## 8. Shell security needs one more hardening pass

The team blocks `sudo` and rejects shell metacharacters in untrusted input. Good instinct, but a shell-centric execution engine remains a risk concentration point. Rejecting metacharacters alone is not enough because:

* shell expansion and environment inheritance remain attack surfaces,
* “trusted vs untrusted” input boundaries become ambiguous in agentic systems,
* many safe operations should not need shell parsing at all. 

**Required amendment:** Prefer a structured command model:

* direct `execve`-style argv invocation where possible,
* separate typed tool adapters for common operations,
* minimized environment,
* explicit cwd policy,
* explicit allowlist for executables.

Use shell only when shell semantics are actually required.

## 9. Web layer is mostly correct, but approval survivability needs a backing store

The WS bridge states pending approvals survive reconnect. Good requirement, but the current proposal does not say where that state lives or how it is reconciled after Browser refresh/backend restart. 

**Required amendment:** Store pending approval state in Core or a durable control-plane store, not process memory in web. Web should render approval state, not own it.

## 10. Plugin phase deferral is correct

Deferring Slack/Telegram until after Phase 3 is stable is the right decision. Do not move that forward. Plugins multiply ingress complexity, auth surfaces, and event ambiguity. The team’s decision to keep plugin requests flowing through VSH-013 first is correct and should remain non-negotiable.  

## Overall engineering assessment

**What I approve now**

* Layer split and ownership boundaries
* Single validated ingress philosophy
* Core-owned persistence and Brain mediation through MCP
* Checkpoint independence from DB
* Deferred plugin rollout
* End-to-end trace propagation as a gate

**What I would block until amended**

* Tier-2 spawn/hold execution model
* Under-specified policy classifier contract
* Auto-approval without stricter bounded policy
* Missing rekey/reconnect protocol for rotating Core token
* Missing goal scheduler arbitration rules
* Incomplete anomaly-detection contract
* Incomplete durable-memory lifecycle spec


