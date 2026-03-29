# Git Registration & Push Governance Specification

## Document Header

* **Title:** Git_Registration_Push_Governance_Spec.md
* **System:** Vashion
* **Document Type:** Subsystem Specification
* **Status:** Draft v1
* **Parent Documents:** Birth_Initiation_Spec.md, Nervous_System.md, Token_Ledger_IO_Spec.md
* **Owner:** Product / Architecture
* **Primary Intent:** Define the governed registration, authorization, and push policy model for Git and GitHub usage within Vashion.

---

## 1. Purpose

This specification defines how Vashion may detect, register, use, and govern Git repositories and remote push behavior.

The goal is to preserve the value of versioned continuity while preventing unsafe assumptions such as:

* silently assuming a local repository exists,
* silently creating a local repository,
* silently binding a remote,
* silently gaining push authority,
* silently creating remote infrastructure,
* silently pushing to an unapproved branch.

Git capability is treated as a governed operational capability, not as a default runtime assumption.

---

## 2. Governing Principles

### 2.1 Registration Before Use

Vashion must not use Git commit or push behavior until Git capability has been explicitly registered.

### 2.2 Detection Is Not Authorization

Detection of a local repository does not grant authority to commit or push.

### 2.3 Birth-Time Governance

Initial Git capability registration belongs to Birth, not ordinary Startup.

### 2.4 Human-in-the-Loop Authority

Remote registration, push authority, and branch policy require explicit human approval.

### 2.5 No Silent Infrastructure Creation

Vashion must not silently create remote repositories or bind to unapproved remotes.

### 2.6 Policy-Driven Finalization

Nightly dreaming may finalize with Git only when Git capability has been registered and push authority granted.

---

## 3. Scope

This specification governs:

* local repository detection,
* local repository initialization,
* repository registration,
* remote registration,
* push authority,
* branch policy,
* dreaming-cycle Git finalization,
* Git capability state transitions.

This specification does not define:

* provider-specific OAuth or credential acquisition flows,
* GitHub App implementation details,
* CI workflow implementation,
* repository content policy beyond push governance,
* secret storage internals.

---

## 4. Terminology

* **Birth** = first self-initiation event
* **Startup** = ordinary runtime activation
* **Rehydration** = continuity restoration into active state
* **Git Capability** = the governed ability to detect, initialize, register, commit to, and/or push a repository according to policy
* **Repository Registration** = the durable recording of an approved repository and its governance policy
* **Push Authority** = explicit permission for Vashion to push to a registered remote according to branch policy
* **Branch Policy** = the rules governing which branch may receive automated commits and pushes

---

## 5. Git Capability States

Vashion shall classify Git capability into one of the following states.

### 5.1 disabled

Git capability is not enabled or not registered.

Behavior:

* Git operations are unavailable
* dreaming completes locally only
* no commit and no push

### 5.2 local_unregistered

A local Git repository may exist, but it is not registered for governed use.

Behavior:

* detection allowed
* reporting allowed
* no commit and no push

### 5.3 local_registered_push_disabled

A local repository is registered for governed use, but remote push authority is disabled.

Behavior:

* local commit behavior may be allowed if policy says so
* no remote push
* dreaming may conclude as local-only finalization

### 5.4 local_registered_push_enabled

A local repository is registered, an approved remote exists, push authority is granted, and branch policy is defined.

Behavior:

* dreaming may perform governed commit and push finalization
* all operations must obey branch policy

---

## 6. Allowed Actions by State

| Capability            |  disabled | local_unregistered | local_registered_push_disabled | local_registered_push_enabled |
| --------------------- | --------: | -----------------: | -----------------------------: | ----------------------------: |
| Detect local repo     |       Yes |                Yes |                            Yes |                           Yes |
| Initialize local repo | HITL only |          HITL only |                            N/A |                           N/A |
| Register local repo   | HITL only |          HITL only |                            N/A |                           N/A |
| Register remote       |        No |          HITL only |                      HITL only |                           N/A |
| Commit locally        |        No |                 No |                   Policy-bound |                  Policy-bound |
| Push remotely         |        No |                 No |                             No |                  Policy-bound |
| Create remote repo    |        No |          HITL only |                      HITL only |                     HITL only |

