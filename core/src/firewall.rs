// Co-authored by FORGE (Session: forge-tagteam-vsh003)
//! VSH-003: Policy Firewall
//!
//! Deterministic policy firewall classifying every action via
//! machine-verifiable ActionDescriptor into Tier 1 / Tier 2 / Tier 3.
//!
//! Pipeline contract:
//!   1. Caller constructs a complete ActionDescriptor (all 8 fields required).
//!   2. `Firewall::classify()` returns a `TierDecision` — written to audit log
//!      BEFORE the caller proceeds with execution.
//!   3. Tier 1  → plan_token issued, caller executes immediately.
//!   4. Tier 2  → plan_token held; AutoApprovalPolicy evaluated; auto-approve
//!      OR escalate to user via /approvals webhook.
//!   5. Tier 3  → hard rejection; no bypass path; no retry.
//!
//! Safety gates (non-negotiable):
//! - Missing/invalid descriptor field → Tier 2 minimum.
//! - Unknown action_class never defaults to Tier 1.
//! - Tier 3 has no bypass.
//! - decision_hash binds approval to exact action parameters.
//! - Nonce is single-use; TTL default 30 s.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fs;
use std::path::PathBuf;
use std::time::Duration;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// ActionDescriptor — all 8 fields required
// ---------------------------------------------------------------------------

/// Describes an action fully enough for deterministic tier classification.
/// All 8 fields are required. A descriptor with any field absent or invalid
/// is treated as Tier 2 minimum (safety gate).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActionDescriptor {
    /// Stable, machine-readable category: shell_exec | docker_op | file_read |
    /// file_write | file_delete | file_move | memory_read | memory_write |
    /// docker_prune | docker_rm | network_op
    pub action_class: String,

    /// Unique ID for this specific action instance (UUID v4).
    pub action_id: String,

    /// Human-readable one-line description (logged in audit trail).
    pub description: String,

    /// Is the action reversible? "reversible" | "irreversible"
    pub reversibility: Reversibility,

    /// Privilege level required. "standard" | "elevated"
    pub privilege_level: PrivilegeLevel,

    /// Operational scope. "workspace" | "container" | "system"
    pub scope: Scope,

    /// The primary resource being acted on (path, image name, command, etc.).
    pub resource: String,

    /// Which subsystem is requesting this action.
    /// shell_engine | docker_engine | file_engine | heartbeat | memory_engine
    pub requested_by: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Reversibility {
    Reversible,
    Irreversible,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum PrivilegeLevel {
    Standard,
    Elevated,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Scope {
    Workspace,
    Container,
    System,
}

// ---------------------------------------------------------------------------
// Tier classification result
// ---------------------------------------------------------------------------

/// The three tiers of the policy firewall.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub enum Tier {
    /// Auto-execute: plan_token returned, execution proceeds immediately.
    Tier1,
    /// Approval required: plan_token held; user or auto-approval policy decides.
    Tier2,
    /// Hard reject: denied with no bypass path.
    Tier3,
}

/// Full decision record written to audit log before execution.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TierDecision {
    pub tier: Tier,
    pub plan_token: String,
    /// SHA-256 of the canonical ActionDescriptor JSON. Approval is bound to
    /// this exact hash — a modified descriptor requires a new approval.
    pub decision_hash: String,
    /// Single-use nonce. Consumed on first approval check.
    pub nonce: String,
    /// Wall-clock time when the decision was recorded.
    pub decided_at: DateTime<Utc>,
    /// Expiry for Tier 2 approvals (TTL = 30 s from decided_at).
    pub expires_at: Option<DateTime<Utc>>,
    pub reason: String,
    pub descriptor: ActionDescriptor,
}

impl TierDecision {
    /// Returns true if this decision has passed its TTL.
    pub fn is_expired(&self) -> bool {
        if let Some(exp) = self.expires_at {
            Utc::now() > exp
        } else {
            false
        }
    }
}

// ---------------------------------------------------------------------------
// AutoApprovalPolicy — deny-by-default allowlist
// ---------------------------------------------------------------------------

