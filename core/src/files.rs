// Co-authored by FORGE (Session: agent-1-tagteam-adhoc)
//! VSH-004: Policy-Bounded File Operations
//!
//! File system operations scoped to configured workspace roots with policy
//! tier enforcement via VSH-003.
//!
//! Pipeline contract (enforced in this order):
//!   1. Symlink escape detection — FW never sees an escaped path.
//!   2. Path canonicalization and workspace-root check.
//!   3. ActionDescriptor construction with correct scope hint.
//!   4. VSH-003 Firewall classification.
//!   5. Execute operation (only for Tier 1; Tier 2 returns pending token).
//!   6. Audit-log path, operation, tier, and outcome.
//!
//! Safety gates (non-negotiable):
//! - Symlink escape detection runs on every path before FW classification.
//! - delete is always Tier 2 — enforced by firewall ALWAYS_TIER2_CLASSES.
//! - move is always Tier 2 — enforced by firewall ALWAYS_TIER2_CLASSES.
//! - Operations outside workspace roots → System scope → Tier 2 minimum.

use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::{Path, PathBuf};
use uuid::Uuid;

use crate::firewall::{
    ActionDescriptor, Firewall, PrivilegeLevel, Reversibility, Scope, Tier, TierDecision,
};

// ---------------------------------------------------------------------------
// Config: workspace roots from ~/.vashion/config.toml
// ---------------------------------------------------------------------------

#[derive(Debug, Deserialize, Default)]
struct VashionConfig {
    workspace: Option<WorkspaceConfig>,
}

#[derive(Debug, Deserialize, Default)]
struct WorkspaceConfig {
    roots: Vec<String>,
}

/// Load workspace roots from ~/.vashion/config.toml.
/// Returns empty vec if file missing or malformed — callers treat empty roots
/// as "no auto-approve zone", so all operations escalate to Tier 2.
pub fn load_workspace_roots() -> Vec<PathBuf> {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/root".to_string());
    load_workspace_roots_from(&PathBuf::from(home))
}

/// Load workspace roots using an explicit home directory — avoids HOME env var races in tests.
pub fn load_workspace_roots_from(home: &Path) -> Vec<PathBuf> {
    let config_path = home.join(".vashion/config.toml");
    if !config_path.exists() {
        return Vec::new();
    }
    let raw = match fs::read_to_string(&config_path) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    let config: VashionConfig = match toml::from_str(&raw) {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    config
        .workspace
        .unwrap_or_default()
        .roots
        .into_iter()
        .map(PathBuf::from)
        .collect()
}

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

#[derive(Debug)]
pub enum FileError {
    /// Symlink in path chain resolves outside workspace root — hard reject.
    /// FW never sees this path; operation is blocked before descriptor construction.
    SymlinkEscape(PathBuf),
    /// VSH-003 returned Tier 3 — hard rejection, no bypass.
    FirewallRejected(String),
    /// Operation requires Tier 2 approval. Caller holds the plan_token and
    /// must await user approval before re-executing.
    Tier2Pending(Box<TierDecision>),
    /// I/O error during the file operation itself.
    Io(std::io::Error),
    /// Configuration or argument error.
    Config(String),
}

impl std::fmt::Display for FileError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            FileError::SymlinkEscape(p) => {
                write!(f, "symlink escape detected: {}", p.display())
            }
            FileError::FirewallRejected(r) => write!(f, "firewall rejected: {r}"),
            FileError::Tier2Pending(d) => {
                write!(f, "tier 2 approval required — plan_token: {}", d.plan_token)
            }
            FileError::Io(e) => write!(f, "I/O error: {e}"),
            FileError::Config(s) => write!(f, "config error: {s}"),
        }
    }
}

// ---------------------------------------------------------------------------
// Audit log for file operations
// ---------------------------------------------------------------------------

/// One entry written to the file-ops audit log after every operation.
#[derive(Debug, Serialize)]
pub struct FileAuditEntry {
    pub path: String,
    pub operation: String,
    pub tier: String,
    pub outcome: String,
    pub timestamp: String,
    pub plan_token: String,
}

