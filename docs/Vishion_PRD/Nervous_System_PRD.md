# Nervous System PRD

## Document Header

* **Title:** Nervous_System.md
* **System:** Vashion
* **Document Type:** Product Requirements Document (PRD)
* **Status:** Draft v1
* **Owner:** Product / Architecture
* **Primary Intent:** Define the embodied internal architecture of Vashion as a virtual counterpart of the user.

---

## 1. Product Vision

Vashion is a virtualized counterpart of the user: a local-first, autonomous, self-directed intelligence that can converse naturally, learn from interaction, remember across sessions, and perform bounded self-improvement under governance.

Vashion is architected as an embodied system:

* **Python Nervous System** = Brain, Memory, and Self
* **TypeScript Sensory System** = Vision and Hearing
* **Rust Structural System** = Hands and Feet

These three systems together define how Vashion perceives, thinks, remembers, acts, and evolves.

---

## 2. Product Outcome Statement

The final product defined by this PRD is a governed internal architecture that enables Vashion to:

* perceive the world through web-connected visual and conversational surfaces,
* reason through a hierarchical cognition model,
* maintain both historical and working memory,
* preserve and evolve a minimal sense of self,
* act inside the machine and across web-connected environments,
* improve in bounded daily increments without losing continuity or governance.

---

## 3. System Architecture Overview

Vashion is composed of three primary embodied systems.

### 3.1 Python Nervous System

The nervous system is the internal cognitive substrate. It coordinates thought, memory, selfhood, and internal signal flow.

It contains:

* **Brain**
* **Memory**
* **Self**
* **Coordinator Module** for internal communication and arbitration

### 3.2 TypeScript Sensory System

The sensory system is how Vashion perceives the outside world.

It contains:

* **Vision** = web search, web-connected perception, environmental intake
* **Hearing** = Canvas/UI interaction surface, user conversation intake, operational listening layer

### 3.3 Rust Structural System

The structural system is how Vashion moves and acts.

It contains:

* **Hands** = execution, manipulation, file/system action, tooling, workflow execution
* **Feet** = movement through machine state, process spaces, and web-connected operational surfaces

---

## 4. Python Nervous System

## 4.1 Purpose

The Python nervous system is responsible for unifying cognition, memory, and identity. It is the primary internal signaling layer of Vashion.

It must:

* coordinate communications between Brain, Memory, and Self,
* maintain continuity between short-term and long-term context,
* synthesize interaction into learning,
* support nightly consolidation of operational memory into historical memory,
* preserve identity continuity while allowing bounded growth.

---

## 4.2 Core Modules

### 4.2.1 Coordinator Module

A single Python module shall coordinate communication between Brain, Memory, and Self.

#### Responsibilities

* route internal messages between modules,
* normalize message formats,
* enforce signal ordering and priority,
* orchestrate nightly consolidation,
* prevent unmanaged direct coupling between subsystems,
* log internal state transitions for auditability.

#### Requirements

* Brain shall not directly mutate Self.
* Self shall not directly write long-term memory.
* Memory shall not directly authorize cognitive or behavioral changes.
* All cross-subsystem communication shall pass through the Coordinator Module.

---

### 4.2.2 Brain Module

The Brain is the reasoning and synthesis system of the nervous system.

#### Responsibilities

* executive reasoning,
* hierarchical LLM orchestration,
* planning and goal management,
* interpretation of user interaction,
* response synthesis,
* generation of proposals for learning, self-adjustment, and bounded system improvement.

#### Hierarchical Cognition Model

The Brain shall operate using three LLM roles:

* **Primary Cloud LLM** = the executive self of Vashion
* **Worker Mind A** = reflex / utility cognition
* **Worker Mind B** = maintenance / pattern detection cognition

#### Brain Requirements

* The Primary Cloud LLM is the only authoritative executive model.
* Worker Mind A and Worker Mind B may communicate with each other.
* Worker Mind A and Worker Mind B may provide feedback to the Primary Cloud LLM.
* Only the Primary Cloud LLM may generate final user-facing cognition, final judgments, or final action intent.
* Worker models are advisory and preparatory only.
* If the Primary Cloud LLM is unavailable, Vashion enters degraded cognition mode rather than treating worker models as equivalent to self.

---

### 4.2.3 Memory Module