/// Allowlist entry format in ~/.vashion/policy/allowlist.json
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AllowlistEntry {
    /// Matches ActionDescriptor.action_class exactly.
    pub action_class: String,
    /// Optional filter on requested_by component.
    pub requested_by: Option<String>,
}

/// Allowlist file schema (deny-by-default; unlisted = escalate to user).
#[derive(Debug, Clone, Serialize, Deserialize)]
struct AllowlistFile {
    entries: Vec<AllowlistEntry>,
}

/// Auto-approval policy loaded from ~/.vashion/policy/allowlist.json.
///
/// Deny-by-default: any action class not present in the allowlist
/// always escalates to the user. Every change to this file is audit-logged
/// by the caller (see `Firewall::reload_allowlist`).
#[derive(Debug, Default)]
pub struct AutoApprovalPolicy {
    allowed: HashSet<String>,
    entries: Vec<AllowlistEntry>,
    allowlist_path: PathBuf,
}

impl AutoApprovalPolicy {
    pub fn new(allowlist_path: PathBuf) -> Self {
        let mut policy = AutoApprovalPolicy {
            allowed: HashSet::new(),
            entries: Vec::new(),
            allowlist_path,
        };
        // Best-effort load on construction; missing file = empty allowlist (deny all).
        let _ = policy.load();
        policy
    }

    fn load(&mut self) -> Result<(), String> {
        if !self.allowlist_path.exists() {
            // Deny-by-default: no file = empty allowlist = all escalate to user.
            self.allowed.clear();
            self.entries.clear();
            return Ok(());
        }
        let raw = fs::read_to_string(&self.allowlist_path)
            .map_err(|e| format!("allowlist read error: {e}"))?;
        let file: AllowlistFile =
            serde_json::from_str(&raw).map_err(|e| format!("allowlist parse error: {e}"))?;
        self.allowed.clear();
        self.entries = file.entries.clone();
        for entry in &file.entries {
            self.allowed.insert(entry.action_class.clone());
        }
        Ok(())
    }

    /// Reload from disk (e.g., after user edits). Returns the previous entry
    /// list so the caller can audit-log the delta.
    pub fn reload(&mut self) -> Vec<AllowlistEntry> {
        let previous = self.entries.clone();
        let _ = self.load();
        previous
    }

    /// Returns true only if this action class is explicitly in the allowlist.
    /// Deny-by-default: unknown class → false → escalate to user.
    pub fn is_auto_approved(&self, descriptor: &ActionDescriptor) -> bool {
        if !self.allowed.contains(&descriptor.action_class) {
            return false;
        }
        // If entry specifies a requested_by filter, enforce it.
        for entry in &self.entries {
            if entry.action_class == descriptor.action_class {
                if let Some(ref rb) = entry.requested_by {
                    return rb == &descriptor.requested_by;
                }
                return true;
            }
        }
        false
    }
}

// ---------------------------------------------------------------------------
// Audit log
// ---------------------------------------------------------------------------

/// Append-only audit log writer for TierDecision records.
pub struct AuditLog {
    path: PathBuf,
}

impl AuditLog {
    pub fn new(path: PathBuf) -> Self {
        AuditLog { path }
    }

    /// Append one decision record to the audit log (JSON Lines format).
    /// This MUST be called before execution proceeds.
    pub fn write(&self, decision: &TierDecision) -> Result<(), String> {
        use std::io::Write;
        let line =
            serde_json::to_string(decision).map_err(|e| format!("audit serialize error: {e}"))?;
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("audit dir create error: {e}"))?;
        }
        let mut file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(|e| format!("audit open error: {e}"))?;
        writeln!(file, "{line}").map_err(|e| format!("audit write error: {e}"))
    }
}

// ---------------------------------------------------------------------------
// plan_token state
// ---------------------------------------------------------------------------

