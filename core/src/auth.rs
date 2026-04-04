// Co-authored by FORGE (Session: forge-vsh-005)
//! VSH-005: Auth & Model Registry — Session Token + Encrypted Credential Store
//!
//! # Token lifecycle
//!   1. Core generates a random token + epoch on startup, writes `~/.vashion/auth/core.token`
//!      (mode 600) atomically (temp-write then rename).
//!   2. Brain reads the token file, uses `(token, epoch)` on every Unix socket connection.
//!   3. Core validates BOTH token AND epoch on every connection. Wrong token OR stale epoch
//!      causes immediate rejection — no retry path.
//!   4. On Core restart: epoch is incremented. Brain detects mismatch → degraded state →
//!      reloads token file → reconnects. No manual intervention required.
//!
//! # Credential store
//!   Credentials are encrypted with AES-256-GCM. A 256-bit master key is generated once and
//!   stored at `~/.vashion/auth/master.key` (mode 600, binary). Encrypted credential blobs
//!   live at `~/.vashion/auth/credentials/<provider>.enc` (nonce prepended).
//!
//! # Safety invariants
//!   - `Redacted<T>` wraps any credential value and formats as `[REDACTED]` in Debug/Display.
//!   - The write layer enforces redaction — callers do not need to remember to redact.
//!   - Credentials never appear in log macros (tracing instruments are always `Redacted`-wrapped).

use aes_gcm::{
    aead::{Aead, AeadCore, KeyInit, OsRng},
    Aes256Gcm, Key, Nonce,
};
use rand::RngCore;
use serde::{Deserialize, Serialize};
use std::{
    fmt,
    fs::{self, File, OpenOptions},
    io::{Read, Write},
    os::unix::fs::PermissionsExt,
    path::{Path, PathBuf},
    time::{Duration, SystemTime, UNIX_EPOCH},
};
use thiserror::Error;

// ---------------------------------------------------------------------------
// Redacted wrapper — credentials never appear in logs
// ---------------------------------------------------------------------------

/// Wraps any value and renders as `[REDACTED]` in all fmt implementations.
/// Use this for any field that must never appear in logs, audit trails, or
/// the memory DB.
#[derive(Clone, Serialize, Deserialize)]
#[serde(transparent)]
pub struct Redacted<T>(pub T);

impl<T> fmt::Debug for Redacted<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("[REDACTED]")
    }
}

impl<T> fmt::Display for Redacted<T> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str("[REDACTED]")
    }
}

impl<T> Redacted<T> {
    pub fn new(val: T) -> Self {
        Self(val)
    }

    /// Explicitly unwrap the inner value. Only call at the use-site where the
    /// value is actually needed (e.g. when injecting into an HTTP header).
    pub fn expose(&self) -> &T {
        &self.0
    }
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

#[derive(Debug, Error)]
pub enum AuthError {
    #[error("I/O error: {0}")]
    Io(#[from] std::io::Error),

    #[error("JSON error: {0}")]
    Json(#[from] serde_json::Error),

    #[error("token epoch mismatch: expected {expected}, got {got}")]
    EpochMismatch { expected: u64, got: u64 },

    #[error("invalid token")]
    InvalidToken,

    #[error("token expired")]
    TokenExpired,

    #[error("encryption error")]
    Encryption,

    #[error("decryption error")]
    Decryption,

    #[error("master key length invalid: expected 32 bytes")]
    MasterKeyLength,
}

// ---------------------------------------------------------------------------
// Session token
// ---------------------------------------------------------------------------

/// On-disk representation at `~/.vashion/auth/core.token`.
#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct SessionToken {
    /// Random hex string (32 bytes → 64 hex chars).
    pub token: String,
    /// Incremented on every Core restart. Brain must match this value.
    pub epoch: u64,
    /// Unix timestamp (seconds) when this token was issued.
    pub issued_at: u64,
    /// Validity period in seconds. 0 means no expiry.
    pub ttl: u64,
}

impl SessionToken {
    /// Generate a fresh token with the given epoch.
    pub fn generate(epoch: u64, ttl: Duration) -> Self {
        let mut raw = [0u8; 32];
        rand::thread_rng().fill_bytes(&mut raw);
        let token = hex::encode(raw);
        let issued_at = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        Self {
            token,
            epoch,
            issued_at,
            ttl: ttl.as_secs(),
        }
    }

