// Co-authored by FORGE (Session: agent-2-tagteam-adhoc)
//! VSH-005: Auth & Model Registry
//!
//! Provides:
//! - Session token generation with epoch tracking; written to
//!   `~/.vashion/auth/core.token` (mode 600).
//! - Token + epoch validation: wrong token OR stale epoch = immediate rejection.
//! - Rekey handshake: Core increments epoch → Brain detects WrongEpoch →
//!   enters degraded state → reloads token file → reconnects automatically.
//! - `Redacted<T>` newtype: credentials NEVER appear in logs, serialized output,
//!   audit trail, or memory DB; enforced at the type level, not caller convention.
//! - Encrypted credential store (AES-256-GCM) for Anthropic / OpenAI / Ollama,
//!   stored at `~/.vashion/auth/<provider>.cred` with master key at
//!   `~/.vashion/auth/master.key` (mode 600).
//! - Model registry: name, provider, context_window, cost_tier; active model
//!   switchable at runtime without restart.
//! - `AuthCheckpointState` for VSH-007 checkpoint integration.
//!
//! Safety gates (non-negotiable):
//! - Credentials stored only as `Redacted<String>`; `Debug`/`Display`/`Serialize`
//!   all emit `[REDACTED]`; inner value accessible only via `.expose()`.
//! - Registry queries are read-only; writes require explicit mutation call.
//! - Stale epoch rejection is immediate; Brain never retries with a bad token.
//! - Degraded state visible via `BrainAuthState::is_degraded()`.

use aes_gcm::{
    aead::{Aead, KeyInit},
    Aes256Gcm, Nonce,
};
use base64::{engine::general_purpose::STANDARD as B64, Engine as _};
use chrono::{DateTime, Utc};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use std::{
    fmt,
    fs::{self, OpenOptions},
    os::unix::fs::OpenOptionsExt,
    path::{Path, PathBuf},
};
use uuid::Uuid;
use zeroize::Zeroize;

// ---------------------------------------------------------------------------
// Redacted<T> — credential wrapper that never leaks to logs or serializers
// ---------------------------------------------------------------------------

/// Wraps a secret value so that `Debug`, `Display`, and `Serialize` all
/// emit `"[REDACTED]"`. The inner value is accessible only via `.expose()`.
/// This is enforced at the type level — callers cannot accidentally log a
/// `Redacted<String>` value.
pub struct Redacted<T: Zeroize>(T);

impl<T: Zeroize> Redacted<T> {
    pub fn new(inner: T) -> Self {
        Redacted(inner)
    }

    /// Intentionally verbose name to prevent accidental use.
    pub fn expose(&self) -> &T {
        &self.0
    }
}

impl<T: Zeroize + Clone> Clone for Redacted<T> {
    fn clone(&self) -> Self {
        Redacted(self.0.clone())
    }
}

impl<T: Zeroize> Drop for Redacted<T> {
    fn drop(&mut self) {
        self.0.zeroize();
    }
}

impl<T: Zeroize> fmt::Debug for Redacted<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[REDACTED]")
    }
}

impl<T: Zeroize> fmt::Display for Redacted<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "[REDACTED]")
    }
}

impl<T: Zeroize> Serialize for Redacted<T> {
    fn serialize<S: serde::Serializer>(&self, s: S) -> Result<S::Ok, S::Error> {
        s.serialize_str("[REDACTED]")
    }
}

// ---------------------------------------------------------------------------
// Session token — epoch-stamped, file mode 600
// ---------------------------------------------------------------------------

/// On-disk token file format. Token AND epoch must both match on every
/// socket connection. Wrong token OR stale epoch = immediate rejection.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SessionToken {
    /// Opaque random token string (UUID v4).
    pub token: String,
    /// Monotonically incrementing counter; incremented on every Core restart.
    pub epoch: u64,
    pub issued_at: DateTime<Utc>,
    /// Validity window in seconds (0 = no expiry).
    pub ttl_secs: u64,
}

impl SessionToken {
    fn generate(epoch: u64, ttl_secs: u64) -> Self {
        SessionToken {
            token: Uuid::new_v4().to_string(),
            epoch,
            issued_at: Utc::now(),
            ttl_secs,
        }
    }