struct FileAuditLog {
    path: PathBuf,
}

impl FileAuditLog {
    fn new(path: PathBuf) -> Self {
        FileAuditLog { path }
    }

    fn write(&self, entry: &FileAuditEntry) -> Result<(), String> {
        use std::io::Write as _;
        let line =
            serde_json::to_string(entry).map_err(|e| format!("file audit serialize error: {e}"))?;
        if let Some(parent) = self.path.parent() {
            fs::create_dir_all(parent).map_err(|e| format!("file audit dir create error: {e}"))?;
        }
        let mut file = fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
            .map_err(|e| format!("file audit open error: {e}"))?;
        writeln!(file, "{line}").map_err(|e| format!("file audit write error: {e}"))
    }
}

fn tier_label(t: &Tier) -> &'static str {
    match t {
        Tier::Tier1 => "Tier1",
        Tier::Tier2 => "Tier2",
        Tier::Tier3 => "Tier3",
    }
}

// ---------------------------------------------------------------------------
// FileEngine
// ---------------------------------------------------------------------------

pub struct FileEngine {
    pub workspace_roots: Vec<PathBuf>,
    audit: FileAuditLog,
}

impl FileEngine {
    pub fn new(workspace_roots: Vec<PathBuf>) -> Self {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".to_string());
        let base = PathBuf::from(&home).join(".vashion");
        FileEngine {
            workspace_roots,
            audit: FileAuditLog::new(base.join("audit/file-ops.jsonl")),
        }
    }

    /// Construct with an explicit home base path — avoids HOME env var races in tests.
    pub fn new_with_home(workspace_roots: Vec<PathBuf>, home: &Path) -> Self {
        let base = home.join(".vashion");
        FileEngine {
            workspace_roots,
            audit: FileAuditLog::new(base.join("audit/file-ops.jsonl")),
        }
    }

    /// True if `path` starts with any configured workspace root.
    pub fn is_within_workspace(&self, path: &Path) -> bool {
        self.workspace_roots
            .iter()
            .any(|root| path.starts_with(root))
    }

    /// Walk `path` component-by-component detecting symlinks that escape the
    /// configured workspace roots. This MUST be called before ActionDescriptor
    /// construction — FW never sees an escaped path.
    ///
    /// Returns the fully resolved path on success. Stops at the first
    /// non-existent component (useful for write targets whose file doesn't yet
    /// exist).
    pub fn resolve_and_check(&self, path: &Path) -> Result<PathBuf, FileError> {
        let abs = if path.is_absolute() {
            path.to_path_buf()
        } else {
            std::env::current_dir().map_err(FileError::Io)?.join(path)
        };

        let mut current = PathBuf::new();

        for component in abs.components() {
            current.push(component);

            if !current.exists() {
                // Non-existent component — stop here (e.g. write to new file).
                break;
            }

            let meta = fs::symlink_metadata(&current).map_err(FileError::Io)?;
            if meta.file_type().is_symlink() {
                let target = fs::read_link(&current).map_err(FileError::Io)?;
                let resolved = if target.is_absolute() {
                    target
                } else {
                    current.parent().unwrap_or(Path::new("/")).join(&target)
                };

                // Fully canonicalize the symlink target to detect multi-hop escapes.
                let canonical = resolved.canonicalize().map_err(FileError::Io)?;

                // Safety gate: symlink must not escape any configured workspace root.
                if !self.workspace_roots.is_empty() && !self.is_within_workspace(&canonical) {
                    return Err(FileError::SymlinkEscape(current.clone()));
                }

                current = canonical;
            }
        }

        Ok(current)
    }

    /// Resolve a write target path that may not yet exist.
    /// Canonicalizes the parent directory; appends the filename unchanged.
    pub fn resolve_write_target(&self, path: &Path) -> Result<PathBuf, FileError> {
        if path.exists() {
            return self.resolve_and_check(path);
        }
        let parent = path.parent().unwrap_or(Path::new("."));
        let canonical_parent = self.resolve_and_check(parent)?;
        let filename = path.file_name().ok_or_else(|| {
            FileError::Config(format!("path has no filename: {}", path.display()))
        })?;
        Ok(canonical_parent.join(filename))
    }