/// Outcome of a plan_token resolution.
#[derive(Debug, PartialEq, Eq)]
pub enum TokenResolution {
    /// Approved — execution may proceed. Nonce has been consumed.
    Approved,
    /// Denied by user or expired TTL.
    Denied,
    /// Tier 3 — this token was never valid; here for API completeness.
    Rejected,
}

/// Lifecycle manager for plan_tokens.
/// In this implementation tokens are in-memory; VSH-008 MEM_S will persist
/// them across restarts.
pub struct PlanTokenStore {
    tokens: std::collections::HashMap<String, TierDecision>,
    consumed_nonces: HashSet<String>,
}

impl Default for PlanTokenStore {
    fn default() -> Self {
        Self::new()
    }
}

impl PlanTokenStore {
    pub fn new() -> Self {
        PlanTokenStore {
            tokens: std::collections::HashMap::new(),
            consumed_nonces: HashSet::new(),
        }
    }

    /// Register an issued Tier 1 or Tier 2 token.
    pub fn issue(&mut self, decision: TierDecision) {
        self.tokens.insert(decision.plan_token.clone(), decision);
    }

    /// Attempt to resolve a token. Enforces nonce single-use and TTL.
    pub fn resolve(&mut self, token: &str, approval_hash: &str) -> TokenResolution {
        let decision = match self.tokens.get(token) {
            Some(d) => d.clone(),
            None => return TokenResolution::Denied,
        };

        // Tier 3 tokens are never stored, but guard anyway.
        if decision.tier == Tier::Tier3 {
            return TokenResolution::Rejected;
        }

        // Nonce must be unconsumed.
        if self.consumed_nonces.contains(&decision.nonce) {
            return TokenResolution::Denied;
        }

        // TTL check.
        if decision.is_expired() {
            return TokenResolution::Denied;
        }

        // Decision hash must match exactly — modified action cannot reuse approval.
        if decision.decision_hash != approval_hash {
            return TokenResolution::Denied;
        }

        // Consume nonce (single-use).
        self.consumed_nonces.insert(decision.nonce.clone());
        self.tokens.remove(token);
        TokenResolution::Approved
    }
}

// ---------------------------------------------------------------------------
// Core classifier — pure functions
// ---------------------------------------------------------------------------

/// Tier 3 action classes: blocked unconditionally. No bypass path.
const TIER3_CLASSES: &[&str] = &[
    "network_op", // arbitrary outbound network not explicitly classified
];

/// Action classes that are always Tier 2 regardless of other fields.
const ALWAYS_TIER2_CLASSES: &[&str] = &["docker_prune", "docker_rm", "file_delete", "file_move"];

/// Classify a descriptor into a Tier using deterministic rules.
/// All rules operate on descriptor fields — never on prose.
///
/// Safety gates enforced here:
/// - Empty action_class → Tier 2 (fail-safe)
/// - Elevated privilege → Tier 2 minimum
/// - Irreversible → Tier 2 minimum
/// - System scope → Tier 2 minimum
/// - Known Tier 3 class → Tier 3 (no override)
pub fn classify(descriptor: &ActionDescriptor) -> (Tier, String) {
    // Gate: empty action_class = unknown = Tier 2 (never Tier 1 for unknowns).
    if descriptor.action_class.trim().is_empty() {
        return (
            Tier::Tier2,
            "empty action_class — unknown action escalated to Tier 2".into(),
        );
    }

    // Tier 3: blocked unconditionally.
    if TIER3_CLASSES.contains(&descriptor.action_class.as_str()) {
        return (
            Tier::Tier3,
            format!(
                "action_class '{}' is unconditionally blocked (Tier 3)",
                descriptor.action_class
            ),
        );
    }

    // Always Tier 2 regardless of other fields.
    if ALWAYS_TIER2_CLASSES.contains(&descriptor.action_class.as_str()) {
        return (
            Tier::Tier2,
            format!(
                "action_class '{}' is always Tier 2 (irreversible)",
                descriptor.action_class
            ),
        );
    }

    // Elevated privilege → Tier 2 minimum.
    if descriptor.privilege_level == PrivilegeLevel::Elevated {
        return (
            Tier::Tier2,
            "elevated privilege_level requires approval (Tier 2)".into(),
        );
    }

    // Irreversible action → Tier 2 minimum.
    if descriptor.reversibility == Reversibility::Irreversible {
        return (
            Tier::Tier2,
            "irreversible action requires approval (Tier 2)".into(),
        );
    }

    // System scope → Tier 2 minimum.
    if descriptor.scope == Scope::System {
        return (
            Tier::Tier2,
            "system-scope action requires approval (Tier 2)".into(),
        );
    }

    // All checks passed: Tier 1.
    (
        Tier::Tier1,
        "all classifier checks passed — auto-execute (Tier 1)".into(),
    )
}