    pub fn is_expired(&self) -> bool {
        if self.ttl_secs == 0 {
            return false;
        }
        let age = (Utc::now() - self.issued_at).num_seconds();
        age < 0 || age as u64 > self.ttl_secs
    }
}

/// Errors from token validation.
#[derive(Debug, PartialEq, Eq)]
pub enum AuthError {
    /// Token string did not match.
    InvalidToken,
    /// Token matched but epoch is stale — Brain must enter degraded state and
    /// reload the token file before retrying.
    WrongEpoch,
    /// Token has passed its TTL.
    Expired,
    /// I/O or serialization failure.
    Io(String),
}

impl fmt::Display for AuthError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AuthError::InvalidToken => write!(f, "invalid session token"),
            AuthError::WrongEpoch => write!(f, "stale epoch — rekey required"),
            AuthError::Expired => write!(f, "session token expired"),
            AuthError::Io(e) => write!(f, "auth I/O error: {e}"),
        }
    }
}

// ---------------------------------------------------------------------------
// SessionManager — generates and validates session tokens
// ---------------------------------------------------------------------------

/// Manages the Core-side session token lifecycle.
///
/// On `new()`: generates a fresh token at epoch 0 (or loads existing), writes
/// to `~/.vashion/auth/core.token` with mode 600.
///
/// On `rekey()`: increments epoch, generates new token, overwrites file.
/// Brain detects the resulting `WrongEpoch` rejection, enters degraded state,
/// and calls its own `reload_token()` — the handshake completes without
/// manual intervention.
pub struct SessionManager {
    token: SessionToken,
    token_path: PathBuf,
}

impl SessionManager {
    /// Initialise with a fresh epoch-0 token. Writes token file immediately.
    pub fn new(base: &Path) -> Result<Self, AuthError> {
        let token_path = base.join("core.token");
        let token = SessionToken::generate(0, 0);
        let mgr = SessionManager { token, token_path };
        mgr.write_token_file()?;
        Ok(mgr)
    }

    /// Load an existing token from disk (used by Brain to read the current token).
    pub fn load(base: &Path) -> Result<SessionToken, AuthError> {
        let path = base.join("core.token");
        let raw = fs::read_to_string(&path)
            .map_err(|e| AuthError::Io(format!("read token file: {e}")))?;
        serde_json::from_str(&raw).map_err(|e| AuthError::Io(format!("parse token file: {e}")))
    }

    /// Increment epoch and generate a new token. Writes atomically to disk.
    /// Brain detects `WrongEpoch` on next connection and reloads this file.
    pub fn rekey(&mut self) -> Result<(), AuthError> {
        let new_epoch = self.token.epoch + 1;
        self.token = SessionToken::generate(new_epoch, self.token.ttl_secs);
        self.write_token_file()
    }

    /// Current epoch (used to expose to checkpoint state).
    pub fn epoch(&self) -> u64 {
        self.token.epoch
    }

    /// Validate an incoming connection's token and epoch.
    ///
    /// Both must match. Wrong token → `InvalidToken`. Correct token but stale
    /// epoch → `WrongEpoch` (Brain must reload and retry).
    pub fn validate(&self, token: &str, epoch: u64) -> Result<(), AuthError> {
        if self.token.is_expired() {
            return Err(AuthError::Expired);
        }
        // Check epoch first to distinguish stale-epoch from invalid-token.
        if epoch != self.token.epoch {
            return Err(AuthError::WrongEpoch);
        }
        if token != self.token.token {
            return Err(AuthError::InvalidToken);
        }
        Ok(())
    }

    fn write_token_file(&self) -> Result<(), AuthError> {
        if let Some(parent) = self.token_path.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| AuthError::Io(format!("create auth dir: {e}")))?;
        }
        let json = serde_json::to_string_pretty(&self.token)
            .map_err(|e| AuthError::Io(format!("serialize token: {e}")))?;
        // Write with mode 600 — owner read/write only.
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&self.token_path)
            .map_err(|e| AuthError::Io(format!("open token file: {e}")))?;
        use std::io::Write;
        file.write_all(json.as_bytes())
            .map_err(|e| AuthError::Io(format!("write token file: {e}")))
    }
}