    pub fn is_expired(&self) -> bool {
        if self.ttl == 0 {
            return false;
        }
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        now.saturating_sub(self.issued_at) >= self.ttl
    }
}

// ---------------------------------------------------------------------------
// Token manager
// ---------------------------------------------------------------------------

/// Manages the Core session token file at `~/.vashion/auth/core.token`.
pub struct TokenManager {
    auth_dir: PathBuf,
    current: SessionToken,
}

impl TokenManager {
    /// Initialise, generating a new token with `epoch = prior_epoch + 1`.
    /// Pass `None` for `prior_epoch` on a fresh install (starts at epoch 1).
    pub fn init(
        auth_dir: &Path,
        prior_epoch: Option<u64>,
        ttl: Duration,
    ) -> Result<Self, AuthError> {
        fs::create_dir_all(auth_dir)?;
        let epoch = prior_epoch.map(|e| e + 1).unwrap_or(1);
        let token = SessionToken::generate(epoch, ttl);
        let mgr = Self {
            auth_dir: auth_dir.to_path_buf(),
            current: token,
        };
        mgr.persist()?;
        Ok(mgr)
    }

    /// Load from an existing token file without regenerating.
    pub fn load(auth_dir: &Path) -> Result<Self, AuthError> {
        let path = auth_dir.join("core.token");
        let data = fs::read_to_string(&path)?;
        let token: SessionToken = serde_json::from_str(&data)?;
        Ok(Self {
            auth_dir: auth_dir.to_path_buf(),
            current: token,
        })
    }

    /// Atomically write the token file (mode 600).
    fn persist(&self) -> Result<(), AuthError> {
        let path = self.auth_dir.join("core.token");
        let tmp = self.auth_dir.join("core.token.tmp");
        let json = serde_json::to_string_pretty(&self.current)?;

        let mut f = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&tmp)?;
        f.set_permissions(fs::Permissions::from_mode(0o600))?;
        f.write_all(json.as_bytes())?;
        f.sync_all()?;
        drop(f);

        fs::rename(&tmp, &path)?;
        tracing::debug!(path = %path.display(), epoch = self.current.epoch, "session token written");
        Ok(())
    }

    pub fn current(&self) -> &SessionToken {
        &self.current
    }

    /// Validate an incoming `(token, epoch)` pair from a socket connection.
    ///
    /// Returns `Err(AuthError::EpochMismatch)` when the epoch is wrong so that
    /// callers can distinguish epoch mismatch (→ Brain degraded + rekey) from
    /// a plain invalid token (→ immediate rejection).
    pub fn validate(&self, token: &str, epoch: u64) -> Result<(), AuthError> {
        // Epoch check first — mismatch must be distinguishable from bad token.
        if epoch != self.current.epoch {
            return Err(AuthError::EpochMismatch {
                expected: self.current.epoch,
                got: epoch,
            });
        }
        if token != self.current.token {
            return Err(AuthError::InvalidToken);
        }
        if self.current.is_expired() {
            return Err(AuthError::TokenExpired);
        }
        Ok(())
    }

    /// Increment epoch, generate a new token, persist. Called on Core restart.
    pub fn rekey(&mut self, ttl: Duration) -> Result<(), AuthError> {
        self.current = SessionToken::generate(self.current.epoch + 1, ttl);
        self.persist()?;
        tracing::info!(epoch = self.current.epoch, "session token rekeyed");
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Provider enum
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "lowercase")]
pub enum Provider {
    Anthropic,
    OpenAI,
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

// ---------------------------------------------------------------------------
// Encrypted credential store
// ---------------------------------------------------------------------------

/// A provider credential. The `api_key` is wrapped in `Redacted` so it can
/// never accidentally appear in logs or debug output.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Credential {
    pub provider: Provider,
    /// API key. For Ollama this may be empty (no auth required).
    pub api_key: Redacted<String>,
    /// Custom base URL (required for Ollama; optional override for others).
    pub base_url: Option<String>,
}

/// Manages AES-256-GCM encrypted credentials at `~/.vashion/auth/credentials/`.
///
/// Master key lives at `~/.vashion/auth/master.key` (32 raw bytes, mode 600).
/// Each provider gets one file: `<provider>.enc` with a 12-byte GCM nonce
/// prepended to the ciphertext.
pub struct CredentialStore {
    master_key: Vec<u8>,
    cred_dir: PathBuf,
}

impl CredentialStore {
    /// Initialise the store, generating a master key if one does not exist.
    pub fn open(auth_dir: &Path) -> Result<Self, AuthError> {
        let key_path = auth_dir.join("master.key");
        let cred_dir = auth_dir.join("credentials");
        fs::create_dir_all(&cred_dir)?;

        let master_key = if key_path.exists() {
            let mut bytes = Vec::new();
            File::open(&key_path)?.read_to_end(&mut bytes)?;
            if bytes.len() != 32 {
                return Err(AuthError::MasterKeyLength);
            }
            bytes
        } else {
            let mut key = vec![0u8; 32];
            rand::thread_rng().fill_bytes(&mut key);
            let mut f = OpenOptions::new()
                .write(true)
                .create(true)
                .truncate(true)
                .open(&key_path)?;
            f.set_permissions(fs::Permissions::from_mode(0o600))?;
            f.write_all(&key)?;
            f.sync_all()?;
            tracing::debug!(path = %key_path.display(), "master key generated");
            key
        };

        Ok(Self {
            master_key,
            cred_dir,
        })
    }

