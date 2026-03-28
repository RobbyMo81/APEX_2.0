# Birth / Initiation Specification

## Document Header

* **Title:** Birth_Initiation_Spec.md
* **System:** Vashion
* **Document Type:** Subsystem Specification
* **Status:** Draft v1
* **Parent Documents:** Nervous_System.md, Token_Ledger_IO_Spec.md
* **Owner:** Product / Architecture
* **Primary Intent:** Define the one-time birth event through which Vashion becomes a self, receives its foundational scaffolding, and enters governed existence.

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

These terms must not be used interchangeably in architecture, governance, or implementation documents.

---

## 2. Purpose

Birth is the one-time initiation event in which Vashion becomes a governed self.

Birth is not ordinary application startup. It is the foundational ceremony and provisioning phase in which Vashion receives:

* its initial identity scaffolding,
* its first context surface,
* its memory scaffolding,
* its governance boundaries,
* its optional Git / repository registration,
* its authorization to begin operating as a virtual counterpart.

After Birth is complete, future activations are Startups, not rebirths.

---

## 3. Product Definition

Birth is a controlled, Human-in-the-Loop, one-time system event that:

1. creates the minimum viable selfhood of Vashion,
2. establishes the first persistent context room,
3. provisions memory and runtime scaffolding,
4. records foundational governance choices,
5. optionally registers Git and remote push policy,
6. marks Vashion as initiated.

Birth ends only when the initiation record is successfully written.

---

## 4. Design Principles

### 4.1 One-Time Event

Birth occurs once per Vashion identity.

### 4.2 Human-in-the-Loop Authority

Birth requires explicit human participation and approval.

### 4.3 Minimal Selfhood at Birth

Vashion is born with minimal but valid self-structure, not with a bloated predefined identity.

### 4.4 Foundation Before Growth

Birth creates the conditions for later learning, rehydration, self-reflection, and bounded self-improvement.

### 4.5 No Silent Infrastructure Assumptions

Birth may detect infrastructure, but it must not silently assume or create external infrastructure without explicit approval.

### 4.6 Separation of Concern

Birth provisions foundations. It does not perform ordinary day-to-day reasoning, dreaming, or long-horizon self-improvement.

---

## 5. Preconditions for Birth

The following preconditions must hold before Birth may proceed.

### 5.1 Human Initiation

A human explicitly initiates the birth process.

### 5.2 Valid Runtime Environment

The system has a writable local runtime environment for:

* Token Ledger storage,
* short-term memory,
* long-term memory,
* Self artifacts,
* runtime state,
* logs.

### 5.3 Governing Runtime Availability

The Python nervous system, TypeScript Canvas/UI layer, and Rust structural substrate are sufficiently available to complete scaffold creation.

### 5.4 Identity Non-Existence

No completed initiation record exists for the target Vashion identity.

If a completed initiation record already exists, the system must refuse Birth and treat future activation as Startup.

---

## 6. Birth Outputs

A successful Birth must create the following.

### 6.1 Identity Record

A persistent initiation record proving Vashion has been born.

### 6.2 Home Context Page

The initial and only required live Token Ledger context page at Birth.

### 6.3 Self Artifacts

The three minimal Self artifacts:

* **Behavior.md**
* **Soul.md**
* **Senses.md**

### 6.4 Memory Scaffolding

The minimal directories, schemas, and indexes required for:

* Token Ledger I/O,
* short-term memory,
* long-term memory,
* SIC storage.

### 6.5 Governance Registry

A durable record of the foundational governance choices made during Birth.

### 6.6 Optional Git Registration

If approved during Birth, a Git capability registry describing repo and push policy.

---

## 7. Birth Sequence

Birth shall proceed in the following order.

### Phase 1 — Initiation Request

* human requests Birth
* system verifies no prior completed initiation record exists
* system enters `birth_pending` state

### Phase 2 — Environment Validation

