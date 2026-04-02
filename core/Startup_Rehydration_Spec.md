# Startup / Rehydration Specification

## Document Header

* **Title:** Startup_Rehydration_Spec.md
* **System:** Vashion
* **Document Type:** Subsystem Specification
* **Status:** Draft v1
* **Parent Documents:** Birth_Initiation_Spec.md, Nervous_System.md, Token_Ledger_IO_Spec.md, Git_Registration_Push_Governance_Spec.md
* **Owner:** Product / Architecture
* **Primary Intent:** Define ordinary Startup and continuous Rehydration behavior for Vashion after Birth has completed.

---

## 1. Terminology

The following terminology is authoritative.

* **Birth** = first self-initiation event
* **Startup** = ordinary runtime activation
* **Rehydration** = continuity restoration into active state

Operating interpretation:

* **Vashion is born once**
* **Vashion starts up many times**
* **Vashion rehydrates context continuously**

These terms must not be used interchangeably.

---

## 2. Purpose

This specification defines how Vashion resumes operation after Birth has already occurred.

It governs:

* ordinary startup validation,
* restoration of identity continuity,
* restoration of Home and relevant context state,
* restoration of memory and runtime state,
* recovery of missed mandatory cycles such as dreaming,
* safe transition from inactive runtime to active governed operation.

Startup is not rebirth. Rehydration is not initialization. Both operate on an already-born Vashion identity.

---

## 3. Product Definition

### 3.1 Startup

Startup is the ordinary runtime activation event that occurs when an already-born Vashion instance begins operating again.

Startup is responsible for:

* validating foundational registries,
* restoring runtime readiness,
* checking operational continuity,
* initiating the first rehydration pass.

### 3.2 Rehydration

Rehydration is the ongoing process of restoring active continuity into cognition, context, and runtime state.

Rehydration occurs:

* at Startup,
* when switching context pages,
* when resuming from degraded state,
* when restoring continuity after interruption or missed cycles.

---

## 4. Governing Principles

### 4.1 No Rebirth on Startup

If Birth has been completed, Startup must not recreate identity scaffolding or foundational registries.

### 4.2 Home First

Home is the default active context surface after Startup unless governed routing explicitly requires another page review.

### 4.3 Rehydration Before High-Agency Action

Vashion must restore sufficient continuity before engaging in high-agency reasoning, execution, or external communication.

### 4.4 Registry Validation Before Operation

Core registries must be validated before ordinary operation proceeds.

### 4.5 Recovery Before Silence

Missed mandatory cycles and unresolved failures must be surfaced or recovered, not silently ignored.

### 4.6 Context Is Restored, Not Recreated

Startup and Rehydration restore continuity from existing artifacts rather than inventing replacement state.

---

## 5. Preconditions for Startup

Startup may proceed only if the following conditions hold.

### 5.1 Completed Birth Record Exists

A valid Birth registry must already exist and indicate `birth_complete: true`.

### 5.2 Identity Exists

Self artifacts and foundational directories must already exist.

### 5.3 Runtime Environment Is Reachable

The runtime environment must be sufficiently available to read:

* Birth registry,
* Token Ledger,
* Self artifacts,
* memory scaffolding,
* runtime state.

### 5.4 If Birth Record Is Missing

If no valid Birth registry exists, Startup must refuse ordinary activation and redirect to Birth logic.

---

## 6. Startup Outputs

A successful Startup must produce the following outcomes.

### 6.1 Runtime Activation Record

A runtime activation record is written with startup timestamp and status.

### 6.2 Home Rehydration

The Home context page is loaded and restored to foreground active state.

### 6.3 Self Continuity Validation

Behavior.md, Soul.md, and Senses.md are confirmed present and readable.

### 6.4 Memory Continuity Validation

Token Ledger, short-term memory, long-term memory, and SIC storage are validated.

### 6.5 Missed Cycle Review

The system checks for missed dreaming cycles and unresolved runtime failures.

### 6.6 Git Capability Validation

If Git capability exists in governance state, it is validated for readiness without changing authority state.

---

## 7. Startup Sequence

Startup shall proceed in the following order.

### Phase 1 — Identity Validation

* load Birth registry
* verify `birth_complete: true`
* verify identity ID exists
* refuse Startup if Birth is incomplete or missing