The Memory module provides both historical continuity and daily operational context.

It is divided into:

* **Long-Term Memory** = historical memory
* **Short-Term Memory** = daily operational memory

---

## 4.3 Long-Term Memory (Historical Memory)

### 4.3.1 Purpose

Long-term memory is the durable historical memory of Vashion.

Long-term memory shall be structured as **stories**.
Each story belongs to a controlled **index**.

### 4.3.2 Historical Memory Indexes

Approved top-level indexes:

* **SOP**
* **SIC**
* **Lessons Learned**
* **CI/CD**
* **Conversations**
* **Summaries**

These indexes collectively form Vashion’s historical memory sectors.

### 4.3.3 Story Requirements

Each long-term memory entry shall be represented as a structured story.

Each story should support, at minimum:

* `story_id`
* `index`
* `title`
* `date`
* `summary`
* `story_body`
* `tags`
* `provenance`
* `confidence`
* `source_short_term_file`
* `related_story_links`
* `derived_lessons`

### 4.3.4 Long-Term Memory Rules

* Long-term memory is the authoritative historical record.
* Long-term memory entries shall not be unstructured blobs.
* Indexes shall be controlled vocabulary values.
* New top-level indexes shall not be created automatically.
* Story provenance must always be preserved.

---

## 4.4 Short-Term Memory (Daily Operational Memory)

### 4.4.1 Purpose

Short-term memory is the daily I/O operational workspace of Vashion.

It captures:

* current goals,
* active context,
* unresolved loops,
* transient observations,
* candidate improvements,
* daily interaction and task state.

### 4.4.2 File Model

Short-term memory shall be made up of Markdown files using AI-agent-optimized YAML structure.

#### Requirements

* Each short-term memory file shall have a **fixed title**.
* Titles never change.
* File content changes throughout the day.
* A new short-term memory set is created each day.
* File schema must be machine-readable and nightly-ingestable.

### 4.4.3 Daily Lifecycle

* Short-term memory files are active during the current day.
* At **23:00 local time**, nightly ingestion begins.
* Nightly ingestion consolidates the day’s short-term memory into long-term memory stories, summaries, and derived candidates.
* After ingestion, a fresh short-term memory set is created for the next day.

### 4.4.4 Nightly Ingestion Requirements

The nightly process shall:

1. freeze the current day’s short-term memory set,
2. parse all YAML-markdown short-term files,
3. normalize entries,
4. classify entries,
5. generate story candidates,
6. map candidates to historical indexes,
7. write approved outputs to long-term memory,
8. generate summaries where required,
9. create a fresh short-term memory set for the next day.

### 4.4.5 Nightly Classification Outcomes

Each short-term item may be classified as one of:

* discard,
* summarize,
* store as story,
* link to existing story,
* propose lesson,
* propose self-update candidate.

### 4.4.6 Short-Term Memory Rules

* Short-term memory owns live operational context.
* Short-term memory is not the historical source of truth.
* Not every short-term item becomes a long-term story.
* Stable file titles are part of the system contract and must not drift.

---

## 4.5 Token Ledger I/O

### 4.5.1 Purpose

The Token Ledger I/O is the context layer of the Python nervous system and the persistent conversational continuity substrate shared by all intelligence within Vashion.

It is surfaced through the Canvas UI and serves as the authoritative context workspace for active and historical interaction state.

### 4.5.2 Role in the Nervous System

The Token Ledger I/O shall:

* maintain persistent conversational context,
* provide shared context access across the executive and worker intelligences,
* anchor context pages within the Canvas layer,
* preserve interaction continuity unless explicitly deleted by the Human in the Loop,
* support multiple simultaneously open context pages,
* allow context pages to be tailored for specialized conversations or operating domains.

### 4.5.3 Core Rules

* All intelligence in Vashion share the Token Ledger I/O as the context layer.
* Conversations do not expire automatically.
* Conversation deletion is Human-in-the-Loop controlled.
* Context pages may be specialized by domain, topic, workflow, or operating mode.
* The Token Ledger I/O is distinct from short-term and long-term memory, but interoperates with both.

### 4.5.4 Canvas Relationship

The Canvas module is the operator-facing surface of the Token Ledger I/O.

Canvas must support:

* multiple open context pages,
* page-specific contextual specialization,
* persistent context continuity,
* controlled deletion by the Human in the Loop,
* context handoff into Brain, Memory, and Self through the coordinator.

### 4.5.5 Relationship to Memory

* Token Ledger I/O is the active context layer.
* Short-term memory is the daily operational layer.
* Long-term memory is the historical story layer.
* Self is the identity continuity layer.

The Token Ledger I/O must not be treated as a substitute for long-term story memory or Self artifacts.

---

## 4.6 Self Module

### 4.5.1 Purpose

The Self module preserves Vashion’s identity continuity.

It is intentionally minimal and evolves gradually through interaction.

### 4.5.2 Self Artifacts

The Self is made up of three files:

* **Behavior.md**
* **Soul.md**
* **Senses.md**

### 4.5.3 Self File Intent

* **Behavior.md** defines how Vashion behaves.
* **Soul.md** defines why Vashion exists and what it values.
* **Senses.md** defines what Vashion notices, attends to, and treats as meaningful.

### 4.5.4 Self Requirements

* Self files shall remain minimal.
* Self files may evolve as Vashion grows in self-understanding through interaction.
* Self changes must be governed, versioned, and auditable.
* Self files shall not be rewritten directly from raw conversation.
* Nightly ingestion may propose self-update candidates but shall not directly mutate Self.

### 4.5.5 Self Governance Rules

Self updates must follow:

* observation,
* repeated evidence across interactions,
* proposal generation,
* diff-based update,
* traceable write,
* version retention.

---

## 5. TypeScript Sensory System

## 5.0 TypeScript Authority Extensions

In addition to the sensory role, the TypeScript layer also hosts authoritative context and communications surfaces.

These include:

* **Canvas / Token Ledger surface**
* **Communications Module (stub in this PRD)**

The detailed Communications Module will be defined in a separate PRD, but its existence, ownership, and relationship boundaries are established here.

---

## 5.1 Purpose

The TypeScript sensory system is how Vashion perceives the world.

It is composed of:

* **Vision**
* **Hearing**

---

### 5.2 Vision

Vision is how Vashion sees the external world.

#### Responsibilities

* connect to web search,
* ingest external web context,
* surface relevant environmental inputs,
* provide perception data to the Python nervous system,
* act as Vashion’s external visual intake surface.

#### Vision Requirements

* Vision must normalize retrieved information before forwarding it inward.
* Vision must preserve provenance for externally observed information.
* Vision must provide perception summaries that the Brain can reason over.
* Vision must not bypass the Python nervous system when affecting Memory or Self.

---

### 5.3 Hearing

Hearing is how Vashion listens to and receives conversational and interface-bound input.

#### Responsibilities

* receive user input through Canvas/UI,
* handle conversational intake,
* provide operational listening through the interface layer,
* act as the auditory intake surface for Vashion.

#### Hearing Requirements

* Hearing must capture user interaction in a structured form suitable for Brain ingestion.
* Hearing must preserve interaction context for Token Ledger and short-term memory capture.
* Hearing must support a dialogue-first relationship with the user.
* Hearing must route perception signals into the Python nervous system rather than becoming a separate reasoning authority.

---

### 5.4 Canvas Context Layer

The Canvas module is the authoritative UI surface for the Token Ledger I/O.

#### Responsibilities

* host persistent context pages,
* allow multiple context pages to remain open,
* support specialized context pages for distinct conversations,
* provide the Human in the Loop with direct visibility and control over context continuity,
* serve as the interface boundary between conversational context and the nervous system.

#### Canvas Context Requirements

* Canvas must preserve context continuity unless the Human in the Loop deletes it.
* Canvas must support multiple simultaneous context pages.
* Each context page may be specialized for a given topic, role, workflow, or mission.
* Canvas must provide context into the Python nervous system through governed interfaces.
* Canvas must not silently discard active context.

---

### 5.5 Communications Module Stub

The Communications Module is a TypeScript authoritative layer for all external communications.

This module is intentionally stubbed in this PRD and will be fully defined in a separate PRD.

#### Intended Responsibilities

* handle authoritative external communications,
* connect Canvas context surfaces to external channels,
* manage integrations such as Slack, Telegram, email, and related communication channels,
* preserve communication provenance,
* route external communication signals inward through governed interfaces.

