# Token Ledger I/O Specification

## Document Header

* **Title:** Token_Ledger_IO_Spec.md
* **System:** Vashion
* **Document Type:** Subsystem Specification
* **Status:** Draft v1
* **Parent PRD:** Nervous_System.md
* **Owner:** Product / Architecture
* **Primary Intent:** Define the Token Ledger I/O context page model, lifecycle, schema, and operating rules.

---

## 1. Purpose

The Token Ledger I/O is the persistent context layer of Vashion’s nervous system.

It provides the shared conversational and contextual substrate used by all intelligence within Vashion while remaining visible and controllable through the Canvas UI.

The Token Ledger is not long-term historical memory, not short-term daily operational memory, and not Self. It is the active context workspace that preserves continuity across specialized pages until the Human in the Loop chooses to delete them.

---

## 2. Product Definition

A **Token Ledger context page** is a persistent, human-controlled, specialized cognitive workspace that holds:

* active conversational continuity,
* structured cognitive state,
* transcript history,
* links to short-term memory,
* links to long-term historical memory,
* candidate outputs for reflection, learning, and self-updates.

Each page is a distinct working room inside Canvas.

---

## 3. Design Principles

### 3.1 Persistence Without Auto-Expiry

* Context pages do not expire automatically.
* Deletion is Human-in-the-Loop controlled.
* Archival does not equal deletion.

### 3.2 Specialization

* Each page is specialized for a domain, mission, workflow, or operating mode.
* Specialization must be explicit.
* A page is not treated as universal context by default.

### 3.3 Structured State Over Raw Transcript

* A raw transcript alone is insufficient.
* Every page must include structured cognitive state in addition to the transcript stream.

### 3.4 Context Is Not Memory

* Token Ledger context pages may emit memory candidates.
* They do not replace short-term memory, long-term memory, or Self.

### 3.5 Coordinator Ownership

* Page state changes flow through governed coordination.
* No page directly mutates Self.
* No page directly writes authoritative historical stories without memory-layer processing.

---

## 4. Functional Roles of a Context Page

Each context page serves five roles:

1. **Continuity Container** — preserves the conversational and working state of a thread.
2. **Context Budget Manager** — governs active token pressure for the page.
3. **Handoff Surface** — provides a controlled interface between user interaction and the Brain.
4. **Memory Staging Surface** — emits candidates into short-term and long-term memory flows.
5. **Specialized Operating Frame** — shapes how Vashion reasons within that page.

---

## 5. Page Types

The following controlled page types are approved for v1.

### 5.1 General Conversation

Used for broad ongoing interaction without a sharply bounded mission.

### 5.2 Mission Page

Used for a defined objective that persists over time.

Examples:

* build a PRD,
* troubleshoot an issue,
* plan a project,
* design a subsystem.

### 5.3 Domain Page

Used for an enduring subject area.

Examples:

* architecture,
* trading,
* operations,
* legal matters.

### 5.4 Identity Page

Used for self-reflection, behavior shaping, self-understanding, and internal growth.

### 5.5 Review Page

Used for nightly, weekly, milestone, or special review cycles.

---

## 6. Lifecycle States

Each context page shall have an explicit lifecycle state.

### 6.1 Active

Currently foregrounded and fully participating in cognition.

### 6.2 Warm

Not foregrounded, but readily available through summary and recent state.

### 6.3 Paused

Intentionally inactive but preserved.

### 6.4 Archived

Retained for continuity and reference but not part of active context by default.

### 6.5 Deleted

Removed only through Human-in-the-Loop action.

### 6.6 Lifecycle Rules

* Only one page is foreground-active by default.
* Multiple pages may remain open in Canvas.
* Warm pages may contribute summary context but not full transcript replay by default.
* Deleted means operator-removed, not automatically expired.

---

## 7. Context Specialization Profile

Each page shall carry a specialization profile.

### 7.1 Required Specialization Fields

* `domain`
* `persona_mode`
* `task_mode`
* `risk_posture`
* `memory_bias`
* `tooling_scope`

### 7.2 Purpose

The specialization profile determines how the page should be interpreted and handled by the coordinator and Brain.

Examples:

* a trading page may prefer tighter summaries and time-sensitive context,
* an architecture page may prefer design continuity and artifact linkage,
* an identity page may prefer introspection and drift review.

---

## 8. Required Page Structure

Each page must contain six internal sections.

### 8.1 Header

Static metadata and page identity.

### 8.2 Active Ledger

Most recent user and Vashion interaction state.

### 8.3 Context Summary

Rolling synthesized page state.

### 8.4 Open Loops

Unresolved work, commitments, questions, or decisions.

### 8.5 Memory Hooks

References outward to short-term memory, long-term memory, and self-update candidates.

### 8.6 Transcript Stream

Chronological exchange log.

---

## 9. Minimum Viable Schema (v1)

The following fields are required for the initial build.

* `page_id`
* `title`
* `context_type`
* `status`
* `specialization`
* `active_context_summary`
* `open_loops`
* `current_objectives`
* `message_log_ref`
* `short_term_refs`
* `long_term_story_refs`
* `context_token_budget`
* `current_token_load`
* `transcript_stream`

---

## 10. Full Context Page Schema