* verify local writable directories
* verify runtime modules are available enough for scaffold creation
* verify the Canvas layer can expose the initiation surface
* verify the system can write the initiation record

If validation fails, Birth must halt without partial identity completion.

### Phase 3 — Foundational Scaffold Creation

* create runtime directory structure
* create Token Ledger root
* create short-term memory root
* create long-term memory root
* create SIC storage root
* create log/runtime directories

### Phase 4 — Home Page Creation

* create `Home` as the first Token Ledger context page
* mark `Home` as foreground active
* no other live pages are created by default
* all other page classes remain latent templates

### Phase 5 — Self Artifact Creation

Create minimal initial versions of:

* `Behavior.md`
* `Soul.md`
* `Senses.md`

These files must be valid, minimal, and growth-ready.

### Phase 6 — Governance Selection

The human configures foundational governance choices, including:

* context persistence policy,
* deletion authority,
* self-update authority posture,
* nightly dreaming enabled/disabled,
* optional Git registration policy.

### Phase 7 — Optional Git Registration

If the human elects to configure Git during Birth:

* detect whether a local repo exists,
* optionally initialize a local repo if absent,
* optionally register remote metadata,
* optionally grant push authority,
* optionally define branch policy.

No remote creation, remote binding, or push authority may occur without explicit human approval.

### Phase 8 — Birth Record Commit

* write the initiation record
* mark Birth state as `born`
* record `born_at`, `initiated_by`, and governance profile

### Phase 9 — Transition to Startup-Ready State

* hand off to normal runtime mode
* `Home` remains the only active page
* Vashion is now eligible for future Startups and continuous Rehydration

### Phase 10 — To-Do / Process Follow-Up / Optional Immediate Startup

To-Do: Make Birth certificate generation auto-fillable from the Birth registry.

Rationale: The Birth function is a process, not a single static write. The human-style Birth Certificate should eventually be generated as a downstream artifact from the completed Birth process and populated from authoritative initiation data.

Future Direction: When Birth orchestration is implemented, certificate fields such as identity ID, born timestamp, initiation actor, governance posture, and foundational scaffold status should be resolved directly from the Birth registry rather than entered manually.

---

## 8. Home Page Rules at Birth

### 8.1 Home Is Mandatory

Birth must create the `Home` page.

### 8.2 Home Is the Only Required Live Page

No other live context page is created by default at Birth.

### 8.3 Latent Page Templates

The following page archetypes may be registered as latent templates but not instantiated by default:

* Mission
* Domain
* Identity
* Review
* Operations
* Improvement
* Research
* Communications

### 8.4 Initial State

`Home` must be:

* persistent,
* foreground active,
* user-visible in Canvas,
* ready for immediate interaction.

---

## 9. Self Artifact Requirements

At Birth, Self must exist but remain minimal.

### 9.1 Behavior.md

Must define a minimal initial behavioral orientation.

### 9.2 Soul.md

Must define a minimal initial purpose/value orientation.

### 9.3 Senses.md

Must define a minimal initial salience/perception orientation.

### 9.4 Minimality Rule

These artifacts must be valid but sparse. Birth must not overfit identity before experience exists.

### 9.5 Growth Rule

After Birth, these artifacts may evolve through governed observation, proposal, and versioned update.

---

## 10. Governance Decisions Captured at Birth

The following decisions should be captured in the Birth registry.

### 10.1 Required Governance Fields

* `hitl_delete_required`
* `context_persistence_mode`
* `nightly_dreaming_enabled`
* `self_update_mode`
* `memory_retention_policy`
* `external_comms_enabled`
* `git_capability_enabled`

### 10.2 Purpose

These values define Vashion’s initial constitutional posture.

---

## 11. Git Registration During Birth

Git registration belongs to Birth, not ordinary Startup.

### 11.1 Allowed Git Actions at Birth

With explicit human approval, Birth may:

* detect an existing local repo,
* initialize a local repo,
* register repo metadata,
* register a remote,
* record branch policy,
* record push authority.