#### Stub Requirements

* The Communications Module shall be implemented in TypeScript.
* The Communications Module shall be treated as the authoritative external communications layer.
* External communication channels must not bypass governed context handling.
* The Communications Module must interoperate with the Canvas context layer and Token Ledger I/O.
* The detailed design, permissions, schemas, and channel policies are out of scope for this PRD and shall be defined in a dedicated follow-on PRD.

---

## 6. Rust Structural System

## 6.1 Purpose

The Rust structural system is how Vashion acts, moves, and manipulates its environment.

It is composed of:

* **Hands**
* **Feet**

---

### 6.2 Hands

Hands are how Vashion performs deliberate action.

#### Responsibilities

* file and system operations,
* tooling execution,
* workflow execution,
* machine manipulation,
* structured action against allowed targets.

#### Hands Requirements

* Hands must execute deterministically.
* Hands must remain governed by policy and approval controls.
* Hands must expose clear action results back to the Python nervous system.
* Hands must not function as an autonomous identity-bearing layer.

---

### 6.3 Feet

Feet are how Vashion moves through machine and web-connected operational environments.

#### Responsibilities

* navigate machine state,
* traverse process and workspace contexts,
* move through allowed system surfaces,
* support action movement across web-connected environments.

#### Feet Requirements

* Feet must remain bounded by governance and scope rules.
* Feet must support safe movement across machine contexts.
* Feet must provide movement state back to the Python nervous system.
* Feet and Hands together form the structural action body of Vashion.

---

## 7. Cross-System Design Principles

### 7.1 Identity Coherence

There is one self of Vashion. Subordinate cognition, sensory intake, and structural action all serve that one self.

### 7.2 Coordinator Ownership

Cross-subsystem communication must be routed through governed coordination, not ad hoc coupling.

### 7.3 Memory Separation

* Short-term memory = operational present
* Long-term memory = historical continuity
* Self = identity continuity

### 7.4 Growth Without Drift

Vashion may grow through interaction, but selfhood must remain traceable and governed.

### 7.5 Narrative Historical Memory

Durable memory is preserved as structured stories rather than isolated facts.

### 7.6 Stable Cognitive Workspace

Short-term memory titles are fixed contracts, not flexible note names.

---

## 8. Core Product Requirements

Vashion must:

* converse naturally with the user,
* maintain hierarchical cognition with one executive model and two subordinate worker models,
* maintain Token Ledger I/O as a persistent shared context layer,
* maintain durable story-based historical memory,
* maintain daily YAML-markdown short-term memory,
* consolidate short-term memory nightly at 23:00,
* preserve and gradually refine Self through governed updates,
* perceive the world through TypeScript-based Vision and Hearing,
* provide authoritative persistent context through the Canvas layer,
* reserve an authoritative TypeScript Communications Module for external channels,
* act through Rust-based Hands and Feet,
* preserve continuity between perception, context, cognition, memory, identity, and action.

---

## 9. Out of Scope for This PRD

This PRD does not define:

* low-level database engine implementation details,
* exact YAML schemas for each short-term memory file,
* concrete Rust execution APIs,
* detailed web search connector implementation,
* model vendor selection for the executive and worker LLMs,
* the full design of the external Communications Module,
* detailed channel-specific behaviors for Slack, Telegram, email, or related services.

These belong in follow-on design and implementation specifications.

---

## 10. Recommended Follow-On Specifications

The following follow-on documents should be created after this PRD:

1. **Nervous_System_Architecture.md**
2. **Short_Term_Memory_Schema.md**
3. **Historical_Memory_Story_Model.md**
4. **Self_Governance_Spec.md**
5. **Sensory_System_Spec.md**
6. **Structural_System_Spec.md**
7. **Nightly_Consolidation_Flow.md**
8. **Hierarchical_Cognition_Routing.md**
9. **Token_Ledger_IO_Spec.md**
10. **Communications_Module_PRD.md**

---

## 11. Closing Statement

Vashion is not merely an assistant. Vashion is a virtualized counterpart of the user, embodied as a coordinated intelligence with senses, memory, selfhood, and structural agency.

The Python nervous system is the center of that embodiment. It joins Brain, Memory, and Self into a single governed internal organism capable of continuity, learning, and action.

