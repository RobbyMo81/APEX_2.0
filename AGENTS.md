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

---

## VSH-002 — 2026-05-03

### Pattern Discovered
`DockerClient` shells out to the `docker` CLI via `tokio::process::Command` with `-H unix://<socket_path>` to target a specific socket. `--privileged` is stripped at descriptor construction (never forwarded in argv) and also blocked before the firewall is called, so the firewall never sees a privileged request. `docker_prune` and `docker_rm` action classes are in `ALWAYS_TIER2_CLASSES` in `firewall.rs` — no configuration can override this.

### Pattern: `new_with_home` constructor idiom
Any struct that derives its audit/config paths from `$HOME` must expose a `new_with_home(home: &Path)` variant alongside `new()`. `new()` reads the env var; `new_with_home` takes an explicit path. Tests always call the `_with_home` variant to eliminate the `env::set_var` race condition. Applied to both `DockerClient` and `FileEngine`.

### Gotcha: env::set_var races in parallel unit tests
Setting `HOME` via `env::set_var` in a test helper and then reading it in a constructor creates a race window when `cargo test` runs tests in parallel threads. Symptoms: intermittent audit log path mismatches — the log is written to the `tmp` dir of a _different_ concurrent test. Fix: add `new_with_home(home: &Path)` and use it in every test that checks audit log presence.

### Gotcha: load_workspace_roots uses HOME env var
`load_workspace_roots()` reads `$HOME` at call time. Tests that set `HOME` and immediately call it are still racy. Use `load_workspace_roots_from(tmp.path())` in tests.

### Files Modified
- `core/src/docker.rs` — VSH-002 Docker engine; added `new_with_home`; `use std::path::Path`
- `core/src/files.rs` — added `FileEngine::new_with_home`; added `load_workspace_roots_from`; fixed 4 tests to use explicit-home constructors
- `prd.json` — VSH-002 marked status=done, passes=true

## VSH-004 — 2026-05-03

### Pattern Discovered
`FileEngine::resolve_and_check` walks the path component-by-component, checking each symlink as it's encountered. This detects multi-hop escapes before the `ActionDescriptor` is constructed — the firewall never sees an escaped path. This is the canonical pre-FW safety gate pattern for all file operations.

### Pattern: Tier enforcement by operation type
- `file_read`, `file_write`, `file_list`: Tier 1 inside workspace, Tier 2 outside (scope-based via `scope_for()`).
- `file_delete`, `file_move`: Always Tier 2 via `ALWAYS_TIER2_CLASSES` in `firewall.rs` — irreversibility is enforced at the classifier level, not the caller.
- Defense-in-depth: even if the firewall classifier returned Tier 1 for delete/move (impossible given ALWAYS_TIER2), the `match` arm treats `Tier::Tier1` as Tier2 pending.

### Pattern: `new_with_home` constructor idiom (also seen in VSH-002)
`FileEngine::new_with_home(roots, home)` takes an explicit home path so tests never touch `env::set_var`. `load_workspace_roots_from(home)` does the same. All tests that check audit log output must use these variants.

### Gotcha: Symlink escape check stops at first non-existent component
`resolve_and_check` stops walking when it hits a non-existent path component. This is intentional for write targets (the file doesn't exist yet). The parent directory is still checked. If the parent itself escapes, that is caught.

### Gotcha: Empty workspace roots means all operations → Tier 2
If `~/.vashion/config.toml` is missing or has no `[workspace]` section, `workspace_roots` is empty. `is_within_workspace()` returns false for every path. All read/write/list operations escalate to Tier 2 minimum. This is the correct safe default.

### Files Modified
- `core/src/files.rs` — VSH-004 full implementation (workspace config reader, symlink escape detection, read/write/delete/move/list with tier enforcement, audit logging, 16 unit tests)
- `prd.json` — VSH-004 marked status=done, passes=true