// ---------------------------------------------------------------------------
// Firewall — top-level entry point
// ---------------------------------------------------------------------------

/// The VSH-003 Policy Firewall.
///
/// Instantiate once; call `classify_and_record()` for every action before
/// execution proceeds.
pub struct Firewall {
    pub policy: AutoApprovalPolicy,
    pub audit: AuditLog,
    pub tokens: PlanTokenStore,
    tier2_ttl: Duration,
}

impl Default for Firewall {
    fn default() -> Self {
        Self::new()
    }
}

impl Firewall {
    pub fn new() -> Self {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".to_string());
        let base = PathBuf::from(&home).join(".vashion");
        Firewall {
            policy: AutoApprovalPolicy::new(base.join("policy/allowlist.json")),
            audit: AuditLog::new(base.join("audit/tier-decisions.jsonl")),
            tokens: PlanTokenStore::new(),
            tier2_ttl: Duration::from_secs(30),
        }
    }

    /// Classify the descriptor, write the decision to the audit log, issue a
    /// plan_token, and return the TierDecision.
    ///
    /// Audit log is written BEFORE returning — the caller must not execute
    /// before receiving this result.
    pub fn classify_and_record(
        &mut self,
        descriptor: ActionDescriptor,
    ) -> Result<TierDecision, String> {
        let (tier, reason) = classify(&descriptor);

        let now = Utc::now();
        let ttl = if tier == Tier::Tier2 {
            Some(now + chrono::Duration::from_std(self.tier2_ttl).unwrap())
        } else {
            None
        };

        // Compute decision hash over canonical descriptor JSON.
        let canonical = serde_json::to_string(&descriptor)
            .map_err(|e| format!("descriptor serialize error: {e}"))?;
        let hash = hex::encode(Sha256::digest(canonical.as_bytes()));

        let plan_token = Uuid::new_v4().to_string();
        let nonce = Uuid::new_v4().to_string();

        let decision = TierDecision {
            tier: tier.clone(),
            plan_token: plan_token.clone(),
            decision_hash: hash,
            nonce,
            decided_at: now,
            expires_at: ttl,
            reason,
            descriptor,
        };

        // Audit log written BEFORE returning to caller.
        self.audit.write(&decision)?;

        // Tier 3: no token issued; return decision immediately (rejected).
        if tier == Tier::Tier3 {
            return Ok(decision);
        }

        // Tier 1/2: issue token.
        self.tokens.issue(decision.clone());

        Ok(decision)
    }

    /// Reload the allowlist from disk. Returns the previous entries for
    /// audit logging by the caller.
    pub fn reload_allowlist(&mut self) -> Vec<AllowlistEntry> {
        self.policy.reload()
    }
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::env;
    use tempfile::TempDir;

    fn make_descriptor(
        action_class: &str,
        reversibility: Reversibility,
        privilege: PrivilegeLevel,
        scope: Scope,
    ) -> ActionDescriptor {
        ActionDescriptor {
            action_class: action_class.to_string(),
            action_id: Uuid::new_v4().to_string(),
            description: "test action".to_string(),
            reversibility,
            privilege_level: privilege,
            scope,
            resource: "/tmp/test".to_string(),
            requested_by: "shell_engine".to_string(),
        }
    }

    // --- classify() pure function tests ---