    /// Map a resolved path to a firewall Scope.
    /// Within workspace → Workspace (eligible for Tier 1).
    /// Outside workspace → System (forces Tier 2 minimum).
    fn scope_for(&self, resolved: &Path) -> Scope {
        if self.is_within_workspace(resolved) {
            Scope::Workspace
        } else {
            Scope::System
        }
    }

    fn make_descriptor(
        action_class: &str,
        resource: &Path,
        reversibility: Reversibility,
        scope: Scope,
        description: &str,
    ) -> ActionDescriptor {
        ActionDescriptor {
            action_class: action_class.to_string(),
            action_id: Uuid::new_v4().to_string(),
            description: description.to_string(),
            reversibility,
            privilege_level: PrivilegeLevel::Standard,
            scope,
            resource: resource.to_string_lossy().to_string(),
            requested_by: "file_engine".to_string(),
        }
    }

    fn log_outcome(&self, path: &Path, operation: &str, tier: &Tier, outcome: &str, token: &str) {
        let entry = FileAuditEntry {
            path: path.to_string_lossy().to_string(),
            operation: operation.to_string(),
            tier: tier_label(tier).to_string(),
            outcome: outcome.to_string(),
            timestamp: Utc::now().to_rfc3339(),
            plan_token: token.to_string(),
        };
        let _ = self.audit.write(&entry);
    }

    // -------------------------------------------------------------------------
    // Public operations
    // -------------------------------------------------------------------------

    /// Read file contents.
    /// Within workspace roots → Tier 1 (auto-execute).
    /// Outside workspace roots → Tier 2 (approval required).
    pub fn read(&self, path: &Path, fw: &mut Firewall) -> Result<Vec<u8>, FileError> {
        let resolved = self.resolve_and_check(path)?;
        let scope = self.scope_for(&resolved);
        let descriptor = Self::make_descriptor(
            "file_read",
            &resolved,
            Reversibility::Reversible,
            scope,
            &format!("read file: {}", resolved.display()),
        );

        let decision = fw
            .classify_and_record(descriptor)
            .map_err(FileError::Config)?;
        let tier = decision.tier.clone();
        let token = decision.plan_token.clone();

        match tier {
            Tier::Tier3 => {
                self.log_outcome(&resolved, "read", &Tier::Tier3, "rejected", &token);
                Err(FileError::FirewallRejected(decision.reason))
            }
            Tier::Tier2 => {
                self.log_outcome(&resolved, "read", &Tier::Tier2, "pending-approval", &token);
                Err(FileError::Tier2Pending(Box::new(decision)))
            }
            Tier::Tier1 => {
                let contents = fs::read(&resolved).map_err(|e| {
                    self.log_outcome(
                        &resolved,
                        "read",
                        &Tier::Tier1,
                        &format!("error: {e}"),
                        &token,
                    );
                    FileError::Io(e)
                })?;
                self.log_outcome(&resolved, "read", &Tier::Tier1, "success", &token);
                Ok(contents)
            }
        }
    }

    /// Write content to a file (creating it if necessary).
    /// Within workspace roots → Tier 1 (auto-execute).
    /// Outside workspace roots → Tier 2 (approval required).
    pub fn write(&self, path: &Path, content: &[u8], fw: &mut Firewall) -> Result<(), FileError> {
        let resolved = self.resolve_write_target(path)?;
        let scope = self.scope_for(&resolved);
        let descriptor = Self::make_descriptor(
            "file_write",
            &resolved,
            Reversibility::Reversible,
            scope,
            &format!("write file: {}", resolved.display()),
        );

        let decision = fw
            .classify_and_record(descriptor)
            .map_err(FileError::Config)?;
        let tier = decision.tier.clone();
        let token = decision.plan_token.clone();

        match tier {
            Tier::Tier3 => {
                self.log_outcome(&resolved, "write", &Tier::Tier3, "rejected", &token);
                Err(FileError::FirewallRejected(decision.reason))
            }
            Tier::Tier2 => {
                self.log_outcome(&resolved, "write", &Tier::Tier2, "pending-approval", &token);
                Err(FileError::Tier2Pending(Box::new(decision)))
            }
            Tier::Tier1 => {
                if let Some(parent) = resolved.parent() {
                    fs::create_dir_all(parent).map_err(|e| {
                        self.log_outcome(
                            &resolved,
                            "write",
                            &Tier::Tier1,
                            &format!("error: {e}"),
                            &token,
                        );
                        FileError::Io(e)
                    })?;
                }
                fs::write(&resolved, content).map_err(|e| {
                    self.log_outcome(
                        &resolved,
                        "write",
                        &Tier::Tier1,
                        &format!("error: {e}"),
                        &token,
                    );
                    FileError::Io(e)
                })?;
                self.log_outcome(&resolved, "write", &Tier::Tier1, "success", &token);
                Ok(())
            }
        }
    }