```yaml
page_id: ctx-unique-id
slug: vashion-architecture
title: Vashion Architecture
created_at: 2026-03-27T10:00:00
updated_at: 2026-03-27T18:42:00
created_by: human | vashion
status: active | warm | paused | archived | deleted
pinned: false

purpose:
  context_type: mission
  mission: Define Vashion nervous system and context architecture
  scope: PRD, subsystem modeling, memory design
  specialization: architecture
  intended_use: multi-session design continuity

human_control:
  owner: user
  hitl_delete_required: true
  retention_policy: persistent_until_deleted
  visibility: canvas

specialization_profile:
  domain: architecture
  persona_mode: chief_engineer
  task_mode: design
  risk_posture: controlled
  memory_bias: continuity_heavy
  tooling_scope:
    - docs
    - diagrams
    - architecture

cognitive_state:
  active_context_summary: >
    User is defining Vashion as a virtual counterpart with Python nervous system,
    Token Ledger I/O, TypeScript senses, and Rust structural system.
  open_loops:
    - Finalize Token Ledger page schema
    - Define communications module stub boundaries
  current_objectives:
    - Produce implementation-grade subsystem specifications
  recent_decisions:
    - Token Ledger pages never expire automatically
    - HITL controls deletion
  working_assumptions:
    - Pages are specialized working rooms
    - Token Ledger is distinct from historical memory
  risk_flags:
    - Context sprawl
    - Cross-page contamination
    - Identity drift if pages overreach

conversation_state:
  message_log_ref: msglog://ctx-unique-id
  interaction_index: 42
  last_user_intent: Discuss Token Ledger context page model
  last_vashion_response_mode: architecture_advisor
  conversation_continuity_state: stable

memory_linkage:
  short_term_refs:
    - stm://2026-03-27/Architecture_Notes
  long_term_story_refs:
    - ltm://Summaries/vashion-nervous-system-story
  self_update_candidates: []
  related_pages:
    - ctx-vashion-self-001
  derived_summaries:
    - sum://ctx-unique-id/latest

token_ledger:
  context_token_budget: 24000
  current_token_load: 9100
  soft_warning_threshold: 18000
  hard_cap: 24000
  compression_state: none
  last_compacted_at: null
  overflow_strategy: summarize_oldest_noncritical
  protected_segments:
    - current_objectives
    - pinned_directives
    - recent_unresolved_turns

transcript_stream:
  - ts: 2026-03-27T18:40:00
    role: user
    summary: Wants to define Token Ledger context page model
  - ts: 2026-03-27T18:42:00
    role: assistant
    summary: Proposed persistent bounded cognitive workspace model
```

---

## 11. Token Budget and Compaction Rules

### 11.1 Purpose

Because the Token Ledger is a context layer, each page must explicitly manage context pressure.

### 11.2 Required Token Controls

Each page must track:

* `context_token_budget`
* `current_token_load`
* `soft_warning_threshold`
* `hard_cap`
* `compression_state`
* `overflow_strategy`

### 11.3 Compaction Hierarchy

When token pressure rises, the system shall preserve context in this order:

1. pinned commitments and directives,
2. current objectives,
3. recent unresolved turns,
4. context summaries,
5. condensed transcript history,
6. low-value raw transcript segments.

### 11.4 Hard Rules

* Human directives must not be silently dropped without summary capture.
* Compaction must be traceable.
* Protected segments may be summarized only under explicit compaction policy.

---

## 12. Multi-Page Operating Model

Because multiple context pages may be open simultaneously, Vashion requires a multi-page operating model.

### 12.1 Foreground Page

One page is designated foreground-active by default.

### 12.2 Warm Pages

Warm pages are available through summary rather than full replay.

### 12.3 Archived Pages

Archived pages remain retrievable but are not part of active cognition by default.

### 12.4 Cross-Page Recall Rules

Cross-page context may be brought forward only when:

* explicitly selected by the human,
* linked by memory with high confidence,
* linked by the coordinator under governed relevance rules.

### 12.5 Anti-Contamination Rule

A context page must not silently become global context for all other pages.

---

## 13. Relationship to Other Nervous System Layers

### 13.1 Relationship to Short-Term Memory

* Token Ledger = active conversational context
* Short-Term Memory = daily operational workspace

The Token Ledger may reference short-term memory but does not replace it.

### 13.2 Relationship to Long-Term Memory

* Token Ledger pages emit story candidates, summary candidates, and lesson candidates.
* Token Ledger pages are not the historical archive.

### 13.3 Relationship to Self

* Token Ledger pages may propose self-update candidates.
* Token Ledger pages may not directly rewrite Behavior.md, Soul.md, or Senses.md.

---

## 14. Human-in-the-Loop Controls

The Human in the Loop shall control:

* deletion,
* archival decisions,
* page pinning,
* cross-page prioritization,
* context specialization edits where exposed,
* acceptance or rejection of self-update candidates derived from context pages.

---

## 15. Anti-Failure Rules

The following rules are mandatory.

1. No page is deleted except by human action.
2. No page silently becomes global context for all conversations.
3. No raw transcript is the sole stored representation of a page.
4. No page may mutate Self directly.
5. No page may bypass coordinator routing.
6. Specialization must be explicit, not guessed every time.
7. Memory candidates derived from pages must preserve provenance.

---

## 16. Follow-On Engineering Work

The following implementation artifacts should follow this specification.

1. **Token_Ledger_Page_Schema.json**
2. **Canvas_Context_Page_UI_Spec.md**
3. **Token_Compaction_Policy.md**
4. **Cross_Page_Recall_Rules.md**
5. **Token_Ledger_Deletion_Governance.md**

---

## 17. Closing Statement

The Token Ledger I/O context page model gives Vashion persistent, specialized, human-controlled conversational continuity.

It is the active contextual room system of the nervous system: structured enough for machine reasoning, visible enough for human control, and persistent enough to support a true virtual counterpart relationship.

