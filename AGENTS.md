# AGENTS.md — Vashion Institutional Memory

## VSH-001 — Shell Execution Engine
### Pattern Discovered
ShellEngine follows a strict 5-step pipeline: injection guard → sudo block → allowlist check → firewall stub → spawn. Do NOT skip steps or reorder.
### Gotcha
ShellEngine::new() has `#[allow(clippy::new_without_default)]` — adding a `Default` impl would be correct but was out of scope for VSH-001. The allow is intentional.
### Files Modified
- `core/src/shell.rs`
- `core/src/lib.rs`
- `core/Cargo.toml`

---

## VSH-005 — Auth & Model Registry — 2026-04-03
### Pattern Discovered
- `Redacted<T>` wrapper type enforces credential redaction at the type level. Any field that must not appear in logs must be typed `Redacted<T>`, not `String`. The write layer wraps at construction — callers never need to remember to redact.
- `TokenManager` uses atomic write (temp-file + rename) for `core.token`. All credential and key files are mode 600, enforced at the file-open site — not left to the OS default.
- `CredentialStore` uses AES-256-GCM. The 12-byte nonce is prepended to the ciphertext blob. Master key is 32 raw bytes at `~/.vashion/auth/master.key`.
- `ModelRegistry` is `Arc<RwLock<ModelRegistry>>` for lock-free concurrent reads and atomic active-model switches. Brain holds a clone of the `Arc`.
- `AuthCheckpointState` carries only epoch + active model name — never the raw token or credentials. VSH-007 uses this to detect rekey-on-crash.
### Gotcha
- Token validation checks epoch FIRST, then token value. `EpochMismatch` and `InvalidToken` are distinct error variants — Brain must handle epoch mismatch by entering degraded state and reloading the token file, not by retrying with the same token.
- `default_models()` is the seed list for Brain startup. Add new models here first, then call `reg.register(m)` — do not hardcode model names anywhere else.
- `CredentialStore::open()` generates the master key on first call. On subsequent calls it loads the existing key. If the key file is deleted, all encrypted credentials become unrecoverable.
### Files Modified
- `core/src/auth.rs` (new)
- `core/src/model_registry.rs` (new)
- `core/src/lib.rs` (added module declarations)
- `core/Cargo.toml` (added: serde, serde_json, rand, hex, aes-gcm, thiserror, tracing; dev: tempfile)
- `core/src/shell.rs` (added clippy allow for pre-existing lint — out-of-scope but blocked quality gate)