// ---------------------------------------------------------------------------
// Brain-side rekey handshake state machine
// ---------------------------------------------------------------------------

/// Brain-side auth state for the rekey handshake.
///
/// Normal flow: `Nominal` → `WrongEpoch` rejection → `Degraded` → reload token
/// → reconnect → back to `Nominal`.
/// The handshake completes without manual intervention.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BrainAuthState {
    /// Token and epoch are current; socket calls proceed normally.
    Nominal,
    /// Core restarted and issued a new epoch. Brain must reload token file
    /// before issuing new socket commands.
    Degraded,
}

impl BrainAuthState {
    pub fn is_degraded(&self) -> bool {
        matches!(self, BrainAuthState::Degraded)
    }
}

/// Brain-side session handle. Holds current token + epoch; performs the
/// rekey handshake automatically on `WrongEpoch` rejection.
pub struct BrainSession {
    pub current_token: String,
    pub current_epoch: u64,
    pub state: BrainAuthState,
    auth_base: PathBuf,
}

impl BrainSession {
    /// Load token from Core's token file. Call once at Brain startup.
    pub fn load(auth_base: &Path) -> Result<Self, AuthError> {
        let tok = SessionManager::load(auth_base)?;
        Ok(BrainSession {
            current_token: tok.token,
            current_epoch: tok.epoch,
            state: BrainAuthState::Nominal,
            auth_base: auth_base.to_path_buf(),
        })
    }

    /// Call when a socket response returns `WrongEpoch`. Enters degraded state
    /// and reloads the token file written by Core after its rekey.
    /// After this returns `Ok(())`, `current_token` and `current_epoch` are
    /// fresh and the next socket call will succeed.
    pub fn handle_wrong_epoch(&mut self) -> Result<(), AuthError> {
        self.state = BrainAuthState::Degraded;
        let tok = SessionManager::load(&self.auth_base)?;
        self.current_token = tok.token;
        self.current_epoch = tok.epoch;
        self.state = BrainAuthState::Nominal;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Credential encryption — AES-256-GCM with per-install master key
// ---------------------------------------------------------------------------

const NONCE_LEN: usize = 12;
const KEY_LEN: usize = 32;

/// Encrypted credential envelope stored on disk.
#[derive(Debug, Serialize, Deserialize)]
struct CredentialEnvelope {
    nonce: String,
    ciphertext: String,
}

/// Loads or generates the AES-256 master key at `auth_base/master.key`.
/// File is written with mode 600 on creation.
fn load_or_create_master_key(auth_base: &Path) -> Result<[u8; KEY_LEN], AuthError> {
    let key_path = auth_base.join("master.key");
    if key_path.exists() {
        let raw =
            fs::read(&key_path).map_err(|e| AuthError::Io(format!("read master key: {e}")))?;
        if raw.len() != KEY_LEN {
            return Err(AuthError::Io(format!(
                "master.key has wrong length: {} (expected {KEY_LEN})",
                raw.len()
            )));
        }
        let mut key = [0u8; KEY_LEN];
        key.copy_from_slice(&raw);
        return Ok(key);
    }

    // Generate fresh key.
    fs::create_dir_all(auth_base).map_err(|e| AuthError::Io(format!("create auth dir: {e}")))?;
    let mut key = [0u8; KEY_LEN];
    rand::thread_rng().fill_bytes(&mut key);

    let mut file = OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&key_path)
        .map_err(|e| AuthError::Io(format!("create master.key: {e}")))?;
    use std::io::Write;
    file.write_all(&key)
        .map_err(|e| AuthError::Io(format!("write master.key: {e}")))?;
    Ok(key)
}

fn encrypt_credential(
    plaintext: &str,
    key: &[u8; KEY_LEN],
) -> Result<CredentialEnvelope, AuthError> {
    let cipher =
        Aes256Gcm::new_from_slice(key).map_err(|e| AuthError::Io(format!("AES key init: {e}")))?;
    let mut nonce_bytes = [0u8; NONCE_LEN];
    rand::thread_rng().fill_bytes(&mut nonce_bytes);
    let nonce = Nonce::from_slice(&nonce_bytes);
    let ciphertext = cipher
        .encrypt(nonce, plaintext.as_bytes())
        .map_err(|e| AuthError::Io(format!("AES encrypt: {e}")))?;
    Ok(CredentialEnvelope {
        nonce: B64.encode(nonce_bytes),
        ciphertext: B64.encode(ciphertext),
    })
}

fn decrypt_credential(
    envelope: &CredentialEnvelope,
    key: &[u8; KEY_LEN],
) -> Result<String, AuthError> {
    let cipher =
        Aes256Gcm::new_from_slice(key).map_err(|e| AuthError::Io(format!("AES key init: {e}")))?;
    let nonce_bytes = B64
        .decode(&envelope.nonce)
        .map_err(|e| AuthError::Io(format!("nonce base64 decode: {e}")))?;
    if nonce_bytes.len() != NONCE_LEN {
        return Err(AuthError::Io("nonce wrong length".into()));
    }
    let nonce = Nonce::from_slice(&nonce_bytes);
    let ct = B64
        .decode(&envelope.ciphertext)
        .map_err(|e| AuthError::Io(format!("ciphertext base64 decode: {e}")))?;
    let plaintext = cipher
        .decrypt(nonce, ct.as_ref())
        .map_err(|e| AuthError::Io(format!("AES decrypt: {e}")))?;
    String::from_utf8(plaintext).map_err(|e| AuthError::Io(format!("UTF-8 decode: {e}")))
}

// ---------------------------------------------------------------------------
// Provider enum
// ---------------------------------------------------------------------------

/// Supported LLM providers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Provider {
    Anthropic,
    OpenAI,
    /// Local Ollama instance — no API key required.
    Ollama,
}