---

## 7. Registration Model

Git registration must be stored as an authoritative governance artifact.

### 7.1 Required Registry File

A governed registry file shall exist, such as:

* `git_registry.yaml`

This registry is the source of truth for Git capability.

### 7.2 Required Fields

The registry must support, at minimum:

```yaml
enabled: true
workspace_name: vashion-core
repo_root: /path/to/repo
repo_registered_by: human
registered_at: 2026-03-28T09:10:00

git_capability_state: local_registered_push_enabled

local_repo:
  detected: true
  initialized_by_vashion: false
  allowed_to_commit: true

remote_repo:
  provider: github
  remote_name: origin
  remote_url: git@github.com:owner/repo.git
  remote_registered_by: human
  allowed_to_push: true
  allowed_to_create_remote_repo: false

branch_policy:
  mode: nightly_branch
  default_branch: main
  push_branch: dreaming/nightly
  direct_push_to_main: false

governance:
  hitl_required_for_registration: true
  hitl_required_for_remote_change: true
  hitl_required_for_branch_policy_change: true
```

---

## 8. Birth-Time Git Registration

Birth is the preferred time for initial Git registration.

### 8.1 Allowed Birth-Time Actions

With explicit human approval, Birth may:

* detect an existing local repo,
* initialize a local repo if absent,
* register repo metadata,
* register remote metadata,
* record push authority,
* define branch policy.

### 8.2 Prohibited Silent Actions During Birth

Birth must not silently:

* create a remote repo,
* bind to a remote,
* enable push authority,
* push commits,
* assume Git availability.

### 8.3 Birth Output

If Git registration is performed during Birth, the Birth registry must reference the Git registry state.

---

## 9. Ordinary Startup Behavior

During ordinary Startup, Vashion may:

* read the Git registry,
* validate that the registered repo still exists,
* validate that the configured remote still matches policy,
* validate branch policy readiness,
* report drift or misconfiguration.

Startup must not silently perform Git registration or remote rebinding.

---

## 10. Rehydration Behavior

Rehydration restores active continuity, not Git authority.

During Rehydration, Vashion may:

* restore awareness of Git readiness state,
* surface pending Git-related failures,
* restore unfinished dreaming finalization status,
* prepare for a later governed push if already authorized.

Rehydration must not alter Git capability state.

---

## 11. Dreaming Cycle Relationship

### 11.1 Governing Rule

The dreaming cycle must consult the Git registry before attempting finalization.

### 11.2 Allowed Outcomes

Dreaming may end in one of the following states:

* `completed_with_push`
* `completed_local_only`
* `failed`

### 11.3 completed_with_push

Allowed only if:

* Git capability state is `local_registered_push_enabled`
* remote and branch policy are valid
* push succeeds

### 11.4 completed_local_only

Used when:

* Git capability is disabled
* local repo is unregistered
* push authority is not granted
* branch policy forbids the current push path

In this state, SIC and nightly outputs are persisted locally and push is recorded as unavailable by policy.

### 11.5 failed

Used when:

* Git finalization was required by policy but execution failed,
* commit or push failed after authorization,
* repo drift invalidated the configured policy,
* local persistence failed.

---

## 12. Branch Policy Model

Branch policy is mandatory when push authority is enabled.

### 12.1 Supported Branch Modes

* `direct_main`
* `nightly_branch`
* `review_branch`
* `disabled`

### 12.2 direct_main

Push directly to the main branch.

This mode is high-risk and should be discouraged by default.

### 12.3 nightly_branch

Push nightly outputs to a dedicated automation branch.

Example:

* `dreaming/nightly`