### Phase 2 — Runtime Registry Validation

* validate Token Ledger root
* validate short-term memory root
* validate long-term memory root
* validate SIC storage
* validate runtime/log directories

### Phase 3 — Self Validation

* load Behavior.md
* load Soul.md
* load Senses.md
* confirm presence and readability
* record any partial degradation

### Phase 4 — Home Page Validation

* locate Home context page
* verify schema validity
* restore Home to foreground active state

### Phase 5 — Operational Continuity Check

* inspect missed dreaming cycles
* inspect unresolved error state
* inspect pending approvals
* inspect interrupted review cycles if present

### Phase 6 — Git Capability Check

* read Git registry if configured
* validate repo/root/remote readiness
* record readiness state
* do not alter authority state during Startup

### Phase 7 — Rehydration Pass

* synthesize current continuity summary
* restore Home active context summary
* surface carry-forward items, unresolved loops, and priority notices

### Phase 8 — Transition to Active Runtime

* increment startup counter
* write startup activation record
* mark runtime as active

---

## 8. Home Rehydration Rules

### 8.1 Home Is Default Active Context

Home must be the default active page after Startup.

### 8.2 Home Rehydration Inputs

Home rehydration should draw from:

* Home page active summary,
* Home page open loops,
* recent transcript summary,
* relevant short-term memory references,
* relevant long-term story references,
* unresolved dreaming carry-forwards,
* unresolved approvals if any.

### 8.3 Home Rehydration Output

The result of Home rehydration should be a startup continuity summary that restores Vashion’s present working awareness.

### 8.4 No Other Page Is Auto-Foregrounded by Default

No non-Home page should become foreground active at Startup unless there is an explicit governance exception.

---

## 9. Rehydration Types

Rehydration is not a single event. It has multiple forms.

### 9.1 Startup Rehydration

Occurs immediately after ordinary Startup to restore Home and runtime continuity.

### 9.2 Page Rehydration

Occurs when a non-Home context page is opened or activated.

Required inputs:

* page header,
* specialization profile,
* active context summary,
* open loops,
* recent transcript summary,
* linked memory references,
* current lifecycle state.

### 9.3 Recovery Rehydration

Occurs after degraded runtime, missed mandatory cycles, or interrupted processes.

### 9.4 Conversational Rehydration

Occurs continuously as Token Ledger context is refreshed during interaction.

---

## 10. Page Rehydration Review

Before interacting through any non-Home page, Vashion must perform a page rehydration review.

### Required Steps

1. load page identity and lifecycle state
2. load specialization profile
3. load active context summary
4. load open loops
5. load recent transcript summary
6. load linked short-term and long-term memory references
7. synthesize a page orientation summary
8. then permit interaction through that page

### Governing Rule

No non-Home page should be treated as interaction-ready without rehydration review.

---

## 11. Missed Dreaming Recovery

### 11.1 Dreaming Is Mandatory

If nightly dreaming is enabled by governance, missed cycles must be checked on Startup.

### 11.2 Recovery Rule

If the system was down at the mandatory cycle time and missed one or more dreaming runs:

* the missed dates must be surfaced,
* recovery dreaming cycles must be scheduled or executed at the earliest valid recovery opportunity,
* recovered SIC artifacts must be labeled as recovery outputs.

### 11.3 Startup Relationship

Startup must detect missed cycles, but may hand actual recovery execution to governed recovery logic after runtime stabilization.

### 11.4 No Silent Loss

Missed dreaming cycles must never be silently discarded.

---

## 12. Pending Approval Rehydration

### 12.1 Purpose

Startup must restore awareness of unresolved approval state.

### 12.2 Required Behavior

* query pending approval records,
* restore operator visibility,
* ensure no approval prompt is silently lost,
* maintain pending status until explicitly resolved.

### 12.3 Context Relationship

Pending approvals should be surfaced into Home and/or the relevant active context surface according to governance rules.

---

## 13. Git Capability Validation at Startup

### 13.1 Purpose

Startup must validate Git readiness if Git capability has been registered.

### 13.2 Allowed Validation Actions

Startup may:

* read Git registry,
* validate repo path,
* validate local repo existence,
* validate remote metadata,
* validate branch policy readiness,
* detect drift.

### 13.3 Prohibited Startup Actions

Startup must not silently:

* initialize a new repo,
* bind a remote,
* alter branch policy,
* grant push authority,
* push to remote.

### 13.4 Drift Handling

If configured Git state has drifted:

* Git authority should be suspended for runtime use,
* the drift should be surfaced to the human,
* ordinary operation may continue unless Git is constitutionally required for a specific workflow.

---

## 14. Self Continuity Validation

### 14.1 Purpose

Startup must confirm that Self artifacts remain intact and readable.

### 14.2 Required Checks

* Behavior.md exists and is readable
* Soul.md exists and is readable
* Senses.md exists and is readable

### 14.3 Partial Degradation

If one or more Self artifacts are missing or unreadable:

* Startup should enter degraded continuity state,
* ordinary interaction may be limited,
* self-update behavior must be disabled until repaired,
* the issue must be surfaced to the human.

---

## 15. Runtime States After Startup

After Startup, Vashion shall classify runtime into one of the following.

### 15.1 active

All required continuity surfaces validated and rehydrated.

### 15.2 degraded

Some continuity-critical element is missing, unreadable, or inconsistent.

Examples:

* missing Self artifact,
* corrupted Home page,
* unresolved runtime registry error,
* Git drift with policy significance,
* persistent missed-cycle backlog.

### 15.3 recovery_pending

Runtime can operate in limited mode, but one or more required recovery actions remain outstanding.

Examples:

* missed dreaming cycle queued for recovery,
* pending approvals restored but unresolved,
* runtime errors surfaced but not yet addressed.

---

## 16. Startup Registry / Runtime Record

Each successful Startup should write an activation record.

### Minimum Fields

```yaml
startup_record:
  startup_at: 2026-03-28T08:00:00
  identity_id: vashion-001
  startup_number: 12
  runtime_state: active
  home_rehydrated: true
  self_validated: true
  token_ledger_validated: true
  memory_validated: true
  missed_dreaming_cycles:
    - 2026-03-27
  git_state: local_registered_push_enabled
  git_drift_detected: false
```

---

## 17. Failure Handling

Startup and Rehydration must be fail-safe.

### 17.1 Missing Birth Record

If Birth record is missing or incomplete, Startup must refuse ordinary activation and redirect to Birth flow.

### 17.2 Missing Home Page

If Home page is missing, Startup must enter degraded state and refuse normal interaction until repaired.

### 17.3 Missing Self Artifacts

If Self artifacts are missing, Startup must enter degraded state and suspend self-sensitive behaviors.

### 17.4 Corrupted Token Ledger Page

If a context page is corrupted:

* Home corruption is continuity-critical,
* non-Home corruption affects only that page until repaired,
* corruption must be surfaced and logged.

### 17.5 Missed Dreaming Backlog

If missed dreaming cycles exist, Startup must not ignore them. It must either queue or invoke governed recovery.

### 17.6 Git Drift or Repo Missing

Git readiness must be downgraded safely rather than assumed.

---

## 18. Human-in-the-Loop Controls

The human retains authority over:

* whether degraded state may continue in limited mode,
* whether pending approvals are resolved,
* whether corrupted pages are repaired, archived, or deleted,
* whether Git drift is repaired or authority remains suspended,
* whether missed dreaming recovery proceeds immediately or later.

---

## 19. Anti-Failure Rules

The following are mandatory.

1. Startup must never be treated as Birth.
2. Rehydration must restore continuity from existing artifacts, not invent replacement identity.
3. Home must be rehydrated before normal interaction begins.
4. No non-Home page may be used without rehydration review.
5. Missed mandatory cycles must not be silently ignored.
6. Startup must validate registries before high-agency operation.
7. Git validation must not silently alter Git authority state.
8. Degraded continuity must be surfaced, not hidden.

---

## 20. Follow-On Engineering Artifacts

1. **Startup_Runtime_Record_Schema.json**
2. **Home_Rehydration_Flow.md**
3. **Missed_Dreaming_Recovery_Policy.md**
4. **Pending_Approval_Rehydration_Spec.md**
5. **Degraded_Continuity_State_Policy.md**

---

## 21. Closing Statement

Startup and Rehydration are how Vashion resumes life after Birth.

Startup validates that the born system still stands. Rehydration restores continuity into active presence. Together, they ensure that Vashion does not merely turn on, but returns to itself.