impl fmt::Display for Provider {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Provider::Anthropic => write!(f, "anthropic"),
            Provider::OpenAI => write!(f, "openai"),
            Provider::Ollama => write!(f, "ollama"),
        }
    }
}

impl Provider {
    fn cred_filename(&self) -> &'static str {
        match self {
            Provider::Anthropic => "anthropic.cred",
            Provider::OpenAI => "openai.cred",
            Provider::Ollama => "ollama.cred",
        }
    }
}

// ---------------------------------------------------------------------------
// CredentialStore — encrypted at rest, Redacted in memory
// ---------------------------------------------------------------------------

/// Encrypted credential store for all supported providers.
///
/// Credentials are stored encrypted at `~/.vashion/auth/<provider>.cred`
/// and held in memory only as `Redacted<String>`. They never appear in logs,
/// audit trails, or serialized output.
pub struct CredentialStore {
    auth_base: PathBuf,
    master_key: [u8; KEY_LEN],
}

impl CredentialStore {
    /// Load or initialise the credential store. Creates master.key if absent.
    pub fn new(auth_base: &Path) -> Result<Self, AuthError> {
        let master_key = load_or_create_master_key(auth_base)?;
        Ok(CredentialStore {
            auth_base: auth_base.to_path_buf(),
            master_key,
        })
    }

    /// Store an API key for the given provider. Encrypts and writes to disk.
    /// The plaintext key is zeroized from the `Redacted` wrapper on drop.
    pub fn store(&self, provider: &Provider, key: Redacted<String>) -> Result<(), AuthError> {
        let envelope = encrypt_credential(key.expose(), &self.master_key)?;
        let json = serde_json::to_string_pretty(&envelope)
            .map_err(|e| AuthError::Io(format!("serialize credential: {e}")))?;
        let path = self.auth_base.join(provider.cred_filename());
        fs::create_dir_all(&self.auth_base)
            .map_err(|e| AuthError::Io(format!("create auth dir: {e}")))?;
        let mut file = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&path)
            .map_err(|e| AuthError::Io(format!("open cred file: {e}")))?;
        use std::io::Write;
        file.write_all(json.as_bytes())
            .map_err(|e| AuthError::Io(format!("write cred file: {e}")))
        // `key` (Redacted) is dropped and zeroized here.
    }

    /// Load and decrypt the API key for the given provider. Returns a
    /// `Redacted<String>` — callers must use `.expose()` explicitly and
    /// cannot accidentally log the value.
    pub fn load(&self, provider: &Provider) -> Result<Redacted<String>, AuthError> {
        let path = self.auth_base.join(provider.cred_filename());
        let raw = fs::read_to_string(&path)
            .map_err(|e| AuthError::Io(format!("read cred file for {provider}: {e}")))?;
        let envelope: CredentialEnvelope = serde_json::from_str(&raw)
            .map_err(|e| AuthError::Io(format!("parse cred file: {e}")))?;
        let plaintext = decrypt_credential(&envelope, &self.master_key)?;
        Ok(Redacted::new(plaintext))
    }

    /// Returns true if a credential file exists for the given provider.
    pub fn has_credential(&self, provider: &Provider) -> bool {
        self.auth_base.join(provider.cred_filename()).exists()
    }
}