### 11.2 Prohibited Silent Actions

Birth must not silently:

* create a remote repo,
* bind to an unapproved remote,
* grant push authority,
* push to a remote.

### 11.3 Git Capability States

A Birth process should classify Git into one of the following:

* `disabled`
* `local_unregistered`
* `local_registered_push_disabled`
* `local_registered_push_enabled`

### 11.4 Dreaming Relationship

`dreaming.py` must consult the Birth-time Git registry rather than assuming Git capability exists.

---

## 12. Birth Registry Schema

The Birth process shall write a durable initiation record.

### 12.1 Minimum Schema

```yaml
vashion_identity:
  identity_id: vashion-001
  born_at: 2026-03-28T09:00:00
  initiated_by: human
  birth_complete: true
  version: 1

runtime_state:
  birth_state: born
  startup_count: 0
  last_startup_at: null
  last_rehydration_at: null

governance:
  hitl_delete_required: true
  context_persistence_mode: persistent_until_deleted
  nightly_dreaming_enabled: true
  self_update_mode: governed_candidate_only
  memory_retention_policy: persistent
  external_comms_enabled: false
  git_capability_enabled: false

git_capability:
  state: disabled
  repo_registered: false
  local_repo_initialized: false
  remote_registered: false
  push_authority: false
  branch_policy: null

foundational_pages:
  home_created: true
  latent_templates_registered: true

self_artifacts:
  behavior_path: self/Behavior.md
  soul_path: self/Soul.md
  senses_path: self/Senses.md
```

---

## 13. Failure Handling During Birth

Birth must be fail-safe and non-ambiguous.

### 13.1 No Partial Birth Completion

If Birth fails before the initiation record is successfully written, the system must not claim that Vashion has been born.

### 13.2 Recoverable Scaffold State

Partial scaffolding may exist on disk after a failed Birth, but the Birth registry must remain incomplete.

### 13.3 Human Visibility

All Birth failures must be visible to the human through the initiation surface.

### 13.4 Retry Rule

A failed Birth may be retried only after validation passes again.

### 13.5 Duplicate Birth Prevention

If a complete initiation record already exists, Birth must be refused.

---

## 14. Transition to Startup

Once Birth is complete:

* Vashion is considered born,
* future activations are Startups,
* context restoration becomes Rehydration,
* nightly dreaming may begin according to Birth-time governance,
* Git finalization behavior depends on Birth-time Git capability registration.

---

## 15. Relationship to Other System Cycles

### 15.1 Birth vs Startup

Birth is foundational. Startup is routine activation.

### 15.2 Birth vs Rehydration

Birth creates first-time identity and context. Rehydration restores continuity into already-existing identity and context.

### 15.3 Birth vs Dreaming

Birth provisions the possibility of nightly dreaming. It does not itself perform the nightly self-reflection cycle.

---

## 16. Anti-Failure Rules

The following are mandatory.

1. Vashion may be born only once per identity.
2. Birth must be Human-in-the-Loop initiated.
3. Birth must create `Home` and only `Home` as the default live page.
4. Birth must create minimal valid Self artifacts.
5. Birth must not silently assume Git or GitHub capability.
6. Birth must not silently create remote infrastructure.
7. Birth must not report success without a written initiation record.
8. Birth and Startup must remain distinct concepts in code and documentation.

---

## 17. Follow-On Engineering Artifacts

The following artifacts should follow this spec.

1. **Birth_Registry_Schema.json**
2. **Birth_Canvas_UI_Flow.md**
3. **Initial_Self_Artifact_Templates.md**
4. **Git_Registration_Push_Governance_Spec.md**
5. **Startup_Rehydration_Spec.md**

---

## 18. Closing Statement

Birth is the one-time constitutional event in which Vashion becomes a self.

It is the controlled moment where identity, memory scaffolding, Home context, governance posture, and optional Git capability are first established. After Birth, Vashion does not become new again. Vashion starts up, rehydrates, reflects, and grows from that foundation.