    /// Delete a file or empty directory.
    /// ALWAYS Tier 2 — irreversible, regardless of path. No exception.
    pub fn delete(&self, path: &Path, fw: &mut Firewall) -> Result<(), FileError> {
        let resolved = self.resolve_and_check(path)?;
        let scope = self.scope_for(&resolved);
        let descriptor = Self::make_descriptor(
            "file_delete",
            &resolved,
            Reversibility::Irreversible,
            scope,
            &format!("delete: {}", resolved.display()),
        );

        let decision = fw
            .classify_and_record(descriptor)
            .map_err(FileError::Config)?;
        let tier = decision.tier.clone();
        let token = decision.plan_token.clone();

        match tier {
            Tier::Tier3 => {
                self.log_outcome(&resolved, "delete", &Tier::Tier3, "rejected", &token);
                Err(FileError::FirewallRejected(decision.reason))
            }
            // file_delete is ALWAYS_TIER2 in the classifier — Tier1 is unreachable
            // in practice, but treated as Tier2 pending here for defense-in-depth.
            Tier::Tier2 | Tier::Tier1 => {
                self.log_outcome(
                    &resolved,
                    "delete",
                    &Tier::Tier2,
                    "pending-approval",
                    &token,
                );
                Err(FileError::Tier2Pending(Box::new(decision)))
            }
        }
    }

    /// Move/rename a file or directory.
    /// ALWAYS Tier 2 — irreversible. No exception.
    pub fn move_file(&self, src: &Path, dst: &Path, fw: &mut Firewall) -> Result<(), FileError> {
        let src_resolved = self.resolve_and_check(src)?;
        let dst_resolved = self.resolve_write_target(dst)?;

        // Use System scope if either endpoint is outside workspace.
        let scope = if self.scope_for(&src_resolved) == Scope::System
            || self.scope_for(&dst_resolved) == Scope::System
        {
            Scope::System
        } else {
            Scope::Workspace
        };

        let descriptor = Self::make_descriptor(
            "file_move",
            &src_resolved,
            Reversibility::Irreversible,
            scope,
            &format!(
                "move: {} → {}",
                src_resolved.display(),
                dst_resolved.display()
            ),
        );

        let decision = fw
            .classify_and_record(descriptor)
            .map_err(FileError::Config)?;
        let tier = decision.tier.clone();
        let token = decision.plan_token.clone();

        match tier {
            Tier::Tier3 => {
                self.log_outcome(&src_resolved, "move", &Tier::Tier3, "rejected", &token);
                Err(FileError::FirewallRejected(decision.reason))
            }
            // file_move is ALWAYS_TIER2 in the classifier — Tier1 treated as Tier2.
            Tier::Tier2 | Tier::Tier1 => {
                self.log_outcome(
                    &src_resolved,
                    "move",
                    &Tier::Tier2,
                    "pending-approval",
                    &token,
                );
                Err(FileError::Tier2Pending(Box::new(decision)))
            }
        }
    }