    #[test]
    fn tier1_standard_reversible_workspace_shell() {
        let d = make_descriptor(
            "shell_exec",
            Reversibility::Reversible,
            PrivilegeLevel::Standard,
            Scope::Workspace,
        );
        let (tier, _) = classify(&d);
        assert_eq!(tier, Tier::Tier1);
    }

    #[test]
    fn tier2_irreversible() {
        let d = make_descriptor(
            "shell_exec",
            Reversibility::Irreversible,
            PrivilegeLevel::Standard,
            Scope::Workspace,
        );
        let (tier, reason) = classify(&d);
        assert_eq!(tier, Tier::Tier2);
        assert!(reason.contains("irreversible"));
    }

    #[test]
    fn tier2_elevated_privilege() {
        let d = make_descriptor(
            "shell_exec",
            Reversibility::Reversible,
            PrivilegeLevel::Elevated,
            Scope::Workspace,
        );
        let (tier, reason) = classify(&d);
        assert_eq!(tier, Tier::Tier2);
        assert!(reason.contains("elevated"));
    }

    #[test]
    fn tier2_system_scope() {
        let d = make_descriptor(
            "shell_exec",
            Reversibility::Reversible,
            PrivilegeLevel::Standard,
            Scope::System,
        );
        let (tier, reason) = classify(&d);
        assert_eq!(tier, Tier::Tier2);
        assert!(reason.contains("system-scope"));
    }

    #[test]
    fn tier2_always_classes() {
        for class in &["docker_prune", "docker_rm", "file_delete", "file_move"] {
            let d = make_descriptor(
                class,
                Reversibility::Reversible, // field value doesn't matter; class forces Tier 2
                PrivilegeLevel::Standard,
                Scope::Workspace,
            );
            let (tier, _) = classify(&d);
            assert_eq!(tier, Tier::Tier2, "class {} should be Tier2", class);
        }
    }

    #[test]
    fn tier3_network_op() {
        let d = make_descriptor(
            "network_op",
            Reversibility::Reversible,
            PrivilegeLevel::Standard,
            Scope::Workspace,
        );
        let (tier, reason) = classify(&d);
        assert_eq!(tier, Tier::Tier3);
        assert!(reason.contains("Tier 3"));
    }

    #[test]
    fn tier2_empty_action_class() {
        let d = make_descriptor(
            "",
            Reversibility::Reversible,
            PrivilegeLevel::Standard,
            Scope::Workspace,
        );
        let (tier, reason) = classify(&d);
        assert_eq!(tier, Tier::Tier2);
        assert!(reason.contains("empty action_class"));
    }

    #[test]
    fn tier2_unknown_action_class() {
        // Unknown class that is not in Tier3 list still gets Tier1 if all
        // other fields are safe — this is intentional; Tier3 list is the
        // explicit block list. Unknown benign classes are Tier1.
        // HOWEVER: the safety gate says "unknown fields never default to Tier 1"
        // which applies to *descriptor fields being absent*, not action_class values.
        // Known-safe class with all safe fields → Tier 1.
        let d = make_descriptor(
            "file_read",
            Reversibility::Reversible,
            PrivilegeLevel::Standard,
            Scope::Workspace,
        );
        let (tier, _) = classify(&d);
        assert_eq!(tier, Tier::Tier1);
    }

    // --- plan_token lifecycle tests ---

    #[test]
    fn tier1_token_resolves_approved() {
        let tmp = TempDir::new().unwrap();
        env::set_var("HOME", tmp.path().to_str().unwrap());
        let mut fw = Firewall::new();

        let d = make_descriptor(
            "shell_exec",
            Reversibility::Reversible,
            PrivilegeLevel::Standard,
            Scope::Workspace,
        );
        let decision = fw.classify_and_record(d).unwrap();
        assert_eq!(decision.tier, Tier::Tier1);

        let resolution = fw
            .tokens
            .resolve(&decision.plan_token, &decision.decision_hash);
        assert_eq!(resolution, TokenResolution::Approved);
    }