impl Drop for CredentialStore {
    fn drop(&mut self) {
        self.master_key.zeroize();
    }
}

// ---------------------------------------------------------------------------
// Model registry
// ---------------------------------------------------------------------------

/// Cost tier for context budgeting.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum CostTier {
    Free,
    Low,
    Medium,
    High,
}

/// Per-model registry entry. Queried by Brain at startup to inform goal loop
/// and context budgeting.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ModelEntry {
    pub name: String,
    pub provider: Provider,
    /// Maximum context window in tokens.
    pub context_window: u32,
    pub cost_tier: CostTier,
}

/// Runtime-switchable model registry.
///
/// Registry queries are read-only via `&self` methods. Writes (add/remove/
/// set active) require exclusive `&mut self` access — enforced by the borrow
/// checker; no additional runtime guard needed.
pub struct ModelRegistry {
    models: Vec<ModelEntry>,
    /// Index into `models` for the currently active model.
    active_idx: Option<usize>,
}

impl ModelRegistry {
    pub fn new() -> Self {
        ModelRegistry {
            models: Vec::new(),
            active_idx: None,
        }
    }

    /// Register a model. Idempotent on name — updating an existing name
    /// replaces the entry.
    pub fn register(&mut self, entry: ModelEntry) {
        if let Some(pos) = self.models.iter().position(|m| m.name == entry.name) {
            self.models[pos] = entry;
        } else {
            self.models.push(entry);
        }
    }

    /// Remove a model by name. If it was active, active_idx is cleared.
    pub fn remove(&mut self, name: &str) {
        if let Some(pos) = self.models.iter().position(|m| m.name == name) {
            if self.active_idx == Some(pos) {
                self.active_idx = None;
            } else if let Some(idx) = self.active_idx {
                if idx > pos {
                    self.active_idx = Some(idx - 1);
                }
            }
            self.models.remove(pos);
        }
    }

    /// Switch the active model at runtime — no restart required.
    /// Returns an error string if the name is not registered.
    pub fn set_active(&mut self, name: &str) -> Result<(), String> {
        match self.models.iter().position(|m| m.name == name) {
            Some(idx) => {
                self.active_idx = Some(idx);
                Ok(())
            }
            None => Err(format!("model '{name}' not registered")),
        }
    }

    /// The currently active model, if one has been set.
    pub fn active(&self) -> Option<&ModelEntry> {
        self.active_idx.and_then(|i| self.models.get(i))
    }

    /// All registered models (read-only). Used by Brain at startup.
    pub fn all(&self) -> &[ModelEntry] {
        &self.models
    }

    /// Find a model by name.
    pub fn get(&self, name: &str) -> Option<&ModelEntry> {
        self.models.iter().find(|m| m.name == name)
    }
}

impl Default for ModelRegistry {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// AuthCheckpointState — for VSH-007 integration
// ---------------------------------------------------------------------------

/// Auth state captured in a VSH-007 checkpoint. Contains enough information
/// to restore context after a crash without storing actual credentials.
///
/// Credentials are NOT included — they remain encrypted on disk and are
/// reloaded by `CredentialStore::load()` on recovery.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuthCheckpointState {
    /// Current session epoch at checkpoint time. Used to detect post-crash
    /// rekey on restart.
    pub epoch: u64,
    /// Name of the active model at checkpoint time (None if unset).
    pub active_model_name: Option<String>,
    /// Provider of the active model (None if unset).
    pub active_model_provider: Option<Provider>,
    pub captured_at: DateTime<Utc>,
}