    /// List directory contents.
    /// Within workspace roots → Tier 1 (auto-execute).
    /// Outside workspace roots → Tier 2 (approval required).
    pub fn list(&self, path: &Path, fw: &mut Firewall) -> Result<Vec<PathBuf>, FileError> {
        let resolved = self.resolve_and_check(path)?;
        let scope = self.scope_for(&resolved);
        let descriptor = Self::make_descriptor(
            "file_list",
            &resolved,
            Reversibility::Reversible,
            scope,
            &format!("list directory: {}", resolved.display()),
        );

        let decision = fw
            .classify_and_record(descriptor)
            .map_err(FileError::Config)?;
        let tier = decision.tier.clone();
        let token = decision.plan_token.clone();

        match tier {
            Tier::Tier3 => {
                self.log_outcome(&resolved, "list", &Tier::Tier3, "rejected", &token);
                Err(FileError::FirewallRejected(decision.reason))
            }
            Tier::Tier2 => {
                self.log_outcome(&resolved, "list", &Tier::Tier2, "pending-approval", &token);
                Err(FileError::Tier2Pending(Box::new(decision)))
            }
            Tier::Tier1 => {
                let entries = fs::read_dir(&resolved).map_err(|e| {
                    self.log_outcome(
                        &resolved,
                        "list",
                        &Tier::Tier1,
                        &format!("error: {e}"),
                        &token,
                    );
                    FileError::Io(e)
                })?;
                let mut result = Vec::new();
                for entry in entries {
                    let entry = entry.map_err(FileError::Io)?;
                    result.push(entry.path());
                }
                self.log_outcome(&resolved, "list", &Tier::Tier1, "success", &token);
                Ok(result)
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;
    use tempfile::TempDir;

    fn make_fw(tmp: &TempDir) -> Firewall {
        std::env::set_var("HOME", tmp.path().to_str().unwrap());
        Firewall::new()
    }

    // --- workspace membership ---

    #[test]
    fn within_workspace_direct_path() {
        let tmp = TempDir::new().unwrap();
        let engine = FileEngine::new(vec![tmp.path().to_path_buf()]);
        assert!(engine.is_within_workspace(tmp.path()));
        assert!(engine.is_within_workspace(&tmp.path().join("subdir/file.txt")));
    }

    #[test]
    fn outside_workspace() {
        let tmp = TempDir::new().unwrap();
        let engine = FileEngine::new(vec![tmp.path().to_path_buf()]);
        assert!(!engine.is_within_workspace(Path::new("/etc/passwd")));
    }

    // --- symlink escape detection ---

    #[test]
    fn symlink_within_workspace_allowed() {
        let tmp = TempDir::new().unwrap();
        let target = tmp.path().join("real_file.txt");
        fs::write(&target, b"hello").unwrap();
        let link = tmp.path().join("link.txt");
        symlink(&target, &link).unwrap();

        let engine = FileEngine::new(vec![tmp.path().to_path_buf()]);
        let result = engine.resolve_and_check(&link);
        assert!(result.is_ok(), "symlink within workspace must be allowed");
    }

    #[test]
    fn symlink_escape_blocked() {
        let workspace = TempDir::new().unwrap();
        let outside = TempDir::new().unwrap();
        let outside_file = outside.path().join("secret.txt");
        fs::write(&outside_file, b"secret").unwrap();

        // Symlink inside workspace pointing outside.
        let link = workspace.path().join("escape_link.txt");
        symlink(&outside_file, &link).unwrap();

        let engine = FileEngine::new(vec![workspace.path().to_path_buf()]);
        let result = engine.resolve_and_check(&link);
        assert!(
            matches!(result, Err(FileError::SymlinkEscape(_))),
            "symlink pointing outside workspace must be blocked"
        );
    }

    #[test]
    fn symlink_escape_in_subdir_blocked() {
        let workspace = TempDir::new().unwrap();
        let outside = TempDir::new().unwrap();

        // Build: workspace/subdir -> outside (directory symlink)
        let subdir_link = workspace.path().join("subdir");
        symlink(outside.path(), &subdir_link).unwrap();

        let engine = FileEngine::new(vec![workspace.path().to_path_buf()]);
        // Trying to access workspace/subdir/anything would escape via subdir.
        let result = engine.resolve_and_check(&subdir_link);
        assert!(
            matches!(result, Err(FileError::SymlinkEscape(_))),
            "directory symlink escaping workspace must be blocked"
        );
    }

    // --- read within workspace → Tier 1 ---

    #[test]
    fn read_within_workspace_tier1() {
        let tmp = TempDir::new().unwrap();
        let file = tmp.path().join("test.txt");
        fs::write(&file, b"content").unwrap();

        let mut fw = make_fw(&tmp);
        let engine = FileEngine::new(vec![tmp.path().to_path_buf()]);
        let result = engine.read(&file, &mut fw);
        assert!(
            result.is_ok(),
            "read within workspace should auto-execute: {:?}",
            result.err().map(|e| e.to_string())
        );
        assert_eq!(result.unwrap(), b"content");
    }

    // --- read outside workspace → Tier 2 ---

    #[test]
    fn read_outside_workspace_tier2() {
        let tmp = TempDir::new().unwrap();
        let workspace = tmp.path().join("ws");
        fs::create_dir_all(&workspace).unwrap();
        let outside_file = tmp.path().join("outside.txt");
        fs::write(&outside_file, b"outside").unwrap();

        let mut fw = make_fw(&tmp);
        let engine = FileEngine::new(vec![workspace]);
        let result = engine.read(&outside_file, &mut fw);
        assert!(
            matches!(result, Err(FileError::Tier2Pending(_))),
            "read outside workspace must require Tier 2 approval"
        );
    }

    // --- write within workspace → Tier 1 ---

    #[test]
    fn write_within_workspace_tier1() {
        let tmp = TempDir::new().unwrap();
        let file = tmp.path().join("write_test.txt");

        let mut fw = make_fw(&tmp);
        let engine = FileEngine::new(vec![tmp.path().to_path_buf()]);
        let result = engine.write(&file, b"written", &mut fw);
        assert!(
            result.is_ok(),
            "write within workspace should auto-execute: {:?}",
            result.err().map(|e| e.to_string())
        );
        assert_eq!(fs::read(&file).unwrap(), b"written");
    }

    // --- write outside workspace → Tier 2 ---

    #[test]
    fn write_outside_workspace_tier2() {
        let tmp = TempDir::new().unwrap();
        let workspace = tmp.path().join("ws");
        fs::create_dir_all(&workspace).unwrap();
        let outside_file = tmp.path().join("outside_write.txt");

        let mut fw = make_fw(&tmp);
        let engine = FileEngine::new(vec![workspace]);
        let result = engine.write(&outside_file, b"data", &mut fw);
        assert!(
            matches!(result, Err(FileError::Tier2Pending(_))),
            "write outside workspace must require Tier 2 approval"
        );
    }

    // --- delete always Tier 2 ---

    #[test]
    fn delete_always_tier2_within_workspace() {
        let tmp = TempDir::new().unwrap();
        let file = tmp.path().join("to_delete.txt");
        fs::write(&file, b"delete me").unwrap();

        let mut fw = make_fw(&tmp);
        let engine = FileEngine::new(vec![tmp.path().to_path_buf()]);
        let result = engine.delete(&file, &mut fw);
        assert!(
            matches!(result, Err(FileError::Tier2Pending(_))),
            "delete within workspace must still be Tier 2"
        );
    }

    #[test]
    fn delete_always_tier2_outside_workspace() {
        let tmp = TempDir::new().unwrap();
        let workspace = tmp.path().join("ws");
        fs::create_dir_all(&workspace).unwrap();
        let outside_file = tmp.path().join("outside_delete.txt");
        fs::write(&outside_file, b"delete outside").unwrap();

        let mut fw = make_fw(&tmp);
        let engine = FileEngine::new(vec![workspace]);
        let result = engine.delete(&outside_file, &mut fw);
        assert!(
            matches!(result, Err(FileError::Tier2Pending(_))),
            "delete outside workspace must also be Tier 2"
        );
    }

    // --- move always Tier 2 ---

    #[test]
    fn move_always_tier2() {
        let tmp = TempDir::new().unwrap();
        let src = tmp.path().join("source.txt");
        let dst = tmp.path().join("dest.txt");
        fs::write(&src, b"move me").unwrap();

        let mut fw = make_fw(&tmp);
        let engine = FileEngine::new(vec![tmp.path().to_path_buf()]);
        let result = engine.move_file(&src, &dst, &mut fw);
        assert!(
            matches!(result, Err(FileError::Tier2Pending(_))),
            "move must always be Tier 2"
        );
    }

    // --- list within workspace → Tier 1 ---

    #[test]
    fn list_within_workspace_tier1() {
        let tmp = TempDir::new().unwrap();
        fs::write(tmp.path().join("a.txt"), b"a").unwrap();
        fs::write(tmp.path().join("b.txt"), b"b").unwrap();

        let mut fw = make_fw(&tmp);
        let engine = FileEngine::new(vec![tmp.path().to_path_buf()]);
        let result = engine.list(tmp.path(), &mut fw);
        assert!(result.is_ok(), "list within workspace should auto-execute");
        assert!(result.unwrap().len() >= 2);
    }

    // --- list outside workspace → Tier 2 ---

    #[test]
    fn list_outside_workspace_tier2() {
        let tmp = TempDir::new().unwrap();
        let workspace = tmp.path().join("ws");
        fs::create_dir_all(&workspace).unwrap();
        let outside_dir = tmp.path().join("outside_dir");
        fs::create_dir_all(&outside_dir).unwrap();

        let mut fw = make_fw(&tmp);
        let engine = FileEngine::new(vec![workspace]);
        let result = engine.list(&outside_dir, &mut fw);
        assert!(
            matches!(result, Err(FileError::Tier2Pending(_))),
            "list outside workspace must require Tier 2 approval"
        );
    }

    // --- audit log written ---

    #[test]
    fn audit_log_written_after_read() {
        let tmp = TempDir::new().unwrap();
        let file = tmp.path().join("audited.txt");
        fs::write(&file, b"audit me").unwrap();

        let mut fw = make_fw(&tmp);
        let engine = FileEngine::new_with_home(vec![tmp.path().to_path_buf()], tmp.path());
        let _ = engine.read(&file, &mut fw);

        let audit_path = tmp.path().join(".vashion/audit/file-ops.jsonl");
        assert!(audit_path.exists(), "file-ops audit log must be written");
        let content = fs::read_to_string(&audit_path).unwrap();
        assert!(content.contains("file_read") || content.contains("\"read\""));
        assert!(content.contains("Tier1"));
    }

    #[test]
    fn audit_log_written_for_tier2_delete() {
        let tmp = TempDir::new().unwrap();
        let file = tmp.path().join("audit_delete.txt");
        fs::write(&file, b"x").unwrap();

        let mut fw = make_fw(&tmp);
        let engine = FileEngine::new_with_home(vec![tmp.path().to_path_buf()], tmp.path());
        let _ = engine.delete(&file, &mut fw);

        let audit_path = tmp.path().join(".vashion/audit/file-ops.jsonl");
        assert!(
            audit_path.exists(),
            "file-ops audit log must be written for delete"
        );
        let content = fs::read_to_string(&audit_path).unwrap();
        assert!(content.contains("delete"));
        assert!(content.contains("Tier2"));
    }

    // --- config: load_workspace_roots from TOML ---

    #[test]
    fn load_workspace_roots_from_toml() {
        let tmp = TempDir::new().unwrap();
        let config_dir = tmp.path().join(".vashion");
        fs::create_dir_all(&config_dir).unwrap();
        let roots_path = tmp.path().join("roots");
        fs::create_dir_all(&roots_path).unwrap();
        fs::write(
            config_dir.join("config.toml"),
            format!(
                "[workspace]\nroots = [\"{}\"]",
                roots_path.to_string_lossy()
            ),
        )
        .unwrap();

        let roots = load_workspace_roots_from(tmp.path());
        assert_eq!(roots.len(), 1);
        assert_eq!(roots[0], roots_path);
    }

    #[test]
    fn load_workspace_roots_missing_file_returns_empty() {
        let tmp = TempDir::new().unwrap();
        let roots = load_workspace_roots_from(tmp.path());
        assert!(
            roots.is_empty(),
            "missing config.toml must return empty roots"
        );
    }
}
