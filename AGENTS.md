# AGENTS.md — Vashion Institutional Memory
# Every FORGE agent appends here after completing a story. Never delete entries.

---

## VSH-005 — 2026-05-02

### Pattern Discovered
`Redacted<T>` is the canonical way to hold credentials in memory. `Debug`, `Display`, and `Serialize` all emit `[REDACTED]`. The inner value is accessible only via `.expose()` — intentionally verbose to prevent accidental logging. Any struct that contains a credential field must use `Redacted<String>`, not a plain `String`. This is enforced at the type level, not by caller convention.

### Pattern: SessionManager / BrainSession rekey handshake
Core owns `SessionManager` (token + epoch). Brain owns `BrainSession`. When Core restarts and rekeys:
1. Core calls `mgr.rekey()` → epoch increments, new token written to `~/.vashion/auth/core.token`.
2. Brain's next socket call gets `WrongEpoch` back.
3. Brain calls `brain_session.handle_wrong_epoch()` → reloads token file → back to Nominal.
No manual intervention required. `BrainAuthState::is_degraded()` is the health endpoint signal.

### Pattern: ModelRegistry write gate
`ModelRegistry` uses `&mut self` for all mutations (`register`, `remove`, `set_active`). Queries use `&self`. The borrow checker enforces the "reads are OK, writes require explicit mut" contract — no runtime lock needed for single-threaded use.

### Pattern: CredentialStore encryption
AES-256-GCM. Master key at `~/.vashion/auth/master.key` (32 raw bytes, mode 600). Each provider credential: JSON envelope `{ nonce: base64, ciphertext: base64 }` at `~/.vashion/auth/<provider>.cred` (mode 600). Key is zeroized from memory on `CredentialStore` drop.

### Gotcha: Concurrent tests + env::set_var
The pre-existing firewall test `audit_log_written_for_tier3` was intermittently failing due to `env::set_var("HOME", ...)` races across parallel tests. By the time VSH-005 was merged it was passing (tests run sequentially in this config), but this is fragile. Auth tests avoid `set_var` entirely — they pass the base path directly to `SessionManager::new()` and `CredentialStore::new()`.

### Gotcha: aes-gcm 0.11 is still RC
Use `aes-gcm = "0.10"` (stable). `0.11.0-rc.3` is the latest on crates.io but is a release candidate. Cargo.toml pinned to `"0.10"`.

### Gotcha: AuthCheckpointState contains NO credentials
`AuthCheckpointState` only captures epoch + active model name/provider. Credentials stay encrypted on disk. VSH-007 restores auth state by calling `CredentialStore::load()` again on recovery — not by reading the checkpoint.

### Files Modified
- `core/Cargo.toml` — added aes-gcm, rand, base64, zeroize, toml
- `core/src/auth.rs` — new file (VSH-005 full implementation)
- `core/src/lib.rs` — added `pub mod auth;`