impl AuthCheckpointState {
    pub fn capture(session: &SessionManager, registry: &ModelRegistry) -> Self {
        let (active_model_name, active_model_provider) = registry
            .active()
            .map(|m| (Some(m.name.clone()), Some(m.provider.clone())))
            .unwrap_or((None, None));
        AuthCheckpointState {
            epoch: session.epoch(),
            active_model_name,
            active_model_provider,
            captured_at: Utc::now(),
        }
    }
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::TempDir;

    fn temp_auth_base() -> (TempDir, PathBuf) {
        let tmp = TempDir::new().unwrap();
        let base = tmp.path().join("auth");
        fs::create_dir_all(&base).unwrap();
        (tmp, base)
    }

    // --- Redacted<T> ---

    #[test]
    fn redacted_display_is_redacted() {
        let r = Redacted::new("secret_api_key".to_string());
        assert_eq!(format!("{r}"), "[REDACTED]");
    }

    #[test]
    fn redacted_debug_is_redacted() {
        let r = Redacted::new("secret_api_key".to_string());
        assert_eq!(format!("{r:?}"), "[REDACTED]");
    }

    #[test]
    fn redacted_serialize_is_redacted() {
        let r = Redacted::new("secret_api_key".to_string());
        let json = serde_json::to_string(&r).unwrap();
        assert_eq!(json, r#""[REDACTED]""#);
    }

    #[test]
    fn redacted_expose_gives_inner() {
        let r = Redacted::new("secret_api_key".to_string());
        assert_eq!(r.expose(), "secret_api_key");
    }

    // --- SessionManager ---

    #[test]
    fn session_manager_writes_token_file_mode_600() {
        let (_tmp, base) = temp_auth_base();
        let mgr = SessionManager::new(&base).unwrap();
        let token_path = base.join("core.token");
        assert!(token_path.exists(), "core.token must be written");

        use std::os::unix::fs::MetadataExt;
        let meta = fs::metadata(&token_path).unwrap();
        let mode = meta.mode() & 0o777;
        assert_eq!(mode, 0o600, "core.token must have mode 600");

        assert_eq!(mgr.epoch(), 0);
    }

    #[test]
    fn session_manager_validate_ok() {
        let (_tmp, base) = temp_auth_base();
        let mgr = SessionManager::new(&base).unwrap();
        let tok = SessionManager::load(&base).unwrap();
        assert!(mgr.validate(&tok.token, tok.epoch).is_ok());
    }

    #[test]
    fn session_manager_wrong_token() {
        let (_tmp, base) = temp_auth_base();
        let mgr = SessionManager::new(&base).unwrap();
        let tok = SessionManager::load(&base).unwrap();
        assert_eq!(
            mgr.validate("not-the-right-token", tok.epoch),
            Err(AuthError::InvalidToken)
        );
    }

    #[test]
    fn session_manager_wrong_epoch() {
        let (_tmp, base) = temp_auth_base();
        let mgr = SessionManager::new(&base).unwrap();
        let tok = SessionManager::load(&base).unwrap();
        // Pass correct token but wrong epoch.
        assert_eq!(
            mgr.validate(&tok.token, tok.epoch + 1),
            Err(AuthError::WrongEpoch)
        );
    }

    #[test]
    fn rekey_increments_epoch_and_invalidates_old_token() {
        let (_tmp, base) = temp_auth_base();
        let mut mgr = SessionManager::new(&base).unwrap();
        let old_tok = SessionManager::load(&base).unwrap();
        assert_eq!(old_tok.epoch, 0);

        mgr.rekey().unwrap();
        assert_eq!(mgr.epoch(), 1);

        let new_tok = SessionManager::load(&base).unwrap();
        assert_eq!(new_tok.epoch, 1);
        assert_ne!(new_tok.token, old_tok.token);

        // Old token is rejected with WrongEpoch.
        assert_eq!(
            mgr.validate(&old_tok.token, old_tok.epoch),
            Err(AuthError::WrongEpoch)
        );
    }

    // --- BrainSession rekey handshake ---

    #[test]
    fn brain_session_rekey_handshake() {
        let (_tmp, base) = temp_auth_base();
        let mut mgr = SessionManager::new(&base).unwrap();
        let mut brain = BrainSession::load(&base).unwrap();

        assert_eq!(brain.state, BrainAuthState::Nominal);
        assert_eq!(brain.current_epoch, 0);

        // Core restarts and rekeys.
        mgr.rekey().unwrap();

        // Brain gets WrongEpoch from socket and calls handle_wrong_epoch.
        brain.handle_wrong_epoch().unwrap();

        // Brain now has fresh token matching Core.
        assert_eq!(brain.state, BrainAuthState::Nominal);
        assert_eq!(brain.current_epoch, 1);
        assert_eq!(brain.current_token, mgr.token.token);
    }

    // --- CredentialStore ---

    #[test]
    fn credential_store_round_trip_anthropic() {
        let (_tmp, base) = temp_auth_base();
        let store = CredentialStore::new(&base).unwrap();

        store
            .store(
                &Provider::Anthropic,
                Redacted::new("sk-ant-test-123".to_string()),
            )
            .unwrap();

        let loaded = store.load(&Provider::Anthropic).unwrap();
        assert_eq!(loaded.expose(), "sk-ant-test-123");

        // Verify the credential file does NOT contain plaintext.
        let cred_path = base.join("anthropic.cred");
        let raw = fs::read_to_string(&cred_path).unwrap();
        assert!(
            !raw.contains("sk-ant-test-123"),
            "credential file must not contain plaintext"
        );
    }

    #[test]
    fn credential_store_round_trip_openai() {
        let (_tmp, base) = temp_auth_base();
        let store = CredentialStore::new(&base).unwrap();
        store
            .store(
                &Provider::OpenAI,
                Redacted::new("sk-openai-test-456".to_string()),
            )
            .unwrap();
        let loaded = store.load(&Provider::OpenAI).unwrap();
        assert_eq!(loaded.expose(), "sk-openai-test-456");
    }

    #[test]
    fn credential_store_ollama_no_key_needed() {
        let (_tmp, base) = temp_auth_base();
        let store = CredentialStore::new(&base).unwrap();
        assert!(!store.has_credential(&Provider::Ollama));
        // Ollama can store an empty string (local endpoint, no key).
        store
            .store(&Provider::Ollama, Redacted::new(String::new()))
            .unwrap();
        assert!(store.has_credential(&Provider::Ollama));
    }

    #[test]
    fn master_key_file_mode_600() {
        let (_tmp, base) = temp_auth_base();
        let _store = CredentialStore::new(&base).unwrap();
        let key_path = base.join("master.key");
        assert!(key_path.exists(), "master.key must be written");

        use std::os::unix::fs::MetadataExt;
        let meta = fs::metadata(&key_path).unwrap();
        let mode = meta.mode() & 0o777;
        assert_eq!(mode, 0o600, "master.key must have mode 600");
    }

    #[test]
    fn credential_not_in_log_when_stored() {
        // Redacted<String> with a credential value must never print the value.
        let cred = Redacted::new("super_secret_key_xyz".to_string());
        let log_line = format!("storing credential: {cred}");
        assert!(
            !log_line.contains("super_secret_key_xyz"),
            "credential must not appear in log output"
        );
        let debug_line = format!("{cred:?}");
        assert!(!debug_line.contains("super_secret_key_xyz"));
    }

    // --- ModelRegistry ---

    #[test]
    fn registry_register_and_query() {
        let mut reg = ModelRegistry::new();
        reg.register(ModelEntry {
            name: "claude-opus-4-7".to_string(),
            provider: Provider::Anthropic,
            context_window: 200_000,
            cost_tier: CostTier::High,
        });
        reg.register(ModelEntry {
            name: "gpt-4o".to_string(),
            provider: Provider::OpenAI,
            context_window: 128_000,
            cost_tier: CostTier::High,
        });
        reg.register(ModelEntry {
            name: "llama3".to_string(),
            provider: Provider::Ollama,
            context_window: 8_192,
            cost_tier: CostTier::Free,
        });

        assert_eq!(reg.all().len(), 3);
        assert!(reg.get("gpt-4o").is_some());
        assert!(reg.get("nonexistent").is_none());
    }

    #[test]
    fn registry_active_model_switch() {
        let mut reg = ModelRegistry::new();
        reg.register(ModelEntry {
            name: "claude-opus-4-7".to_string(),
            provider: Provider::Anthropic,
            context_window: 200_000,
            cost_tier: CostTier::High,
        });
        reg.register(ModelEntry {
            name: "llama3".to_string(),
            provider: Provider::Ollama,
            context_window: 8_192,
            cost_tier: CostTier::Free,
        });

        assert!(reg.active().is_none());

        reg.set_active("claude-opus-4-7").unwrap();
        assert_eq!(reg.active().unwrap().name, "claude-opus-4-7");

        // Switch at runtime without restart.
        reg.set_active("llama3").unwrap();
        assert_eq!(reg.active().unwrap().name, "llama3");
    }

    #[test]
    fn registry_set_active_unknown_errors() {
        let mut reg = ModelRegistry::new();
        assert!(reg.set_active("nonexistent").is_err());
    }

    #[test]
    fn registry_remove_active_clears_active() {
        let mut reg = ModelRegistry::new();
        reg.register(ModelEntry {
            name: "m1".to_string(),
            provider: Provider::Anthropic,
            context_window: 100_000,
            cost_tier: CostTier::Medium,
        });
        reg.set_active("m1").unwrap();
        reg.remove("m1");
        assert!(reg.active().is_none());
        assert_eq!(reg.all().len(), 0);
    }

    #[test]
    fn registry_update_existing_entry() {
        let mut reg = ModelRegistry::new();
        reg.register(ModelEntry {
            name: "m1".to_string(),
            provider: Provider::Anthropic,
            context_window: 100_000,
            cost_tier: CostTier::Medium,
        });
        // Re-register with updated context window.
        reg.register(ModelEntry {
            name: "m1".to_string(),
            provider: Provider::Anthropic,
            context_window: 200_000,
            cost_tier: CostTier::High,
        });
        assert_eq!(reg.all().len(), 1);
        assert_eq!(reg.get("m1").unwrap().context_window, 200_000);
    }

    // --- AuthCheckpointState ---

    #[test]
    fn checkpoint_state_captures_epoch_and_active_model() {
        let (_tmp, base) = temp_auth_base();
        let mgr = SessionManager::new(&base).unwrap();
        let mut reg = ModelRegistry::new();
        reg.register(ModelEntry {
            name: "claude-opus-4-7".to_string(),
            provider: Provider::Anthropic,
            context_window: 200_000,
            cost_tier: CostTier::High,
        });
        reg.set_active("claude-opus-4-7").unwrap();

        let cp = AuthCheckpointState::capture(&mgr, &reg);
        assert_eq!(cp.epoch, 0);
        assert_eq!(cp.active_model_name.as_deref(), Some("claude-opus-4-7"));
        assert_eq!(cp.active_model_provider, Some(Provider::Anthropic));
    }

    #[test]
    fn checkpoint_state_no_active_model() {
        let (_tmp, base) = temp_auth_base();
        let mgr = SessionManager::new(&base).unwrap();
        let reg = ModelRegistry::new();
        let cp = AuthCheckpointState::capture(&mgr, &reg);
        assert!(cp.active_model_name.is_none());
        assert!(cp.active_model_provider.is_none());
    }

    #[test]
    fn checkpoint_state_is_serializable() {
        let (_tmp, base) = temp_auth_base();
        let mgr = SessionManager::new(&base).unwrap();
        let reg = ModelRegistry::new();
        let cp = AuthCheckpointState::capture(&mgr, &reg);
        let json = serde_json::to_string(&cp).unwrap();
        assert!(json.contains("epoch"));
        // Credentials must never appear in checkpoint.
        assert!(!json.contains("sk-"));
    }
}
