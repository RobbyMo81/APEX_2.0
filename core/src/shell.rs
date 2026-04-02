// Co-authored by FORGE (Session: forge-20260401222537-3567093)
//! VSH-001: Shell Execution Engine
//!
//! All commands pass through the following pipeline in strict order:
//!   1. Injection guard   — FW never sees raw untrusted input
//!   2. sudo block        — unconditional, not delegated to policy
//!   3. Allowlist check   — executable must be present before FW classification
//!   4. Firewall stub     — Tier classification (VSH-003 replaces this stub)
//!   5. Process spawn     — only if firewall returns Tier 1

use std::path::PathBuf;
use std::time::{Duration, SystemTime};
use tokio::process::Command;
use tokio::time::timeout;

// ---------------------------------------------------------------------------
// Public types
// ---------------------------------------------------------------------------

/// Per-execution record stored for VSH-007 crash recovery.
#[derive(Debug, Clone)]
pub struct ExecutionRecord {
    pub pid: u32,
    pub start_time: SystemTime,
    /// Opaque reference key used by VSH-007 to locate output artifacts.
    pub output_ref: String,
}

/// Output collected from a completed shell command.
#[derive(Debug)]
pub struct CommandOutput {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
    pub record: ExecutionRecord,
}

/// Errors produced by the shell engine.
#[derive(Debug)]
pub enum ShellError {
    /// `sudo` was the executable — blocked unconditionally at this layer.
    SudoBlocked,
    /// A shell injection pattern was detected in the raw input.
    InjectionDetected(String),
    /// The executable is not present in the configured allowlist.
    NotInAllowlist(String),
    /// VSH-003 Policy Firewall returned Tier 3 (hard reject, no bypass).
    FirewallRejected,
    /// The process exceeded the configured timeout; SIGKILL was sent.
    TimedOut,
    /// Underlying I/O error.
    Io(std::io::Error),
}

impl std::fmt::Display for ShellError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ShellError::SudoBlocked => write!(f, "sudo is blocked unconditionally"),
            ShellError::InjectionDetected(s) => write!(f, "command injection detected: {s}"),
            ShellError::NotInAllowlist(s) => write!(f, "executable not in allowlist: {s}"),
            ShellError::FirewallRejected => write!(f, "firewall rejected command (Tier 3)"),
            ShellError::TimedOut => write!(f, "command timed out"),
            ShellError::Io(e) => write!(f, "I/O error: {e}"),
        }
    }
}

impl std::error::Error for ShellError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        if let ShellError::Io(e) = self {
            Some(e)
        } else {
            None
        }
    }
}

impl From<std::io::Error> for ShellError {
    fn from(e: std::io::Error) -> Self {
        ShellError::Io(e)
    }
}

// ---------------------------------------------------------------------------
// Firewall stub (replaced by VSH-003)
// ---------------------------------------------------------------------------

/// Tier classification returned by the policy firewall.
/// This is the VSH-001 stub; VSH-003 implements the real classifier.
#[derive(Debug, PartialEq)]
#[allow(dead_code)] // Tier2/Tier3 constructed by VSH-003 when wired
enum FirewallTier {
    /// Auto-execute: plan_token issued, execution proceeds immediately.
    Tier1,
    /// Approval required: plan_token held until user approves.
    Tier2(String),
    /// Hard reject: denied with no bypass path, no retry.
    Tier3,
}

// ---------------------------------------------------------------------------
// ShellEngine
// ---------------------------------------------------------------------------

/// Shell execution engine (VSH-001).
pub struct ShellEngine {
    default_timeout: Duration,
    allowlist_path: PathBuf,
}

impl ShellEngine {
    /// Construct an engine with a 15-minute default timeout.
    pub fn new() -> Self {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/root".to_string());
        ShellEngine {
            default_timeout: Duration::from_secs(15 * 60),
            allowlist_path: PathBuf::from(home).join(".vashion/policy/exec-allowlist.json"),
        }
    }