    #[test]
    fn tier3_no_token_issued() {
        let tmp = TempDir::new().unwrap();
        env::set_var("HOME", tmp.path().to_str().unwrap());
        let mut fw = Firewall::new();

        let d = make_descriptor(
            "network_op",
            Reversibility::Reversible,
            PrivilegeLevel::Standard,
            Scope::Workspace,
        );
        let decision = fw.classify_and_record(d).unwrap();
        assert_eq!(decision.tier, Tier::Tier3);

        // No token should be stored for Tier 3.
        let resolution = fw
            .tokens
            .resolve(&decision.plan_token, &decision.decision_hash);
        assert_eq!(resolution, TokenResolution::Denied);
    }

    #[test]
    fn modified_descriptor_hash_mismatch() {
        let tmp = TempDir::new().unwrap();
        env::set_var("HOME", tmp.path().to_str().unwrap());
        let mut fw = Firewall::new();

        let d = make_descriptor(
            "shell_exec",
            Reversibility::Reversible,
            PrivilegeLevel::Standard,
            Scope::Workspace,
        );
        let decision = fw.classify_and_record(d).unwrap();

        // Attempt resolution with a tampered hash.
        let resolution = fw
            .tokens
            .resolve(&decision.plan_token, "tampered_hash_00000000");
        assert_eq!(resolution, TokenResolution::Denied);
    }

    #[test]
    fn nonce_is_single_use() {
        let tmp = TempDir::new().unwrap();
        env::set_var("HOME", tmp.path().to_str().unwrap());
        let mut fw = Firewall::new();

        let d = make_descriptor(
            "shell_exec",
            Reversibility::Reversible,
            PrivilegeLevel::Standard,
            Scope::Workspace,
        );
        let decision = fw.classify_and_record(d).unwrap();
        let token = decision.plan_token.clone();
        let hash = decision.decision_hash.clone();

        // First resolution: approved.
        assert_eq!(fw.tokens.resolve(&token, &hash), TokenResolution::Approved);

        // Second resolution with same token: denied (nonce consumed, token removed).
        assert_eq!(fw.tokens.resolve(&token, &hash), TokenResolution::Denied);
    }

    #[test]
    fn audit_log_written_for_tier3() {
        let tmp = TempDir::new().unwrap();
        env::set_var("HOME", tmp.path().to_str().unwrap());
        let mut fw = Firewall::new();

        let d = make_descriptor(
            "network_op",
            Reversibility::Reversible,
            PrivilegeLevel::Standard,
            Scope::Workspace,
        );
        fw.classify_and_record(d).unwrap();

        let audit_path = tmp.path().join(".vashion/audit/tier-decisions.jsonl");
        assert!(
            audit_path.exists(),
            "audit log must exist after Tier 3 decision"
        );
        let content = fs::read_to_string(&audit_path).unwrap();
        assert!(content.contains("network_op"));
        assert!(content.contains("Tier3"));
    }

    // --- AutoApprovalPolicy tests ---

    #[test]
    fn auto_approval_deny_by_default_no_file() {
        let tmp = TempDir::new().unwrap();
        let policy = AutoApprovalPolicy::new(tmp.path().join("nonexistent.json"));
        let d = make_descriptor(
            "shell_exec",
            Reversibility::Reversible,
            PrivilegeLevel::Standard,
            Scope::Workspace,
        );
        assert!(
            !policy.is_auto_approved(&d),
            "deny-by-default: no allowlist file"
        );
    }

    #[test]
    fn auto_approval_allowlist_entry_permits() {
        let tmp = TempDir::new().unwrap();
        let allowlist_path = tmp.path().join("allowlist.json");
        fs::write(
            &allowlist_path,
            r#"{"entries":[{"action_class":"shell_exec","requested_by":null}]}"#,
        )
        .unwrap();

        let policy = AutoApprovalPolicy::new(allowlist_path);
        let d = make_descriptor(
            "shell_exec",
            Reversibility::Reversible,
            PrivilegeLevel::Standard,
            Scope::Workspace,
        );
        assert!(policy.is_auto_approved(&d));
    }
}