This is the recommended default.

### 12.4 review_branch

Push to a review branch for later merge or approval workflow.

### 12.5 disabled

Git push is not allowed.

### 12.6 Recommendation

Default recommendation:

* `mode: nightly_branch`
* `direct_push_to_main: false`

---

## 13. Commit Policy

### 13.1 Commit Message Discipline

Automated commits must use structured messages.

Recommended format:

* `dreaming: nightly SIC YYYY-MM-DD`
* `dreaming: recovered SIC YYYY-MM-DD`
* `governance: update git registry`

### 13.2 Commit Scope

Only governed outputs and intended changes may be committed.

### 13.3 No Unbounded Stage-All by Default

Production policy should prefer bounded file selection over unrestricted repository-wide staging.

### 13.4 Recovery Commit Labeling

Recovered missed dreaming cycles should be clearly labeled as recovery commits.

---

## 14. Remote Governance

### 14.1 Approved Remote Required

Remote push requires an explicitly approved remote recorded in the Git registry.

### 14.2 Remote Drift Detection

If the configured remote URL changes unexpectedly, push authority must be suspended until reviewed.

### 14.3 Remote Change Policy

Changes to remote URL, remote name, or provider require HITL approval.

### 14.4 No Silent Provider Switching

Vashion must not silently switch from GitHub to another remote provider or vice versa.

---

## 15. Failure Handling

Git governance must explicitly address failure scenarios.

### 15.1 No Repo Present

If no repo exists and none is registered:

* report `git_finalization_unavailable`
* persist locally only
* do not fail ordinary operation unless Git is constitutionally required

### 15.2 Repo Registered but Missing

If the registry references a repo that is no longer present:

* suspend Git capability
* surface configuration drift
* do not push

### 15.3 Remote Unreachable

If remote push fails due to network or auth issues:

* persist locally
* record push failure
* mark dreaming result according to policy
* queue recovery if required

### 15.4 Auth Failure

Auth failure must be treated as governance-significant, not as a transient success.

### 15.5 Branch Policy Violation

If current branch behavior violates configured policy:

* push must be blocked
* violation must be surfaced to the human

---

## 16. Security and Governance Rules

The following rules are mandatory.

1. Git detection does not grant commit or push authority.
2. Local repo initialization requires explicit human approval.
3. Remote registration requires explicit human approval.
4. Push authority requires explicit human approval.
5. Branch policy is mandatory when push is enabled.
6. Remote infrastructure must not be silently created.
7. Remote drift must suspend push authority until reviewed.
8. Dreaming must honor Git governance rather than bypass it.

---

## 17. Human-in-the-Loop Controls

The human must retain authority over:

* whether Git capability is enabled,
* which repo is registered,
* whether local repo initialization is allowed,
* whether remote registration is allowed,
* whether push authority is granted,
* branch policy selection,
* remote changes,
* suspension or revocation of Git capability.

---

## 18. Recommended Runtime Outcomes

### 18.1 Best-Case Setup

* repo registered at Birth
* push authority granted
* nightly branch policy active
* dreaming concludes with governed push

### 18.2 Safe Minimal Setup

* repo not yet registered
* dreaming completes locally only
* Vashion surfaces Git as unconfigured rather than broken

### 18.3 Recovery-Oriented Setup

* registered repo exists
* push temporarily fails
* outputs remain local
* recovery push occurs later under policy

---

## 19. Follow-On Engineering Artifacts

1. **git_registry.yaml schema**
2. **Git_Capability_Check.md**
3. **Dreaming_Git_Finalization_Policy.md**
4. **Remote_Drift_Detection_Spec.md**
5. **Git_Branch_Policy_Enforcement.md**

---

## 20. Closing Statement

Git capability is a governed embodiment choice for Vashion, not an ambient assumption.

By requiring explicit registration, explicit push authority, and explicit branch policy, Vashion can preserve continuity through version control without silently crossing operational boundaries.