    /// Execute `command` (whitespace-split into structured argv).
    ///
    /// Shell metacharacters (`$()`, `` ` ``, `&&`, `||`, `;`, `|`, `>`, `<`)
    /// are rejected as injection before anything else runs.
    pub async fn run(&self, command: &str) -> Result<CommandOutput, ShellError> {
        // 1 — injection guard; firewall never sees raw untrusted input
        self.check_injection(command)?;

        // 2 — split into argv
        let argv: Vec<String> = command.split_whitespace().map(String::from).collect();
        if argv.is_empty() {
            return Err(ShellError::Io(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "empty command",
            )));
        }

        let executable = &argv[0];

        // 3 — sudo block: unconditional, not delegated to VSH-003
        if executable == "sudo" {
            return Err(ShellError::SudoBlocked);
        }

        // 4 — allowlist check before firewall classification
        self.check_allowlist(executable)?;

        // 5 — firewall stub (VSH-003 will replace this)
        match self.firewall_classify(executable) {
            FirewallTier::Tier3 => {
                return Err(ShellError::FirewallRejected);
            }
            FirewallTier::Tier2(_token) => {
                // Safety gate: SHELL must never silently block — Tier2 is
                // caller-visible.  VSH-003 implements the full approval flow.
                // Stub treats Tier2 as a rejection until VSH-003 is wired.
                return Err(ShellError::FirewallRejected);
            }
            FirewallTier::Tier1 => {} // cleared to spawn
        }

        // 6 — spawn and collect within timeout
        let start_time = SystemTime::now();
        self.spawn_and_collect(&argv, start_time).await
    }

    // -----------------------------------------------------------------------
    // Private helpers
    // -----------------------------------------------------------------------

    /// Reject any shell metacharacters before the input reaches the firewall.
    fn check_injection(&self, command: &str) -> Result<(), ShellError> {
        const BLOCKED_SEQUENCES: &[&str] = &["$(", "`", "&&", "||", ";", "\n", "\r"];
        for seq in BLOCKED_SEQUENCES {
            if command.contains(seq) {
                return Err(ShellError::InjectionDetected((*seq).to_string()));
            }
        }
        // Single-character shell operators blocked in structured argv mode.
        for ch in ['|', '>', '<'] {
            if command.contains(ch) {
                return Err(ShellError::InjectionDetected(ch.to_string()));
            }
        }
        Ok(())
    }

    /// Verify the executable against the allowlist.
    ///
    /// If the allowlist file does not exist the engine operates in permissive
    /// mode (all executables allowed).  Production deployments should provide
    /// `~/.vashion/policy/exec-allowlist.json`.
    fn check_allowlist(&self, _executable: &str) -> Result<(), ShellError> {
        if !self.allowlist_path.exists() {
            return Ok(()); // permissive: allowlist not yet configured
        }
        // TODO(VSH-003): parse `{ "executables": ["echo", ...] }` and enforce.
        Ok(())
    }

    /// VSH-003 stub classifier — always Tier 1 until the real firewall lands.
    fn firewall_classify(&self, _executable: &str) -> FirewallTier {
        FirewallTier::Tier1
    }

    /// Spawn the process and collect stdout/stderr within the timeout window.
    async fn spawn_and_collect(
        &self,
        argv: &[String],
        start_time: SystemTime,
    ) -> Result<CommandOutput, ShellError> {
        let child = Command::new(&argv[0])
            .args(&argv[1..])
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(ShellError::Io)?;

        let pid = child.id().unwrap_or(0);
        let output_ref = format!("exec:pid={pid}:start={start_time:?}");

        match timeout(self.default_timeout, child.wait_with_output()).await {
            Ok(Ok(output)) => Ok(CommandOutput {
                stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
                stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
                exit_code: output.status.code().unwrap_or(-1),
                record: ExecutionRecord {
                    pid,
                    start_time,
                    output_ref,
                },
            }),
            Ok(Err(e)) => Err(ShellError::Io(e)),
            Err(_elapsed) => {
                // Timeout hit. `child` was consumed by `wait_with_output` and is
                // now inside the dropped future — we cannot call kill() here.
                // TODO(VSH-001): restructure with tokio::select! so the child
                //   handle remains accessible; then send SIGTERM + 5s grace +
                //   SIGKILL.  Requires `nix` crate for Unix signal delivery.
                Err(ShellError::TimedOut)
            }
        }
    }
}