    fn cipher(&self) -> Aes256Gcm {
        let key = Key::<Aes256Gcm>::from_slice(&self.master_key);
        Aes256Gcm::new(key)
    }

    /// Encrypt and write a credential. The credential's `api_key` is never
    /// written to any log — it is always wrapped in `Redacted`.
    pub fn write(&self, cred: &Credential) -> Result<(), AuthError> {
        let plain = serde_json::to_vec(cred)?;
        let cipher = self.cipher();
        let nonce = Aes256Gcm::generate_nonce(&mut OsRng);
        let mut ciphertext = cipher
            .encrypt(&nonce, plain.as_ref())
            .map_err(|_| AuthError::Encryption)?;

        // Prepend 12-byte nonce.
        let mut blob = nonce.to_vec();
        blob.append(&mut ciphertext);

        let path = self.cred_dir.join(format!("{}.enc", cred.provider));
        let tmp = self.cred_dir.join(format!("{}.enc.tmp", cred.provider));

        let mut f = OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .open(&tmp)?;
        f.set_permissions(fs::Permissions::from_mode(0o600))?;
        f.write_all(&blob)?;
        f.sync_all()?;
        drop(f);

        fs::rename(&tmp, &path)?;
        // Log only provider — never the key.
        tracing::debug!(provider = %cred.provider, "credential written");
        Ok(())
    }

    /// Read and decrypt a credential for the given provider.
    pub fn read(&self, provider: &Provider) -> Result<Credential, AuthError> {
        let path = self.cred_dir.join(format!("{provider}.enc"));
        let mut blob = Vec::new();
        File::open(&path)?.read_to_end(&mut blob)?;

        if blob.len() < 12 {
            return Err(AuthError::Decryption);
        }
        let (nonce_bytes, ciphertext) = blob.split_at(12);
        let nonce = Nonce::from_slice(nonce_bytes);
        let cipher = self.cipher();
        let plain = cipher
            .decrypt(nonce, ciphertext)
            .map_err(|_| AuthError::Decryption)?;
        let cred: Credential = serde_json::from_slice(&plain)?;
        // Log only provider — never the key.
        tracing::debug!(provider = %provider, "credential read");
        Ok(cred)
    }

    /// Check whether a credential file exists for the given provider.
    pub fn exists(&self, provider: &Provider) -> bool {
        self.cred_dir.join(format!("{provider}.enc")).exists()
    }
}

// ---------------------------------------------------------------------------
// Auth state checkpoint (VSH-007 integration)
// ---------------------------------------------------------------------------

/// Subset of auth state included in the VSH-007 checkpoint so that model
/// context survives Core crashes and restarts.
///
/// This deliberately does NOT include the raw token or credentials — only the
/// epoch (so Brain can detect a rekey) and the active model name.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuthCheckpointState {
    /// Current token epoch. On crash recovery Brain compares this against the
    /// reloaded token file to detect whether a rekey occurred mid-session.
    pub epoch: u64,
    /// Name of the model that was active when the checkpoint was written.
    pub active_model: Option<String>,
    /// Filesystem path of the token file (so recovery code knows where to look).
    pub token_path: String,
}

impl AuthCheckpointState {
    pub fn new(epoch: u64, active_model: Option<String>, auth_dir: &Path) -> Self {
        Self {
            epoch,
            active_model,
            token_path: auth_dir.join("core.token").to_string_lossy().into_owned(),
        }
    }
}

// ---------------------------------------------------------------------------
// Unit tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;
    use tempfile::TempDir;

    fn tmp_dir() -> TempDir {
        tempfile::tempdir().expect("tempdir")
    }

    #[test]
    fn redacted_never_reveals_value() {
        let r = Redacted::new("super-secret-key".to_string());
        assert_eq!(format!("{r:?}"), "[REDACTED]");
        assert_eq!(format!("{r}"), "[REDACTED]");
        // But expose() gives access at the call-site.
        assert_eq!(r.expose(), "super-secret-key");
    }

    #[test]
    fn token_generation_and_validation() {
        let dir = tmp_dir();
        let mgr = TokenManager::init(dir.path(), None, Duration::from_secs(3600)).unwrap();
        assert_eq!(mgr.current().epoch, 1);

        let tok = mgr.current().token.clone();
        let epoch = mgr.current().epoch;

        // Correct token + epoch → ok.
        assert!(mgr.validate(&tok, epoch).is_ok());

        // Wrong token → err.
        assert!(matches!(
            mgr.validate("wrong", epoch),
            Err(AuthError::InvalidToken)
        ));

        // Wrong epoch → EpochMismatch.
        assert!(matches!(
            mgr.validate(&tok, 99),
            Err(AuthError::EpochMismatch { .. })
        ));
    }

    #[test]
    fn token_file_is_mode_600() {
        let dir = tmp_dir();
        TokenManager::init(dir.path(), None, Duration::from_secs(3600)).unwrap();
        let path = dir.path().join("core.token");
        let meta = fs::metadata(&path).unwrap();
        let mode = meta.permissions().mode();
        // Owner read+write only (0600).
        assert_eq!(mode & 0o777, 0o600, "token file must be mode 600");
    }

    #[test]
    fn rekey_increments_epoch() {
        let dir = tmp_dir();
        let mut mgr = TokenManager::init(dir.path(), None, Duration::from_secs(3600)).unwrap();
        assert_eq!(mgr.current().epoch, 1);

        let old_token = mgr.current().token.clone();
        mgr.rekey(Duration::from_secs(3600)).unwrap();

        assert_eq!(mgr.current().epoch, 2);
        assert_ne!(mgr.current().token, old_token);

        // Old token + old epoch → EpochMismatch.
        assert!(matches!(
            mgr.validate(&old_token, 1),
            Err(AuthError::EpochMismatch { .. })
        ));
    }

    #[test]
    fn token_load_roundtrip() {
        let dir = tmp_dir();
        let mgr = TokenManager::init(dir.path(), None, Duration::from_secs(3600)).unwrap();
        let epoch = mgr.current().epoch;
        let token = mgr.current().token.clone();

        let loaded = TokenManager::load(dir.path()).unwrap();
        assert_eq!(loaded.current().epoch, epoch);
        assert_eq!(loaded.current().token, token);
    }

    #[test]
    fn credential_store_roundtrip() {
        let dir = tmp_dir();
        let store = CredentialStore::open(dir.path()).unwrap();

        let cred = Credential {
            provider: Provider::Anthropic,
            api_key: Redacted::new("sk-ant-test123".to_string()),
            base_url: None,
        };

        store.write(&cred).unwrap();
        assert!(store.exists(&Provider::Anthropic));

        let loaded = store.read(&Provider::Anthropic).unwrap();
        assert_eq!(loaded.provider, Provider::Anthropic);
        // The key is wrapped in Redacted — use expose() to verify.
        assert_eq!(loaded.api_key.expose(), "sk-ant-test123");
    }

    #[test]
    fn credential_file_is_mode_600() {
        let dir = tmp_dir();
        let store = CredentialStore::open(dir.path()).unwrap();
        let cred = Credential {
            provider: Provider::OpenAI,
            api_key: Redacted::new("sk-openai-test".to_string()),
            base_url: None,
        };
        store.write(&cred).unwrap();
        let path = dir.path().join("credentials").join("openai.enc");
        let meta = fs::metadata(&path).unwrap();
        assert_eq!(meta.permissions().mode() & 0o777, 0o600);
    }

    #[test]
    fn master_key_is_mode_600() {
        let dir = tmp_dir();
        CredentialStore::open(dir.path()).unwrap();
        let path = dir.path().join("master.key");
        let meta = fs::metadata(&path).unwrap();
        assert_eq!(meta.permissions().mode() & 0o777, 0o600);
    }

    #[test]
    fn auth_checkpoint_state() {
        let dir = tmp_dir();
        let state = AuthCheckpointState::new(3, Some("claude-sonnet".to_string()), dir.path());
        assert_eq!(state.epoch, 3);
        assert_eq!(state.active_model.as_deref(), Some("claude-sonnet"));
        assert!(state.token_path.contains("core.token"));
    }
}
